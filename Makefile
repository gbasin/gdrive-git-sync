MAKEFLAGS += -r       # disable built-in implicit rules (.SUFFIXES: alone doesn't work on Make 3.81)
.PHONY: install install-hooks lint lint-shell format typecheck test test-shell ci deploy setup clean lock

install:
	uv sync --all-extras
	uv run pre-commit install

install-hooks:
	uv run pre-commit install

lock:
	uv lock

lint: lint-shell
	uv run ruff check functions/ tests/
	uv run ruff format --check functions/ tests/

lint-shell:
	shellcheck scripts/*.sh

format:
	uv run ruff format functions/ tests/
	uv run ruff check --fix functions/ tests/
	terraform fmt -recursive infra/

typecheck:
	uv run mypy functions/

test: test-shell
	uv run pytest tests/ -v

test-shell:
	bats tests/shell/

ci: lint typecheck test

deploy:
	./scripts/deploy.sh

setup:
	./scripts/setup.sh

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
