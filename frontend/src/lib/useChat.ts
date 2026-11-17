import { useCallback, useRef, useState } from 'react'
import { api } from './api'
import { AGENT_BY_NAME, type AgentEvent, type AgentKey, type FinalState, type Pending } from './types'

export type AgentStatus = 'idle' | 'thinking' | 'working' | 'done' | 'failed' | 'skipped'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  state?: FinalState
  error?: boolean
}

export interface Flight { id: number; from: AgentKey; to: AgentKey; kind: 'handoff' | 'return' | 'override' }

export interface AgentView {
  status: AgentStatus
  tool?: string
  runs: number
  ms?: number
}

const AGENTS: AgentKey[] = ['supervisor', 'health_assessment', 'nutrition_knowledge', 'meal_planning', 'workout_planning', 'validation', 'recommendation']
const freshAgents = () => Object.fromEntries(AGENTS.map(a => [a, { status: 'idle', runs: 0 } as AgentView])) as Record<AgentKey, AgentView>

let flightId = 0
const newThread = () => `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`

export function useChat() {
  const [threadId, setThreadId] = useState(newThread)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [agents, setAgents] = useState(freshAgents)
  const [flights, setFlights] = useState<Flight[]>([])
  const [busy, setBusy] = useState(false)
  const [latest, setLatest] = useState<FinalState | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const lastAgent = useRef<AgentKey>('supervisor')
  const abort = useRef<AbortController | null>(null)

  const patch = (k: AgentKey, v: Partial<AgentView>) => setAgents(a => ({ ...a, [k]: { ...a[k], ...v } }))
  const fly = (from: AgentKey, to: AgentKey, kind: Flight['kind']) => {
    const f = { id: ++flightId, from, to, kind }
    setFlights(fs => [...fs.slice(-8), f])
  }

  const onAgent = useCallback((e: AgentEvent) => {
    setEvents(es => [...es, e])
    if (e.type === 'supervisor_decision') {
      const prev = lastAgent.current
      if (prev !== 'supervisor') fly(prev, 'supervisor', 'return')
      patch('supervisor', { status: 'thinking' })
      const next = (e.next === 'clarify' ? 'recommendation' : e.next) as AgentKey
      if (next) {
        setTimeout(() => fly('supervisor', next, e.override ? 'override' : 'handoff'), 350)
        lastAgent.current = next
      }
      return
    }
    const key = e.agent ? AGENT_BY_NAME[e.agent] : undefined
    if (!key) return
    if (e.type === 'agent_started') {
      patch('supervisor', { status: 'done' })
      setAgents(a => ({ ...a, [key]: { ...a[key], status: 'thinking', runs: a[key].runs + 1, tool: undefined } }))
    } else if (e.type === 'tool_call') patch(key, { status: 'working', tool: e.tool })
    else if (e.type === 'tool_result' || e.type === 'agent_thought') patch(key, { status: 'thinking' })
    else if (e.type === 'agent_finished') patch(key, { status: e.status === 'done' ? 'done' : 'failed', tool: undefined, ms: e.ms })
  }, [])

  /** Run a turn; text=null resumes the interrupted one from its last checkpoint. */
  const run = useCallback(async (text: string | null) => {
    if (busy || (text !== null && !text.trim())) return
    setBusy(true)
    setPending(null)
    setEvents([])
    setFlights([])
    setAgents(freshAgents())
    lastAgent.current = 'supervisor'
    if (text !== null) setMessages(m => [...m, { id: crypto.randomUUID(), role: 'user', text }])
    else setMessages(m => m.filter(x => !x.error))
    abort.current = new AbortController()
    let failed = false
    try {
      await api.stream(threadId, text, {
        agent: onAgent,
        final: s => {
          setLatest(s)
          setAgents(a => {
            const out = { ...a }
            for (const k of AGENTS) if (out[k].status === 'idle') out[k] = { ...out[k], status: 'skipped' }
            out.supervisor = { ...out.supervisor, status: 'done' }
            return out
          })
          const r = s.recommendation
          setMessages(m => [...m, { id: crypto.randomUUID(), role: 'assistant', text: r?.summary ?? '', state: s }])
        },
        error: msg => {
          failed = true
          setMessages(m => [...m, { id: crypto.randomUUID(), role: 'assistant', text: msg, error: true }])
        },
      }, abort.current.signal)
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        failed = true
        setMessages(m => [...m, { id: crypto.randomUUID(), role: 'assistant', text: (err as Error).message, error: true }])
      }
    } finally {
      setBusy(false)
    }
    // A dropped connection or API restart leaves the turn's checkpoint behind: offer to continue from it.
    if (failed) api.pending(threadId).then(r => setPending(r.pending)).catch(() => {})
  }, [busy, threadId, onAgent])

  const send = useCallback((text: string) => run(text), [run])
  const resume = useCallback(() => run(null), [run])

  /** Reopen a past conversation: its messages, the last state shown and the last run's activity. */
  const open = useCallback(async (id: string) => {
    abort.current?.abort()
    const t = await api.threadTurns(id)
    const msgs: ChatMessage[] = []
    for (const turn of t.turns) {
      msgs.push({ id: `${turn.run_id}-u`, role: 'user', text: turn.request })
      msgs.push({ id: `${turn.run_id}-a`, role: 'assistant', text: turn.state?.recommendation?.summary ?? '', state: turn.state })
    }
    if (t.pending) msgs.push({ id: 'pending-u', role: 'user', text: t.pending.request })
    const view = freshAgents()
    for (const e of t.events) {
      const key = e.agent ? AGENT_BY_NAME[e.agent] : undefined
      if (key && e.type === 'agent_finished')
        view[key] = { status: e.status === 'done' ? 'done' : 'failed', runs: view[key].runs + 1, ms: e.ms }
    }
    for (const k of AGENTS) if (view[k].status === 'idle') view[k] = { ...view[k], status: 'skipped' }
    view.supervisor = { status: 'done', runs: 1 }
    setThreadId(id); setMessages(msgs); setEvents(t.events); setFlights([]); setAgents(view)
    setLatest(t.turns.at(-1)?.state ?? null)
    setPending(t.pending)
  }, [])

  const reset = () => {
    abort.current?.abort()
    setThreadId(newThread()); setMessages([]); setEvents([]); setFlights([]); setAgents(freshAgents()); setLatest(null); setPending(null)
  }

  return { threadId, messages, events, agents, flights, busy, latest, pending, send, resume, reset, open }
}
