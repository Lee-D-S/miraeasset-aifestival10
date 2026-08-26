"""프로젝트 전역 경로 설정.

RDB(SQLite), VectorDB(Chroma) 등 로컬 DB 관련 경로를 한 곳에서 관리합니다.
DB를 생성하거나 참조하는 모든 코드(app/agent/db.py, dart_preprocessing/preprocesser.py 등)는
이 모듈의 상수를 사용해야 합니다.
"""
from pathlib import Path

# 프로젝트 루트 경로 (app/config.py 기준 parent)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# DB들이 저장될 통합 디렉토리: 프로젝트_루트/db_tmp
DB_DIR = PROJECT_ROOT / "db_tmp"
DB_DIR.mkdir(parents=True, exist_ok=True)

# 1. RDB (SQLite)
SQLITE_PATH = DB_DIR / "dart_financials.db"
SQLITE_URL = f"sqlite:///{SQLITE_PATH}"

# 2. Vector DB (Chroma)
CHROMA_PATH = str(DB_DIR / "chroma_db_temp")
CHROMA_COLLECTION_NAME = "periodic_table"
