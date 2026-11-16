import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Dumbbell, Salad, X } from 'lucide-react'
import clsx from 'clsx'
import { api } from '../lib/api'
import type { CalendarEntry } from '../lib/types'

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const WEEK = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export function CalendarPage() {
  const [todayIso] = useState(() => iso(new Date()))
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1) })
  const [entries, setEntries] = useState<CalendarEntry[]>([])
  const [selected, setSelected] = useState(todayIso)
  const days = useMemo(() => {
    const first = new Date(month)
    first.setDate(1 - ((month.getDay() + 6) % 7))  // back to Monday
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(first); d.setDate(first.getDate() + i); return d })
  }, [month])
  const load = () => api.calendar(iso(days[0]), iso(days[41])).then(r => setEntries(r.entries)).catch(() => setEntries([]))
  useEffect(() => { load() }, [days])  // eslint-disable-line react-hooks/exhaustive-deps
  const byDate = (d: string) => entries.filter(e => e.date === d)
  const picked = byDate(selected)
  const toggle = async (e: CalendarEntry) => { await api.setDone(e.id, !e.done); load() }

  return (
    <div className="grid lg:grid-cols-[minmax(0,1fr)_18rem] gap-6">
      <section aria-label="Month">
        <div className="flex items-center gap-2 mb-3">
          <button aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
            className="p-1.5 rounded-full border border-line hover:border-mist"><ChevronLeft size={16} /></button>
          <h2 className="font-display text-xl font-semibold m-0 flex-1 text-center">
            {month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
          </h2>
          <button aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
            className="p-1.5 rounded-full border border-line hover:border-mist"><ChevronRight size={16} /></button>
        </div>
        <div className="grid grid-cols-7 gap-1 text-xs text-dim mb-1">{WEEK.map(w => <div key={w} className="text-center">{w}</div>)}</div>
        <div className="grid grid-cols-7 gap-1">
          {days.map(d => {
            const k = iso(d)
            const items = byDate(k)
            const inMonth = d.getMonth() === month.getMonth()
            return (
              <button key={k} onClick={() => setSelected(k)} aria-pressed={selected === k}
                aria-label={`${d.toDateString()}${items.length ? `, ${items.length} planned` : ''}`}
                className={clsx('min-h-[4.5rem] rounded-xl border p-1.5 text-left align-top flex flex-col gap-1',
                  selected === k ? 'border-turmeric' : 'border-line/60 hover:border-line', inMonth ? 'bg-plum' : 'bg-transparent opacity-50')}>
                <span className={clsx('text-xs tabular-nums', k === todayIso ? 'text-turmeric font-bold' : 'text-mist')}>{d.getDate()}</span>
                {items.slice(0, 2).map(e => (
                  <span key={e.id} className={clsx('text-[10px] leading-tight rounded px-1 py-0.5 truncate',
                    e.kind === 'meal' ? 'bg-turmeric/20 text-turmeric' : 'bg-chilli/20 text-chilli', e.done && 'line-through opacity-60')}>
                    {e.kind === 'meal' ? 'Meals' : e.title}
                  </span>
                ))}
                {items.length > 2 && <span className="text-[10px] text-dim">+{items.length - 2}</span>}
              </button>
            )
          })}
        </div>
      </section>
      <aside aria-label="Selected day" className="space-y-3">
        <h3 className="font-display text-lg font-semibold m-0">{new Date(selected + 'T00:00').toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}</h3>
        {!picked.length && <p className="text-sm text-dim">Nothing planned. Save a plan from the chat and add it to the calendar.</p>}
        <ul className="space-y-2 list-none p-0">
          {picked.map(e => (
            <li key={e.id} className="rounded-xl border border-line bg-plum p-3 text-sm">
              <div className="flex items-start gap-2">
                {e.kind === 'meal' ? <Salad size={16} className="text-turmeric mt-0.5" aria-hidden /> : <Dumbbell size={16} className="text-chilli mt-0.5" aria-hidden />}
                <div className="flex-1">
                  <p className="m-0 font-medium">{e.kind === 'meal' ? 'Meal plan' : e.title}</p>
                  <p className="m-0 text-xs text-dim">{e.kind === 'meal' ? e.title : `${e.minutes ?? '?'} min · ${e.kcal ?? '?'} kcal`}</p>
                  <p className="m-0 text-xs text-dim">from “{e.plan_title}”</p>
                </div>
                <button aria-label="Remove from calendar" onClick={async () => { await api.unschedule(e.id); load() }}
                  className="p-1 rounded-full text-dim hover:text-chilli"><X size={14} /></button>
              </div>
              <label className="mt-2 flex items-center gap-2 text-xs text-mist cursor-pointer">
                <input type="checkbox" checked={e.done} onChange={() => toggle(e)} className="accent-[var(--color-leaf)]" /> Done
              </label>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  )
}
