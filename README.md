Momentic Onsite – Goal 1

Overview
- FastAPI backend reads Momentic test YAMLs in `tests/` and exposes a simple API.
- React + TypeScript (Vite) frontend lists tests with: name, created, updated, and step count.

Backend (FastAPI)
- Path: `backend/`
- Endpoints:
  - `GET /health` – sanity check, shows tests directory
  - `GET /api/tests` – array of tests with metadata

Run backend
1) Create venv (optional):
   python3 -m venv .venv && source .venv/bin/activate
2) Install deps:
   pip install -r backend/requirements.txt
3) Start API:
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


