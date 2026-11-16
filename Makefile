# Nutrition Intelligence Platform - developer commands
.PHONY: setup infra index data retag mcp api web dev test lint eval eval-offline docker clean-db

BACKEND := cd backend &&
# API port; e.g. `make dev API_PORT=8010` if 8000 is taken (the web dev proxy follows it)
API_PORT ?= 8000
export API_PORT

setup:            ## install backend + frontend dependencies
	$(BACKEND) uv sync --extra dev
	cd frontend && npm install
	@test -f .env || cp .env.example .env

infra:            ## start Postgres (pgvector) and Phoenix tracing
	docker compose up -d db phoenix

index:            ## embed and index the guideline corpus
	$(BACKEND) uv run python -m nip.rag.store

data:             ## rebuild data/processed/foods.csv from the raw IFCT + USDA tables
	@test -f data/raw/usda_sr28.sql || (cd /tmp && rm -rf usda-sr28 && git clone --depth 1 https://github.com/Adyg/usdanl-sr28-postgresql usda-sr28 && cp usda-sr28/usda_sr28.sql $(CURDIR)/data/raw/)
	$(BACKEND) uv run python -m nip.data.ingest_foods --ifct ../data/raw/ifct2017/compositions.csv \
	  --ifct-groups ../data/raw/ifct2017/groups.csv --usda ../data/raw/usda_sr28.sql --out ../data/processed/foods.csv

retag:            ## re-derive allergen tags in data/processed/foods.csv after a taxonomy change (no download)
	$(BACKEND) uv run python -m nip.data.ingest_foods --retag ../data/processed/foods.csv

mcp:              ## MCP tool server on :8765
	$(BACKEND) uv run python -m nip.mcp_server.server

api:              ## API on :8000 with reload
	$(BACKEND) uv run uvicorn nip.api.main:app --reload --port $(API_PORT)

web:              ## frontend on :5173
	cd frontend && npm run dev

dev:              ## infra + MCP + API + web together (Ctrl-C stops all)
	$(MAKE) infra
	$(MAKE) -j3 mcp api web

test:             ## full test suite (no LLM needed)
	$(BACKEND) uv run pytest -q

lint:
	$(BACKEND) uv run ruff check src tests
	cd frontend && npm run lint

eval:             ## evaluation suite against the configured LLM (writes eval/results)
	$(BACKEND) uv run python -m nip.eval.run --suite all

eval-offline:     ## retrieval metrics only (no LLM)
	$(BACKEND) uv run python -m nip.eval.run --suite retrieval

docker:           ## everything in containers
	docker compose --profile app up --build

clean-db:
	docker compose down -v
