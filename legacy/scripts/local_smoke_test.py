from rag.retrieval.vector_search import VectorRetriever
from rag.services.fake_embedding import FakeEmbeddingClient
from rag.storage.local import LocalVectorRow, LocalVectorStore


def main() -> None:
    embedder = FakeEmbeddingClient()
    store = LocalVectorStore()
    store.upsert([
        LocalVectorRow(
            id="sample-1",
            text="2025년 매출은 100억원입니다.",
            source_path="sample/financial.xml",
            embedding=embedder.embed_text("2025년 매출은 100억원입니다.").vector,
            metadata={"corp_name": "샘플기업", "report_period": "2025"},
        ),
        LocalVectorRow(
            id="sample-2",
            text="2025년 영업이익은 20억원입니다.",
            source_path="sample/financial.xml",
            embedding=embedder.embed_text("2025년 영업이익은 20억원입니다.").vector,
            metadata={"corp_name": "샘플기업", "report_period": "2025"},
        ),
    ])
    documents = VectorRetriever(store, embedder).search("2025년 매출", limit=2)
    assert len(documents) == 2
    assert documents[0].metadata["corp_name"] == "샘플기업"
    print("local vector smoke test ok")


if __name__ == "__main__":
    main()
