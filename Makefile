.PHONY: install lint format typecheck test ci deploy clean lock

install:
	uv sync --all-extras

lock:
	uv lock

lint:
	uv run ruff check functions/ tests/

format:
	uv run ruff format functions/ tests/
	uv run ruff check --fix functions/ tests/
	terraform fmt -recursive infra/

typecheck:
	uv run mypy functions/

test:
	uv run pytest tests/ -v

ci: lint typecheck test

deploy:
	./scripts/deploy.sh

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
