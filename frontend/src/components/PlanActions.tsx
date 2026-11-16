import { useState } from 'react'
import { BookmarkPlus, CalendarPlus, Check } from 'lucide-react'
import { api } from '../lib/api'
import { today } from '../lib/dates'
import type { SavedPlan } from '../lib/types'

/** Schedule a saved plan on the calendar from a start date. */
export function ScheduleForm({ plan, onDone }: { plan: SavedPlan; onDone?: (n: number) => void }) {
  const [start, setStart] = useState(today())
  const [weeks, setWeeks] = useState(1)
  const [msg, setMsg] = useState<string | null>(null)
  return (
    <form className="flex flex-wrap items-end gap-2" onSubmit={async e => {
      e.preventDefault()
      try {
        const r = await api.schedule(plan.id, start, weeks)
        setMsg(`Added ${r.entries.length} day${r.entries.length === 1 ? '' : 's'} to your calendar`)
        onDone?.(r.entries.length)
      } catch (x) { setMsg((x as Error).message) }
    }}>
      <label className="text-xs text-dim">Start
        <input type="date" value={start} onChange={e => setStart(e.target.value)}
          className="block mt-1 rounded-lg bg-plum border border-line px-2 py-1.5 text-sm text-cream" />
      </label>
      <label className="text-xs text-dim">Weeks
        <select value={weeks} onChange={e => setWeeks(Number(e.target.value))}
          className="block mt-1 rounded-lg bg-plum border border-line px-2 py-1.5 text-sm text-cream">
          {[1, 2, 4, 8].map(n => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      <button className="inline-flex items-center gap-1.5 rounded-full bg-turmeric text-night text-sm font-semibold px-3.5 py-1.5">
        <CalendarPlus size={14} aria-hidden /> Add to calendar
      </button>
      {msg && <p role="status" className="w-full text-xs text-leaf m-0">{msg}</p>}
    </form>
  )
}

/** Save the conversation's checked plan (server takes it from validated state), then schedule it. */
export function PlanActions({ threadId, kind }: { threadId: string; kind: 'meal' | 'workout' }) {
  const [saved, setSaved] = useState<SavedPlan | null>(null)
  const [err, setErr] = useState<string | null>(null)
  if (saved) {
    return (
      <div className="rounded-2xl border border-leaf/40 bg-leaf/5 p-3 space-y-2">
        <p className="text-sm text-leaf m-0 inline-flex items-center gap-1.5"><Check size={14} aria-hidden /> Saved as “{saved.title}”</p>
        <ScheduleForm plan={saved} />
      </div>
    )
  }
  return (
    <div className="flex items-center gap-3">
      <button onClick={async () => { try { setSaved(await api.savePlan(threadId, kind)) } catch (x) { setErr((x as Error).message) } }}
        className="inline-flex items-center gap-1.5 rounded-full border border-turmeric/60 text-turmeric text-sm px-3.5 py-1.5 hover:bg-turmeric/10">
        <BookmarkPlus size={14} aria-hidden /> Save {kind === 'meal' ? 'meal plan' : 'workout'}
      </button>
      {err && <span role="alert" className="text-xs text-chilli">{err}</span>}
    </div>
  )
}
