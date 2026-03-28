.PHONY: dev test test-unit test-integration lint format redis

# Setup
dev:
	docker compose up -d
	pip install -e ".[dev]"

# Redis
redis:
	docker compose up -d

redis-stop:
	docker compose down

# Testing
test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

# Code quality
lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

typecheck:
	mypy src/
