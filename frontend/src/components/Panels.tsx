import { AnimatePresence, LayoutGroup, motion } from 'motion/react'
import { ChevronDown, ExternalLink, ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import clsx from 'clsx'
import { MacroRing } from './MacroRing'
import { WorkoutView } from './WorkoutView'
import type { Citation, Evidence, Health, MealPlan, Validation, Workout } from '../lib/types'

const SLOT: Record<string, string> = {
  breakfast: 'Breakfast', mid_morning: 'Mid-morning', lunch: 'Lunch', snack: 'Snack', dinner: 'Dinner', bedtime: 'Bedtime',
}
const spring = { type: 'spring' as const, stiffness: 380, damping: 32 }

export function WorkoutPanel({ workout, requested }: { workout: Workout | null | undefined; requested?: boolean }) {
  if (!workout) return <Empty title="No workout plan yet" body={requested
    ? "The last workout plan didn't pass the workout checks. Try again, or check your training settings on the Profile page."
    : 'Ask for one, for example "Build me a 3-day workout plan".'} />
  return <WorkoutView workout={workout} />
}

export function PlanPanel({ plan, health, validation, targets }: {
  plan: MealPlan | null | undefined; health: Health | null; validation: Validation | null; targets?: Health['targets'] | null
}) {
  const [day, setDay] = useState(0)
  const [open, setOpen] = useState<string | null>(null)
  if (!plan) {
    return (
      <Empty title="No meal plan yet"
        body={validation && validation.verdict !== 'PASS'
          ? `The last plan wasn't approved (${validation.verdict.toLowerCase()}): ${validation.summary}`
          : 'Ask for one, for example "Plan 3 days of high-protein vegetarian meals".'} />
    )
  }
  const d = plan.days[Math.min(day, plan.days.length - 1)]
  const t = targets ?? health?.targets
  return (
    <div className="space-y-5">
      {plan.days.length > 1 && (
        <LayoutGroup id="days">
          <div role="tablist" className="flex gap-1">
            {plan.days.map((p, i) => (
              <button key={p.day} role="tab" aria-selected={i === day} onClick={() => { setDay(i); setOpen(null) }}
                className={clsx('relative px-3.5 py-1.5 text-sm rounded-full transition-colors', i === day ? 'text-night' : 'text-mist hover:text-cream')}>
                {i === day && <motion.span layoutId="day-pill" className="absolute inset-0 rounded-full bg-turmeric" transition={spring} />}
                <span className="relative">Day {p.day}</span>
              </button>
            ))}
          </div>
        </LayoutGroup>
      )}
      {t && d.totals && (
        <div className="flex justify-between gap-2 px-1">
          <MacroRing label="Energy" value={d.totals.kcal} target={t.kcal} unit="kcal" color="var(--color-turmeric)" />
          <MacroRing label="Protein" value={d.totals.protein_g} target={t.protein_g} unit="g" color="var(--color-beet)" />
          <MacroRing label="Carbs" value={d.totals.carbs_g} target={t.carbs_g} unit="g" color="var(--color-lilac)" />
          <MacroRing label="Fat" value={d.totals.fat_g} target={t.fat_g} unit="g" color="var(--color-cardamom)" />
        </div>
      )}
      <LayoutGroup id={`meals-${d.day}`}>
        <ul className="space-y-2">
          {d.meals.map(m => {
            const id = `${d.day}-${m.slot}`
            const isOpen = open === id
            return (
              <motion.li key={id} layout transition={spring}
                className={clsx('rounded-2xl border overflow-hidden', isOpen ? 'border-turmeric/50 bg-plum-2' : 'border-line bg-plum')}>
                <motion.button layout="position" onClick={() => setOpen(isOpen ? null : id)} aria-expanded={isOpen}
                  className="w-full flex items-center gap-3 px-4 py-3 text-left">
                  <span className="text-xs text-dim w-20 shrink-0">{SLOT[m.slot] ?? m.slot}</span>
                  <span className="font-display font-medium flex-1">{m.name}</span>
                  {m.totals && <span className="text-sm text-mist">{Math.round(m.totals.kcal)} kcal</span>}
                  <motion.span animate={{ rotate: isOpen ? 180 : 0 }} transition={spring}><ChevronDown size={16} className="text-dim" /></motion.span>
                </motion.button>
                <AnimatePresence initial={false}>
                  {isOpen && (
                    <motion.div key="body" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }} transition={spring} className="px-4">
                      <table className="w-full text-sm mb-3">
                        <tbody>
                          {m.items.map(it => (
                            <tr key={it.food_code} className="border-t border-line/60">
                              <td className="py-1.5 pr-3">{it.food_name}</td>
                              <td className="py-1.5 text-right text-mist whitespace-nowrap">{Math.round(it.grams)} g</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {m.totals && (
                        <p className="text-xs text-dim pb-3">
                          Protein {m.totals.protein_g} g, carbs {m.totals.carbs_g} g, fat {m.totals.fat_g} g, fiber {m.totals.fiber_g} g, sodium {Math.round(m.totals.sodium_mg)} mg
                        </p>
                      )}
                      {m.notes && <p className="text-sm text-mist pb-3">{m.notes}</p>}
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.li>
            )
          })}
        </ul>
      </LayoutGroup>
      <p className="text-xs text-dim">
        Raw ingredient weights. Nutrients are calculated from the {plan.region === 'IN' ? 'Indian Food Composition Tables 2017' : 'USDA SR28'} food table, not estimated.
      </p>
    </div>
  )
}

export function TargetsPanel({ health }: { health: Health | null }) {
  if (!health) return <Empty title="Targets appear after your first request" body="They're calculated from your age, sex, height, weight and activity." />
  const t = health.targets
  return (
    <div className="space-y-5">
      {health.escalation_required && (
        <div className="flex gap-3 rounded-2xl border border-chilli/50 bg-chilli/10 p-4 text-sm">
          <ShieldAlert className="text-chilli shrink-0" size={18} />
          <p>Your situation needs a clinician's input, so you'll get general guidance rather than a personalised plan.</p>
        </div>
      )}
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4">
        {[['Daily energy target', `${t.kcal} kcal`], ['Maintenance (TDEE)', `${health.tdee_kcal} kcal`],
          ['Protein', health.limits.protein_g_max ? `${t.protein_g}-${health.limits.protein_g_max} g` : `${t.protein_g} g`],
          ['Carbs', `${t.carbs_g} g`], ['Fat', `${t.fat_g} g`], ['Fiber', `${t.fiber_g} g`],
          ['Sodium limit', `${health.limits.sodium_mg_max} mg`], ['Resting energy (BMR)', `${health.bmr_kcal} kcal`],
          ...(health.bmi ? [['BMI', `${health.bmi}`]] : []),
          ...(health.healthy_weight_kg?.length === 2 ? [['Healthy weight for your height', `${health.healthy_weight_kg[0]}-${health.healthy_weight_kg[1]} kg`]] : []),
        ].map(([k, v]) => (
          <div key={k}><dt className="text-xs text-dim">{k}</dt><dd className="m-0 font-display text-xl font-semibold">{v}</dd></div>
        ))}
      </dl>
      <p className="text-sm text-mist">{health.rationale}</p>
      {health.risk_flags.length > 0 && (
        <ul className="space-y-2">
          {health.risk_flags.map(f => (
            <li key={f.code} className={clsx('text-sm rounded-xl px-3 py-2 border',
              f.severity === 'critical' ? 'border-chilli/50 text-chilli' : f.severity === 'warning' ? 'border-turmeric/40 text-turmeric' : 'border-line text-mist')}>
              {f.message}
            </li>
          ))}
        </ul>
      )}
      {health.assumptions.length > 0 && <p className="text-xs text-dim">{health.assumptions.join(' ')}</p>}
    </div>
  )
}

export interface GuidelineSearch { agent: string; query: string; found: string }

export function CitationItem({ c, open, setOpen }: { c: Citation; open: string | null; setOpen: (id: string | null) => void }) {
  return (
    <motion.li layout className="rounded-xl border border-line bg-plum text-sm">
      <button className="w-full text-left px-3 py-2 flex items-start gap-2" aria-expanded={open === c.chunk_id}
        onClick={() => setOpen(open === c.chunk_id ? null : c.chunk_id)}>
        <span className="flex-1">{c.title}<span className="block text-xs text-dim">{c.source}</span></span>
        <ChevronDown size={14} className={clsx('mt-1 text-dim transition-transform', open === c.chunk_id && 'rotate-180')} />
      </button>
      <AnimatePresence initial={false}>
        {open === c.chunk_id && (
          <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }} className="overflow-hidden">
            <p className="px-3 pb-2 text-mist">{c.snippet}</p>
            {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="px-3 pb-3 inline-flex items-center gap-1 text-xs text-turmeric hover:underline">Read the original source <ExternalLink size={12} /></a>}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  )
}

/** Everything the agents looked up in the local guideline library this turn: passages the answer cited,
 * the Knowledge agent's answers, and every search any agent ran (with what it returned). */
export function EvidencePanel({ evidence, cited = [], searches = [] }: {
  evidence: Evidence[]; cited?: Citation[]; searches?: GuidelineSearch[]
}) {
  const [open, setOpen] = useState<string | null>(null)
  const note = <p className="text-xs text-dim">Searched in the project's vetted guideline library (34 curated summaries of WHO, ICMR-NIN, DGA, NIH and other sources, indexed locally). No web search; source links are for you to read the originals.</p>
  if (!evidence.length && !cited.length && !searches.length)
    return <div className="space-y-2"><Empty title="Nothing looked up yet" body="When an agent searches the guideline library for your request, the passages it found and cited appear here." />{note}</div>
  return (
    <div className="space-y-5">
      {note}
      {cited.length > 0 && (
        <section className="space-y-2">
          <h3 className="font-display font-medium m-0">Cited in the answer</h3>
          <ul className="space-y-1.5 list-none p-0">{cited.map(c => <CitationItem key={c.chunk_id} c={c} open={open} setOpen={setOpen} />)}</ul>
        </section>
      )}
      {searches.length > 0 && (
        <section className="space-y-1.5">
          <h3 className="font-display font-medium m-0">Searches this turn</h3>
          <ul className="space-y-1 list-none p-0 text-sm">
            {searches.map((q, i) => (
              <li key={i} className="text-mist"><span className="text-dim">{q.agent}:</span> “{q.query}” <span className="text-xs text-dim">→ {q.found}</span></li>
            ))}
          </ul>
        </section>
      )}
      {evidence.length > 0 && <h3 className="font-display font-medium m-0">Knowledge agent answers</h3>}
    <ul className="space-y-4">
      {evidence.map(e => (
        <li key={e.question} className="space-y-2">
          <p className="font-display font-medium">{e.question}</p>
          <p className="text-sm text-mist">{e.answer}{!e.sufficient && <span className="text-turmeric"> The guidelines here don't fully answer this.</span>}</p>
          <ul className="space-y-1.5">
            {e.citations.map(c => (
              <motion.li key={c.chunk_id} layout className="rounded-xl border border-line bg-plum text-sm">
                <button className="w-full text-left px-3 py-2 flex items-start gap-2" aria-expanded={open === c.chunk_id}
                  onClick={() => setOpen(open === c.chunk_id ? null : c.chunk_id)}>
                  <span className="flex-1">{c.title}<span className="block text-xs text-dim">{c.source}</span></span>
                  <ChevronDown size={14} className={clsx('mt-1 text-dim transition-transform', open === c.chunk_id && 'rotate-180')} />
                </button>
                <AnimatePresence initial={false}>
                  {open === c.chunk_id && (
                    <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }} className="overflow-hidden">
                      <p className="px-3 pb-2 text-mist">{c.snippet}</p>
                      {c.url && <a href={c.url} target="_blank" rel="noreferrer" className="px-3 pb-3 inline-flex items-center gap-1 text-xs text-turmeric hover:underline">Open source <ExternalLink size={12} /></a>}
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
    </div>
  )
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="py-10 text-center">
      <p className="font-display text-lg">{title}</p>
      <p className="text-sm text-dim mt-1 max-w-xs mx-auto">{body}</p>
    </div>
  )
}
