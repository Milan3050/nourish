import { AnimatePresence, LayoutGroup, MotionConfig, motion } from 'motion/react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { CalendarDays, ClipboardList, History, MessageSquare, Plus, Soup, Target, UserRound } from 'lucide-react'
import clsx from 'clsx'
import { api, session } from './lib/api'
import { AGENT_BY_NAME, AGENT_LABEL, EMPTY_PROFILE, type AgentEvent, type AgentKey, type Health, type Profile } from './lib/types'
import { useChat, type AgentView, type Flight } from './lib/useChat'
import { AgentGraph } from './components/AgentGraph'
import { ActivityLog } from './components/ActivityLog'
import { Chat } from './components/Chat'
import { EvidencePanel, PlanPanel, TargetsPanel, WorkoutPanel, type GuidelineSearch } from './components/Panels'
import { Onboarding } from './components/Onboarding'
import { PlanActions } from './components/PlanActions'
import { ErrorBoundary } from './components/ErrorBoundary'
import { SessionList } from './components/SessionList'
import { RunsPage } from './pages/RunsPage'
import { PlansPage } from './pages/PlansPage'
import { CalendarPage } from './pages/CalendarPage'
import { GoalsPage } from './pages/GoalsPage'
import { ProfilePage } from './pages/ProfilePage'

const spring = { type: 'spring' as const, stiffness: 380, damping: 32 }
const DEMO: AgentKey[] = ['health_assessment', 'meal_planning', 'validation', 'recommendation']

/** The landing hero: the agent plate plays through one request while you type your name. */
function useDemo() {
  const idle = (): Record<AgentKey, AgentView> => Object.fromEntries(
    (['supervisor', 'health_assessment', 'nutrition_knowledge', 'meal_planning', 'workout_planning', 'validation', 'recommendation'] as AgentKey[])
      .map(k => [k, { status: 'idle', runs: 0 }])) as Record<AgentKey, AgentView>
  const [agents, setAgents] = useState(idle)
  const [flights, setFlights] = useState<Flight[]>([])
  useEffect(() => {
    const timers: number[] = []
    DEMO.forEach((k, i) => {
      timers.push(window.setTimeout(() => {
        setFlights(f => [...f, { id: i + 1, from: 'supervisor', to: k, kind: 'handoff' }])
        setAgents(a => ({ ...a, supervisor: { status: 'thinking', runs: 1 }, [k]: { status: 'working', runs: 1 } }))
      }, 600 + i * 1100))
      timers.push(window.setTimeout(() => setAgents(a => ({ ...a, [k]: { status: 'done', runs: 1 } })), 1350 + i * 1100))
    })
    timers.push(window.setTimeout(() => setAgents(a => ({ ...a, supervisor: { status: 'done', runs: 1 }, nutrition_knowledge: { status: 'skipped', runs: 0 } })), 600 + DEMO.length * 1100))
    return () => timers.forEach(clearTimeout)
  }, [])
  return { agents, flights }
}

function Welcome({ onIn }: { onIn: (name: string) => Promise<void> }) {
  const demo = useDemo()
  const [name, setName] = useState('')
  const [err, setErr] = useState<string | null>(null)
  return (
    <main className="min-h-full grid lg:grid-cols-[1fr_1.1fr] items-center gap-8 px-4 sm:px-6 lg:px-16 py-10 sm:py-12 max-w-7xl mx-auto">
      <div className="max-w-lg">
        <p className="font-display text-turmeric text-lg mb-6">Nourish</p>
        <h1 className="font-display text-4xl sm:text-5xl lg:text-6xl font-semibold leading-[1.04] tracking-tight m-0">
          Meals planned around your body, your kitchen and your medicines.
        </h1>
        <p className="text-mist text-base sm:text-lg mt-5 sm:mt-6 leading-relaxed">
          Five specialist agents assess your health, look up guidelines, plan meals from Indian or US food tables, and check every plan before you see it.
        </p>
        <form className="mt-10 flex gap-2 max-w-md" onSubmit={async e => {
          e.preventDefault(); setErr(null)
          try { await onIn(name.trim()) } catch (x) { setErr((x as Error).message) }
        }}>
          <label htmlFor="name" className="sr-only">Your first name</label>
          <input id="name" value={name} onChange={e => setName(e.target.value)} maxLength={40} placeholder="Your first name"
            className="flex-1 rounded-full bg-plum border border-line px-5 py-3 outline-none focus:border-turmeric" />
          <motion.button whileTap={{ scale: 0.96 }} disabled={!name.trim()} className="shrink-0 whitespace-nowrap rounded-full bg-turmeric text-night font-semibold px-5 sm:px-6 disabled:opacity-40">
            Get started
          </motion.button>
        </form>
        {err && <p role="alert" className="text-chilli text-sm mt-3">{err} Is the API running?</p>}
      </div>
      <motion.div initial={{ opacity: 0, scale: 0.94, rotate: -6 }} animate={{ opacity: 1, scale: 1, rotate: 0 }}
        transition={{ type: 'spring', stiffness: 60, damping: 16 }} className="aspect-square w-full max-w-[560px] justify-self-center">
        <AgentGraph agents={demo.agents} flights={demo.flights} revisions={0} verdict={demo.agents.validation.status === 'done' ? 'PASS' : undefined} />
      </motion.div>
    </main>
  )
}

type Tab = 'plan' | 'workout' | 'targets' | 'sources'
const TABS: [Tab, string][] = [['plan', 'Meal plan'], ['workout', 'Workout'], ['targets', 'Your targets'], ['sources', 'Sources']]

/** Tabs with the ARIA tabs pattern: roving tabindex, arrow keys, Home/End, linked panels. */
function useTabs(tab: Tab, setTab: (t: Tab) => void) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  const onKeyDown = (e: KeyboardEvent) => {
    const i = TABS.findIndex(([k]) => k === tab)
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[e.key]
    if (next === undefined) return
    e.preventDefault()
    const k = TABS[(next + TABS.length) % TABS.length][0]
    setTab(k)
    refs.current[k]?.focus()
  }
  return { refs, onKeyDown }
}

/** Guideline searches from this turn's tool events, paired with what each returned. */
function guidelineSearches(events: AgentEvent[]): GuidelineSearch[] {
  const out: GuidelineSearch[] = []
  for (const e of events) {
    if (e.type === 'tool_call' && e.tool === 'search_guidelines') {
      const args = (e.args ?? {}) as { query?: string; _preview?: string }
      out.push({ agent: AGENT_LABEL[AGENT_BY_NAME[e.agent ?? ''] as AgentKey] ?? e.agent ?? '', query: args.query ?? args._preview ?? '', found: '…' })
    } else if (e.type === 'tool_result' && e.tool === 'search_guidelines' && out.length) {
      const titles = [...(e.preview ?? '').matchAll(/"title": "([^"]+)"/g)].map(m => m[1])
      out[out.length - 1].found = titles.length ? titles.slice(0, 3).join('; ') : 'results returned'
    }
  }
  return out
}

type Pane = 'chat' | 'work'
type Chat = ReturnType<typeof useChat>

function Workspace({ chat, profile, name, historyOpen, setHistoryOpen }: {
  chat: Chat; profile: Profile; name: string; historyOpen: boolean; setHistoryOpen: (v: boolean) => void
}) {
  const [tab, setTab] = useState<Tab>('plan')
  const [pane, setPane] = useState<Pane>('chat')  // small screens show one pane at a time
  const [seenPlan, setSeenPlan] = useState<string | undefined>()
  const tabs = useTabs(tab, setTab)
  const s = chat.latest
  const verdictEvents = chat.events.filter(e => e.type === 'validation_verdict')
  const verdict = verdictEvents.at(-1)?.verdict
  const revisions = verdictEvents.filter(e => e.verdict === 'REJECT').length
  const planMsg = [...chat.messages].reverse().find(m => m.state?.recommendation?.plan || m.state?.recommendation?.workout)
  const plan = [...chat.messages].reverse().find(m => m.state?.recommendation?.plan)?.state?.recommendation?.plan
  const workout = [...chat.messages].reverse().find(m => m.state?.recommendation?.workout)?.state?.recommendation?.workout
  const unseen = !!planMsg && planMsg.id !== seenPlan && pane !== 'work'
  const showPane = (k: Pane) => { setPane(k); if (k === 'work') setSeenPlan(planMsg?.id) }
  const answers = chat.messages.filter(m => m.role === 'assistant').length
  const openThread = async (id: string) => {
    setHistoryOpen(false)
    if (id !== chat.threadId) { await chat.open(id).catch(() => {}); setPane('chat') }
  }
  const sessions = (
    <SessionList current={chat.threadId} refresh={answers} onOpen={openThread}
      onNew={() => { chat.reset(); setHistoryOpen(false) }} />
  )
  useEffect(() => {
    if (s?.recommendation?.plan) setTab('plan')
    else if (s?.recommendation?.workout) setTab('workout')
  }, [s])

  return (
    <div className="h-full flex flex-col">
      <nav aria-label="Panels" className="lg:hidden grid grid-cols-2 gap-1 p-1 mx-4 mt-3 rounded-full bg-plum border border-line">
        {([['chat', 'Chat', MessageSquare], ['work', 'Agents & plan', Soup]] as const).map(([k, l, Icon]) => (
          <button key={k} onClick={() => showPane(k)} aria-pressed={pane === k}
            className={clsx('relative inline-flex items-center justify-center gap-1.5 rounded-full py-1.5 text-sm transition-colors',
              pane === k ? 'text-night' : 'text-mist')}>
            {pane === k && <motion.span layoutId="pane-pill" transition={spring} className="absolute inset-0 rounded-full bg-turmeric" />}
            <Icon size={14} aria-hidden className="relative" /><span className="relative">{l}</span>
            {k === 'work' && pane !== 'work' && (chat.busy || unseen) && (
              <span className="relative size-2 rounded-full bg-leaf" aria-label={chat.busy ? 'agents working' : 'new plan'} />
            )}
          </button>
        ))}
      </nav>
      {historyOpen && (
        <div className="xl:hidden fixed inset-0 z-30 flex" role="dialog" aria-modal="true" aria-label="Conversation history">
          <div className="w-72 max-w-[85vw] h-full bg-night border-r border-line shadow-2xl">{sessions}</div>
          <button className="flex-1 bg-black/40" aria-label="Close history" onClick={() => setHistoryOpen(false)} />
        </div>
      )}
      <div className="flex-1 min-h-0 flex">
      <aside className="hidden xl:block w-64 shrink-0 border-r border-line/70">{sessions}</aside>
      <div className="flex-1 min-w-0 min-h-0 grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div className={clsx('min-h-0 lg:border-r border-line/70', pane === 'chat' ? 'block' : 'hidden lg:block')}>
          <Chat messages={chat.messages} busy={chat.busy} onSend={chat.send} region={profile.region} name={name}
            onShowWork={() => showPane('work')} />
        </div>
        <div className={clsx('min-h-0 overflow-y-auto scroll-thin', pane === 'work' ? 'block' : 'hidden lg:block')}>
          <section aria-label="Agents" className="grid sm:grid-cols-[minmax(0,1fr)_14rem] gap-4 p-4 sm:p-5 border-b border-line/70">
            <div className="aspect-square max-h-[380px] w-full mx-auto">
              <AgentGraph agents={chat.agents} flights={chat.flights} verdict={verdict} revisions={revisions} />
            </div>
            <div className="min-h-0 max-h-[380px] flex flex-col">
              <h2 className="font-display text-base font-semibold mt-1 mb-3">What the agents are doing</h2>
              <div className="flex-1 min-h-0"><ActivityLog events={chat.events} /></div>
            </div>
          </section>
          <section className="p-4 sm:p-5">
            <LayoutGroup id="tabs">
              <div role="tablist" aria-label="Results" onKeyDown={tabs.onKeyDown} className="flex gap-5 border-b border-line/70 mb-5 overflow-x-auto">
                {TABS.map(([k, l]) => (
                  <button key={k} id={`tab-${k}`} role="tab" aria-selected={tab === k} aria-controls={`panel-${k}`}
                    tabIndex={tab === k ? 0 : -1} ref={el => { tabs.refs.current[k] = el }} onClick={() => setTab(k)}
                    className={clsx('relative pb-2.5 text-[15px] whitespace-nowrap transition-colors', tab === k ? 'text-cream' : 'text-dim hover:text-mist')}>
                    {l}
                    {tab === k && <motion.span layoutId="tab-line" transition={spring} className="absolute left-0 right-0 -bottom-px h-0.5 bg-turmeric rounded-full" />}
                  </button>
                ))}
              </div>
            </LayoutGroup>
            <AnimatePresence mode="wait">
              <motion.div key={tab} id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18 }}
                className="space-y-4">
                {tab === 'plan' && <>
                  {plan && <PlanActions key={`meal-${planMsg?.id}`} threadId={chat.threadId} kind="meal" />}
                  <PlanPanel plan={plan} health={s?.health ?? null} validation={s?.latest_validation ?? null} />
                </>}
                {tab === 'workout' && <>
                  {workout && <PlanActions key={`workout-${planMsg?.id}`} threadId={chat.threadId} kind="workout" />}
                  <WorkoutPanel workout={workout} requested={!!s && !workout && chat.messages.some(m => /workout|exercise|training/i.test(m.text) && m.role === 'user')} />
                </>}
                {tab === 'targets' && <TargetsPanel health={s?.health ?? null} />}
                {tab === 'sources' && <EvidencePanel evidence={s?.evidence ?? []} cited={s?.recommendation?.citations ?? []}
                  searches={guidelineSearches(chat.events)} />}
              </motion.div>
            </AnimatePresence>
          </section>
        </div>
      </div>
      </div>
    </div>
  )
}

type Page = 'chat' | 'plans' | 'calendar' | 'goals' | 'runs' | 'profile'
const PAGES: [Page, string, typeof MessageSquare][] = [
  ['chat', 'Chat', MessageSquare], ['plans', 'Plans', ClipboardList], ['calendar', 'Calendar', CalendarDays],
  ['goals', 'Goals', Target], ['runs', 'Runs', History], ['profile', 'Profile', UserRound],
]
const pageFromHash = (): Page => {
  const h = window.location.hash.slice(1) as Page
  return PAGES.some(([k]) => k === h) ? h : 'chat'
}

/** Signed-in app: top navigation, the chat workspace (kept mounted so a run continues in the background)
 * and the other pages. */
function Shell({ profile, name, onEditProfile, onProfile }: {
  profile: Profile; name: string; onEditProfile: () => void; onProfile: (p: Profile) => void
}) {
  const chat = useChat()
  const [page, setPage] = useState<Page>(pageFromHash)
  const [historyOpen, setHistoryOpen] = useState(false)
  useEffect(() => {
    const on = () => setPage(pageFromHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const health: Health | null = [...chat.messages].reverse().find(m => m.state?.health)?.state?.health ?? null
  const go = (p: Page) => { window.location.hash = p; setPage(p) }
  const title = PAGES.find(([k]) => k === page)?.[1]
  return (
    <div className="h-full flex flex-col">
      <header className="flex items-center gap-3 px-4 sm:px-6 py-2.5 border-b border-line/70">
        <span className="font-display text-xl font-semibold text-turmeric">Nourish</span>
        <nav aria-label="Main" className="flex-1 min-w-0 overflow-x-auto scroll-thin">
          <ul className="flex gap-1 list-none m-0 p-0">
            {PAGES.map(([k, l, Icon]) => (
              <li key={k}>
                <a href={`#${k}`} onClick={e => { e.preventDefault(); go(k) }} aria-current={page === k ? 'page' : undefined}
                  className={clsx('inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm whitespace-nowrap transition-colors',
                    page === k ? 'bg-plum-2 text-cream' : 'text-dim hover:text-mist')}>
                  <Icon size={14} aria-hidden />{l}
                  {k === 'chat' && chat.busy && page !== 'chat' && <span className="size-1.5 rounded-full bg-leaf" aria-label="agents working" />}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        {page === 'chat' && (
          <button onClick={() => setHistoryOpen(true)} aria-label="Conversation history" title="History"
            className="xl:hidden inline-flex items-center gap-1.5 text-sm rounded-full border border-line px-3 py-1.5 hover:border-mist shrink-0">
            <History size={14} aria-hidden /><span className="hidden md:inline">History</span>
          </button>
        )}
        {page === 'chat' && (
          <button onClick={chat.reset} aria-label="New conversation" title="New conversation"
            className="inline-flex items-center gap-1.5 text-sm rounded-full border border-line px-3 py-1.5 hover:border-mist shrink-0">
            <Plus size={14} aria-hidden /><span className="hidden md:inline">New conversation</span>
          </button>
        )}
      </header>
      <div className={clsx('flex-1 min-h-0', page === 'chat' ? 'block' : 'hidden')}>
        <Workspace chat={chat} profile={profile} name={name} historyOpen={historyOpen} setHistoryOpen={setHistoryOpen} />
      </div>
      {page !== 'chat' && (
        <main className="flex-1 min-h-0 overflow-y-auto scroll-thin">
          <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
            <h1 className="font-display text-3xl font-semibold mt-0 mb-5">{title}</h1>
            <ErrorBoundary key={page} label={title ?? page}>
              {page === 'plans' && <PlansPage />}
              {page === 'calendar' && <CalendarPage />}
              {page === 'goals' && <GoalsPage profile={profile} />}
              {page === 'runs' && <RunsPage />}
              {page === 'profile' && <ProfilePage profile={profile} name={name} health={health} onEdit={onEditProfile} onSaved={onProfile} />}
            </ErrorBoundary>
          </div>
        </main>
      )}
    </div>
  )
}

export default function App() {
  const [stage, setStage] = useState<'loading' | 'welcome' | 'onboarding' | 'app'>(session.token ? 'loading' : 'welcome')
  const [profile, setProfile] = useState<Profile>(EMPTY_PROFILE)
  const name = session.name ?? 'there'

  useEffect(() => {
    if (stage !== 'loading') return
    api.getProfile()
      .then(r => { if (r.profile) { setProfile({ ...EMPTY_PROFILE, ...r.profile }); setStage('app') } else setStage('onboarding') })
      .catch(() => setStage('welcome'))
  }, [stage])

  return (
    <MotionConfig reducedMotion="user">
    <div className="grain relative h-full">
      <div className="relative z-10 h-full">
        <AnimatePresence mode="wait">
          <motion.div key={stage} className="h-full" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.25 }}>
            {stage === 'welcome' && <Welcome onIn={async n => { await api.login(n); setStage('loading') }} />}
            {stage === 'onboarding' && (
              <Onboarding initial={{ ...profile, name }} onDone={async p => { const r = await api.putProfile(p); setProfile({ ...EMPTY_PROFILE, ...r.profile }); setStage('app') }} />
            )}
            {stage === 'app' && <Shell profile={profile} name={name} onEditProfile={() => setStage('onboarding')}
              onProfile={p => setProfile({ ...EMPTY_PROFILE, ...p })} />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
    </MotionConfig>
  )
}
