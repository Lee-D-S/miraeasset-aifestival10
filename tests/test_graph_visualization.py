from __future__ import annotations

from integration import StageNodes, build_graph


def test_supervisor_graph_can_render_mermaid() -> None:
    graph = build_graph(
        StageNodes(
            stage1=lambda _state: {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}},
            stage2=lambda _state: {"stage2_result": {"status": "ok", "cited_documents": [{"id": "d"}]}},
            stage3=lambda _state: {"stage3_result": {"status": "success"}},
            stage4=lambda _state: {"stage4_result": {"status": "success"}},
        )
    )

    mermaid = graph.get_graph().draw_mermaid()

    assert "supervisor_after_stage1" in mermaid
    assert "supervisor_after_stage2" in mermaid
    assert "supervisor_after_stage3" in mermaid
    assert "calculation_planner" in mermaid
    assert "retry_search" in mermaid
