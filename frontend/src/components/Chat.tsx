import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { ArrowUp, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import clsx from 'clsx'
import type { ChatMessage } from '../lib/useChat'
import type { Region } from '../lib/types'

const SUGGESTIONS: Record<Region, string[]> = {
  IN: [
    'Plan 3 days of high-protein vegetarian meals for fat loss',
    'Is creatine safe for me?',
    'Swap dinner on day 2 for something without paneer',
    'What should I eat to manage blood sugar?',
  ],
  US: [
    'Plan 3 days of meals to help me build muscle',
    'Can I eat grapefruit on simvastatin?',
    'Make tomorrow a high-fiber day',
    'How much protein do I need?',
  ],
}

function Words({ text }: { text: string }) {
  const reduce = useReducedMotion()
  if (reduce) return <>{text}</>
  const words = text.split(/(\s+)/)
  return (
    <>
      {words.map((w, i) => (
        <motion.span key={i} initial={{ opacity: 0, filter: 'blur(4px)' }} animate={{ opacity: 1, filter: 'blur(0px)' }}
          transition={{ delay: Math.min(i * 0.012, 1.2), duration: 0.25 }}>{w}</motion.span>
      ))}
    </>
  )
}

function Assistant({ m, onAsk, isLast }: { m: ChatMessage; onAsk: (q: string) => void; isLast: boolean }) {
  const r = m.state?.recommendation
  if (m.error || !r) return <p className={clsx('text-[15px]', m.error && 'text-chilli')}>{m.text}</p>
  return (
    <div className="flex flex-col gap-3">
      {r.headline && <h3 className="font-display text-xl font-semibold leading-tight m-0">{r.headline}</h3>}
      <p className="text-[15px] leading-relaxed whitespace-pre-line m-0">{isLast ? <Words text={r.summary} /> : r.summary}</p>
      {r.key_points.length > 0 && (
        <ul className="space-y-1.5 text-[15px] list-none p-0 m-0">
          {r.key_points.map(k => <li key={k} className="pl-4 relative before:absolute before:left-0 before:top-[0.6em] before:size-1.5 before:rounded-full before:bg-turmeric">{k}</li>)}
        </ul>
      )}
      {r.tips.length > 0 && <p className="text-sm text-mist m-0">{r.tips.join(' ')}</p>}
      {r.citations.length > 0 && (
        <p className="text-xs text-dim m-0">Sources: {r.citations.map(c => c.source.split(/[;,]/)[0]).filter((v, i, a) => a.indexOf(v) === i).join('; ')}</p>
      )}
      {r.disclaimer && <p className="text-xs text-dim m-0">{r.disclaimer}</p>}
      {isLast && r.follow_up_questions.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {r.follow_up_questions.map(q => (
            <button key={q} onClick={() => onAsk(q)} className="text-sm rounded-full border border-line px-3 py-1 text-mist hover:border-turmeric hover:text-cream transition-colors">{q}</button>
          ))}
        </div>
      )}
    </div>
  )
}

export function Chat({ messages, busy, onSend, region, name, onShowWork }: {
  messages: ChatMessage[]; busy: boolean; onSend: (t: string) => void; region: Region; name: string
  onShowWork?: () => void
}) {
  const [text, setText] = useState('')
  const end = useRef<HTMLDivElement>(null)
  const reduce = useReducedMotion()
  useEffect(() => { end.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'end' }) }, [messages.length, busy, reduce])
  const submit = (t = text) => { if (t.trim() && !busy) { onSend(t.trim()); setText('') } }

  return (
    <section className="flex flex-col h-full min-h-0" aria-label="Conversation">
      <div className="flex-1 overflow-y-auto scroll-thin px-4 sm:px-6 pt-5 sm:pt-6 pb-4 space-y-6">
        {messages.length === 0 && (
          <div className="pt-6">
            <h2 className="font-display text-2xl sm:text-3xl font-semibold leading-tight m-0 max-w-md">
              What are you working towards, {name}?
            </h2>
            <p className="text-mist mt-3 max-w-md">Ask for a meal plan, a swap, or whether something is right for you. You'll see each specialist pick up the work <span className="lg:hidden">under Agents &amp; plan</span><span className="hidden lg:inline">on the right</span>.</p>
            <div className="mt-6 flex flex-col items-start gap-2">
              {SUGGESTIONS[region].map(s => (
                <button key={s} onClick={() => submit(s)}
                  className="text-left text-[15px] rounded-2xl border border-line bg-plum px-4 py-2.5 hover:border-turmeric transition-colors">{s}</button>
              ))}
            </div>
          </div>
        )}
        <div role="log" aria-label="Messages" aria-live="polite" className="space-y-6">
        <AnimatePresence initial={false}>
          {messages.map((m, i) => (
            <motion.div key={m.id} layout initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}
              transition={{ type: 'spring', stiffness: 300, damping: 28 }}
              className={m.role === 'user' ? 'flex justify-end' : ''}>
              {m.role === 'user'
                ? <p className="max-w-[85%] rounded-2xl rounded-br-md bg-plum-2 px-4 py-2.5 text-[15px] m-0">{m.text}</p>
                : <Assistant m={m} onAsk={submit} isLast={i === messages.length - 1} />}
            </motion.div>
          ))}
        </AnimatePresence>
        </div>
        {busy && (
          <motion.p role="status" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-wrap items-center gap-2 text-sm text-mist">
            <Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden /> The agents are working on it
            {onShowWork && (
              <button type="button" onClick={onShowWork} className="lg:hidden text-turmeric underline underline-offset-2">Watch the agents</button>
            )}
          </motion.p>
        )}
        <div ref={end} />
      </div>
      <form className="p-3 sm:p-4 pt-2" onSubmit={e => { e.preventDefault(); submit() }}>
        <div className="flex items-end gap-2 rounded-3xl border border-line bg-plum focus-within:border-turmeric/70 transition-colors p-2 pl-4">
          <label htmlFor="msg" className="sr-only">Message</label>
          <textarea id="msg" rows={1} value={text} onChange={e => setText(e.target.value)} maxLength={4000}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
            placeholder="Ask about meals, swaps or nutrition"
            className="flex-1 resize-none bg-transparent outline-none py-2 text-[15px] placeholder:text-dim max-h-40" />
          <motion.button whileTap={{ scale: 0.9 }} type="submit" disabled={busy || !text.trim()} aria-label="Send"
            className="size-10 rounded-full grid place-items-center bg-turmeric text-night disabled:opacity-30 transition-opacity">
            <ArrowUp size={18} />
          </motion.button>
        </div>
      </form>
    </section>
  )
}
