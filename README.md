# Thali: Nutrition Intelligence Platform

Personalised nutrition guidance, meal plans and workout plans from a team of goal-driven AI agents. A supervisor routes
each request to five specialists (health assessment, nutrition knowledge with agentic RAG, meal planning,
validation and recommendation), and no plan reaches the user until it passes deterministic safety rules and
an independent validation agent. Food data covers India (IFCT 2017) and the US (USDA SR28), selected by region.

The app also keeps saved plans on a calendar, tracks goals, and records every run with its agents,
timings, tokens and cost. Screenshots of a real local-model session: [docs/screenshots](docs/screenshots/README.md).

- **Architecture:** [docs/architecture.md](docs/architecture.md)
- **Evaluation framework:** [docs/evaluation.md](docs/evaluation.md)
- **Production deployment:** [docs/deployment.md](docs/deployment.md)

## Run it locally

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node 20+, [Ollama](https://ollama.com).

```bash
make ollama-models  # qwen2.5:7b-instruct + nomic-embed-text, and a 32k-context variant of qwen
make setup          # Python + Node dependencies, creates .env from .env.example
make infra          # Postgres (pgvector) + Phoenix tracing UI in Docker
make index          # embed the guideline corpus into pgvector
make dev            # MCP tool server :8765, API :8000, web app :5173
```

Open http://localhost:5173. Traces are at http://localhost:6006 (project `nip-api`).
If port 8000 is taken, `make dev API_PORT=8010` (the web dev proxy follows it).

Why the 32k variant: many Ollama installs default to a 2-8k context (`OLLAMA_CONTEXT_LENGTH`) and silently
drop the start of long agent transcripts, which is where the system prompt and goal are.

To use a hosted model instead of Ollama, edit the `NIP_LLM_*` lines in `.env` (OpenAI, Groq and Gemini
examples are included). Embeddings can stay on Ollama.

Run everything in containers instead: `make docker` (web app on http://localhost:8080). The API and MCP
containers reach Ollama on the host through `host.docker.internal`, so Ollama must listen on the Docker
bridge, not only on 127.0.0.1 (on Linux: `OLLAMA_HOST=0.0.0.0:11434` in the Ollama service, then restart
it). Set `NIP_API_HOST_PORT` if 8000 is taken on the host.

## Tests and evaluation

```bash
make test           # 76 deterministic tests: tools, rules, retrieval, MCP, API, orchestration. No LLM needed.
make eval-offline   # retrieval metrics: Recall@K, Precision@K, MRR, nDCG for dense / sparse / hybrid
make eval           # agent scenarios + red team against the configured LLM
cd backend && uv run python -m nip.eval.run --suite agents --judge   # + faithfulness, groundedness, relevance
```

## Repository layout

```
backend/src/nip/
  agents/        GoalAgent base and the five agents (goals, tools, acceptance checks)
  graph/         LangGraph state, supervisor with invariants, graph build
  tools/         energy maths, food search and nutrition arithmetic, tool registry, MCP/in-process backends
  rules/         deterministic safety rule engine + versioned rules.yaml
  rag/           corpus chunking, embeddings, hybrid retrieval on Postgres
  security/      input/output guards, PII redaction
  mcp_server/    MCP server exposing the tools
  api/           FastAPI: auth, profile, streaming chat, metrics
  observability/ structured logging, OpenTelemetry tracing
  eval/          metrics, LLM judge, runner
backend/tests/   deterministic test suite (scripted LLM for orchestration tests)
frontend/        Vite + React + Motion UI with the live agent graph
data/            food tables (processed + raw IFCT) and the guideline corpus
eval/            datasets and results
```

## Notes

- Nutrition numbers are always computed from the food tables by tools, never estimated by a model.
- Guidance is general information, not medical advice; the app escalates to a clinician for minors,
  pregnancy, kidney disease, type 1 diabetes and signs of disordered eating.
- `tests/ui_dev_server.py` runs the real API and graph with a scripted model, for frontend work without Ollama.
