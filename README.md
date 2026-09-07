# AIPaperbase Agent

> 中文版：[README.zh-CN.md](./README.zh-CN.md)

> Your agent over the AI-paper base you build and read.

> **Status — personal project, work in progress.** Built for one user, actively developed. The catalog console is solid; the RAG/agent layer works end-to-end but is still evolving. Use at your own pace.

![AIPaperbase Agent data-insights overview](screenshots/overview.png)

## What this is, for whom

**If you are an AI researcher who reads a lot of papers**, AIPaperbase Agent gives you one local, verifiable index over the field you track — instead of a pile of PDFs and scattered lists.

It turns **metadata of top AI conferences and journals** into something you can actually *use*:

- **See the field at a glance.** Per-venue and per-topic maps of ~122k records (20 conferences + 10 journals, 2023–2026): which directions are hot, in which venues, year by year — with an auditable paper list under every number.
- **Find exactly what you mean.** Every paper is tagged on two independent axes that never fight each other: a **topic tree** (*what* it studies — “Image super-resolution”, “RAG & knowledge grounding”…) and **method tags** (*how* — `diffusion`, `agentic`, `llm-based`…). Results carry an evidence chain (which words/benchmarks fired), not a black-box score.
- **Keep your own library.** Save collections, download PDFs (with content dedup), parse them to Markdown (MinerU) — all versioned locally.
- **Ask grounded questions.** Research Q&A answers *only from the papers you’ve put in*, with citations you can click back to the source PDF/section. Agent tools can also query arXiv / OpenAlex / the web on your behalf.
- **Turn reading into research material.** Keep reading progress, evidence notes, collection progress and editable comparison tables across restarts.

The *base* is the local catalog, collections and full-text library. The *agent* retrieves evidence and proposes organization drafts within an explicit paper scope. Collection changes and model suggestions are shown before the user confirms a write.

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

1. Search **Paper Library → Public Catalog** and add selected papers to a collection.
2. Use **My Papers** to inspect full-text readiness and record reading progress.
3. Select papers to start a scope-bound chat or an editable comparison table.
4. Review paper scope, reasons and source evidence before confirming organization changes.
5. Continue notes, comparisons and collection progress under **Research Material**.

## Quick start

Requirement: **Python 3.12+** on macOS or Linux (use WSL on Windows). Catalog browsing and local research organization need no API key, Docker or Node.js.

```bash
git clone https://github.com/szxiulin/aipaperbase-agent.git
cd aipaperbase-agent
python3 -m venv .venv
./run.sh
```

On first run, `run.sh` builds the public catalog from the committed CSV files. Expect roughly 2–5 minutes and about 1 GB of disk space. Later starts reuse it. Open <http://127.0.0.1:8765> to browse papers, create collections and keep research material.

### One-prompt install with a coding agent

Paste this prompt into Codex, Claude Code, Cursor, or another coding agent that can use a terminal:

```text
Install and start AIPaperbase Agent for me: https://github.com/szxiulin/aipaperbase-agent

Requirements:
1. Clone it in the current directory. If the directory already exists, inspect its state first and preserve my changes.
2. Require Python 3.12 or newer and create the repository-local .venv.
3. Start ./run.sh in a persistent terminal session. The first catalog build may take 2–5 minutes and about 1 GB of disk; wait until the service is ready.
4. Verify http://127.0.0.1:8765/api/summary, then report the page URL and verification result.
5. Do not create or read .env, install optional RAG dependencies, start Qdrant, or call any model, MinerU, or paid API yet.
6. Do not modify the project code. If anything fails, preserve the current files and data and report the exact error.
```

This prompt installs the key-free base version. After it works, use the next section to enable PDF parsing, full-text Q&A, and agent chat.

For PDF parsing, full-text RAG and agent chat, add the optional runtime:

```bash
.venv/bin/pip install -r requirements-rag.txt
cp .env.example .env
# Fill only the services you use; never commit .env
docker run -d -p 6333:6333 qdrant/qdrant
./run.sh
```

MinerU is needed only for PDF parsing; embeddings power indexing and retrieval; the generator powers chat; reranking is optional. See [`.env.example`](./.env.example).

### What needs what

| You want to… | You need |
|---|---|
| Browse/search/insights, collections and research material | Python 3.12+, then `./run.sh` |
| Download & parse PDFs for a collection | network; MinerU token for parsing |
| **RAG Q&A with citations** | Qdrant + `EMBEDDING_*`/`GENERATOR_*` (optional `RERANKER_*`) + downloaded and parsed papers |
| Research-agent chat (arXiv / OpenAlex / web) | generator configuration; full-text tools also need embeddings and Qdrant |

`GENERATOR_TOKEN_BUDGET` (process-wide) protects your API spend.

## Configuration reference

Full key list lives in [`.env.example`](./.env.example). Embedding, reranking, and generation use OpenAI-compatible interfaces, so switching those providers mainly means changing `base_url` + `api_key` + `model`; MinerU uses its own token.

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
- Real model, download, MinerU and production-Qdrant behavior depends on provider configuration, quota and network conditions. Release checks use isolated fixtures and do not claim every provider combination is verified.
