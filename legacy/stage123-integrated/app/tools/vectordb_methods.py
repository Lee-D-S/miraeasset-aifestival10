from typing import List
from langchain_chroma import Chroma
from langchain_core.documents import Document


def search_vector_by_ids(
    vectorstore: Chroma, query: str, chunk_ids: List[str], top_k: int = 3
) -> List[Document]:
    if not chunk_ids:
        return []
    safe_chunk_ids = chunk_ids[:500]

    search_query = query if query and str(query).strip() else "공시 보고서"
    # ChromaDB 메타데이터 필터링 ($in 연산자 활용)
    results = vectorstore.similarity_search(
        query=search_query, k=top_k, filter={"chunk_id": {"$in": safe_chunk_ids}}
    )
    return results