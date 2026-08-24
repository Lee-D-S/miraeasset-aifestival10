from langgraph_rag.adapters import build_dependencies
from langgraph_rag.graph import build_graph
from langgraph_rag.graph import render_mermaid
from common.config import settings


def main() -> None:
    dependencies = build_dependencies(settings)
    if dependencies is None:
        raise SystemExit("CLOVA API key and a local vector index or PostgreSQL DSN are required.")
    graph = build_graph(dependencies)
    print(render_mermaid(graph))


if __name__ == "__main__":
    main()
