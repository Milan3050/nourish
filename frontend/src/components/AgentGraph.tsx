import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useMemo } from 'react'
import type { AgentStatus, AgentView, Flight } from '../lib/useChat'
import { AGENT_LABEL, type AgentKey } from '../lib/types'

/**
 * The agent graph drawn as a plate: the Supervisor is the centre bowl, the specialists are smaller bowls
 * around the rim. A bowl fills while its agent works; handoffs travel across the plate as a grain of light.
 */

const C = 200
const RING = 128
const ORDER: AgentKey[] = ['health_assessment', 'nutrition_knowledge', 'meal_planning', 'validation', 'recommendation', 'workout_planning']
const COLOR: Record<AgentKey, string> = {
  supervisor: 'var(--color-turmeric)',
  health_assessment: 'var(--color-beet)',
  nutrition_knowledge: 'var(--color-lilac)',
  meal_planning: 'var(--color-turmeric)',
  workout_planning: 'var(--color-chilli)',
  validation: 'var(--color-leaf)',
  recommendation: 'var(--color-cardamom)',
}

function pos(k: AgentKey): [number, number] {
  if (k === 'supervisor') return [C, C]
  const i = ORDER.indexOf(k)
  const a = (-90 + i * (360 / ORDER.length)) * (Math.PI / 180)
  return [C + RING * Math.cos(a), C + RING * Math.sin(a)]
}

function curve(from: AgentKey, to: AgentKey, n = 24): { xs: number[]; ys: number[]; d: string } {
  const [x1, y1] = pos(from)
  const [x2, y2] = pos(to)
  // Bend each path slightly so outbound and return trips don't overlap.
  const mx = (x1 + x2) / 2 + (y2 - y1) * 0.18
  const my = (y1 + y2) / 2 - (x2 - x1) * 0.18
  const xs: number[] = []
  const ys: number[] = []
  for (let i = 0; i <= n; i++) {
    const t = i / n
    xs.push((1 - t) ** 2 * x1 + 2 * (1 - t) * t * mx + t * t * x2)
    ys.push((1 - t) ** 2 * y1 + 2 * (1 - t) * t * my + t * t * y2)
  }
  return { xs, ys, d: `M${x1},${y1} Q${mx},${my} ${x2},${y2}` }
}

const FILL: Record<AgentStatus, number> = { idle: 0, skipped: 0, thinking: 0.55, working: 0.85, done: 1, failed: 1 }

function Bowl({ k, view, verdict }: { k: AgentKey; view: AgentView; verdict?: string }) {
  const reduce = useReducedMotion()
  const [x, y] = pos(k)
  const r = k === 'supervisor' ? 50 : 34
  const color = view.status === 'failed' ? 'var(--color-chilli)' : COLOR[k]
  const active = view.status === 'thinking' || view.status === 'working'
  const fill = FILL[view.status]
  return (
    <g opacity={view.status === 'skipped' ? 0.35 : 1}>
      {/* rim */}
      <circle cx={x} cy={y} r={r + 6} fill="var(--color-plum-2)" stroke="var(--color-line)" strokeWidth={1.5} />
      <circle cx={x} cy={y} r={r} fill="var(--color-night)" />
      {/* contents */}
      <motion.circle
        cx={x} cy={y} fill={color}
        initial={false}
        animate={{ r: r * Math.sqrt(fill) * 0.92, opacity: fill ? (view.status === 'done' ? 0.9 : 0.75) : 0 }}
        transition={{ duration: 0.45, ease: 'easeOut' }}  // a spring overshoots below r = 0, which SVG rejects
      />
      {active && !reduce && (
        <motion.circle
          cx={x} cy={y} r={r} fill="none" stroke={color} strokeWidth={2}
          initial={{ opacity: 0.8, scale: 1 }}
          animate={{ opacity: 0, scale: 1.45 }}
          transition={{ duration: view.status === 'working' ? 0.9 : 1.6, repeat: Infinity, ease: 'easeOut' }}
          style={{ transformOrigin: `${x}px ${y}px` }}
        />
      )}
      <text x={x} y={y + (k === 'supervisor' ? 5 : 4)} textAnchor="middle"
        className="font-display" fontSize={k === 'supervisor' ? 13 : 11} fontWeight={600}
        fill={fill >= 0.85 ? 'var(--color-night)' : 'var(--color-cream)'}>
        {AGENT_LABEL[k]}
      </text>
      <AnimatePresence>
        {view.tool && (
          <motion.text key={view.tool} x={x} y={y + r + 22} textAnchor="middle" fontSize={10.5} fill="var(--color-mist)"
            initial={{ opacity: 0, y: y + r + 28 }} animate={{ opacity: 1, y: y + r + 22 }} exit={{ opacity: 0 }}>
            {view.tool.replaceAll('_', ' ')}
          </motion.text>
        )}
      </AnimatePresence>
      {view.runs > 1 && (
        <g>
          <circle cx={x + r * 0.75} cy={y - r * 0.75} r={10} fill="var(--color-cream)" />
          <text x={x + r * 0.75} y={y - r * 0.75 + 3.5} textAnchor="middle" fontSize={10} fontWeight={700} fill="var(--color-night)">
            ×{view.runs}
          </text>
        </g>
      )}
      <AnimatePresence>
        {verdict && (
          <motion.g key={verdict}
            initial={{ scale: 2.2, opacity: 0, rotate: -18 }} animate={{ scale: 1, opacity: 1, rotate: -8 }} exit={{ opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 14 }}
            style={{ transformOrigin: `${x}px ${y - r - 14}px` }}>
            <rect x={x - 32} y={y - r - 26} width={64} height={22} rx={5}
              fill="var(--color-night)" stroke={verdict === 'PASS' ? 'var(--color-leaf)' : 'var(--color-chilli)'} strokeWidth={2} />
            <text x={x} y={y - r - 10.5} textAnchor="middle" fontSize={11} fontWeight={800} letterSpacing={1}
              fill={verdict === 'PASS' ? 'var(--color-leaf)' : 'var(--color-chilli)'}>{verdict}</text>
          </motion.g>
        )}
      </AnimatePresence>
    </g>
  )
}

export function AgentGraph({ agents, flights, verdict, revisions }: {
  agents: Record<AgentKey, AgentView>; flights: Flight[]; verdict?: string; revisions: number
}) {
  const reduce = useReducedMotion()
  const spokes = useMemo(() => ORDER.map(k => ({ k, ...curve('supervisor', k) })), [])
  const loop = useMemo(() => curve('meal_planning', 'validation'), [])
  return (
    <svg viewBox="0 0 400 400" className="w-full h-full" role="img"
      aria-label="Agent activity: the supervisor in the centre routes work to five specialist agents">
      <defs>
        <radialGradient id="plate" cx="50%" cy="45%" r="60%">
          <stop offset="0%" stopColor="#3a3048" />
          <stop offset="70%" stopColor="#2a2236" />
          <stop offset="100%" stopColor="#211a2b" />
        </radialGradient>
        <filter id="glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="3" result="b" />
          <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      {/* the plate, with a hammered-steel ring of dots */}
      <circle cx={C} cy={C} r={192} fill="url(#plate)" stroke="var(--color-line)" strokeWidth={2} />
      <circle cx={C} cy={C} r={180} fill="none" stroke="var(--color-line)" strokeWidth={1} strokeDasharray="1 7" strokeLinecap="round" />
      {spokes.map(s => (
        <path key={s.k} d={s.d} fill="none" strokeWidth={1.5}
          stroke={agents[s.k].status === 'idle' || agents[s.k].status === 'skipped' ? 'var(--color-line)' : COLOR[s.k]}
          strokeOpacity={agents[s.k].status === 'idle' || agents[s.k].status === 'skipped' ? 0.6 : 0.5} />
      ))}
      <motion.path d={loop.d} fill="none" strokeWidth={2} strokeDasharray="4 5"
        stroke={revisions > 0 ? 'var(--color-chilli)' : 'var(--color-line)'}
        animate={revisions > 0 && !reduce ? { strokeDashoffset: [0, -18] } : {}}
        transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }} />
      {revisions > 0 && (
        <text x={(pos('meal_planning')[0] + pos('validation')[0]) / 2} y={pos('validation')[1] + 62}
          textAnchor="middle" fontSize={11} fill="var(--color-chilli)">revision {revisions}</text>
      )}
      {(['supervisor', ...ORDER] as AgentKey[]).map(k => (
        <Bowl key={k} k={k} view={agents[k]} verdict={k === 'validation' ? verdict : undefined} />
      ))}
      {!reduce && flights.map(f => {
        const p = curve(f.from, f.to)
        const color = f.kind === 'override' ? 'var(--color-chilli)' : f.kind === 'return' ? 'var(--color-cream)' : COLOR[f.to]
        return (
          <motion.circle key={f.id} r={f.kind === 'return' ? 3.5 : 5} fill={color} filter="url(#glow)"
            initial={{ cx: p.xs[0], cy: p.ys[0], opacity: 0 }}
            animate={{ cx: p.xs, cy: p.ys, opacity: [0, 1, 1, 0] }}
            transition={{ duration: 0.75, ease: 'easeInOut' }} />
        )
      })}
    </svg>
  )
}
