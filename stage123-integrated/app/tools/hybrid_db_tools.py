from typing import Any, Optional
from langchain_core.tools import tool
from app.schemas.dbquery_args import DbQueryArgs


_repository: Any | None = None


def configure_repository(repository: Any) -> None:
    """Inject a Stage2 repository without coupling the Tool to one DB backend."""

    global _repository
    _repository = repository


def _get_repository() -> Any:
    if _repository is not None:
        return _repository

    from integration.production_repository import ProductionStage2Repository

    return ProductionStage2Repository.from_environment()


@tool("dart_hybrid_search_tool", args_schema=DbQueryArgs)
def dart_hybrid_search_tool(
    corp_names: Optional[list[str]] = None,
    corp_name: Optional[str] = None,
    query: str = "가장 최근 공시 뭐가 있어?",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    section_name: Optional[str] = None,
    sector: Optional[str] = None,
    exclude_corp_name: Optional[str] = None,
    doc_group: Optional[str] = None,
    doc_group_candidates: Optional[list[str]] = None,
    doc_subtype: Optional[str] = None,
    doc_subtype_candidates: Optional[list[str]] = None,
    base_years: Optional[list[int]] = None,
    base_months: Optional[list[int]] = None,
    is_correction: Optional[bool] = False,
    basis: Optional[str] = None,
    report_nm_contains: Optional[list[str]] = None,
    top_k: int = 3,
    sort_by_latest: bool =True,
    limit: Optional[int] = None,
) -> str:
    """DART 전자공시 데이터베이스에서 하이브리드(RDB 필터링 + VectorDB 의미 검색) 방식으로 본문을 검색합니다.

    Args:
        corp_name: 검색할 기업 명칭 (예: '삼성전자', '한화에어로스페이스') [필수]
        query: 검색할 질문 또는 키워드 (예: '신규 사업 투자 위험 요인', '주요 매출 현황') [필수]
        start_date: 검색 시작일 (YYYYMMDD 형식, 예: '20240101', 선택사항)
        end_date: 검색 종료일 (YYYYMMDD 형식, 예: '20241231', 선택사항)
        section_name: 검색할 공시 목차/섹션 이름 (예: '사업의 내용', 선택사항)
        sector: 검색할 업종/섹터 이름 (예: '2차전지', 선택사항). corp_name 없이 업종 단위로 여러 기업을 비교/나열할 때 사용.
        exclude_corp_name: 검색 결과에서 제외할 기업명 (예: '삼성SDI', 선택사항)
        top_k: 추출할 관련 청크 개수 (기본값 3)
    """
    from integration.stage2_repository import SearchRequest

    result = _get_repository().search(
        SearchRequest(
            query=query,
            corp_names=tuple(corp_names or ([corp_name] if corp_name else [])),
            sector=sector,
            doc_group=doc_group,
            doc_group_candidates=tuple(doc_group_candidates or ()),
            doc_subtype=doc_subtype,
            doc_subtype_candidates=tuple(doc_subtype_candidates or ()),
            base_years=tuple(base_years or ()),
            base_months=tuple(base_months or ()),
            start_date=start_date,
            end_date=end_date,
            section_name=section_name,
            exclude_corp_name=exclude_corp_name,
            is_correction=is_correction,
            basis=basis,
            report_nm_contains=tuple(report_nm_contains or ()),
            top_k=top_k,
            sort_by_latest=sort_by_latest,
            limit=limit,
        )
    )
    return result.to_dict()
