import { useEffect, useState } from 'react'
import { Coins, Cpu, HeartPulse, PenLine, Ruler, Scale } from 'lucide-react'
import clsx from 'clsx'
import { api } from '../lib/api'
import type { Health, Profile } from '../lib/types'
import { Stat } from './RunsPage'

const EQUIPMENT: [string, string][] = [['none', 'Bodyweight'], ['dumbbells', 'Dumbbells'], ['barbell', 'Barbell'], ['machines', 'Gym machines'],
  ['bands', 'Bands'], ['pullup_bar', 'Pull-up bar'], ['bench', 'Bench']]
const label = (s: string) => s.replaceAll('_', ' ')

function Row({ k, v }: { k: string; v: string | number | null | undefined }) {
  return <div><dt className="text-xs text-dim">{k}</dt><dd className="m-0">{v === '' || v == null ? '-' : v}</dd></div>
}

/** Profile summary, training setup (editable here), computed health numbers and usage totals. */
export function ProfilePage({ profile, name, health, onEdit, onSaved }: {
  profile: Profile; name: string; health: Health | null; onEdit: () => void; onSaved: (p: Profile) => void
}) {
  // Profiles saved before the training fields existed lack them: fill the defaults.
  const [p, setP] = useState<Profile>({ ...profile, equipment: profile.equipment ?? ['none'], injuries: profile.injuries ?? [],
    training_days_per_week: profile.training_days_per_week ?? 3, session_minutes: profile.session_minutes ?? 45,
    experience: profile.experience ?? 'beginner' })
  const [saved, setSaved] = useState<string | null>(null)
  const [totals, setTotals] = useState<{ runs: number; cost_usd: number; reference_cost_usd: number; tokens: number } | null>(null)
  useEffect(() => { api.runs().then(r => setTotals(r.total)).catch(() => {}) }, [])
  const bmi = health?.bmi ?? (profile.weight_kg && profile.height_cm ? +(profile.weight_kg / (profile.height_cm / 100) ** 2).toFixed(1) : null)
  const chip = (on: boolean) => clsx('rounded-full border px-3 py-1 text-sm', on ? 'border-turmeric bg-turmeric text-night' : 'border-line text-mist hover:border-mist')
  const save = async () => {
    try { const r = await api.putProfile(p); onSaved(r.profile); setSaved('Training settings saved') } catch (x) { setSaved((x as Error).message) }
  }
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="size-12 rounded-full bg-turmeric text-night grid place-items-center font-display text-xl font-bold" aria-hidden>{name.slice(0, 1).toUpperCase()}</div>
        <div className="flex-1">
          <h2 className="font-display text-2xl font-semibold m-0">{name}</h2>
          <p className="text-sm text-dim m-0">{profile.region === 'IN' ? 'India (IFCT food table)' : 'United States (USDA food table)'} · {label(profile.goal)}</p>
        </div>
        <button onClick={onEdit} className="inline-flex items-center gap-1.5 text-sm rounded-full border border-line px-3 py-1.5 hover:border-mist"><PenLine size={14} aria-hidden /> Edit details</button>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat icon={Scale} label="BMI" value={bmi ? String(bmi) : '-'} sub={health?.healthy_weight_kg?.length === 2 ? `healthy: ${health.healthy_weight_kg[0]}-${health.healthy_weight_kg[1]} kg` : 'ask a question to compute your targets'} />
        <Stat icon={HeartPulse} label="Daily energy target" value={health ? `${health.targets.kcal} kcal` : '-'} sub={health ? `protein ${health.targets.protein_g}${health.limits.protein_g_max ? `-${health.limits.protein_g_max}` : ''} g` : undefined} />
        <Stat icon={Cpu} label="Runs" value={totals ? String(totals.runs) : '-'} sub={totals ? `${totals.tokens.toLocaleString()} tokens` : undefined} />
        <Stat icon={Coins} label="Spent" value={totals ? `$${totals.cost_usd.toFixed(2)}` : '-'} sub={totals ? `hosted estimate $${totals.reference_cost_usd.toFixed(4)}` : undefined} />
      </div>
      <section className="rounded-2xl border border-line bg-plum p-4">
        <h3 className="font-display text-lg font-semibold mt-0 mb-3 inline-flex items-center gap-2"><Ruler size={16} aria-hidden /> About you</h3>
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-3 text-sm">
          <Row k="Age" v={profile.age} /><Row k="Sex" v={profile.sex} />
          <Row k="Height" v={profile.height_cm ? `${Math.round(profile.height_cm)} cm` : null} /><Row k="Weight" v={profile.weight_kg ? `${profile.weight_kg} kg` : null} />
          <Row k="Activity" v={label(profile.activity)} /><Row k="Diet" v={profile.diet_pattern} />
          <Row k="Usual meals a day" v={profile.meals_per_day} /><Row k="Religious rules" v={(profile.religious_rules ?? []).map(label).join(', ')} />
          <Row k="Allergies" v={(profile.allergies ?? []).join(', ')} /><Row k="Conditions" v={(profile.conditions ?? []).join(', ')} />
          <Row k="Medications" v={(profile.medications ?? []).join(', ')} /><Row k="Target weight" v={profile.target_weight_kg ? `${profile.target_weight_kg} kg` : null} />
        </dl>
      </section>
      <section className="rounded-2xl border border-line bg-plum p-4 space-y-4">
        <h3 className="font-display text-lg font-semibold m-0">Training</h3>
        <fieldset className="border-0 p-0 m-0"><legend className="text-xs text-dim mb-1.5 p-0">Training days a week</legend>
          <div className="flex flex-wrap gap-1.5">{[0, 1, 2, 3, 4, 5, 6].map(n => <button key={n} type="button" aria-pressed={p.training_days_per_week === n} className={chip(p.training_days_per_week === n)} onClick={() => setP({ ...p, training_days_per_week: n })}>{n}</button>)}</div>
        </fieldset>
        <fieldset className="border-0 p-0 m-0"><legend className="text-xs text-dim mb-1.5 p-0">Minutes per session</legend>
          <div className="flex flex-wrap gap-1.5">{[20, 30, 45, 60, 75, 90].map(n => <button key={n} type="button" aria-pressed={p.session_minutes === n} className={chip(p.session_minutes === n)} onClick={() => setP({ ...p, session_minutes: n })}>{n}</button>)}</div>
        </fieldset>
        <fieldset className="border-0 p-0 m-0"><legend className="text-xs text-dim mb-1.5 p-0">Experience</legend>
          <div className="flex flex-wrap gap-1.5">{(['beginner', 'intermediate', 'advanced'] as const).map(x => <button key={x} type="button" aria-pressed={p.experience === x} className={chip(p.experience === x)} onClick={() => setP({ ...p, experience: x })}>{x}</button>)}</div>
        </fieldset>
        <fieldset className="border-0 p-0 m-0"><legend className="text-xs text-dim mb-1.5 p-0">Equipment you have</legend>
          <div className="flex flex-wrap gap-1.5">{EQUIPMENT.map(([v, l]) => {
            const on = p.equipment.includes(v)
            return <button key={v} type="button" aria-pressed={on} className={chip(on)}
              onClick={() => setP({ ...p, equipment: on ? p.equipment.filter(x => x !== v) : [...p.equipment, v] })}>{l}</button>
          })}</div>
        </fieldset>
        <label className="block text-xs text-dim">Injuries or limits (comma separated, e.g. knee, lower back)
          <input value={p.injuries.join(', ')} onChange={e => setP({ ...p, injuries: e.target.value.split(',').map(x => x.trim().toLowerCase()).filter(Boolean) })}
            className="block w-full mt-1 rounded-lg bg-night border border-line px-2.5 py-1.5 text-sm text-cream" />
        </label>
        <div className="flex items-center gap-3">
          <button onClick={save} className="rounded-full bg-turmeric text-night font-semibold text-sm px-4 py-1.5">Save training settings</button>
          {saved && <span role="status" className="text-xs text-leaf">{saved}</span>}
        </div>
      </section>
    </div>
  )
}
