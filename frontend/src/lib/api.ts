import type { AgentEvent, CalendarEntry, FinalState, Goal, Profile, RunDetail, RunSummary, SavedPlan, ThreadSummary, ThreadTurns } from './types'

const TOKEN_KEY = 'nip.token'
const NAME_KEY = 'nip.name'

function store(): Storage | null {
  try { return window.localStorage } catch { return null }
}

export const session = {
  get token() { return store()?.getItem(TOKEN_KEY) ?? null },
  get name() { return store()?.getItem(NAME_KEY) ?? null },
  save(token: string, name: string) {
    try { store()?.setItem(TOKEN_KEY, token); store()?.setItem(NAME_KEY, name) } catch { /* private mode */ }
  },
  clear() { try { store()?.removeItem(TOKEN_KEY); store()?.removeItem(NAME_KEY) } catch { /* ignore */ } },
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(session.token ? { Authorization: `Bearer ${session.token}` } : {}), ...init.headers },
  })
  if (r.status === 401) { session.clear(); throw new Error('Your session expired. Sign in again.') }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${r.status})`)
  }
  return r.json()
}

export const api = {
  async login(name: string) {
    const r = await req<{ token: string }>('/api/auth/dev-token', { method: 'POST', body: JSON.stringify({ name }) })
    session.save(r.token, name)
  },
  getProfile: () => req<{ profile: Profile | null }>('/api/profile'),
  putProfile: (p: Profile) => req<{ profile: Profile }>('/api/profile', { method: 'PUT', body: JSON.stringify(p) }),

  threads: () => req<{ threads: ThreadSummary[] }>('/api/threads'),
  threadTurns: (id: string) => req<ThreadTurns>(`/api/threads/${id}/turns`),

  runs: () => req<{ runs: RunSummary[]; total: { runs: number; cost_usd: number; reference_cost_usd: number; tokens: number } }>('/api/runs'),
  run: (id: string) => req<RunDetail>(`/api/runs/${id}`),

  plans: () => req<{ plans: SavedPlan[] }>('/api/plans'),
  savePlan: (thread_id: string, kind: 'meal' | 'workout', title?: string) =>
    req<SavedPlan>('/api/plans', { method: 'POST', body: JSON.stringify({ thread_id, kind, title }) }),
  deletePlan: (id: string) => req<{ ok: boolean }>(`/api/plans/${id}`, { method: 'DELETE' }),

  calendar: (start: string, end: string) => req<{ entries: CalendarEntry[] }>(`/api/calendar?start=${start}&end=${end}`),
  schedule: (plan_id: string, start_date: string, weeks = 1) =>
    req<{ entries: CalendarEntry[] }>('/api/calendar', { method: 'POST', body: JSON.stringify({ plan_id, start_date, weeks }) }),
  setDone: (id: string, done: boolean) => req<CalendarEntry>(`/api/calendar/${id}`, { method: 'PATCH', body: JSON.stringify({ done }) }),
  unschedule: (id: string) => req<{ ok: boolean }>(`/api/calendar/${id}`, { method: 'DELETE' }),

  goals: () => req<{ goals: Goal[] }>('/api/goals'),
  addGoal: (g: Partial<Goal>) => req<Goal>('/api/goals', { method: 'POST', body: JSON.stringify(g) }),
  updateGoal: (id: string, g: Partial<Goal>) => req<Goal>(`/api/goals/${id}`, { method: 'PATCH', body: JSON.stringify(g) }),
  deleteGoal: (id: string) => req<{ ok: boolean }>(`/api/goals/${id}`, { method: 'DELETE' }),

  /** POST + server-sent events. EventSource can't POST, so we parse the stream ourselves. */
  async stream(threadId: string, message: string, on: {
    agent: (e: AgentEvent) => void; final: (s: FinalState) => void; error: (m: string) => void
  }, signal?: AbortSignal) {
    const r = await fetch(`/api/chat/${threadId}/stream`, {
      method: 'POST', signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${session.token}` },
      body: JSON.stringify({ message }),
    })
    if (r.status === 429) return on.error('You are sending messages quickly. Wait a moment and try again.')
    if (!r.ok || !r.body) return on.error(`The assistant is unavailable (${r.status}). Check that the API is running.`)
    const reader = r.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      const parts = buf.split(/\r?\n\r?\n/)
      buf = parts.pop() ?? ''
      for (const part of parts) {
        let ev = 'message'; let data = ''
        for (const line of part.split(/\r?\n/)) {
          if (line.startsWith('event:')) ev = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (!data) continue
        const payload = JSON.parse(data)
        if (ev === 'agent') on.agent(payload)
        else if (ev === 'final') on.final(payload)
        else if (ev === 'error') on.error(payload.message)
      }
    }
  },
}
