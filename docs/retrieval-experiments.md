# Retrieval backend replacement experiments

This document explains how to compare FTS5, exact scoring, and FAISS sidecars
while keeping the production HNSW path as the baseline.

## Scope

The first experiment has ten profiles:

~~~text
hnsw-python       hnsw-fts5
exact-python      exact-fts5
ivfflat-python    ivfflat-fts5
ivf-sq8-python    ivf-sq8-fts5
ivf-pq-python     ivf-pq-fts5
~~~

SQLite chunk_index remains the metadata source. The supplied Chroma files stay
read-only and candidate artifacts are written under a separate directory.

The query embedding contract is the production
intfloat/multilingual-e5-large model with the same raw query adapter. Candidate
profiles do not use the separate instruct A/B path.

## Install

Install experiment-only dependencies separately from the production image.
The base requirements.txt supplies fastembed for multilingual-e5-large; this
file contains only experiment-specific FAISS and psutil dependencies.

~~~powershell
python -m pip install -r requirements-retrieval-experiments.txt
~~~

Use --skip-faiss to build only the vector matrix and FTS5 sidecar when FAISS is
not installed.

## Build sidecars

The index root may contain chunk_index.db and chunk_index_chroma directly, or
may be their parent directory.

~~~powershell
python scripts/build_retrieval_experiment.py --index-root data/team-feature2-local-db/local_db --artifact-root data/retrieval_experiments/supplied --skip-faiss
~~~

Remove --skip-faiss to build the FAISS indexes.

Default FAISS settings:

- nlist=4096
- nprobe is selected by the benchmark command
- IVF-PQ M=64 and nbits=8
- training and vector addition use batches
- source SQLite and Chroma are read-only

manifest.json records source fingerprints, row/vector counts, dimension,
coverage, embedding contract, build revision, and FAISS parameters.

## Benchmark

Run one profile against the 25-query gold set:

~~~powershell
python scripts/benchmark_retrieval_experiment.py --profile exact-fts5 --index-root data/team-feature2-local-db/local_db --artifact-root data/retrieval_experiments/supplied --nprobe 32 --output data/retrieval_experiments/reports/exact-fts5.json
~~~

The report contains Recall/MRR, query latency, artifact size, load time, RSS,
and backend fallback statistics.

Run FAISS profiles separately with nprobe values 8, 32, and 128. Do not run
candidate profiles concurrently.

To include deterministic Stage1 to Stage4 and the five-string response check,
add a corpus directory:

~~~powershell
python scripts/benchmark_retrieval_experiment.py --profile exact-fts5 --index-root data/team-feature2-local-db/local_db --artifact-root data/retrieval_experiments/supplied --pipeline-corpus-dir tests/fixtures/real_index_corpus --output data/retrieval_experiments/reports/exact-fts5-pipeline.json
~~~

The pipeline option accepts the same metadata-only gold-set schema. A
competition-specific 44-query file can be passed with --gold-set when that
file is available.

## Experiment API server

The candidate service runs as a separate process from production app.py and
keeps the existing API contract.

Required environment variables:

~~~powershell
$env:EXPERIMENT_PROFILE = "exact-fts5"
$env:EXPERIMENT_INDEX_ROOT = "C:\path\to\local_db"
$env:EXPERIMENT_ARTIFACT_ROOT = "C:\path\to\retrieval_experiments\supplied"
$env:EXPERIMENT_CORPUS_DIR = "C:\path\to\corpus"
~~~

Start it with:

~~~powershell
uvicorn scripts.retrieval_experiment_server:app --host 127.0.0.1 --port 8100
~~~

The answer endpoint keeps these five string fields:

- question_id
- question
- retrieved_context
- think_trace
- answer

Stage3 and Stage4 are deterministic by default. Set EXPERIMENT_LIVE_LLM=true
only for the final live validation.

## Limitations

- The local partial index has about 1.15 million SQLite rows and 0.8 million vectors.
- Vector sidecars contain only vector IDs present in the supplied Chroma index.
- NCP-scale memory, disk, and p95 conclusions require a full-index rerun.
- PostgreSQL, Qdrant, Milvus, OpenSearch, DuckDB, and DiskANN are out of scope for this first implementation.
- Production HNSW remains the default until the candidate passes the quality gate.
