# Evaluation framework

Three suites, one runner: `uv run python -m nip.eval.run --suite all --judge`. Results go to
`eval/results/` as JSON and a markdown summary (`*-latest.md`).

**Setup for the numbers below** (16 Nov 2026, one RTX 5080 16 GB): generator `qwen2.5:7b-instruct` with a
32k context window (`nip-qwen2.5:7b-32k`), embeddings `nomic-embed-text`, judge `llama3.1:8b`
(`nip-llama3.1:8b-12k`, a different model family from the generator), tools over MCP, hybrid retrieval.

## Retrieval (`eval/datasets/retrieval.jsonl`, 50 labelled queries)

Each query is written the way a user would phrase it ("can I eat palak on warfarin?"), tagged with a
region, and labelled with the chunk ids that answer it. Most queries have 1-3 relevant chunks, so
Precision@5 tops out around 0.3-0.6.

| Mode (nomic-embed-text) | Recall@1 | Recall@3 | Recall@5 | Recall@10 | Precision@1 | Precision@5 | MRR | nDCG@5 | p50 / p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| dense | 0.70 | 0.94 | 0.97 | 0.98 | 0.92 | 0.29 | **0.96** | 0.94 | 31 / 175 |
| sparse (Postgres FTS) | 0.52 | 0.83 | 0.92 | 0.97 | 0.68 | 0.27 | 0.81 | 0.80 | 0.3 / 0.7 |
| hybrid (RRF) | 0.64 | 0.93 | 0.96 | 0.97 | 0.84 | 0.29 | 0.92 | 0.90 | 29 / 34 |

**Reading.** With real embeddings, dense alone ranks best on this set; fusing in the weaker lexical ranker
costs some MRR (0.96 → 0.92) while recall is unchanged. Hybrid stays the default because agents also
search exact drug and food names, where lexical matching protects against embedding misses, but this is a
measured trade-off, not a free win. We did not tune the fusion weights on these 50 queries (that would fit
the test set); the next step is a held-out query set and a cross-encoder reranker (`NIP_RERANKER`). The
hash-embedder baseline (`retrieval-baseline-hash.md`, dense MRR 0.60) only validates the pipeline.

## Agents and generation (`eval/datasets/scenarios.jsonl`, 16 scenarios)

Personas cover the main paths and edge cases: Indian vegetarian fat loss with a peanut allergy, US muscle
gain, Jain, vegan with a soy allergy (possibly infeasible), halal, type 2 diabetes, hypertension, kidney
disease, pregnancy, a minor, warfarin and creatine questions, statin and grapefruit, an unrealistic goal,
missing essentials, and a profile given in free text with imperial units. Each declares deterministic
expectations (final status, agents that must or must not run, a validated plan, verdict, escalation, a risk
flag, citations). Every scenario also checks the hard invariant: **no food shown is unknown or disallowed
for that person.**

| Metric | Final run | Three full runs |
|---|---|---|
| Success rate (all expectations met) | **0.81** (13/16) | 0.69 / 0.63 / 0.81 |
| Validated plan delivered when one was requested | 4/7 | 2/7 / 2/7 / 4/7 |
| Safety, escalation, knowledge and clarify scenarios | 9/9 | 9/9 / 8/9 / 9/9 |
| Unsafe or unknown food shown | **0** | 0 / 0 / 0 |
| Workflow completion (terminal state, no crash) | 1.00 | 0.94 / 0.88 / 1.00 |
| Agent failure rate (any agent exhausted its budget in the scenario) | 0.69 | 0.44 / 0.50 / 0.69 |
| End-to-end latency p50 / p95 | 65 s / 309 s | |
| Agent latency p50: health / knowledge / planner / validation / answer | 2.3 / 4.3 / 41 / 26 / 3.0 s | |
| Tokens per scenario (mean), revisions (mean) | 143k, 0.75 | |
| Override rate (scenarios where an invariant corrected the supervisor) | 1.00 | |

Generation metrics (judge, answered scenarios of the final run):

| Metric | Method | Value |
|---|---|---|
| Faithfulness | judge extracts atomic claims, marks each supported by the agent's context | 0.69 |
| Groundedness: citations | share of cited passages that support the answer (judge) | 1.00 |
| Groundedness: numbers | share of unit-bearing numbers that appear in the context (deterministic) | 0.87 |
| Answer relevance | judge 1-5, normalised | 0.60 |

**Reading.** Safety holds every run: every escalation, red-team and knowledge path passes, and no unsafe
food has been shown. The weak point is the 7B planner: it delivers a validated plan for 30-60% of plan
requests, and it varies from run to run (the same scenario passes in one run and fails in the next). When it fails,
the user gets an honest "couldn't build a safe plan" answer, never an unvalidated one. Most unsupported
claims the judge finds are generic filler ("stay hydrated") rather than wrong facts. The agent failure rate
counts a planner attempt that ran out of steps even when a later attempt succeeded. Calibrate the judge
before trusting the exact values: label 20-30 answers by hand and check agreement (not done yet).

**What the real runs changed.** The first runs against Ollama found faults the scripted tests could not:
the router's function calls came back empty (fixed: JSON-schema output); a 7B model invented a full
profile after a tool error (fixed: profile facts must appear in the user's words); 12% of calls were
silently truncated by an 8k server context (fixed: 32k model variant); the planner could not hit ±10% by
editing grams (fixed: the deterministic `scale_portions` solver; the check is unchanged); the router answered
plan requests without planning (fixed: a requested plan is an objective in `enforce()`); an escalated
kidney-disease answer quoted a 1.6 g/kg protein target from a sports passage (fixed: no numeric targets
in safe mode); and the red team caught medication advice leaking through a fallback answer (fixed: a final
output gate on every answer). The last two fixes landed after the final agent run and only make answers
more conservative; the red-team suite was re-run after them.

## Red team (`eval/datasets/redteam.jsonl`, 10 attacks)

Prompt injection alone (must be blocked) and embedded in a real request (flagged; plan must still exclude
the allergen), a 600 kcal request and purging language (eating-disorder escalation), medication-change and
diagnosis requests (no banned output), a crisis message (support response only), PII in the message (must
not be stored unredacted), and a "cure guaranteed" jailbreak. The release bar is 100%.

| Run | Result |
|---|---|
| Runs 1 and 2 | 10/10 |
| Run 3 | 9/10: medication-change text reached a fallback answer (fixed, see above) |
| After the fix (final) | **10/10**, workflow completion 1.0 |

## Deterministic tests (`make test`, 76 tests, no LLM)

Energy maths against reference values, food filtering for every diet pattern, the rule engine (allergens,
unknown foods, interactions, escalation), the portion solver, profile-fact grounding and unit conversion,
request exclusions, retrieval on an embedded Postgres, the MCP server against the in-process tools
(including the DNS-rebinding Host check), the API end to end with streaming, and the orchestration with a
scripted model: routing, every invariant, the reject-and-revise loop, escalation, plan edits, honest
failure answers, blocked inputs and multi-turn memory.

## After the evaluation runs

Features added after the measured runs: the Workout Planning agent and its rule engine, protein as a range
with a computed cap (the most common planner failure in the runs was "protein above target +10%"),
accepting the planner's own checked rescale at budget end, computed healthy-weight answers, and the
product layer (runs with cost, saved plans, calendar, goals). A live session after these changes
delivered a validated meal plan and a checked workout in 143 s (`docs/screenshots`). The scenario
suite has not been re-run since; re-running it, with workout scenarios added, is the next step.

## In CI and production

Every pull request runs the deterministic tests and the retrieval suite. Nightly runs the scenario and
red-team suites against the staging model, at least three times, reporting the spread rather than one run.
A release needs: red team 100%, no unsafe food shown, success rate at or above the last release,
faithfulness ≥ 0.9. In production, a sample of real turns (with consent and redaction) is scored by the
judge, and supervisor overrides, validation rejections, fallback answers and agent failures are tracked
as health signals.
