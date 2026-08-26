from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_naver import ClovaXEmbeddings
from sqlalchemy import create_engine

from app.config import SQLITE_URL, CHROMA_PATH, CHROMA_COLLECTION_NAME
from dart_preprocessing.embedding_model import embedding_model

load_dotenv()

# 1. RDB Engine (SQLite)
rdb_engine = create_engine(SQLITE_URL)

# 2. Vector DB (Chroma)
vectorstore = Chroma(
    persist_directory=CHROMA_PATH,
    embedding_function=embedding_model,
    collection_name=CHROMA_COLLECTION_NAME,
)
