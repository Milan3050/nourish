import { useEffect, useState } from 'react'
import { ChevronDown, Dumbbell, Salad, Trash2 } from 'lucide-react'
import clsx from 'clsx'
import { api } from '../lib/api'
import type { SavedPlan } from '../lib/types'
import { Empty, PlanPanel } from '../components/Panels'
import { WorkoutView } from '../components/WorkoutView'
import { ScheduleForm } from '../components/PlanActions'

export function PlansPage() {
  const [plans, setPlans] = useState<SavedPlan[] | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const load = () => api.plans().then(r => setPlans(r.plans)).catch(() => setPlans([]))
  useEffect(() => { load() }, [])
  if (!plans) return <p className="text-dim">Loading plans…</p>
  if (!plans.length) return <Empty title="No saved plans yet" body="When a meal plan or workout passes its checks in the chat, use Save to keep it here and put it on your calendar." />
  return (
    <ul className="space-y-3 list-none p-0">
      {plans.map(p => {
        const isOpen = open === p.id
        const Icon = p.kind === 'meal' ? Salad : Dumbbell
        return (
          <li key={p.id} className={clsx('rounded-2xl border', isOpen ? 'border-turmeric/50 bg-plum-2' : 'border-line bg-plum')}>
            <div className="flex items-center gap-3 px-4 py-3">
              <Icon size={18} className={p.kind === 'meal' ? 'text-turmeric' : 'text-chilli'} aria-hidden />
              <button className="flex-1 text-left" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : p.id)}>
                <span className="font-display font-medium">{p.title}</span>
                <span className="block text-xs text-dim">
                  Saved {new Date(p.created_at).toLocaleDateString()} ·{' '}
                  {p.kind === 'meal'
                    ? `${p.data.days.length} day(s), about ${Math.round(p.data.days[0]?.totals?.kcal ?? 0)} kcal a day`
                    : `${p.data.sessions.length} sessions, ${p.data.totals.minutes_week} min a week`}
                </span>
              </button>
              <ChevronDown size={16} className={clsx('text-dim transition-transform', isOpen && 'rotate-180')} aria-hidden />
              <button aria-label={`Delete ${p.title}`} title="Delete" onClick={async () => { await api.deletePlan(p.id); load() }}
                className="p-1.5 rounded-full text-dim hover:text-chilli hover:bg-chilli/10"><Trash2 size={15} /></button>
            </div>
            {isOpen && (
              <div className="px-4 pb-4 space-y-4">
                <ScheduleForm plan={p} />
                {p.kind === 'meal'
                  ? <PlanPanel plan={p.data} health={null} validation={null} targets={p.targets} />
                  : <WorkoutView workout={p.data} />}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
