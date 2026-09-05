from scripts.run_competition_tests import evaluate_response


def test_evaluate_response_requires_all_expected_phrases():
    case = {"required_phrases": ["차량부문", "기타부문"], "forbidden_phrases": []}
    result = evaluate_response(
        case,
        200,
        {
            "question_id": "R-03",
            "question": "질문",
            "retrieved_context": "",
            "think_trace": "",
            "answer": "차량부문 매출입니다.",
        },
    )
    assert not result["pass"]
    assert "기대 문구 누락: 기타부문" in result["errors"]


def test_evaluate_response_accepts_valid_contract_and_phrases():
    case = {"required_phrases": ["차량부문"], "forbidden_phrases": ["오류"]}
    result = evaluate_response(
        case,
        200,
        {
            "question_id": "R-03",
            "question": "질문",
            "retrieved_context": "",
            "think_trace": "",
            "answer": "차량부문 매출액은 100입니다.",
        },
    )
    assert result["pass"]


def test_evaluate_response_rejects_http_error():
    result = evaluate_response({"required_phrases": [], "forbidden_phrases": []}, 503, {"detail": "not ready"})
    assert not result["pass"]
    assert result["contract_valid"] is False
