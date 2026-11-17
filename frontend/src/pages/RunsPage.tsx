import { useEffect, useState } from 'react'
import { ArrowLeft, Coins, Cpu, Timer } from 'lucide-react'
import clsx from 'clsx'
import { api } from '../lib/api'
import { AGENT_BY_NAME, AGENT_LABEL, type AgentEvent, type AgentKey, type RunDetail, type RunSources } from '../lib/types'
import { CitationItem, Empty } from '../components/Panels'

const usd = (n?: number | null) => (n == null ? '-' : n === 0 ? '$0.00' : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`)
const num = (n: number) => n.toLocaleString()
const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const STATUS = { answered: 'text-leaf border-leaf/40', clarifying: 'text-turmeric border-turmeric/40', blocked: 'text-chilli border-chilli/40', failed: 'text-chilli border-chilli/40' } as Record<string, string>
const agentLabel = (a: string) => AGENT_LABEL[a as AgentKey] ?? AGENT_LABEL[AGENT_BY_NAME[a] as AgentKey] ?? a

export function Stat({ icon: Icon, label, value, sub }: { icon: typeof Coins; label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl border border-line bg-plum px-4 py-3">
      <p className="text-xs text-dim m-0 inline-flex items-center gap-1.5"><Icon size={13} aria-hidden />{label}</p>
      <p className="font-display text-2xl font-semibold m-0 mt-1">{value}</p>
      {sub && <p className="text-xs text-dim m-0">{sub}</p>}
    </div>
  )
}

/** Agent bars on a shared time axis, from agent_started / agent_finished events. */
function Timeline({ events }: { events: AgentEvent[] }) {
  if (!events.length) return null
  const t0 = events[0].ts
  const open: Record<string, number> = {}
  const bars: { agent: string; start: number; end: number; status: string }[] = []
  for (const e of events) {
    if (e.type === 'agent_started' && e.agent) open[e.agent] = e.ts
    if (e.type === 'agent_finished' && e.agent && open[e.agent] != null) {
      bars.push({ agent: e.agent, start: open[e.agent] - t0, end: e.ts - t0, status: e.status ?? '' })
      delete open[e.agent]
    }
  }
  const total = Math.max(...events.map(e => e.ts - t0), 1)
  return (
    <div className="space-y-1.5" role="list" aria-label="Agent timeline">
      {bars.map((b, i) => (
        <div key={i} role="listitem" className="grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-2 text-xs">
          <span className="text-mist truncate">{agentLabel(b.agent)}</span>
          <div className="relative h-3 rounded bg-plum-2">
            <div className={clsx('absolute h-3 rounded', b.status === 'done' ? 'bg-turmeric/80' : 'bg-chilli/80')}
              style={{ left: `${(b.start / total) * 100}%`, width: `${Math.max(((b.end - b.start) / total) * 100, 0.8)}%` }} />
          </div>
          <span className="text-dim tabular-nums text-right">{(b.end - b.start).toFixed(1)} s</span>
        </div>
      ))}
    </div>
  )
}

function eventLine(e: AgentEvent): string {
  switch (e.type) {
    case 'supervisor_decision': return `${e.proposed} → ${e.next}${e.override ? ` [override: ${e.override}]` : ''} ${e.reason ?? ''}`
    case 'tool_call': return `call ${e.tool} ${JSON.stringify(e.args ?? {}).slice(0, 160)}`
    case 'tool_result': return `${e.tool} returned in ${e.ms} ms: ${(e.preview ?? '').slice(0, 160)}`
    case 'submission_rejected': return `result rejected: ${(e.errors ?? []).join('; ').slice(0, 220)}`
    case 'agent_finished': return `${e.status} after ${e.ms ? (e.ms / 1000).toFixed(1) : '?'} s${e.error ? `: ${e.error.slice(0, 160)}` : ''}`
    case 'agent_thought': return `thought: ${(e.text ?? '').slice(0, 200)}`
    case 'validation_verdict': return `plan v${e.plan_version}: ${e.verdict}`
    case 'intake': return `input check${e.flags?.length ? `: ${e.flags.join(', ')}` : ': clean'}`
    default: return e.type
  }
}

/** What the agents looked up in the local guideline library during the run. */
function Sources({ src }: { src?: RunSources }) {
  const [open, setOpen] = useState<string | null>(null)
  if (!src || (!src.cited.length && !src.searches.length && !src.evidence.length))
    return <p className="text-sm text-dim">No guideline searches in this run{src ? '' : ' (recorded before sources were tracked)'}.</p>
  return (
    <div className="space-y-4">
      <p className="text-xs text-dim m-0">From the project's vetted guideline library, searched locally (no web search).</p>
      {src.cited.length > 0 && (
        <div className="space-y-1.5">
          <h4 className="text-sm font-medium m-0">Cited in the answer</h4>
          <ul className="space-y-1.5 list-none p-0">{src.cited.map(c => <CitationItem key={c.chunk_id} c={c} open={open} setOpen={setOpen} />)}</ul>
        </div>
      )}
      {src.searches.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-sm font-medium m-0">Searches ({src.searches.length})</h4>
          <ul className="space-y-1 list-none p-0 text-sm">
            {src.searches.map((q, i) => (
              <li key={i} className="text-mist">
                <span className="text-dim">{agentLabel(q.agent)}:</span> “{q.query}”
                <span className="block text-xs text-dim pl-3">{q.found.length ? q.found.join(' · ') : 'no passages returned'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {src.evidence.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-medium m-0">Knowledge agent answers</h4>
          {src.evidence.map(e => (
            <div key={e.question} className="rounded-xl border border-line bg-plum p-3 text-sm">
              <p className="m-0 font-medium">{e.question}</p>
              <p className="m-0 mt-1 text-mist">{e.answer}</p>
              <p className="m-0 mt-1 text-xs text-dim">{e.citations.map(c => c.title).join(' · ')}{!e.sufficient && ' · the guidelines do not fully answer this'}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RunView({ id, onBack }: { id: string; onBack: () => void }) {
  const [run, setRun] = useState<RunDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.run(id).then(setRun).catch(x => setErr((x as Error).message)) }, [id])
  if (err) return <p role="alert" className="text-chilli">{err}</p>
  if (!run) return <p className="text-dim">Loading run…</p>
  const u = run.usage
  const t0 = run.events[0]?.ts ?? 0
  return (
    <div className="space-y-6">
      <button onClick={onBack} className="inline-flex items-center gap-1.5 text-sm text-mist hover:text-cream"><ArrowLeft size={14} aria-hidden /> All runs</button>
      <div>
        <p className="text-xs text-dim m-0">{when(run.created_at)} · turn {run.turn} · {run.model}</p>
        <h2 className="font-display text-2xl font-semibold mt-1 mb-0">“{run.request}”</h2>
        <p className="text-mist mt-1 mb-0">{run.headline}</p>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat icon={Timer} label="Latency" value={`${run.seconds.toFixed(1)} s`} sub={run.status} />
        <Stat icon={Cpu} label="Tokens" value={num(u.input_tokens + u.output_tokens)} sub={`${num(u.input_tokens)} in · ${num(u.output_tokens)} out · ${u.calls} calls`} />
        <Stat icon={Coins} label="Cost" value={usd(u.cost_usd)} sub={u.cost_usd === 0 ? 'local model, no API charges' : undefined} />
        <Stat icon={Coins} label="If run on a hosted model" value={usd(u.reference_cost_usd)} sub={u.reference_model ? `estimate at ${u.reference_model} prices` : 'set NIP_PRICING_REFERENCE_MODEL'} />
      </div>
      <section>
        <h3 className="font-display text-lg font-semibold mb-3">Agent timeline</h3>
        <Timeline events={run.events} />
      </section>
      <section>
        <h3 className="font-display text-lg font-semibold mb-3">Sources <span className="text-sm text-dim font-normal">({run.sources?.count ?? 0} cited)</span></h3>
        <Sources src={run.sources} />
      </section>
      <section>
        <h3 className="font-display text-lg font-semibold mb-3">Supervisor decisions</h3>
        <ol className="space-y-1.5 text-sm list-none p-0">
          {run.decisions.map((d, i) => (
            <li key={i} className="grid grid-cols-[1.5rem_1fr] gap-2">
              <span className="text-dim tabular-nums">{d.step}</span>
              <span>
                <span className="text-mist">{agentLabel(d.proposed)}</span>
                {d.override ? <> → <b className="text-turmeric">{agentLabel(d.next)}</b> <span className="text-turmeric">(rule: {d.override})</span></>
                  : <> → <b>{agentLabel(d.next)}</b></>}
                <span className="block text-xs text-dim">{d.reason}</span>
              </span>
            </li>
          ))}
        </ol>
      </section>
      <section>
        <h3 className="font-display text-lg font-semibold mb-3">Agents</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-dim"><th className="py-1 pr-3">Agent</th><th className="pr-3">Status</th><th className="pr-3">Steps</th><th className="pr-3">Time</th><th>Note</th></tr></thead>
            <tbody>
              {run.agents.map((a, i) => (
                <tr key={i} className="border-t border-line/60 align-top">
                  <td className="py-1.5 pr-3">{agentLabel(a.agent)}</td>
                  <td className={clsx('pr-3', a.status === 'done' ? 'text-leaf' : 'text-chilli')}>{a.status}</td>
                  <td className="pr-3 tabular-nums">{a.steps}</td>
                  <td className="pr-3 tabular-nums">{(a.ms / 1000).toFixed(1)} s</td>
                  <td className="text-xs text-dim">{a.error?.slice(0, 160)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section>
        <h3 className="font-display text-lg font-semibold mb-3">Event log <span className="text-sm text-dim font-normal">({run.events.length} events)</span></h3>
        <ol className="font-mono text-[12px] leading-relaxed space-y-0.5 max-h-[32rem] overflow-y-auto scroll-thin rounded-xl border border-line bg-plum p-3 list-none">
          {run.events.map((e, i) => (
            <li key={i} className={clsx(e.type === 'submission_rejected' && 'text-turmeric', e.type === 'supervisor_decision' && e.override && 'text-turmeric', e.status === 'failed' && 'text-chilli')}>
              <span className="text-dim tabular-nums">{(e.ts - t0).toFixed(1).padStart(6)}s </span>
              <span className="text-lilac">{e.agent ? agentLabel(e.agent) : e.type === 'supervisor_decision' ? 'Supervisor' : ''}</span> {eventLine(e)}
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}

export function RunsPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.runs>> | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => { api.runs().then(setData).catch(x => setErr((x as Error).message)) }, [open])
  if (open) return <RunView id={open} onBack={() => setOpen(null)} />
  if (err) return <p role="alert" className="text-chilli">{err}</p>
  if (!data) return <p className="text-dim">Loading runs…</p>
  if (!data.runs.length) return <Empty title="No runs yet" body="Every message you send is recorded here with its agents, timings, tokens and cost." />
  const t = data.total
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat icon={Cpu} label="Runs" value={num(t.runs)} />
        <Stat icon={Cpu} label="Tokens used" value={num(t.tokens)} />
        <Stat icon={Coins} label="Total cost" value={usd(t.cost_usd)} sub={t.cost_usd === 0 ? 'local model' : undefined} />
        <Stat icon={Coins} label="Hosted-model estimate" value={usd(t.reference_cost_usd)} sub="same tokens at reference prices" />
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-dim">
              <th className="py-2 pr-3">When</th><th className="pr-3">Request</th><th className="pr-3">Result</th>
              <th className="pr-3">Agents</th><th className="pr-3 text-right">Time</th><th className="pr-3 text-right">Tokens</th><th className="text-right">Cost (est.)</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map(r => (
              <tr key={r.id} className="border-t border-line/60 hover:bg-plum cursor-pointer align-top" onClick={() => setOpen(r.id)}>
                <td className="py-2 pr-3 whitespace-nowrap text-mist">{when(r.created_at)}</td>
                <td className="pr-3 max-w-[22rem]">
                  <button className="text-left hover:text-turmeric" onClick={e => { e.stopPropagation(); setOpen(r.id) }}>{r.request}</button>
                  <span className="block text-xs text-dim truncate">{r.headline}</span>
                </td>
                <td className="pr-3">
                  <span className={clsx('text-xs rounded-full border px-2 py-0.5', STATUS[r.status] ?? 'text-mist border-line')}>{r.status}</span>
                  {r.has_plan && <span className="ml-1 text-xs text-leaf">meal plan</span>}
                  {r.has_workout && <span className="ml-1 text-xs text-leaf">workout</span>}
                  {!!r.source_count && <span className="block text-xs text-dim mt-0.5">{r.source_count} source{r.source_count > 1 ? 's' : ''} cited</span>}
                </td>
                <td className="pr-3 text-xs text-mist">{r.agents.map(a => agentLabel(a.agent)).join(' › ')}</td>
                <td className="pr-3 text-right tabular-nums">{r.seconds.toFixed(0)} s</td>
                <td className="pr-3 text-right tabular-nums">{num(r.usage.input_tokens + r.usage.output_tokens)}</td>
                <td className="text-right tabular-nums">{usd(r.usage.cost_usd)} <span className="text-xs text-dim">({usd(r.usage.reference_cost_usd)})</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
