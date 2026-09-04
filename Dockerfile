# syntax=docker/dockerfile:1
#
# dis-164 공시 Agent -- 서빙 이미지 (STAGE2_MODE=local 기본).
#
# 인덱스(chunk_index.db ~20GB, chunk_index_chroma ~95GB)는 이미지에 넣지 않는다.
# 런타임에 볼륨으로 /app/data/local_db 에 마운트한다 (docker-compose.yml 참고).
# e5 임베딩 ONNX 가중치만 빌드 시 미리 받아 이미지에 굽는다.

ARG PYTHON_VERSION=3.12

########################################################################
# builder -- 의존성 + e5 모델 다운로드
########################################################################
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt requirements-langgraph.txt ./
RUN pip install -r requirements.txt -r requirements-langgraph.txt

# e5 (intfloat/multilingual-e5-large, non-instruct) ONNX 가중치를 고정 경로에 캐시.
# CI 에서는 --build-arg SKIP_MODEL_DOWNLOAD=1 로 건너뛴다.
ARG SKIP_MODEL_DOWNLOAD=0
ENV FASTEMBED_CACHE_DIR=/opt/models/fastembed
RUN if [ "$SKIP_MODEL_DOWNLOAD" != "1" ]; then \
        python -c "from fastembed import TextEmbedding; TextEmbedding('intfloat/multilingual-e5-large', cache_dir='/opt/models/fastembed')" ; \
    else mkdir -p /opt/models/fastembed ; fi

########################################################################
# runtime
########################################################################
FROM python:${PYTHON_VERSION}-slim AS runtime

# libgomp1: onnxruntime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models
COPY --chown=app:app . /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_DIR=/opt/models/fastembed \
    HF_HUB_OFFLINE=1 \
    STAGE2_MODE=local \
    STAGE2_EMBEDDING=e5 \
    STAGE2_INDEX_PATH=/app/data/local_db/chunk_index.db \
    STAGE2_CHROMA_PATH=/app/data/local_db/chunk_index_chroma \
    STAGE2_CHROMA_COLLECTION=chunk_vectors \
    STAGE2_SQL_TABLE=chunk_index \
    STAGE2_ALLOW_PARTIAL_INDEX=true

USER app
EXPOSE 8000

# /health 는 프로세스가 살아있는지만.  /ready 는 마운트된 인덱스+provider 까지 검증
# (HNSW 로드 때문에 start-period 를 넉넉히).
HEALTHCHECK --interval=30s --timeout=10s --start-period=240s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# HNSW mmap 압박 때문에 워커는 1개.  (평가 트래픽은 순차 호출)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
