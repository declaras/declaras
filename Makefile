.PHONY: install dev test lint fmt typecheck browsers api worker probar stop logs calibrar ensayo explorar

install:
	uv sync --all-extras --dev

browsers:
	uv run playwright install chromium

api:
	uv run uvicorn declaras.api.app:app --reload --port 8000

test:
	uv run pytest -q

lint:
	uv run ruff check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

typecheck:
	uv run mypy

check: lint typecheck test

probar:
	./scripts/probar.sh $${PORT:-8000}

stop:
	-lsof -ti:$${PORT:-8000} | xargs -r kill -9

logs:
	tail -f /tmp/declaras-api.log

calibrar:
	uv run python scripts/calibrar.py

ensayo:
	uv run python scripts/ensayo_login.py --ver

explorar:
	uv run python scripts/explorar.py $(if $(CC),--cc $(CC),)
