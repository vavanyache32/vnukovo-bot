.PHONY: help install dev test lint fmt typecheck cov \
        run monitor resolve replay backtest discover proxy-check \
        docker-build docker-up docker-down logs \
        provision install-prod deploy rollback backup-now \
        migrate

PY ?= python
APP ?= vnukovo-bot
TAG ?= latest

help:
	@echo "Targets:"
	@echo "  install       - install runtime deps"
	@echo "  dev           - install dev + runtime deps"
	@echo "  test          - run pytest"
	@echo "  lint          - ruff lint"
	@echo "  fmt           - ruff format"
	@echo "  typecheck     - mypy strict"
	@echo "  cov           - tests with coverage"
	@echo "  monitor       - run monitor loop (default city)"
	@echo "  resolve DATE= SLUG= - run resolver"
	@echo "  replay  DATE= SLUG= - replay historical day"
	@echo "  backtest FROM= TO=  - run backtest"
	@echo "  discover      - run market discovery once"
	@echo "  proxy-check   - test all configured proxies from this host"
	@echo "  docker-build  - build container"
	@echo "  docker-up     - up -d compose"
	@echo "  docker-down   - down compose"
	@echo "  logs          - follow compose logs"
	@echo "  provision     - bootstrap VPS (docker, ufw, fail2ban, botuser)"
	@echo "  install-prod  - clone + first up on VPS"
	@echo "  deploy        - pull + up on VPS"
	@echo "  rollback      - rollback to previous tag"
	@echo "  backup-now    - manual backup"
	@echo "  migrate       - alembic upgrade head"

install:
	$(PY) -m pip install -e .

dev:
	$(PY) -m pip install -e ".[dev]"
	pre-commit install

test:
	$(PY) -m pytest

lint:
	ruff check src tests

fmt:
	ruff format src tests

typecheck:
	mypy src

cov:
	$(PY) -m pytest --cov=src --cov-report=term-missing --cov-report=xml

migrate:
	alembic upgrade head

monitor:
	$(PY) -m src.cli monitor

resolve:
	$(PY) -m src.cli resolve --date $(DATE) --slug $(SLUG)

replay:
	$(PY) -m src.cli replay --date $(DATE) --slug $(SLUG)

backtest:
	$(PY) -m src.cli backtest --from $(FROM) --to $(TO)

discover:
	$(PY) -m src.cli discover

proxy-check:
	$(PY) -m src.cli proxy-check

docker-build:
	docker build -t ghcr.io/yourorg/$(APP):$(TAG) -f deploy/Dockerfile .

docker-up:
	docker compose -f deploy/docker-compose.yml up -d

docker-down:
	docker compose -f deploy/docker-compose.yml down

logs:
	docker compose -f deploy/docker-compose.yml logs -f --tail=200

provision:
	bash deploy/scripts/provision.sh

install-prod:
	bash deploy/scripts/install.sh

deploy:
	bash deploy/scripts/deploy.sh $(TAG)

rollback:
	bash deploy/scripts/rollback.sh

backup-now:
	bash deploy/scripts/backup.sh
