from typing import List, Optional, Tuple, Dict, Any
import pandas as pd
from sqlalchemy import Engine


def fetch_filtered_chunk_ids(
    engine: Engine,
    corp_names: Optional[List[str]] = None,
    corp_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    section_name: Optional[str] = None,
    sector: Optional[str] = None,
    exclude_corp_name: Optional[str] = None,
    doc_group: Optional[str] = None,
    doc_group_candidates: Optional[List[str]] = None,
    doc_subtype: Optional[str] = None,
    doc_subtype_candidates: Optional[List[str]] = None,
    base_years: Optional[List[int]] = None,
    base_months: Optional[List[int]] = None,
    is_correction: Optional[bool] = False,
    report_nm_contains: Optional[List[str]] = None,
    sort_by_latest: bool = True,
    limit: Optional[int] = 50,
) -> List[str]:
    """
    필터 조건에 따라 finance_db를 조회하여 chunk_id 리스트를 반환합니다.
    """
    # 1. WHERE 절과 매개변수(params) 동적 생성
    where_str, params = build_where_and_params(
        corp_names=corp_names,
        corp_name=corp_name,
        start_date=start_date,
        end_date=end_date,
        section_name=section_name,
        sector=sector,
        exclude_corp_name=exclude_corp_name,
        doc_group=doc_group,
        doc_group_candidates=doc_group_candidates,
        doc_subtype=doc_subtype,
        doc_subtype_candidates=doc_subtype_candidates,
        base_years=base_years,
        base_months=base_months,
        is_correction=is_correction,
        report_nm_contains=report_nm_contains,
    )

    # 2. 최종 SQL문 완성
    sql = make_final_sql(
        where_str=where_str,
        sort_by_latest=sort_by_latest,
        limit=limit,
    )

    # 3. LIMIT 매개변수 추가
    if limit is not None:
        params["limit"] = limit

    # 4. DB 조회 후 chunk_id 리스트 반환
    return make_chunks_list(engine=engine, sql=sql, params=params)


def build_where_and_params(
    corp_names: Optional[List[str]] = None,
    corp_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    section_name: Optional[str] = None,
    sector: Optional[str] = None,
    exclude_corp_name: Optional[str] = None,
    doc_group: Optional[str] = None,
    doc_group_candidates: Optional[List[str]] = None,
    doc_subtype: Optional[str] = None,
    doc_subtype_candidates: Optional[List[str]] = None,
    base_years: Optional[List[int]] = None,
    base_months: Optional[List[int]] = None,
    is_correction: Optional[bool] = False,
    report_nm_contains: Optional[List[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    입력된 필터 옵션을 바탕으로 WHERE절 문자열과 바인딩할 params 사전 생성
    """
    where_clauses = []
    params = {}

    # 1. 기업명 조건
    effective_corp_names = [str(item).strip() for item in (corp_names or []) if str(item).strip()]
    if effective_corp_names:
        corp_clauses = []
        for index, name in enumerate(effective_corp_names):
            key = f"corp_name_{index}"
            corp_clauses.append(f"corp_name LIKE :{key}")
            params[key] = f"%{name}%"
        where_clauses.append("(" + " OR ".join(corp_clauses) + ")")
    elif corp_name and corp_name.strip():
        # 완전 일치(=) 대신 LIKE 사용으로 "(주)삼성전자", "삼성전자주식회사" 등 유연한 매칭 지원
        where_clauses.append("corp_name LIKE :corp_name")
        params["corp_name"] = f"%{corp_name.strip()}%"

    # 2. 시작일 조건
    if start_date and str(start_date).replace("-", "") <= "20260331":
        where_clauses.append("rcept_dt >= :start_date")
        params["start_date"] = str(start_date).replace("-", "")

    # 3. 종료일 조건
    if end_date:
        where_clauses.append("rcept_dt <= :end_date")
        params["end_date"] = str(end_date).replace("-", "")

    # 4. 섹션명 조건
    if section_name:
        where_clauses.append("section_name LIKE :section_name")
        params["section_name"] = f"%{section_name}%"

    # 5. 업종/섹터 조건 (특정 기업명 없이 업종 단위로 여러 기업을 비교/나열할 때 사용)
    if sector and sector.strip():
        where_clauses.append("sector LIKE :sector")
        params["sector"] = f"%{sector.strip()}%"

    # 6. 제외 기업명 조건
    if exclude_corp_name and exclude_corp_name.strip():
        where_clauses.append("corp_name NOT LIKE :exclude_corp_name")
        params["exclude_corp_name"] = f"%{exclude_corp_name.strip()}%"

    if doc_group:
        where_clauses.append("doc_group = :doc_group")
        params["doc_group"] = doc_group
    elif doc_group_candidates:
        placeholders = []
        for index, value in enumerate(doc_group_candidates):
            key = f"doc_group_{index}"
            placeholders.append(f":{key}")
            params[key] = value
        if placeholders:
            where_clauses.append("doc_group IN (" + ", ".join(placeholders) + ")")

    if doc_subtype:
        where_clauses.append("doc_subtype = :doc_subtype")
        params["doc_subtype"] = doc_subtype
    elif doc_subtype_candidates:
        placeholders = []
        for index, value in enumerate(doc_subtype_candidates):
            key = f"doc_subtype_{index}"
            placeholders.append(f":{key}")
            params[key] = value
        if placeholders:
            where_clauses.append("doc_subtype IN (" + ", ".join(placeholders) + ")")

    if base_years:
        placeholders = []
        for index, value in enumerate(base_years):
            key = f"base_year_{index}"
            placeholders.append(f":{key}")
            params[key] = value
        where_clauses.append("base_year IN (" + ", ".join(placeholders) + ")")

    if base_months:
        placeholders = []
        for index, value in enumerate(base_months):
            key = f"base_month_{index}"
            placeholders.append(f":{key}")
            params[key] = value
        where_clauses.append("base_month IN (" + ", ".join(placeholders) + ")")

    if is_correction is not None:
        where_clauses.append("is_correction = :is_correction")
        params["is_correction"] = bool(is_correction)

    if report_nm_contains:
        report_clauses = []
        for index, value in enumerate(report_nm_contains):
            key = f"report_nm_{index}"
            report_clauses.append(f"report_nm LIKE :{key}")
            params[key] = f"%{value}%"
        if report_clauses:
            where_clauses.append("(" + " OR ".join(report_clauses) + ")")

    where_str = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    return where_str, params


def make_final_sql(
    where_str: str = "",
    sort_by_latest: bool = True,
    limit: Optional[int] = 50, # optional의 디폴트도 실제 전달. 단지 None도 허용한다는 뜻.
) -> str:
    """
    WHERE절, 정렬(ORDER BY), LIMIT을 종합하여 SQL 문장 조합
    """
    # 1. 정렬 조건 생성
    order_clause = (
        " ORDER BY rcept_dt DESC, rcept_no DESC"
        if sort_by_latest
        else " ORDER BY rcept_dt ASC, rcept_no ASC"
    )

    # 2. LIMIT 절 생성
    limit_clause = " LIMIT :limit" if limit is not None else "" # is not은 주소 참조 연산자

    # 3. 필요한 chunk_id만 조회하는 SQL 조합
    sql = f"SELECT chunk_id FROM finance_db{where_str}{order_clause}{limit_clause}"
    return sql


def make_chunks_list(
    engine: Engine,
    sql: str,
    params: Dict[str, Any],
) -> List[str]:
    """
    SQL 문을 실행하여 결과를 리스트로 반환
    """
    df = pd.read_sql(sql, con=engine, params=params)
    
    # 조회가 비어있을 경우 빈 리스트 반환
    if df.empty or "chunk_id" not in df.columns:
        return []
    
    return df["chunk_id"].tolist()
