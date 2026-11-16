import { AnimatePresence, LayoutGroup, MotionConfig, motion } from 'motion/react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { MessageSquare, PenLine, Plus, Soup } from 'lucide-react'
import clsx from 'clsx'
import { api, session } from './lib/api'
import { EMPTY_PROFILE, type AgentKey, type Profile } from './lib/types'
import { useChat, type AgentView, type Flight } from './lib/useChat'
import { Thali } from './components/Thali'
import { ActivityLog } from './components/ActivityLog'
import { Chat } from './components/Chat'
import { EvidencePanel, PlanPanel, TargetsPanel } from './components/Panels'
import { Onboarding } from './components/Onboarding'

const spring = { type: 'spring' as const, stiffness: 380, damping: 32 }
const DEMO: AgentKey[] = ['health_assessment', 'meal_planning', 'validation', 'recommendation']

/** The landing hero: the thali plays through one request while you type your name. */
function useDemo() {
  const idle = (): Record<AgentKey, AgentView> => Object.fromEntries(
    (['supervisor', 'health_assessment', 'nutrition_knowledge', 'meal_planning', 'validation', 'recommendation'] as AgentKey[])
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
        <p className="font-display text-turmeric text-lg mb-6">Thali</p>
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
        <Thali agents={demo.agents} flights={demo.flights} revisions={0} verdict={demo.agents.validation.status === 'done' ? 'PASS' : undefined} />
      </motion.div>
    </main>
  )
}

type Tab = 'plan' | 'targets' | 'sources'
const TABS: [Tab, string][] = [['plan', 'Meal plan'], ['targets', 'Your targets'], ['sources', 'Sources']]

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

type Pane = 'chat' | 'work'

function Workspace({ profile, name, onEditProfile }: { profile: Profile; name: string; onEditProfile: () => void }) {
  const chat = useChat()
  const [tab, setTab] = useState<Tab>('plan')
  const [pane, setPane] = useState<Pane>('chat')  // small screens show one pane at a time
  const [seenPlan, setSeenPlan] = useState<string | undefined>()
  const tabs = useTabs(tab, setTab)
  const s = chat.latest
  const verdictEvents = chat.events.filter(e => e.type === 'validation_verdict')
  const verdict = verdictEvents.at(-1)?.verdict
  const revisions = verdictEvents.filter(e => e.verdict === 'REJECT').length
  const planMsg = [...chat.messages].reverse().find(m => m.state?.recommendation?.plan)
  const plan = planMsg?.state?.recommendation?.plan
  const unseen = !!planMsg && planMsg.id !== seenPlan && pane !== 'work'
  const showPane = (k: Pane) => { setPane(k); if (k === 'work') setSeenPlan(planMsg?.id) }
  useEffect(() => { if (s?.recommendation?.plan) setTab('plan') }, [s])

  const headerBtn = 'inline-flex items-center gap-1.5 text-sm rounded-full border border-line px-3 py-1.5 hover:border-mist'
  return (
    <div className="h-full flex flex-col">
      <header className="flex items-center gap-3 sm:gap-4 px-4 sm:px-6 py-3 border-b border-line/70">
        <span className="font-display text-xl font-semibold text-turmeric">Thali</span>
        <span className="text-sm text-dim hidden md:inline">
          {profile.region === 'IN' ? 'India' : 'United States'} food table, {profile.diet_pattern}
          {profile.allergies.length > 0 && `, avoiding ${profile.allergies.join(', ')}`}
        </span>
        <div className="ml-auto flex gap-2">
          <button onClick={onEditProfile} aria-label="Edit profile" title="Edit profile" className={headerBtn}>
            <PenLine size={14} aria-hidden /><span className="hidden sm:inline">Edit profile</span>
          </button>
          <button onClick={chat.reset} aria-label="New conversation" title="New conversation" className={headerBtn}>
            <Plus size={14} aria-hidden /><span className="hidden sm:inline">New conversation</span>
          </button>
        </div>
      </header>
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
      <div className="flex-1 min-h-0 grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div className={clsx('min-h-0 lg:border-r border-line/70', pane === 'chat' ? 'block' : 'hidden lg:block')}>
          <Chat messages={chat.messages} busy={chat.busy} onSend={chat.send} region={profile.region} name={name}
            onShowWork={() => showPane('work')} />
        </div>
        <div className={clsx('min-h-0 overflow-y-auto scroll-thin', pane === 'work' ? 'block' : 'hidden lg:block')}>
          <section aria-label="Agents" className="grid sm:grid-cols-[minmax(0,1fr)_14rem] gap-4 p-4 sm:p-5 border-b border-line/70">
            <div className="aspect-square max-h-[380px] w-full mx-auto">
              <Thali agents={chat.agents} flights={chat.flights} verdict={verdict} revisions={revisions} />
            </div>
            <div className="min-h-0 max-h-[380px] flex flex-col">
              <h2 className="font-display text-base font-semibold mt-1 mb-3">What the agents are doing</h2>
              <div className="flex-1 min-h-0"><ActivityLog events={chat.events} /></div>
            </div>
          </section>
          <section className="p-4 sm:p-5">
            <LayoutGroup id="tabs">
              <div role="tablist" aria-label="Results" onKeyDown={tabs.onKeyDown} className="flex gap-6 border-b border-line/70 mb-5">
                {TABS.map(([k, l]) => (
                  <button key={k} id={`tab-${k}`} role="tab" aria-selected={tab === k} aria-controls={`panel-${k}`}
                    tabIndex={tab === k ? 0 : -1} ref={el => { tabs.refs.current[k] = el }} onClick={() => setTab(k)}
                    className={clsx('relative pb-2.5 text-[15px] transition-colors', tab === k ? 'text-cream' : 'text-dim hover:text-mist')}>
                    {l}
                    {tab === k && <motion.span layoutId="tab-line" transition={spring} className="absolute left-0 right-0 -bottom-px h-0.5 bg-turmeric rounded-full" />}
                  </button>
                ))}
              </div>
            </LayoutGroup>
            <AnimatePresence mode="wait">
              <motion.div key={tab} id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.18 }}>
                {tab === 'plan' && <PlanPanel plan={plan} health={s?.health ?? null} validation={s?.latest_validation ?? null} />}
                {tab === 'targets' && <TargetsPanel health={s?.health ?? null} />}
                {tab === 'sources' && <EvidencePanel evidence={s?.evidence ?? []} />}
              </motion.div>
            </AnimatePresence>
          </section>
        </div>
      </div>
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
            {stage === 'app' && <Workspace profile={profile} name={name} onEditProfile={() => setStage('onboarding')} />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
    </MotionConfig>
  )
}
