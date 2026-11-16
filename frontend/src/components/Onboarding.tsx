import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import clsx from 'clsx'
import { X } from 'lucide-react'
import type { Profile } from '../lib/types'

const spring = { type: 'spring' as const, stiffness: 320, damping: 32 }

function Chip({ on, children, onClick }: { on: boolean; children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={on}
      className={clsx('relative rounded-full px-4 py-2 text-[15px] border transition-colors',
        on ? 'border-turmeric text-night' : 'border-line text-cream hover:border-mist')}>
      {on && <motion.span initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
        transition={spring} className="absolute inset-0 rounded-full bg-turmeric" />}
      <span className="relative">{children}</span>
    </button>
  )
}

/** One labelled input. */
function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="block text-sm text-mist mb-1.5">{label}</span>
      {children}
      {hint && <span className="block text-xs text-dim mt-1">{hint}</span>}
    </label>
  )
}

/** A group of controls (chips, tag lists). A <label> must not wrap these: buttons are labelable, so
 * clicking the label text would press the first chip, or remove the first allergy in a tag list. */
function Group({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <fieldset className="block border-0 p-0 m-0 min-w-0">
      <legend className="block text-sm text-mist mb-1.5 p-0">{label}</legend>
      {children}
      {hint && <p className="text-xs text-dim mt-1 mb-0">{hint}</p>}
    </fieldset>
  )
}

const input = 'w-full rounded-xl bg-plum border border-line px-3.5 py-2.5 text-[15px] outline-none focus:border-turmeric'

function Tags({ label, values, onChange, suggestions, placeholder }: {
  label: string; values: string[]; onChange: (v: string[]) => void; suggestions: string[]; placeholder: string
}) {
  const [t, setT] = useState('')
  const add = (v: string) => { const s = v.trim().toLowerCase(); if (s && !values.includes(s)) onChange([...values, s]); setT('') }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <AnimatePresence>
          {values.map(v => (
            <motion.span key={v} layout initial={{ scale: 0.7, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.7, opacity: 0 }}
              className="inline-flex items-center gap-1 rounded-full bg-plum-2 border border-line pl-3 pr-1.5 py-1 text-sm">
              {v}
              <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter(x => x !== v))}
                className="rounded-full p-0.5 hover:bg-line"><X size={13} /></button>
            </motion.span>
          ))}
        </AnimatePresence>
      </div>
      <input className={input} value={t} placeholder={placeholder} aria-label={`Add ${label.toLowerCase()}`} onChange={e => setT(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add(t) } }} />
      <div className="flex flex-wrap gap-1.5">
        {suggestions.filter(s => !values.includes(s)).map(s => (
          <button type="button" key={s} onClick={() => add(s)} aria-label={`Add ${s}`} className="text-xs text-mist rounded-full border border-dashed border-line px-2.5 py-1 hover:text-cream">+ {s}</button>
        ))}
      </div>
    </div>
  )
}

export function Onboarding({ initial, onDone }: { initial: Profile; onDone: (p: Profile) => Promise<void> }) {
  const [p, setP] = useState<Profile>(initial)
  const [step, setStep] = useState(0)
  const [dir, setDir] = useState(1)
  const [imperial, setImperial] = useState(initial.region === 'US')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => setP(x => ({ ...x, [k]: v }))
  const num = (v: string) => (v === '' ? null : Number(v))

  const steps: { title: string; body: ReactNode; ok: boolean }[] = [
    {
      title: 'About you',
      ok: !!(p.age && p.sex && p.height_cm && p.weight_kg),
      body: (
        <div className="space-y-5">
          <Group label="Sex (used in the energy equation)">
            <div className="flex gap-2">
              <Chip on={p.sex === 'female'} onClick={() => set('sex', 'female')}>Female</Chip>
              <Chip on={p.sex === 'male'} onClick={() => set('sex', 'male')}>Male</Chip>
            </div>
          </Group>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Age"><input className={input} inputMode="numeric" value={p.age ?? ''} onChange={e => set('age', num(e.target.value))} /></Field>
            <Field label={imperial ? 'Height (in)' : 'Height (cm)'}>
              <input className={input} inputMode="decimal"
                value={p.height_cm ? (imperial ? Math.round(p.height_cm / 2.54) : Math.round(p.height_cm)) : ''}
                onChange={e => { const v = num(e.target.value); set('height_cm', v == null ? null : imperial ? v * 2.54 : v) }} />
            </Field>
            <Field label={imperial ? 'Weight (lb)' : 'Weight (kg)'}>
              <input className={input} inputMode="decimal"
                value={p.weight_kg ? (imperial ? Math.round(p.weight_kg * 2.20462) : p.weight_kg) : ''}
                onChange={e => { const v = num(e.target.value); set('weight_kg', v == null ? null : imperial ? +(v / 2.20462).toFixed(1) : v) }} />
            </Field>
          </div>
          <button type="button" className="text-sm text-turmeric" onClick={() => setImperial(!imperial)}>
            Use {imperial ? 'centimetres and kilograms' : 'inches and pounds'}
          </button>
        </div>
      ),
    },
    {
      title: 'Your goal',
      ok: true,
      body: (
        <div className="space-y-6">
          <div className="flex flex-wrap gap-2">
            {([['lose_weight', 'Lose weight'], ['maintain', 'Maintain'], ['gain_muscle', 'Build muscle'], ['gain_weight', 'Gain weight'],
              ['manage_condition', 'Manage a condition'], ['general_health', 'Eat healthier']] as const).map(([v, l]) => (
              <Chip key={v} on={p.goal === v} onClick={() => set('goal', v)}>{l}</Chip>
            ))}
          </div>
          <Group label="How active are you?">
            <div className="flex flex-wrap gap-2">
              {([['sedentary', 'Mostly sitting'], ['light', '1-3 workouts a week'], ['moderate', '3-5 a week'], ['active', '6-7 a week'], ['very_active', 'Physical job or athlete']] as const).map(([v, l]) => (
                <Chip key={v} on={p.activity === v} onClick={() => set('activity', v)}>{l}</Chip>
              ))}
            </div>
          </Group>
          {p.goal === 'lose_weight' && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Target weight (kg)" hint="Optional"><input className={input} inputMode="decimal" value={p.target_weight_kg ?? ''} onChange={e => set('target_weight_kg', num(e.target.value))} /></Field>
              <Field label="In how many weeks?" hint="Optional"><input className={input} inputMode="numeric" value={p.timeline_weeks ?? ''} onChange={e => set('timeline_weeks', num(e.target.value))} /></Field>
            </div>
          )}
        </div>
      ),
    },
    {
      title: 'How you eat',
      ok: true,
      body: (
        <div className="space-y-6">
          <Group label="Where do you shop and cook?" hint="Picks the food table: Indian Food Composition Tables or USDA.">
            <div className="flex gap-2">
              <Chip on={p.region === 'IN'} onClick={() => set('region', 'IN')}>India</Chip>
              <Chip on={p.region === 'US'} onClick={() => set('region', 'US')}>United States</Chip>
            </div>
          </Group>
          <Group label="Diet">
            <div className="flex flex-wrap gap-2">
              {([['vegetarian', 'Vegetarian'], ['eggetarian', 'Eggetarian'], ['vegan', 'Vegan'], ['jain', 'Jain'], ['pescatarian', 'Pescatarian'], ['omnivore', 'Eat everything']] as const).map(([v, l]) => (
                <Chip key={v} on={p.diet_pattern === v} onClick={() => set('diet_pattern', v)}>{l}</Chip>
              ))}
            </div>
          </Group>
          <Group label="Religious or cultural rules">
            <div className="flex flex-wrap gap-2">
              {([['halal', 'Halal'], ['kosher', 'Kosher'], ['no_beef', 'No beef'], ['no_pork', 'No pork']] as const).map(([v, l]) => (
                <Chip key={v} on={p.religious_rules.includes(v)}
                  onClick={() => set('religious_rules', p.religious_rules.includes(v) ? p.religious_rules.filter(x => x !== v) : [...p.religious_rules, v])}>{l}</Chip>
              ))}
            </div>
          </Group>
          <Group label="Meals a day">
            <div className="flex gap-2">{[2, 3, 4, 5].map(n => <Chip key={n} on={p.meals_per_day === n} onClick={() => set('meals_per_day', n)}>{n}</Chip>)}</div>
          </Group>
        </div>
      ),
    },
    {
      title: 'Health details',
      ok: true,
      body: (
        <div className="space-y-6">
          <Group label="Allergies and intolerances">
            <Tags label="Allergies" values={p.allergies} onChange={v => set('allergies', v)} placeholder="Type and press Enter"
              suggestions={['peanut', 'tree nuts', 'dairy', 'gluten', 'egg', 'soy', 'shellfish', 'sesame']} />
          </Group>
          <Group label="Health conditions">
            <Tags label="Conditions" values={p.conditions} onChange={v => set('conditions', v)} placeholder="For example: type 2 diabetes"
              suggestions={['type 2 diabetes', 'hypertension', 'pcos', 'high cholesterol', 'kidney disease']} />
          </Group>
          <Group label="Medications" hint="Some foods interact with medicines, so this keeps your plan safe.">
            <Tags label="Medications" values={p.medications} onChange={v => set('medications', v)} placeholder="For example: metformin" suggestions={[]} />
          </Group>
          {p.sex === 'female' && (
            <Group label="Pregnancy">
              <div className="flex gap-2">
                <Chip on={p.pregnant} onClick={() => set('pregnant', !p.pregnant)}>Pregnant</Chip>
                <Chip on={p.breastfeeding} onClick={() => set('breastfeeding', !p.breastfeeding)}>Breastfeeding</Chip>
              </div>
            </Group>
          )}
        </div>
      ),
    },
  ]

  const heading = useRef<HTMLHeadingElement>(null)
  const moved = useRef(false)
  useEffect(() => { if (moved.current) heading.current?.focus() }, [step])
  const go = (d: number) => { moved.current = true; setDir(d); setStep(s => s + d) }
  const last = step === steps.length - 1
  const finish = async () => {
    setSaving(true); setError(null)
    try { await onDone(p) } catch (e) { setError((e as Error).message); setSaving(false) }
  }

  return (
    <div className="min-h-full grid place-items-center px-5 py-12">
      <div className="w-full max-w-xl">
        <div className="h-1 rounded-full bg-plum-2 overflow-hidden mb-10" role="progressbar" aria-label="Setup progress" aria-valuenow={step + 1} aria-valuemin={1} aria-valuemax={steps.length}>
          <motion.div className="h-full bg-turmeric" animate={{ width: `${((step + 1) / steps.length) * 100}%` }} transition={spring} />
        </div>
        <p className="text-sm text-dim mb-2">Step {step + 1} of {steps.length}</p>
        <AnimatePresence mode="wait" custom={dir}>
          <motion.div key={step} custom={dir}
            initial={{ opacity: 0, x: 40 * dir }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -40 * dir }} transition={spring}>
            <h1 ref={heading} tabIndex={-1} className="font-display text-3xl sm:text-4xl font-semibold mt-0 mb-6 sm:mb-8 outline-none">{steps[step].title}</h1>
            {steps[step].body}
          </motion.div>
        </AnimatePresence>
        {error && <p role="alert" className="text-chilli text-sm mt-6">{error}</p>}
        <div className="flex justify-between items-center mt-12">
          <button type="button" onClick={() => go(-1)} disabled={step === 0} className="text-mist disabled:opacity-0">Back</button>
          <motion.button whileTap={{ scale: 0.96 }} type="button" disabled={!steps[step].ok || saving}
            onClick={() => (last ? finish() : go(1))}
            className="rounded-full bg-turmeric text-night font-semibold px-6 py-3 disabled:opacity-40">
            {last ? (saving ? 'Saving…' : 'Save and start') : 'Continue'}
          </motion.button>
        </div>
      </div>
    </div>
  )
}
