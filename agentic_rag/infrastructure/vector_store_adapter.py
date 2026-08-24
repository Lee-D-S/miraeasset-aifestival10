from typing import Protocol, Any


class VectorStorePort(Protocol):
    def search(self, vector: list[float], *, limit: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]: ...


class VectorStoreAdapter:
    def __init__(self, store: VectorStorePort):
        self.store = store

    def search(self, vector: list[float], *, limit: int, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
        return self.store.search(vector, limit=limit, filters=filters)

