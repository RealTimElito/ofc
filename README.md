# OFC — Offline Report Composer

Air-gapped report writing with **local LLMs**, **file uploads**, and **database** context/examples.

## Why

In locked-down networks you often have:
- a private OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, internal gateway)
- result data in a DB or files
- prior reports that should set tone/structure

OFC wires those together into a small pipeline + UI. No cloud fallbacks, no CDN at runtime.

## Architecture

```
UI (React)  →  API (FastAPI)  →  LLM endpoint (your LAN)
                    ↓
              SQLite app store
                    ↓
         uploads / external DBs (read-only SELECT)
```

Pipeline stages: **style notes → outline → draft → critique → revise** (or run one stage at a time).
When examples are attached, style notes capture formulation signals (phrases, voice, section
naming, metrics/hedging) so later stages prefer that language without copying example-only facts.
Style notes are **cached on disk** per example-set fingerprint (`data/style_cache/`) so identical
packs skip re-extraction. Changing library examples, example uploads, or example SQL queries
clears that cache and stored notes on reports; changing a report’s example selection or brief
(when “use all examples” ranks the pack) clears that report’s notes when the fingerprint shifts.

### Training vs retrieval (small corpora)

Continuously fine-tuning / “training” on every stored report is a **poor default under ~1000
documents**: costly, brittle offline, and needs an eval harness. Prefer attaching the best
example reports, the style-notes stage, and the cheap style-notes cache. When “use all
examples” is on, OFC ranks library examples by cheap lexical similarity to the title/brief
(and keeps a top budget) — not model-weight training.

## Quick start

```bash
# 0) Config
cp .env.example .env   # already present if you cloned this tree

# 1) Sample archive DB (results + prior reports)
python3 scripts/seed_sample_db.py

# 2) API
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
OFC_DATA_DIR=../data uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 3) UI (another terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

Point `LLM_*` in `.env` at your local model server first (default assumes Ollama on `11434`).

## Docker

```bash
docker compose up --build
```

- UI: http://localhost:8080  
- API: http://localhost:8000  

For true air-gap: build on a connected host, `docker save` the images, transfer, `docker load`.

## Typical workflow

1. **LLM & settings** — add a profile (base URL, model, API key) or rely on `.env`
2. **Sources**
   - Upload `.md` / `.txt` / `.csv` / `.json` / `.docx` as **context**, **example**, or **both**
   - Add a DB connection (SQLite path or Postgres/MySQL DSN) and saved SELECT queries
     - `purpose=results` → metrics/tables for the new report
     - `purpose=examples` → prior report bodies (`example_body_column`)
   - Review the document library: short smoke/placeholder entries are flagged as **stubs** —
     prune them so they do not dilute example packs
3. **Reports** — create a project, write the brief, tick files/queries, run **Full generate**
   (runs in the background with live stage status; **Cancel** aborts the in-flight LLM HTTP
   call). The editor warns early if the configured LLM is unreachable or lists no models.
   If a draft still echoes example-only phrasing, use **Scrub example phrasing**
4. Optionally **Import theme** from a `.docx` example or library doc (fonts, header/footer,
   logos) for Word export
5. Edit the draft; export Markdown, HTML, or `.docx`

### Sample DB hints

After `scripts/seed_sample_db.py`:

| Field | Value |
|-------|--------|
| dialect | `sqlite` |
| dsn | absolute path to `data/samples/archive.sqlite3` |
| results SQL | `SELECT metric, value, unit, period, notes FROM quarterly_results` |
| examples SQL | `SELECT title, year, body_md FROM prior_reports` |
| example body column | `body_md` |

## Air-gap notes

- Frontend production build is self-contained (no runtime CDN)
- App state + uploads live under `OFC_DATA_DIR` (default `./data`)
- External DB credentials are Fernet-encrypted using `OFC_SECRET_KEY` / `OFC_FERNET_KEY`
- SQL is restricted to read-only `SELECT` / `WITH`
- LLM traffic stays on the URLs you configure — nothing phones home

## Config reference

| Variable | Purpose |
|----------|---------|
| `LLM_BASE_URL` | OpenAI-compatible root, e.g. `http://127.0.0.1:11434/v1` |
| `LLM_API_KEY` | Bearer token (placeholder OK for many local servers) |
| `LLM_MODEL` | Model id |
| `OFC_DATA_DIR` | Uploads, SQLite app DB, exported reports |
| `OFC_SECRET_KEY` | Derives encryption key for stored secrets |
| `OFC_CORS_ORIGINS` | Allowed browser origins |

## Layout

```
ofc/
  backend/app/          FastAPI + pipeline
  frontend/             Vite React UI
  data/                 Local store (gitignored contents)
  data/samples/         Demo SQLite + hint file
  docker/               API + nginx UI images
  scripts/              Seed helpers
```
