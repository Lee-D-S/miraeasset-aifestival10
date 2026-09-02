# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`dis-164` — the execution engine for an AI Festival 2026 agent that answers Korean
corporate-disclosure (공시) questions. A FastAPI process runs a bounded, Supervisor-controlled
LangGraph pipeline of four stages. Most docs (`README.md`, `*/README.md`, `NCP_DEPLOYMENT.md`)
are in Korean and are the authoritative spec for each stage's contract.

## Common commands

Interpreter is `python3` (3.11/3.12); the Korean docs write `python` and PowerShell `$env:`.
Set up a venv and install all three requirement files before anything works:

```bash
python3 -m pip install -r requirements.txt -r requirements-langgraph.txt -r requirements-dev.txt
uvicorn app:app --reload
```

| Task | Command |
|---|---|
| Full test suite | `pytest` (config in `pytest.ini`; `testpaths` = `tests` + each `stageN/tests`) |
| Integration tests only, no pytest | `python -m unittest discover -s tests -p "test_*.py"` |
| Single test | `pytest tests/test_stage2.py::TestClass::test_case` |
| No-network end-to-end | `pytest tests/test_local_e2e.py` |
| Stage1 gold-query / invariant checks | `python -m stage1.tests.run_checks` |
| "Lint" (there is no ruff/flake8 — this is what the docs use) | `python -m compileall -q integration shared_state.py stage1 stage2 stage3 stage4` |
| Offline deployment preflight (no CLOVA calls) | `python scripts/check_deployment.py` |
| Regenerate the graph image after node/edge changes | `python scripts/render_graph.py --png integration/graph.png` |

`legacy/` is excluded from pytest (`norecursedirs`) and has its own `legacy/tests/`.

## Architecture

### Execution flow

```
START → stage1 → supervisor → stage2 → supervisor → stage3 → supervisor → stage4 → supervisor → END
```

`integration/graph.py` assembles the actual `StateGraph`. Two things that look like they
should be separate nodes are not:

- **One `supervisor` node handles all four phases.** It reads `supervisor_phase`
  (`after_stage1`..`after_stage4`) and picks an action from `ALLOWED_ACTIONS`. Per-phase
  action→node routing lives in `_PHASE_ROUTES` in `graph.py`; a missing action fails closed.
- **Phase advance + write-authorization are middleware**, not nodes. `_stage_node()` wraps each
  stage callable: it runs `validate_node_update(owner, ...)` on the stage's partial update and
  then stamps `supervisor_phase`/`phase`. The compiled graph only ever shows `stage1`..`stage4`.

The Supervisor only chooses an action + reason. Iteration limits and real work are the code's
job: default **12 supervisor steps**, **1 search retry**, **1 planner retry**, **1 answer
regeneration** (`build_supervisor_node`, `retry_search_tool`, `build_planner_tool`, and the
`recursion_limit=24` in `integration/service.py`). Unknown action, provider error, or
insufficient evidence → `fail_closed`.

Supervisor policy is pluggable: `DeterministicSupervisor` (default, no LLM) or
`StructuredSupervisorClient` (LLM returns only an action JSON). Inject via `StageNodes.supervisor`.

### State contract — `shared_state.py`

`AgentState` is the single cross-stage state. Each stage returns a **partial update only**;
`STAGE_WRITE_FIELDS` defines exactly which keys each stage may write and `validate_node_update`
rejects anything else. `question_id`, `question`, `original_question` are immutable for the whole
run (`IMMUTABLE_STATE_FIELDS`); a retry changes `search_query`, never `question`. Build the
initial state only through `make_initial_agent_state()`.

### `config.py` — the only place paths and the Stage2 mode are decided

Every module that opens a DB, vector store, corpus, or fixture reads its location from `config`.
Do **not** add `os.getenv("...PATH...")` or `Path(__file__).parents[...]` elsewhere. Relative
env values resolve against `PROJECT_ROOT`, never the CWD, so behavior is identical from the repo
root, `scripts/`, or a container `WORKDIR`. `Stage2Settings.from_env()` snapshots the
environment into a frozen settings object per `build_pipeline()` call.

### Stage2 has three interchangeable backends — `STAGE2_MODE`

| mode | store | needs |
|---|---|---|
| `fixture` (default) | CLOVA-precomputed embedding JSON (`legacy/test_data/disclosure_clova_local.json`); queries embedded live against CLOVA | no DB service |
| `local` | SQLite table `chunk_index` (metadata `WHERE` filter) + local Chroma persist dir (vectors) | built index; embedding provider (`STAGE2_EMBEDDING`) |
| `container` | Dockerized Postgres + Chroma **server** | `STAGE2_RDB_URL` **and** `STAGE2_CHROMA_HOST` both set, else hard-fails at startup |

`local` and `container` run identical `LocalHybridRetriever` SQL/vector code — only the
connections differ (`stage2/backends.py`). The SQL side is one table, `chunk_index`
(`stage2/local_store.py::CHUNK_TABLE`): `metadata_json` holds the full metadata, a subset is
promoted to typed WHERE-clause columns (incl. `section_name`), and table chunks keep their rows
as JSON in `raw_json_content`. `build_manifest_where_and_params` (ported from
`app/tools/rdb_methods.py`) supports `exclude_corp_names` and `section_name_contains`; candidates
are ordered latest-disclosure-first (`rcept_dt DESC, rcept_no DESC`).

Legacy `STAGE2_BACKEND=fixture|sqlite` is still honored (`sqlite` → `container` if a DSN/host is
set, else `local`). `local`/`container` share one vector collection name
`STAGE2_CHROMA_COLLECTION` (default `chunk_vectors`) that **must match** the value used when the
index was built.

**`STAGE2_EMBEDDING`** (`local`/`container` only): `clova` (default, CLOVA Embedding v2 HTTP API,
same space as `fixture`) or `e5` (local `intfloat/multilingual-e5-large` via
`stage2.embedding.E5Embeddings`, backed by fastembed/ONNX — no torch/transformers). Both
1024-dim. The index must be built with the same provider; `e5` + `fixture` is rejected at
startup. `e5` needs `stage2/ingestion/dart/requirements.txt`.

### Ingestion — two chunkers, one contract

`stage2/ingestion/` maps files on disk to `stage2.contracts.ChunkRow` lists. `plain`
(`build_chunk_rows`, dependency-free, flattens tables) and `dart` (`build_dart_chunk_rows`,
disclosure-aware: synthesized `[corp | report | section]` headers, row-wise table splitting with
a JSON copy per table, per-chunk 연결/별도 detection) — the latter vendors the reference
`dart_preprocessing` package under `stage2/ingestion/dart/`. Both feed
`LocalHybridRetriever.write_rows()` unchanged; there is no per-source adapter. `build_dart_chunk_rows`
parses documents sequentially by default; pass `max_workers` (or `iter_dart_chunk_rows` for a
per-document stream) to fan the parse out over processes. Build a `chunk_index` with
`scripts/build_chunk_index.py` (local, `--workers`, fastembed e5) or the two-cell Colab script
`scripts/colab_build_chunk_index.py` (runbook: `docs/colab_build.md` — sentence-transformers on
Colab's torch CUDA, local build with Drive `rsync` snapshots, `RESUME`). The Colab script carries a
hand-copied "vendored" copy of `stage2/ingestion/dart/*` so it never imports the repo — keep it in
sync when parsing/chunking changes; `tests/test_colab_cell.py` checks the two produce identical rows.

### Composition and startup

`integration/composition.py::build_pipeline()` is the canonical runtime factory (Stage1–4 +
CLOVA adapters, mode selected by `config`). `app.py` passes it as `pipeline_factory` so nothing
touches corpus/DB/providers at import — initialization is lazy at the first `/ready` or
`/answer`, and failures surface as HTTP 503, never an import crash.
`integration/testing.py::build_deterministic_pipeline()` is the no-network test factory
(deterministic Intent, `InMemoryRetriever`, fake semantic validator) — **test-only, never a
production fallback**.

### CLOVA / provider boundary

`integration/clova.py` + `integration/rate_limit.py`: embedding and chat adapters with a
process-local QPM/TPM limiter and bounded retries/timeouts (`CLOVA_*` env vars, see
`.env.example`). Fail-closed on missing capability: no CLOVA key → Stage2 returns
`embedding_unavailable` (never a fake embedding); no semantic validator → Stage4 cannot pass
validation (never a faked success; empty or uncited answers also fail).

### API

`GET /health` (process liveness only) · `GET /ready` (lazy pipeline build) ·
`GET /answer?question_id=&question=`. `/answer` always returns the competition's five string
fields — `question_id`, `question`, `retrieved_context`, `think_trace`, `answer` — via
`integration/api.py::to_submission_response()`. `think_trace` is a JSON blob of per-stage
`status`/`warnings`/`trace`, Stage1's `think_trace`, and the Supervisor's action/counters.

### Stage responsibilities (see each `stageN/README.md` for the full contract)

- **stage1** — normalize the question, extract 기업/기간/공시유형/지표/질문유형, emit `intent` +
  `route` and a `manifest_filter` for Stage2. Reads only `universe.csv` / `manifest.jsonl`
  metadata, never raw disclosures. Rule-based by default; calls injected HyperCLOVA only for
  unresolved slots when `use_llm=True`. Corpus location: `corpus_dir` arg → `CORPUS_DIR` →
  local auto-discovery.
- **stage2** — retrieval only. `manifest_filter` → query embedding → keyword + vector search →
  merge by ID → rerank → `cited_documents`. Writes `stage2_result` (+ compat `documents`).
  Canonical runtime passes up to 20 cited docs to Stage3; the deterministic test factory keeps 8.
- **stage3** — single node: extract Facts from cited docs, run the deterministic
  calculation/comparison/event-link that Stage1's `calculation`/`question_type` plan specifies
  (whitelisted ops only), then write a grounded answer via HyperCLOVA or a deterministic
  grounding fallback. Does not search, rerank, or validate. If `question_type=calculation` but
  no `calculation.operation`, it returns `missing_calculation_plan` — it never re-guesses the op
  from the question text.
- **stage4** — validate Stage3's answer: numeric → citation/provenance → semantic. No new
  retrieval or fact generation. `regenerate_answer` (max once) rewrites, then Stage4 re-validates.

### Graph image

`integration/graph.png` is generated only by `scripts/render_graph.py` — never hand-edit it, and
regenerate it whenever nodes or edges change so it stays in sync with `graph.py`.

## Conventions

- New path/mode config goes through `config.py`; do not read env or compute repo-relative paths
  in stage or integration modules.
- A stage node returns a partial `dict` update within its `STAGE_WRITE_FIELDS` set — it must not
  mutate the input state or write another stage's fields.
- Never substitute a fake embedding/LLM/validator on the production path; degrade to an explicit
  `*_unavailable` / `validation_failed` status instead.
- Do not log or echo API keys, request bodies, or secrets (adapters keep only `x-ratelimit-*`
  headers in `last_rate_limit`).
- `legacy/` is a frozen archive of the previous implementation kept for design history and the
  default fixture data — not an import target for current code.
