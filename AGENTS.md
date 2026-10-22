# AGENTS.md

Guide for AI coding agents (and humans) working in this repository.

## What this is

A local research assistant over ~138k deduplicated research papers from 30 top AI venues (2023–2026): a chat agent with
tools, an area map (21 areas → 383 directions), paper search and collections, and an MCP server.
Backend: Python standard library (`http.server` + `sqlite3`); chat additionally needs `httpx`.
Frontend: vanilla JS single page, no build step.

## Run it

```bash
./run.sh            # creates .venv, installs requirements.txt, builds the catalog on first start
                    # (2–5 min, ~1 GB), then serves http://127.0.0.1:8765
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python -m backend.mcp_server          # MCP over stdio
```

Chat needs an OpenAI-compatible model: set it in the web UI (⚙ Settings) or in `.env`
(see `.env.example`). Never read, print or commit `.env`.

## Layout

- `backend/catalog/` — CSV import, paper merging, FTS search (`paper_search.py` is the agent's search).
- `backend/capabilities/` — the single business entry for statistics/search; HTTP `/api/cap/*`, MCP and the agent all call it.
- `backend/agent/` — agent runner (tool loop, streaming, live progress), tools in `tools/`, model calls in `llm.py`.
- `backend/chats/` — conversation storage and the ask flow; `backend/common/settings.py` — model/web-search settings.
- `frontend/app.js` (routing, chat, papers), `frontend/atlas.js` (area map), `frontend/palette.css` (design layer).

## Gotchas

- Always use `.venv/bin/python`.
- `data/catalog/` CSVs and `data/labels/` are the committed sources; `data/database/` (the SQLite catalog) is generated and rebuildable (move old files to macOS Trash rather than permanently deleting them) — rebuild with `.venv/bin/python -m backend.catalog.import_csv`.
- User data (`data/user/`: chats, collections, notes), downloads and parsed text are git-ignored and must stay that way.
- The paper text is English: agent search queries must be English keywords.
- Statistics count each paper once (canonical venue/year) and exclude non-paper records; 2026 is a partial year.
- `requirements-catalog.txt` is only for maintainers re-collecting venue metadata; `requirements-rag.txt` is only for full-text Q&A.
- Report failures verbatim; a fresh install has no conversations, collections or downloads by design.
