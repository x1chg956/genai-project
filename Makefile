# AuditGuard developer commands (CLAUDE.md "Commands"). Keep every target working.
.DEFAULT_GOAL := help
.PHONY: help install test lint format llm-check seed ingest run-case eval up down ps db-bootstrap

# An unrelated activated virtualenv must not leak into uv's project environment.
unexport VIRTUAL_ENV
UV := uv run
COMPOSE := docker compose

help:  ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install:  ## install dependencies from the lock file
	uv sync --locked

test:  ## unit + MCP + approval tests (no live model or database)
	$(UV) pytest

lint:  ## ruff + mypy
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) mypy

format:  ## apply ruff formatting and fixes
	$(UV) ruff format .
	$(UV) ruff check --fix .

llm-check:  ## verify the configured model servers (tool calling, structured output)
	$(UV) python -m scripts.llm_check $(ARGS)

db-bootstrap:  ## create local Postgres roles and databases (prompts for the admin password)
	./scripts/db_bootstrap.sh

up:  ## start Postgres (host port 5433) and Qdrant in Docker
	$(COMPOSE) up -d --wait postgres qdrant

down:  ## stop the Docker services (data volumes are kept)
	$(COMPOSE) down

ps:  ## show Docker service health
	$(COMPOSE) ps

SEED ?= 42
PROFILE ?= tiny
seed:  ## generate synthetic ledger into Postgres, e.g. make seed SEED=42 PROFILE=tiny
	$(UV) python -m data.generator --seed $(SEED) --profile $(PROFILE) $(ARGS)

ingest:  ## fetch + index corpus (Phase 1)
	@echo "make ingest: not implemented until Phase 1 (scripts/ingest.py, docs/07)"; exit 1

run-case:  ## run one case from the CLI, e.g. make run-case CASE=ALR-SYN-0001 (Phase 1)
	@echo "make run-case: not implemented until Phase 1 (docs/02 Phase 1 task 8)"; exit 1

eval:  ## run the evaluation harness, e.g. make eval CONFIG=... SPLIT=dev (Phase 2)
	@echo "make eval: not implemented until Phase 2 (eval/harness.py, docs/10 §3)"; exit 1
