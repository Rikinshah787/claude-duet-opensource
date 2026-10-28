# =============================================================================
# Claude Duet - developer entry points
#
#   make            # same as `make help`
#   make install    # create .venv + install API and web dependencies
#   make dev        # run API + web together
#
# Works with GNU Make on Linux, macOS, WSL and Git Bash. On Windows the
# PowerShell scripts under scripts\ are the first-class path:
#   .\scripts\dev.ps1   /   .\scripts\stop.ps1
# =============================================================================

.DEFAULT_GOAL := help

ROOT    := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
API_DIR := $(ROOT)/apps/api
WEB_DIR := $(ROOT)/apps/web
VENV    := $(ROOT)/.venv

ifeq ($(OS),Windows_NT)
  PY := $(VENV)/Scripts/python.exe
else
  PY := $(VENV)/bin/python
endif

API_PORT ?= 8787
WEB_PORT ?= 3000
API_BASE ?= http://127.0.0.1:$(API_PORT)

COMPOSE ?= docker compose

.PHONY: help install dev api web test lint sync up down logs clean

help: ## Show this help
	@echo ""
	@echo "Claude Duet - make targets"
	@echo "--------------------------------------------------------------"
	@echo "  install   Create .venv, install API deps and web deps"
	@echo "  dev       Run API + web together (scripts/dev.sh)"
	@echo "  api       Run only the API with --reload on :$(API_PORT)"
	@echo "  web       Run only the Next.js dev server on :$(WEB_PORT)"
	@echo "  test      Run the API test suite (pytest)"
	@echo "  lint      Ruff on the API, tsc on the web app"
	@echo "  sync      POST $(API_BASE)/api/sync (check every connected app now)"
	@echo "  up        docker compose up -d (full stack)"
	@echo "  down      docker compose down"
	@echo "  logs      Follow docker compose logs"
	@echo "  clean     Remove caches, build output and local databases"
	@echo "--------------------------------------------------------------"
	@echo "  Overrides: API_PORT=$(API_PORT) WEB_PORT=$(WEB_PORT)"
	@echo ""

install: ## Create .venv and install all dependencies
	@echo "==> Python environment"
	@test -x "$(PY)" || python3 -m venv "$(VENV)" || python -m venv "$(VENV)"
	"$(PY)" -m pip install --upgrade pip --disable-pip-version-check
	"$(PY)" -m pip install -r "$(API_DIR)/requirements.txt"
	"$(PY)" -m pip install ruff pytest pytest-asyncio
	@echo "==> Web dependencies"
	cd "$(WEB_DIR)" && npm install
	@echo "==> Done. Next: make dev"

dev: ## Run API + web together
	bash "$(ROOT)/scripts/dev.sh"

api: ## Run only the API (uvicorn --reload)
	cd "$(API_DIR)" && DUET_PORT=$(API_PORT) "$(PY)" -m uvicorn duet.main:app \
		--host 127.0.0.1 --port $(API_PORT) --reload

web: ## Run only the Next.js dev server
	cd "$(WEB_DIR)" && npm run dev

test: ## Run the API test suite
	cd "$(API_DIR)" && "$(PY)" -m pytest

lint: ## Ruff on the API, tsc on the web app
	cd "$(API_DIR)" && "$(PY)" -m ruff check .
	cd "$(API_DIR)" && "$(PY)" -m ruff format --check .
	cd "$(WEB_DIR)" && npm run typecheck

sync: ## Check every connected app now
	@echo "==> POST $(API_BASE)/api/sync"
	curl -fsS -X POST "$(API_BASE)/api/sync" -H "Content-Type: application/json" -d '{}'
	@echo ""

up: ## Start the full stack in Docker
	$(COMPOSE) up -d --build
	@echo ""
	@echo "  Web    http://localhost:$(WEB_PORT)"
	@echo "  API    http://localhost:$(API_PORT)"
	@echo "  Health http://localhost:$(API_PORT)/api/health"
	@echo "  Qdrant http://localhost:6333/dashboard"
	@echo ""

down: ## Stop the Docker stack (volumes are kept)
	$(COMPOSE) down

logs: ## Follow Docker logs
	$(COMPOSE) logs -f --tail=100

clean: ## Remove caches, build output and local databases
	@echo "==> Cleaning"
	-rm -rf "$(WEB_DIR)/.next" "$(WEB_DIR)/out"
	-rm -rf "$(API_DIR)/.pytest_cache" "$(API_DIR)/.ruff_cache"
	-rm -rf "$(ROOT)/.pytest_cache" "$(ROOT)/.ruff_cache"
	-find "$(API_DIR)" -type d -name __pycache__ -prune -exec rm -rf {} +
	-rm -f "$(API_DIR)/.data/duet.db" "$(API_DIR)/.data/duet.db-wal" "$(API_DIR)/.data/duet.db-shm"
	@echo "    done (vault.enc and .venv were left in place)"
