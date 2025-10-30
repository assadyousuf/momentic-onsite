Momentic Onsite

Overview
- FastAPI backend exposes a simple API and streams AI summaries.
- React + TypeScript (Vite) frontend lists tests with: name, created, updated, and step count.
 - MongoDB stores tests and modules for scalable listing and pagination.

Backend (FastAPI)
- Path: `backend/`
- Endpoints:
  - `GET /health` – sanity check, shows tests directory and Mongo/Redis availability
  - `GET /api/tests?page=1&pageSize=20` – paginated tests with metadata (from Mongo)
  - `GET /api/tests/{id}/summary/stream` – SSE stream of AI summary tokens (cached via Redis)

Run backend
1) Create venv (optional):
   python3 -m venv .venv && source .venv/bin/activate
2) Install deps:
   pip install -r backend/requirements.txt
3) Start Mongo and Redis in Docker:
   docker compose up -d mongo redis
4) Ingest YAML tests/modules into Mongo (one-time, can re-run to refresh):
   python backend/scripts/ingest_tests_to_mongo.py
5) Start API:
   uvicorn app.main:app --reload --port 8000 --app-dir backend

Frontend (React + TS)
- Path: `client/`
- Dev server proxies `/api` and `/health` to `http://localhost:8000`.

Run frontend
1) Install deps:
   cd client && npm install
2) Start dev server:
   npm run dev
3) Open the URL it prints (typically `http://localhost:5173`).

Notes
- Configure Anthropic via `ANTHROPIC_API_KEY` in repo‑root `.env` to enable summaries.
