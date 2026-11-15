.PHONY: install api test lint audit migrate web-test web-build up down seed export-sql load
install:
	cd services/api && pip install -r requirements.txt -r requirements-dev.txt && cd ../../apps/web && npm ci
api:
	cd services/api && uvicorn app.main:app --reload --port 8000
migrate:
	cd services/api && alembic upgrade head
test:
	cd services/api && python -m pytest -q
lint:
	cd services/api && ruff check app tests
audit:
	cd services/api && pip-audit -r requirements.txt
export-sql:
	bash scripts/export_supabase_sql.sh
seed:
	python scripts/seed_dev.py
web-test:
	cd apps/web && npm test -- --run && npm run typecheck
web-build:
	cd apps/web && npm run build
load:
	cd tests/load && locust -f locustfile.py --headless -u 50 -r 10 -t 60s --host $${API_URL:-http://localhost:8000}
up:
	docker compose up --build
down:
	docker compose down
