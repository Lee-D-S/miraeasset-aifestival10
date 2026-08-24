from langgraph_app.adapters import build_dependencies
from langgraph_app.graph import render_mermaid
from rag.config import settings


def main() -> None:
    dependencies = build_dependencies(settings)
    if dependencies is None:
        raise SystemExit("CLOVA API key and a local vector index or PostgreSQL DSN are required.")
    from langgraph_app.graph import build_graph

    graph = build_graph(dependencies)
    print(render_mermaid(graph))


if __name__ == "__main__":
    main()
