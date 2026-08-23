import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("APP_ENV", "local")
    clova_enabled: bool = os.getenv("CLOVA_ENABLED", "false").lower() == "true"
    clova_api_key: str = os.getenv("CLOVA_API_KEY", "")
    clova_api_gateway_key: str = os.getenv("CLOVA_API_GATEWAY_KEY", "")
    clova_request_id: str = os.getenv("CLOVA_REQUEST_ID", "")
    clova_api_host: str = os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com")
    postgres_dsn: str = os.getenv("POSTGRES_DSN", "")
    local_vector_index: str = os.getenv(
        "LOCAL_VECTOR_INDEX", "test_data/disclosure_clova_local.json"
    )
    source_root: str = os.getenv("RAG_SOURCE_ROOT", "")
    retrieval_top_k: int = int(os.getenv("RAG_RETRIEVAL_TOP_K", "20"))
    rerank_top_k: int = int(os.getenv("RAG_RERANK_TOP_K", "10"))
    max_tool_rounds: int = int(os.getenv("RAG_MAX_TOOL_ROUNDS", "2"))


settings = Settings()
