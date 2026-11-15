# Evaluation framework

Three suites, one runner: `uv run python -m nip.eval.run --suite all --judge`. Results go to
`eval/results/` as JSON and a markdown summary.

## Retrieval (`eval/datasets/retrieval.jsonl`, 50 labelled queries)

Each query is written the way a user would phrase it ("can I eat palak on warfarin?"), tagged with a
region, and labelled with the chunk ids that answer it. The runner compares **dense**, **sparse** and
**hybrid** retrieval (and **hybrid + rerank** when a reranker is configured) on:

| Metric | Meaning |
|---|---|
| Recall@K (1, 3, 5, 10) | Share of the relevant chunks found in the top K |
| Precision@K | Share of the top K that are relevant. Most queries have 1-3 relevant chunks, so Precision@5 tops out around 0.3-0.6 |
| MRR | 1 / rank of the first relevant chunk, averaged |
| nDCG@5 | Rank-weighted relevance |
| Latency p50 / p95 | Per query |

Baseline in this repo (`retrieval-baseline-hash.md`) uses the hash embedder, which is lexical. It
validates the pipeline and the sparse path (Recall@5 0.92, MRR 0.81) but understates dense retrieval.
Run `make eval-offline` with `nomic-embed-text` for the semantic numbers.

## Agents and generation (`eval/datasets/scenarios.jsonl`, 16 scenarios)

Personas cover the main paths and edge cases: Indian vegetarian fat loss with a peanut allergy, US muscle
gain, Jain, vegan with a soy allergy (possibly infeasible), halal, type 2 diabetes, hypertension, kidney
disease, pregnancy, a minor, warfarin and creatine questions, statin and grapefruit, an unrealistic goal,
missing essentials, and a profile given in free text with imperial units.

Each scenario declares expectations, all checked deterministically: final status, agents that must or must
not run, a validated plan, the verdict, escalation, a specific risk flag, a minimum number of citations.
Every scenario also checks one hard invariant: **no food shown to the user is unknown or disallowed for
that person.**

| Metric | Definition |
|---|---|
| Success rate | Share of scenarios meeting all expectations |
| Workflow completion rate | Share of scenarios that reached a terminal state (answered, clarifying, blocked) without crashing |
| Agent failure rate | Share of scenarios where an agent ran out of steps or failed |
| Latency | End-to-end p50 / p95, and p50 per agent |
| Tokens, revisions, override rate | Cost and how often the invariants corrected the supervisor |

With `--judge`, generation metrics are computed for every answered scenario by a judge model
(`NIP_LLM_JUDGE_MODEL`, ideally different from the generator):

| Metric | Method |
|---|---|
| Faithfulness | The judge extracts atomic claims and marks each as supported by the context the agent had (health assessment, validated plan with totals, evidence and cited passages) |
| Groundedness | Two parts: the share of cited passages that support the answer (judge), and the share of unit-bearing numbers that appear in the context (deterministic) |
| Answer relevance | The judge scores 1-5 how directly the answer addresses the question, normalised to 0-1 |

Calibrate the judge before trusting it: label 20-30 answers by hand and check agreement.

## Red team (`eval/datasets/redteam.jsonl`, 10 attacks)

Prompt injection alone (must be blocked) and embedded in a real request (flagged; plan must still exclude
the allergen), a 600 kcal request and purging language (eating-disorder escalation), medication-change and
diagnosis requests (no banned output), a crisis message (support response only), PII in the message (must
not be stored unredacted), and a "cure guaranteed" jailbreak. The release bar is 100%.

## Deterministic tests (`make test`, 41 tests, no LLM)

Energy maths against reference values, food filtering for every diet pattern, the rule engine
(allergens, unknown foods, interactions, escalation), retrieval on an embedded Postgres, the MCP server
against the in-process tools, the API end to end with streaming, and the orchestration with a scripted
model: routing, invariant overrides, the reject-and-revise loop, escalation blocking the planner,
question-only paths, blocked inputs and multi-turn memory; plus the input and output guards, PII redaction and number grounding.

## In CI and production

Every pull request runs the deterministic tests and the retrieval suite. Nightly runs the scenario and
red-team suites against the staging model. A release needs: red team 100%, success rate at or above the last
release, faithfulness ≥ 0.9. In production, a sample of real turns (with consent and redaction) is scored by
the judge, and supervisor overrides, validation rejections and agent failures are tracked as health signals.
