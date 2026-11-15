import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef } from 'react'
import { AGENT_BY_NAME, AGENT_LABEL, type AgentEvent, type AgentKey } from '../lib/types'

function line(e: AgentEvent): { who: string; text: string; tone?: 'warn' | 'good' | 'bad' } | null {
  const who = e.agent ? AGENT_LABEL[AGENT_BY_NAME[e.agent] as AgentKey] ?? e.agent : 'Supervisor'
  switch (e.type) {
    case 'intake':
      return e.flags?.length ? { who: 'Input check', text: `Flagged: ${e.flags.join(', ').replaceAll('_', ' ')}`, tone: 'warn' } : null
    case 'supervisor_decision': {
      const to = AGENT_LABEL[(e.next === 'clarify' ? 'recommendation' : e.next) as AgentKey] ?? e.next
      if (e.override) return { who: 'Supervisor', text: `Rerouted to ${to}: ${e.override}`, tone: 'warn' }
      return { who: 'Supervisor', text: `${to}: ${e.reason ?? ''}` }
    }
    case 'tool_call':
      return { who, text: `Using ${e.tool?.replaceAll('_', ' ')}` }
    case 'submission_rejected':
      return { who, text: `Result not accepted yet: ${e.errors?.[0] ?? ''}`, tone: 'warn' }
    case 'agent_finished':
      return e.status === 'done'
        ? { who, text: `Finished in ${((e.ms ?? 0) / 1000).toFixed(1)} s`, tone: 'good' }
        : { who, text: `Stopped: ${e.error ?? 'no result'}`, tone: 'bad' }
    case 'validation_verdict':
      return { who: 'Validation', text: `Plan v${e.plan_version}: ${e.verdict}${e.critical ? `, ${e.critical} critical issue${e.critical > 1 ? 's' : ''}` : ''}`, tone: e.verdict === 'PASS' ? 'good' : 'bad' }
    default:
      return null
  }
}

const TONE = { warn: 'text-turmeric', good: 'text-leaf', bad: 'text-chilli' }

export function ActivityLog({ events }: { events: AgentEvent[] }) {
  const ref = useRef<HTMLOListElement>(null)
  const lines = events.map((e, i) => ({ i, l: line(e) })).filter(x => x.l)
  useEffect(() => { ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: 'smooth' }) }, [lines.length])
  if (!lines.length)
    return <p className="text-sm text-dim px-1">Each step the agents take shows up here while they work.</p>
  return (
    <ol ref={ref} className="scroll-thin overflow-y-auto max-h-full space-y-1.5 pr-1 text-[13px] leading-snug" aria-live="polite">
      <AnimatePresence initial={false}>
        {lines.map(({ i, l }) => (
          <motion.li key={i} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}
            transition={{ type: 'spring', stiffness: 400, damping: 30 }}
            className="grid grid-cols-[5.5rem_1fr] gap-2">
            <span className="text-dim">{l!.who}</span>
            <span className={l!.tone ? TONE[l!.tone] : 'text-mist'}>{l!.text}</span>
          </motion.li>
        ))}
      </AnimatePresence>
    </ol>
  )
}
