Generated service stack contract (fixed by the harness; the same for every domain):

- App root layout:
  - `backend/main.py` defines the FastAPI `app`. Dependencies are listed in `backend/requirements.txt`.
  - `frontend/` is a Vite + React app. `npm run build` writes `frontend/dist`.
  - FastAPI serves the JSON API under `/api/` and serves `frontend/dist` at `/` (unknown non-API paths return `index.html`).
- Data: SQLite at `data/app.db` relative to the app root, using Python's `sqlite3`. Create tables on startup and insert sample (mock) data only when the tables are empty, so data survives restarts and page reloads.
- Users: no real login or accounts. Exactly one primary user role from the reviewed PRD. No role switch or X-Role header routing. Other actors may appear only as sample result states, never additional interactive views/actions.
- External integrations (payment, email, maps, etc.) are mocked. The UI must label them as mock behavior (e.g. "모의 결제").
- No external sharing links, no export features.
- UI text is Korean.
- `.gitignore` excludes `node_modules/`, `frontend/dist/`, `data/`, `.venv/`, `__pycache__/`.
- The harness builds and runs the app from the app root with:
  `cd frontend && npm install && npm run build && cd .. && python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt && .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000`
  Runtime: Node 22, Python 3.11, Linux container.
