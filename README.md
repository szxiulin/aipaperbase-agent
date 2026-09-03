# AIPaperbase Agent

> 中文版：[README.zh-CN.md](./README.zh-CN.md)

> **AIPaperbase Agent** — your agent over the AI-paper base you build and read.

> **Status — personal project, work in progress.** Built for one user, actively developed. The catalog console is solid; the RAG/agent layer works end-to-end but is still evolving. Use at your own pace.

![Console overview](screenshots/overview.png)

## What this is, for whom

**If you are an AI researcher who reads a lot of papers**, AIPaperbase Agent gives you one local, verifiable index over the field you track — instead of a pile of PDFs and scattered lists.

It turns **metadata of top AI conferences and journals** into something you can actually *use*:

- **See the field at a glance.** Per-venue and per-topic maps of ~122k records (20 conferences + 10 journals, 2023–2026): which directions are hot, in which venues, year by year — with an auditable paper list under every number.
- **Find exactly what you mean.** Every paper is tagged on two independent axes that never fight each other: a **topic tree** (*what* it studies — “Image super-resolution”, “RAG & knowledge grounding”…) and **method tags** (*how* — `diffusion`, `agentic`, `llm-based`…). Results carry an evidence chain (which words/benchmarks fired), not a black-box score.
- **Keep your own library.** Save collections, download PDFs (with content dedup), parse them to Markdown (MinerU) — all local and reproducible.
- **Ask grounded questions.** Research Q&A answers *only from the papers you’ve put in*, with citations you can click back to the source PDF/section. Optional agent tools can also query arXiv / OpenAlex / the web on your behalf.

**Why the name — *base*, and *agent*?** The *base* is the local index and paper library you build from the conference metadata (the catalog, collections, downloads, parsed full-text). The *agent* is the optional research copilot that reads inside that base and answers with citations. Neither is required to start: the console runs on nothing but the metadata.

Everything is **local-first**: data and code run on your machine. The only things that ever leave it are the API calls you opt into (PDF parsing, embedding, an LLM, optional web lookups).

## How it works (in one picture)

```text
venue CSVs (committed, data/catalog)
   → catalog.sqlite: validation + entity dedup (arXiv/preprint ↔ published) + integrity checks
        → classification: topic tree (primary + ≤2 extras)  +  method tags
              → console: topic maps, venue wall, search, drill-down to papers
then, for the papers YOU choose:
   collection → PDF download (content dedup) → MinerU Markdown
        → chunks → embeddings → Qdrant (vector store)
              → retrieve → rerank → LLM answer with citations
```

Two vocabularies, deliberately separate: the **topic tree** answers “research *what*”, **method tags** answer “done *how*” — so a super-resolution paper that merely *uses* an agent is counted as super-resolution, not as an agent paper.

## 60-second tour

1. Open the console → the **overview** tab shows the whole catalog at a glance (topics × venues × years).
2. Click a topic or venue → its **detail** page: subtopics, method-tag mix, yearly trend, top venues, constituent papers.
3. Open the **paper catalog** → search / filter → the “view abstract / source” and evidence cells show *why* a paper is where it is.
4. Create a **collection** → run **download → parse → ingest** on it.
5. Ask the **research assistant** a question; the answer cites the local papers it used.

## Quick start

Requirements: **Python 3.12+**, optional `.venv`, and — for the RAG features only — **Docker** (Qdrant) plus a couple of OpenAI-compatible API keys.

```bash
# 1) Install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-rag.txt

# 2) Configure (needed only for RAG/agent features)
cp .env.example .env
#   GENERATOR_*  your LLM (OpenAI-compatible chat, e.g. DeepSeek / OpenRouter)
#   EMBEDDING_*  embeddings API for RAG (default qwen3-embedding via OpenRouter)
#   RERANKER_*   reranker API for RAG (default Qwen3-Reranker via SiliconFlow)
#   MINERU_TOKEN only if you want to parse downloaded PDFs (MinerU)
#   OPENALEX_MAILTO optional; raises your free OpenAlex research-API quota

# 3) Start Qdrant (RAG only — the vector store)
docker run -d -p 6333:6333 qdrant/qdrant

# 4) Build the read-only catalog (metadata console needs no Qdrant and no keys)
.venv/bin/python -m backend.catalog.import_csv

# 5) Run the console → http://127.0.0.1:8765
.venv/bin/python -m backend.api.server

# 6) Tests
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

### What needs what

| You want to… | You need |
|---|---|
| Browse / search / insights over the catalog | Steps 1, 4, 5 (Python stdlib only) |
| Download & parse PDFs for a collection | network; MinerU token for parsing |
| **RAG Q&A with citations** | Qdrant running + `EMBEDDING_*`/`GENERATOR_*`/`RERANKER_*` + downloaded & parsed papers |
| Research-agent chat (arXiv / OpenAlex / web) | Qdrant + generator keys (OpenAlex anonymous is free) |

`GENERATOR_TOKEN_BUDGET` (process-wide) protects your API spend.

## Configuration reference

Full key list lives in [`.env.example`](./.env.example). Platform is OpenAI-compatible, so switching providers is just `base_url` + `api_key` + `model`.

## Repository layout

| Path | What it is |
|---|---|
| [`backend/`](./backend/README.md) | Modular Python monolith: catalog import/entity merge, analytics, collections, library, RAG glue, console HTTP API |
| [`raglib/`](./raglib/) | Reusable, business-free RAG core (chunk → embed → retrieve → rerank → generate) |
| [`frontend/`](./frontend/README.md) | Local console SPA (vanilla JS, no build step) — **language switch 中文/EN, default 中文** |
| [`data/catalog/`](./data/catalog/README.md) | Yearly venue metadata CSVs — the committed, rebuildable source of `catalog.sqlite` |
| [`config/`](./config/README.md) | `topics.json` (topic tree), `methods.json` (method tags), evaluation scaffolding |
| [`scripts/`](./scripts/README.md) | Maintainer tools: collectors, abstract enrichment, validators |
| [`tests/`](./tests/README.md) | `unittest` suite |

## Honest notes

- **Scope of the catalog** is 20 AI conferences + 10 journals, 2023–2026 (~122k records, ~121k entities after dedup); 2026 is a rolling list. Metadata comes from public program pages/APIs — review each source’s terms before redistributing derived data.
- **Classification is a local, evidence-based baseline** — not an official taxonomy, not human-reviewed per paper. Every label is reproducible and drillable to its evidence.
- **Topic names in `topics.json` are currently Chinese**; EN mode shows English via a mirrored vocabulary (best-effort). Full native-English taxonomy is a known TODO.
- **Local-first**: generated DBs, downloads, parsed text and user data are not committed. `data/catalog/` CSVs are the exception and are the rebuild source.
- Design & development docs live in `docs/` and are excluded from public mirrors.
