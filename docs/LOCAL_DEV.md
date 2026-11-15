# Local development

Prereqs: Python 3.13, Node 22+, PostgreSQL 16 (or Docker), optionally Redis.

## Linux / macOS
```bash
cp .env.example .env
docker compose up -d db redis
cd services/api && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python ../../scripts/seed_dev.py --employees 20 --json ../../.seed.json
uvicorn app.main:app --reload --port 8000
python -m app.worker                    # second terminal
cd ../../apps/web && npm ci && npm run dev
```
## Windows PowerShell
```powershell
Copy-Item .env.example .env
docker compose up -d db redis
cd services\api; python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt; alembic upgrade head
python ..\..\scripts\seed_dev.py --employees 20
uvicorn app.main:app --reload --port 8000
cd ..\..\apps\web; npm ci; npm run dev
```
## Windows CMD
```bat
copy .env.example .env
docker compose up -d db redis
cd services\api && python -m venv .venv && .venv\Scripts\activate.bat
pip install -r requirements.txt && alembic upgrade head
uvicorn app.main:app --reload --port 8000
```
The shell scripts (`scripts/*.sh`) need Git Bash or WSL on Windows. These Windows commands were not run on Windows.

## Tests
```bash
cd services/api && ruff check app tests && pytest      # needs Postgres (see tests/conftest.py)
cd apps/web && npm run typecheck && npm test && npm run build
bash scripts/run_e2e.sh      # Playwright against real API + Vite
bash scripts/run_load.sh     # Locust (USERS, SPAWN, DURATION, EMPLOYEES)
```
Dev-token sign-in in the web app exists only in `vite dev`; `seed_dev.py` mints dev JWTs and refuses to run outside development.
