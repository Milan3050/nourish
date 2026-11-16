# Production deployment design

## Topology

```
            CDN (static SPA) ── WAF ── Load balancer (TLS)
                                          │
                     ┌────────────────────┴────────────────────┐
                     │ API pods (FastAPI + LangGraph), stateless │  HPA on CPU + in-flight streams
                     └───┬──────────────┬───────────────┬──────┘
                         │ MCP (private)│               │ OTLP
               ┌─────────▼────────┐  ┌──▼──────────┐  ┌─▼────────────────────┐
               │ MCP tool pods     │  │ LLM gateway │  │ Collector → traces,   │
               │ (food, rules, RAG)│  │ (routing,   │  │ logs, metrics         │
               └─────────┬────────┘  │ fallback,   │  └──────────────────────┘
                         │           │ cost caps)  │
             ┌───────────▼─────────┐ └──┬──────────┘
             │ Managed Postgres     │    │ hosted model (BAA / DPA) or vLLM on GPU nodes
             │ pgvector, checkpoints│
             │ profiles, audit log  │      Temporal cluster + workers: weekly re-planning,
             └──────────────────────┘      bulk re-evaluation, corpus re-indexing
```

- **API pods** are stateless: conversation state lives in the Postgres checkpointer, so any pod can resume
  any thread. Streams are long-lived, so autoscaling uses in-flight streams as well as CPU.
- **MCP tool pods** run on the private network only, with service-to-service auth (mTLS or signed tokens).
  DNS-rebinding protection stays on with an explicit Host allow-list (`NIP_MCP_ALLOWED_HOSTS`): the MCP SDK
  only enables it for loopback binds by default, so a pod bound to 0.0.0.0 would otherwise accept any Host.
  The food tables and rules ship inside the image, versioned with the code.
- **LLM gateway** (LiteLLM or similar): per-environment model routing (small model for the supervisor, larger
  for planning), automatic fallback to a second provider, per-user and per-tenant token budgets, response caching
  for retrieval-only questions. Self-hosted models (vLLM, Ollama) are configured with an explicit context
  window large enough for agent transcripts (32k here); a server default of 8k silently truncated 12% of
  calls in the first real runs.
- **Postgres**: managed, multi-AZ, point-in-time recovery, encryption at rest with KMS, read replica for
  analytics. Row-level security on profiles. Partition checkpoints by month and expire them by retention policy.
- **Temporal** handles work that outlives a request: regenerating weekly plans, re-validating stored plans when
  the rules version changes, re-indexing the corpus. Each workflow calls the same LangGraph graph as an activity,
  so retries and timeouts are durable.

## Security and compliance

- Identity from an OIDC provider; the API verifies its JWTs (the dev token endpoint is disabled in prod).
- Secrets in a secrets manager, injected at runtime; nothing in images or prompts.
- Health data: minimum necessary fields to the model, PII redaction, prompt/response text hidden from traces,
  configurable retention, export and delete on request (GDPR / DPDP Act 2023 in India), audit log of profile access.
  For US PHI, use HIPAA-eligible model endpoints under a BAA.
- WAF and rate limits at the edge and per user in the API (Redis-backed in prod).
- Telemetry: the OTLP collector requires auth in every shared environment. (The local Phoenix in
  docker-compose has auth off and binds to 127.0.0.1 only.)
- Supply chain: pinned lockfiles, dependency and image scanning, non-root containers, read-only filesystems.

## Release process

1. PR: lint, deterministic tests, retrieval eval.
2. Merge to main: build images, deploy to staging, run scenario + red-team suites against the staging model.
3. Canary 5% of traffic with online metrics (agent failure rate, override rate, rejection rate, p95 latency,
   judge-scored sample). Automatic rollback on regression.
4. Prompt, rule and model changes follow the same path: they are versioned files, not console edits.

## SLOs and alerts

| Signal | Target |
|---|---|
| Turn completion (non-error) | ≥ 99.5% |
| p95 turn latency (plan requests) | < 30 s hosted model |
| Agent failure rate | < 2% |
| Unsafe food shown | 0 (paging alert; rule engine invariant) |
| Red-team suite | 100% per release |

## Cost controls

Router on a small model, planner on a capable one; cache embeddings and retrieval results; cap revisions and
steps per request; stream early so users aren't waiting on a blank screen; track tokens per agent in metrics.
