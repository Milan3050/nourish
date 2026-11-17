import { useEffect, useState } from 'react'
import { Dumbbell, MessageSquare, Plus, Salad } from 'lucide-react'
import clsx from 'clsx'
import { api } from '../lib/api'
import type { ThreadSummary } from '../lib/types'

function ago(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

/** Past conversations, newest first. `refresh` changes after every answer so the list stays current. */
export function SessionList({ current, refresh, onOpen, onNew }: {
  current: string; refresh: number; onOpen: (id: string) => void; onNew: () => void
}) {
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.threads().then(r => setThreads(r.threads)).catch(x => setErr((x as Error).message)) }, [refresh])
  return (
    <nav aria-label="Conversation history" className="h-full flex flex-col">
      <div className="flex items-center justify-between px-3 pt-4 pb-2">
        <h2 className="font-display text-sm font-semibold m-0 text-mist">History</h2>
        <button onClick={onNew} aria-label="New conversation" title="New conversation"
          className="p-1.5 rounded-full border border-line text-mist hover:border-mist hover:text-cream"><Plus size={14} /></button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto scroll-thin px-2 pb-3">
        {err && <p role="alert" className="text-xs text-chilli px-2">{err}</p>}
        {threads && !threads.length && <p className="text-xs text-dim px-2">Your conversations will appear here.</p>}
        <ul className="space-y-0.5 list-none p-0 m-0">
          {threads?.map(t => (
            <li key={t.thread_id}>
              <button onClick={() => onOpen(t.thread_id)} aria-current={t.thread_id === current ? 'true' : undefined}
                className={clsx('w-full text-left rounded-xl px-2.5 py-2 transition-colors',
                  t.thread_id === current ? 'bg-plum-2' : 'hover:bg-plum')}>
                <span className="flex items-start gap-2">
                  <MessageSquare size={13} className="mt-1 shrink-0 text-dim" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm leading-snug line-clamp-2">{t.title}</span>
                    <span className="flex items-center gap-1.5 text-[11px] text-dim mt-0.5">
                      {ago(t.updated_at)} · {t.turns} message{t.turns > 1 ? 's' : ''}
                      {t.has_plan && <Salad size={11} className="text-turmeric" aria-label="meal plan" />}
                      {t.has_workout && <Dumbbell size={11} className="text-chilli" aria-label="workout" />}
                    </span>
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </nav>
  )
}
