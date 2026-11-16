import { useEffect, useState } from 'react'
import { CheckCircle2, Dumbbell, Scale, Sparkles, Trash2, Utensils } from 'lucide-react'
import clsx from 'clsx'
import { api } from '../lib/api'
import type { Goal, Profile } from '../lib/types'
import { Empty } from '../components/Panels'

const KIND = {
  weight: { label: 'Weight', icon: Scale, color: 'text-beet' },
  workout: { label: 'Workout', icon: Dumbbell, color: 'text-chilli' },
  nutrition: { label: 'Nutrition', icon: Utensils, color: 'text-turmeric' },
  habit: { label: 'Habit', icon: Sparkles, color: 'text-cardamom' },
}

function progress(g: Goal): number | null {
  if (g.target == null || g.current == null) return null
  if (g.kind === 'weight' && g.start != null && g.start !== g.target) return Math.max(0, Math.min(1, (g.current - g.start) / (g.target - g.start)))
  return g.target ? Math.max(0, Math.min(1, g.current / g.target)) : null
}

function suggestions(p: Profile): Partial<Goal>[] {
  const out: Partial<Goal>[] = []
  if (p.weight_kg && p.target_weight_kg) out.push({ kind: 'weight', title: `Reach ${p.target_weight_kg} kg`, target: p.target_weight_kg, unit: 'kg', current: p.weight_kg })
  else if (p.weight_kg && (p.goal === 'gain_muscle' || p.goal === 'gain_weight')) out.push({ kind: 'weight', title: `Gain 3 kg (to ${p.weight_kg + 3} kg)`, target: p.weight_kg + 3, unit: 'kg', current: p.weight_kg })
  out.push({ kind: 'workout', title: `Train ${p.training_days_per_week || 3} times a week`, target: p.training_days_per_week || 3, unit: 'sessions/week', current: 0 })
  out.push({ kind: 'nutrition', title: 'Hit my protein target every day this week', target: 7, unit: 'days', current: 0 })
  out.push({ kind: 'habit', title: 'Walk 8,000 steps a day', target: 8000, unit: 'steps', current: 0 })
  return out
}

export function GoalsPage({ profile }: { profile: Profile }) {
  const [goals, setGoals] = useState<Goal[] | null>(null)
  const [draft, setDraft] = useState<Partial<Goal>>({ kind: 'weight', title: '', unit: 'kg' })
  const load = () => api.goals().then(r => setGoals(r.goals)).catch(() => setGoals([]))
  useEffect(() => { load() }, [])
  const add = async (g: Partial<Goal>) => { if (g.title?.trim()) { await api.addGoal(g); setDraft({ kind: 'weight', title: '', unit: 'kg' }); load() } }
  const input = 'rounded-lg bg-plum border border-line px-2.5 py-1.5 text-sm'
  return (
    <div className="grid lg:grid-cols-[minmax(0,1fr)_20rem] gap-6">
      <section aria-label="Your goals">
        {goals && !goals.length && <Empty title="No goals yet" body="Add one on the right. Your active goals are shared with the agents, so plans and answers take them into account." />}
        <ul className="space-y-3 list-none p-0">
          {goals?.map(g => {
            const k = KIND[g.kind]
            const pct = progress(g)
            return (
              <li key={g.id} className={clsx('rounded-2xl border bg-plum p-4', g.status === 'done' ? 'border-leaf/40' : 'border-line')}>
                <div className="flex items-start gap-3">
                  <k.icon size={18} className={clsx(k.color, 'mt-0.5')} aria-hidden />
                  <div className="flex-1">
                    <p className="m-0 font-display font-medium">{g.title}</p>
                    <p className="m-0 text-xs text-dim">{k.label}{g.deadline ? ` · by ${g.deadline}` : ''}{g.status === 'done' ? ' · done' : ''}</p>
                  </div>
                  <button aria-label={g.status === 'done' ? 'Mark active' : 'Mark done'} title={g.status === 'done' ? 'Mark active' : 'Mark done'}
                    onClick={async () => { await api.updateGoal(g.id, { status: g.status === 'done' ? 'active' : 'done' }); load() }}
                    className={clsx('p-1.5 rounded-full', g.status === 'done' ? 'text-leaf' : 'text-dim hover:text-leaf')}><CheckCircle2 size={16} /></button>
                  <button aria-label={`Delete ${g.title}`} onClick={async () => { await api.deleteGoal(g.id); load() }}
                    className="p-1.5 rounded-full text-dim hover:text-chilli"><Trash2 size={15} /></button>
                </div>
                {g.target != null && (
                  <div className="mt-3 flex items-center gap-3">
                    <div className="flex-1 h-2 rounded-full bg-plum-2 overflow-hidden" role="progressbar" aria-label={`${g.title} progress`}
                      aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((pct ?? 0) * 100)}>
                      <div className="h-full bg-turmeric rounded-full transition-[width]" style={{ width: `${(pct ?? 0) * 100}%` }} />
                    </div>
                    <label className="text-xs text-dim inline-flex items-center gap-1.5">
                      <span className="sr-only">Current value</span>
                      <input type="number" step="any" defaultValue={g.current ?? ''} className={clsx(input, 'w-20 py-1 text-xs')}
                        onBlur={async e => { if (e.target.value !== '') { await api.updateGoal(g.id, { current: Number(e.target.value) }); load() } }} />
                      / {g.target} {g.unit}
                    </label>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      </section>
      <aside className="space-y-4">
        <form className="rounded-2xl border border-line bg-plum p-4 space-y-2.5" onSubmit={e => { e.preventDefault(); add(draft) }}>
          <h3 className="font-display text-lg font-semibold m-0">New goal</h3>
          <label className="block text-xs text-dim">Type
            <select value={draft.kind} onChange={e => setDraft({ ...draft, kind: e.target.value as Goal['kind'] })} className={clsx(input, 'block w-full mt-1')}>
              {Object.entries(KIND).map(([v, k]) => <option key={v} value={v}>{k.label}</option>)}
            </select>
          </label>
          <label className="block text-xs text-dim">Goal
            <input value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} maxLength={80} required
              placeholder="e.g. Reach 65 kg" className={clsx(input, 'block w-full mt-1')} />
          </label>
          <div className="grid grid-cols-3 gap-2">
            <label className="text-xs text-dim">Now<input type="number" step="any" value={draft.current ?? ''} onChange={e => setDraft({ ...draft, current: e.target.value === '' ? null : Number(e.target.value) })} className={clsx(input, 'block w-full mt-1')} /></label>
            <label className="text-xs text-dim">Target<input type="number" step="any" value={draft.target ?? ''} onChange={e => setDraft({ ...draft, target: e.target.value === '' ? null : Number(e.target.value) })} className={clsx(input, 'block w-full mt-1')} /></label>
            <label className="text-xs text-dim">Unit<input value={draft.unit ?? ''} onChange={e => setDraft({ ...draft, unit: e.target.value })} maxLength={20} className={clsx(input, 'block w-full mt-1')} /></label>
          </div>
          <label className="block text-xs text-dim">By (optional)
            <input type="date" value={draft.deadline ?? ''} onChange={e => setDraft({ ...draft, deadline: e.target.value || null })} className={clsx(input, 'block w-full mt-1')} />
          </label>
          <button className="rounded-full bg-turmeric text-night font-semibold text-sm px-4 py-1.5">Add goal</button>
        </form>
        <div className="space-y-1.5">
          <p className="text-xs text-dim m-0">Suggestions from your profile</p>
          {suggestions(profile).filter(s => !goals?.some(g => g.title === s.title)).map(s => (
            <button key={s.title} onClick={() => add(s)}
              className="block w-full text-left text-sm rounded-xl border border-dashed border-line px-3 py-2 hover:border-turmeric">+ {s.title}</button>
          ))}
        </div>
      </aside>
    </div>
  )
}
