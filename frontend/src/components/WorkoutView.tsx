import { Dumbbell, Footprints, Flame, Timer } from 'lucide-react'
import clsx from 'clsx'
import type { Workout } from '../lib/types'

const TYPE_ICON = { strength: Dumbbell, cardio: Footprints, mobility: Timer }

/** A checked weekly workout: one card per session, with totals computed by the workout rules. */
export function WorkoutView({ workout, compact = false }: { workout: Workout; compact?: boolean }) {
  const t = workout.totals
  const notes = workout.violations.filter(v => v.severity !== 'critical')
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-3 gap-3">
        {[['Sessions', `${t.sessions} / week`], ['Training time', `${t.minutes_week} min`], ['Energy', `${t.kcal_week} kcal`]].map(([k, v]) => (
          <div key={k} className="rounded-xl bg-plum border border-line px-3 py-2">
            <dt className="text-xs text-dim">{k}</dt><dd className="m-0 font-display text-lg font-semibold">{v}</dd>
          </div>
        ))}
      </dl>
      <ul className="space-y-2">
        {workout.sessions.map(s => (
          <li key={s.day} className="rounded-2xl border border-line bg-plum px-4 py-3">
            <div className="flex items-baseline gap-3">
              <span className="text-xs text-dim w-10 shrink-0">{s.weekday}</span>
              <span className="font-display font-medium flex-1">{s.focus}</span>
              <span className="text-xs text-dim inline-flex items-center gap-1"><Timer size={12} aria-hidden />{s.minutes} min</span>
              <span className="text-xs text-dim inline-flex items-center gap-1"><Flame size={12} aria-hidden />{s.kcal} kcal</span>
            </div>
            {!compact && (
              <ul className="mt-2 space-y-1 text-sm">
                {s.exercises.map((e, i) => {
                  const Icon = TYPE_ICON[e.type] ?? Dumbbell
                  return (
                    <li key={i} className="flex items-center gap-2 text-mist">
                      <Icon size={13} className="text-dim shrink-0" aria-hidden />
                      <span className="flex-1 text-cream">{e.name}</span>
                      <span className="tabular-nums">
                        {e.type === 'strength' ? `${e.sets} × ${e.reps}${e.rest_s ? `, rest ${e.rest_s}s` : ''}` : `${e.minutes} min`}
                      </span>
                    </li>
                  )
                })}
              </ul>
            )}
          </li>
        ))}
      </ul>
      {!compact && Object.keys(t.sets_by_muscle).length > 0 && (
        <div>
          <p className="text-xs text-dim mb-1.5">Weekly sets per muscle group (computed)</p>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(t.sets_by_muscle).map(([m, n]) => (
              <span key={m} className="text-xs rounded-full border border-line px-2.5 py-1 text-mist">{m} <b className="text-cream">{n}</b></span>
            ))}
          </div>
        </div>
      )}
      {workout.progression && <p className="text-sm text-mist">{workout.progression}</p>}
      {notes.length > 0 && (
        <ul className="space-y-1">
          {notes.map((v, i) => (
            <li key={i} className={clsx('text-xs', v.severity === 'warning' ? 'text-turmeric' : 'text-dim')}>{v.message}</li>
          ))}
        </ul>
      )}
      <p className="text-xs text-dim">Minutes, sets and energy are calculated by the workout rules (energy from MET values × your weight × time), not estimated.</p>
    </div>
  )
}
