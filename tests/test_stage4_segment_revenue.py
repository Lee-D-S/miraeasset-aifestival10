from __future__ import annotations

from stage4.node import build_stage4_node


def test_stage4_rejects_answer_missing_a_requested_segment_fact():
    stage3_result = {
        "status": "success", "answer": "",
        "facts": [
            {
                "metric": "revenue", "label": "\ub9e4\ucd9c\uc561", "value": 109041330,
                "raw_value": "109041330", "normalized_value": 109041330, "unit": "\ubc31\ub9cc\uc6d0",
                "period": "2025", "basis": "\uc5f0\uacb0", "company": "\ud604\ub300\uc790\ub3d9\ucc28",
                "document_id": "vehicle", "source": "segment.xml", "evidence": "\ucc28\ub7c9\ubd80\ubb38 \ub9e4\ucd9c\uc561 109041330",
                "table_context": {"row_label": "\ucc28\ub7c9\ubd80\ubb38 \ub9e4\ucd9c\uc561"}, "aggregation_scope": "segment",
            },
            {
                "metric": "revenue", "label": "\ub9e4\ucd9c\uc561", "value": 7573182,
                "raw_value": "7573182", "normalized_value": 7573182, "unit": "\ubc31\ub9cc\uc6d0",
                "period": "2025", "basis": "\uc5f0\uacb0", "company": "\ud604\ub300\uc790\ub3d9\ucc28",
                "document_id": "other", "source": "segment.xml", "evidence": "\uae30\ud0c0\ubd80\ubb38 \ub9e4\ucd9c\uc561 7573182",
                "table_context": {"row_label": "\uae30\ud0c0\ubd80\ubb38 \ub9e4\ucd9c\uc561"}, "aggregation_scope": "segment",
            },
        ],
        "calculations": [], "comparison_results": [], "linked_events": [],
        "citations": [
            {"document_id": "vehicle", "source": "segment.xml", "evidence": "vehicle"},
            {"document_id": "other", "source": "segment.xml", "evidence": "other"},
        ],
    }
    update = build_stage4_node()({
        "route": "ok",
        "question": "\ud604\ub300\uc790\ub3d9\ucc28 2025\ub144 3\ubd84\uae30 \uc0ac\uc5c5\ubd80\ubb38\ubcc4 \ub9e4\ucd9c",
        "intent": {
            "question_type": "lookup", "metric": "revenue", "companies": ["\ud604\ub300\uc790\ub3d9\ucc28"],
            "basis": "\uc5f0\uacb0", "time": {"years": [2025], "base_months": [9]},
        },
        "answer": "\ucc28\ub7c9\ubd80\ubb38 \ub9e4\ucd9c\uc561 109041330\ubc31\ub9cc\uc6d0 [source:vehicle]",
        "stage3_result": stage3_result,
    })
    assert update["stage4_result"]["status"] == "validation_failed"
    assert any("missing segment facts" in error for error in update["stage4_result"]["numeric_check"]["errors"])
