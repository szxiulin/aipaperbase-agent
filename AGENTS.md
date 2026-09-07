# AGENTS.md

Operational guide for AI coding agents (and humans) working in this repository.

## What this is

A **local-first tool for AI researchers**: an index + library over top AI conference/journal metadata, plus optional RAG/agent Q&A that answers only from papers you download and parse. The web console is a no-build vanilla-JS SPA with a Chinese-default / English-switchable UI (toggle is bottom-left). The metadata console runs on the Python standard library alone; only the RAG/agent features need extra dependencies, Qdrant, and API keys.

Status: **personal project, work in progress.** The catalog console is solid; the RAG/agent layer is end-to-end but still evolving.

## Run it

Requirements: **Python 3.12+**; **Docker** and OpenAI-compatible API keys are needed only for RAG/agent features.

```bash
# 1) key-free base version; run.sh builds the catalog on first start
#    (~2–5 minutes and ~1 GB), then opens http://127.0.0.1:8765
python3 -m venv .venv
./run.sh

# 2) tests
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Optional, for RAG/agent features only:

```bash
.venv/bin/pip install -r requirements-rag.txt
cp .env.example .env
docker run -d -p 6333:6333 qdrant/qdrant   # vector store
# then fill EMBEDDING_* / RERANKER_* / GENERATOR_* (OpenAI-compatible) and,
# if parsing downloaded PDFs, MINERU_TOKEN in .env
```

## Gotchas

- **Always invoke project commands via `.venv/bin/python`.** Some system Python installations refuse package installs, and the project environment should stay isolated.
- `data/catalog/` CSVs are the **committed, rebuildable source** of the catalog. Generated DBs, PDF downloads, parsed text and user data live under ignored paths (`data/database`, `data/papers`, …) and are never committed. Deleting any generated DB is safe — rebuild with `import_csv`.
- `requirements-catalog.txt` (pandas/pyarrow/…) is for **maintainers re-collecting venue metadata**; it is not needed to install or run.
- Qdrant point IDs are UUIDs derived from chunk ids; the collection default is `papers`.
- Embedding, reranking, and generation endpoints are OpenAI-compatible: switching those providers means changing `base_url` + `model` + `api_key` in `.env`. MinerU uses its own token.
- The frontend `frontend/i18n.js` keeps the UI Chinese-first; English chrome is a mirrored vocabulary (best-effort). `topics.json` taxonomy names are currently Chinese.
- Report back failures verbatim; it is normal for a fresh setup to have no downloaded papers or collections — those start empty by design.
