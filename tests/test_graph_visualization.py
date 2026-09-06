from __future__ import annotations

from integration import StageNodes, build_graph


def test_supervisor_graph_can_render_mermaid() -> None:
    graph = build_graph(
        StageNodes(
            interpreter=lambda _state: {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}},
            retriever=lambda _state: {"retriever_result": {"status": "ok", "cited_documents": [{"id": "d"}]}},
            reasoner=lambda _state: {"reasoner_result": {"status": "success"}},
            validator=lambda _state: {"validator_result": {"status": "success"}},
        )
    )

    mermaid = graph.get_graph().draw_mermaid()

    assert "supervisor" in mermaid
    assert "calculation_planner" in mermaid
    assert "retry_search" in mermaid
