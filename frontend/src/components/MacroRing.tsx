import { motion } from 'motion/react'

export function MacroRing({ label, value, target, unit, color, size = 76 }: {
  label: string; value: number; target: number; unit: string; color: string; size?: number
}) {
  const r = size / 2 - 6
  const circ = 2 * Math.PI * r
  const pct = target > 0 ? value / target : 0
  const shown = Math.min(pct, 1)
  const off = Math.abs(pct - 1) > 0.1
  return (
    <figure className="flex flex-col items-center gap-1.5 m-0" aria-label={`${label}: ${Math.round(value)} of ${Math.round(target)} ${unit}`}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-plum-2)" strokeWidth={7} />
          <motion.circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={7} strokeLinecap="round"
            strokeDasharray={circ} initial={{ strokeDashoffset: circ }} animate={{ strokeDashoffset: circ * (1 - shown) }}
            transition={{ type: 'spring', stiffness: 60, damping: 14 }} />
        </svg>
        <div className="absolute inset-0 grid place-items-center text-center leading-none">
          <span className="font-display text-[15px] font-semibold">{Math.round(value)}</span>
        </div>
      </div>
      <figcaption className="text-xs text-mist text-center leading-tight">
        {label}<br />
        <span className={off ? 'text-turmeric' : 'text-dim'}>of {Math.round(target)} {unit}</span>
      </figcaption>
    </figure>
  )
}
