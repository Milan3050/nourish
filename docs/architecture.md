# Nutrition Intelligence Platform: Architecture

## 1. What it does

The platform produces personalised nutrition guidance and meal plans from a person's health data,
preferences and goals. Five specialist agents and a supervisor cooperate on each request. Two rules
shape the design:

1. **Agents decide, tools compute.** Agents are goal-driven: each has a goal, tools and a typed result
   it must submit, and it decides which tools to call and when it is done. Every number that matters
   (energy needs, macros, nutrients per portion, limits) comes from deterministic tools over curated
   food tables, never from the language model.
2. **Routing is dynamic; safety invariants are fixed.** The supervisor chooses the next agent from the
   state of the work. A short list of invariants is enforced in code, outside any prompt. The main
   one: no plan reaches the user without passing validation.

| Request | Path the supervisor takes |
|---|---|
| "Plan 3 days of vegetarian meals for fat loss" | Health → Meal planning ⇄ Validation → Recommendation |
| "I take warfarin, can I eat spinach?" | Knowledge → Recommendation |
| "Swap dinner on day 2" | Meal planning → Validation → Recommendation (profile reused) |
| "I have kidney disease, give me a high-protein plan" | Health (escalates) → Recommendation in safe mode; the planner is blocked |
| "Make me a meal plan" (no body data) | Health → Clarify (asks for age, sex, height, weight) |

## 2. System overview

```
 Browser (Vite + React)                 API service (FastAPI)                    Tool service (MCP)
 ┌──────────────────────┐  POST+SSE   ┌──────────────────────────────────┐ MCP  ┌─────────────────────┐
 │ chat · live agent    │────────────▶│ auth · rate limit · input guard  │─────▶│ search_foods        │
 │ graph · plan · rings │◀────────────│ LangGraph orchestration          │◀─────│ compute_nutrition   │
 └──────────────────────┘  events     │  supervisor + 5 goal agents      │      │ assess_energy       │
                                      │ checkpoints (Postgres)           │      │ check_plan (rules)  │
                                      └───────┬─────────────┬────────────┘      │ search_guidelines   │
                                              │             │                   └───────┬─────────────┘
                               OpenAI-compatible LLM    OpenTelemetry                   │
                               (Ollama / hosted)        → Phoenix / OTLP        Postgres + pgvector
                                                                                food tables (IN, US)
```

Every model call goes through one OpenAI-compatible client. Development runs fully local on Ollama
(`qwen2.5:7b-instruct`, `nomic-embed-text`); a single environment variable switches to OpenAI, Groq,
Gemini or a vLLM cluster.

## 3. Orchestration (LangGraph)

```
intake ─▶ supervisor ─┬─▶ health_assessment ───┐
            ▲         ├─▶ nutrition_knowledge ─┤
            │         ├─▶ meal_planning ───────┤ back to supervisor
            └─────────┼─▶ validation ──────────┘
                      ├─▶ recommendation ─▶ END
                      └─▶ clarify ─▶ END
```

**Intake** runs deterministic guards before any model sees the text: PII redaction, prompt-injection
patterns, eating-disorder and crisis signals, and medical-scope requests (diagnosis, medication changes).
Crisis messages get a fixed support response with helplines and never reach an agent.

**Supervisor.** An LLM call with a structured `Decision {objectives, next, instruction, reason}`, given a
compact summary of the state (which outputs exist, what failed, budgets used). Its proposal then passes
through `enforce()`, which applies these invariants:

| Invariant | Effect |
|---|---|
| A plan needs a health profile | meal_planning → health_assessment |
| Escalation (minor, pregnancy, kidney disease, type 1 diabetes, eating-disorder risk) | planner blocked; safe-mode answer |
| Unvalidated plans are never shown | recommendation → validation |
| Rejected plans are revised, at most N times | recommendation → meal_planning, then give up honestly |
| Step and token budgets per request | forced answer with what is available |
| No repeated loops | an agent can't run more than twice in one turn |

Every override is recorded and streamed, so it shows in traces and in the UI ("Rerouted to Validation:
plans must be validated before they are shown"). If the router model fails, a deterministic fallback
picks the next missing piece, so a bad model response never stalls a request.

**Goal agents.** Each agent is a `GoalAgent`: a reusable LangGraph subgraph (`think → act → think …`)
with tools and a `submit_result` tool whose schema is the agent's output model. Submissions are
validated against the schema and against acceptance checks specific to the agent; failures go back to the
agent as feedback and it keeps working within a step budget. This is what makes the agents goal-driven
rather than prompt chains: "done" means the goal's acceptance criteria hold.

| Agent | Goal | Tools | Accepted only when |
|---|---|---|---|
| Health Assessment | An accurate, safe health profile | `update_profile`, `assess_energy`, `search_guidelines` | energy was computed by the tool, or questions are listed for missing essentials |
| Nutrition Knowledge (agentic RAG) | Answer evidence questions from vetted sources | `search_guidelines` (rewrites, splits, retries) | every cited chunk was actually retrieved in this run; uncited answers are marked insufficient |
| Meal Planning | A plan that meets targets and constraints | `search_foods`, `compute_nutrition` | every food code exists and is allowed for this person; kcal and protein within ±10% each day |
| Validation | Decide if the plan is safe to show | `run_safety_rules`, `search_guidelines` | the rule engine ran; the verdict is never more lenient than the rules |
| Recommendation | A personal, cited, grounded answer | `search_guidelines` | citations exist, every number with a unit appears in the context, no banned claims |

Tools close over the run's context, so the model cannot search another region's food table or check a
plan against someone else's allergies. The planner's search only returns foods the person can eat.

**State.** A typed `NIPState` holds the durable context (profile, health assessment, evidence, plan
versions, validation reports) and per-turn orchestration (objectives, budgets, decisions, agent runs).
Reducers append plan versions and validations (full history for debugging) and de-duplicate evidence.
Agent transcripts stay inside their subgraphs, which keeps the supervisor's context small. A Postgres
checkpointer persists each thread; threads are namespaced by user id, so a guessed thread id from another
user resolves to nothing. Multi-turn works naturally: the profile and plan carry into the next turn.

## 4. Knowledge and data

**Food tables.** Indian Food Composition Tables 2017 (542 foods, NIN Hyderabad) and USDA SR28 (7,785
foods) are normalised into one schema, per 100 g, with explicit allergen and animal-source tags.
**Region is a column, so region-based selection is a filter**: an Indian user's planner sees IFCT, a US
user's sees USDA. Adding a region means adding a table and a tag, not a new system. Tagging favours
recall: when unsure, a food is tagged as containing the allergen.

**Guideline corpus.** 31 documents (92 chunks) summarising Dietary Guidelines for Americans, ICMR-NIN
2020/2024, NIH Office of Dietary Supplements, FDA, NIDDK, WHO and position papers, each with source,
URL, region and topic. Ingestion takes only vetted files from the repository, which closes the
corpus-poisoning route.

**Hybrid retrieval.** pgvector cosine search and Postgres full-text search (weighted title and section)
are fused with Reciprocal Rank Fusion, optionally followed by a cross-encoder reranker. Results are
filtered to the user's region plus global documents. Retrieved text is wrapped as
`<untrusted_document>` before it reaches an agent, and the agents' rules say such text is data.

## 5. Safety and security

**Deterministic controls (code, cannot be prompted away)**
- JWT authentication, per-user rate limiting, thread namespacing, CORS, security headers.
- Pydantic validation at every boundary: physiological ranges on the profile, schemas on every agent result.
- PII redaction (email, phone, Aadhaar, PAN, SSN, card numbers) before text reaches a model, a log or a trace;
  prompt and response text hidden from traces in production.
- Rule engine (versioned YAML + Python): allergens with a second, independent name-based check; diet patterns
  (vegetarian, eggetarian, vegan, Jain, pescatarian); halal, kosher, no-beef, no-pork; calorie floors; macro
  targets; sodium, saturated fat and potassium limits; upper intake levels; drug-nutrient interactions
  (warfarin, statins, MAOIs, metronidazole, ACE inhibitors, levothyroxine); warfarin vitamin-K consistency.
- Escalation triggers that switch to safe mode and recommend a clinician.
- Step, token and revision budgets; tool timeouts; recursion limits.

**AI-specific controls**
- Prompt injection in user text: pattern detection, a hard block when the message has no nutrition
  content, user text fenced as `<user_message>` data.
- Indirect injection from documents: vetted corpus only, tag stripping, `<untrusted_document>` fencing.
- Hallucinated numbers: numbers come only from tools; the Recommendation agent's acceptance check rejects any
  unit-bearing number that isn't in the context.
- Hallucinated citations: citations are resolved from retrieved chunk ids, never from model text.
- Scope: output filter blocks medication-change advice, cure claims, guarantees and diagnoses; a disclaimer is
  added in code.

## 6. Observability

Structured JSON logs (structlog) carry trace and span ids and pass through the PII redactor.
OpenTelemetry with OpenInference instrumentation traces every LLM call, tool call and graph node
to Arize Phoenix locally or any OTLP backend in production. Prometheus metrics: turn latency, agent
latency by agent and status, validation verdicts, supervisor overrides, request counts by outcome. The
UI shows every agent hand-off, tool call, rejected submission, override and verdict as it happens.

## 7. Key decisions

| Decision | Why | Alternatives considered |
|---|---|---|
| LangGraph | Explicit typed state, cycles for the revision loop, checkpointing, streaming, subgraphs | CrewAI hides control flow and state; OpenAI Agents SDK has lighter state and ties you to one vendor |
| Custom GoalAgent over prebuilt ReAct | Acceptance checks on submissions make "done" verifiable | Prebuilt agents stop when the model says so |
| MCP tool server | One versioned tool contract for these agents and any other MCP client; separately deployable | Direct imports (kept as `inprocess` transport for tests) |
| Postgres for vectors, text search, checkpoints, profiles | One datastore to secure, back up and scale | Qdrant + Redis: more moving parts at this scale |
| OpenAI-compatible client | Local Ollama in development, any hosted model in production, no code change | Provider SDKs |
| Temporal (design, not MVP) | Durable long-running jobs (weekly re-planning, bulk re-evaluation) | Not needed for request/response turns |
| Vite SPA | All logic lives in the Python service; no server rendering needed | Next.js would add a second server |

## 8. Limits of the MVP

The guideline corpus consists of curated summaries, not full source documents. Plans use raw ingredients from
the food tables, so composite dishes are built from their parts. A 7-8B local model is slower and weaker at
tool use than hosted models; the acceptance checks and invariants keep its output safe, but it may need
more revisions. Retrieval quality numbers depend on the embedding model; see `docs/evaluation.md`.
