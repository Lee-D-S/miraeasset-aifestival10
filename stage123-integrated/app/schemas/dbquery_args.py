from enum import IntEnum
from typing import Optional
from pydantic import BaseModel, Field


class DbQueryArgs(BaseModel):
    corp_names: Optional[list[str]] = Field(
        default=None,
        description="여러 기업을 동시에 검색할 때 사용할 기업명 목록. corp_name보다 우선합니다.",
    )
    corp_name: Optional[str] = Field(
        default=None,
        description="검색할 대상 상장 기업의 명칭 (예: '삼성전자', '한화에어로스페이스', 'SK하이닉스')",
    )
    query: str = Field(
        description="검색할 키워드 또는 질문 문장. 반드시 채워야 함! Vector DB에서 본문 유사도 검색에 사용할 질문이나 핵심 키워드 (예: '신규 사업 투자 위험 요인', '주요 제품 매출 비중')",
    )
    start_date: Optional[str] = Field(
        default=None,
        description="공시 검색 시작일 (YYYYMMDD 형식, 예: '20240101'). 특정 날짜 이후의 공시만 찾을 때 사용합니다.",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="공시 검색 종료일 (YYYYMMDD 형식, 예: '20241231'). 특정 날짜 이전의 공시만 찾을 때 사용합니다.",
    )
    section_name: Optional[str] = Field(
        default=None,
        description="공시 문서의 목차 또는 섹션 이름 (예: '사업의 내용', '재무제표', '이사회의 경영진단'). 특정 섹션으로 범위를 제한할 때 사용합니다.",
    )
    sector: Optional[str] = Field(
        default=None,
        description="검색 대상 산업/업종 섹터명 (예: '2차전지', '반도체·전자부품'). 질문에서 특정 기업명이 명시되지 않고 '~업종/섹터 기업 중'처럼 업종 단위로 여러 기업을 비교하거나 나열해야 할 때 corp_name 대신(또는 함께) 사용합니다.",
    )
    exclude_corp_name: Optional[str] = Field(
        default=None,
        description="검색 결과에서 제외할 기업명 (예: '삼성SDI'). 질문에서 '~를 제외하고'처럼 특정 기업을 배제하라는 조건이 있을 때 사용합니다.",
    )
    top_k: int = Field(
        default=3,
        description="Vector DB에서 최종 추출할 유사 공시 청크의 개수 (기본값: 3)",
    )
    sort_by_latest: bool = Field(
        default=True,
        description="최신순 정렬 여부. 기본값 True",
    )
    limit: Optional[int] = Field(
        default=None,
        description="가져올 상위 RDB 데이터 개수. query실패시 limit을 늘릴 것. (기본값: None)",
    )
    doc_group: Optional[str] = Field(
        default=None,
        description="Stage1이 선택한 공시 그룹(periodic, major, exchange, holding)",
    )
    doc_group_candidates: Optional[list[str]] = Field(
        default=None,
        description="공시 그룹 후보 목록",
    )
    doc_subtype: Optional[str] = Field(
        default=None,
        description="정기공시 유형(annual, half, quarter) 또는 수시공시 유형",
    )
    doc_subtype_candidates: Optional[list[str]] = Field(
        default=None,
        description="공시 세부 유형 후보 목록",
    )
    base_years: Optional[list[int]] = Field(
        default=None,
        description="정기공시 기준연도 목록",
    )
    base_months: Optional[list[int]] = Field(
        default=None,
        description="정기공시 기준월 목록",
    )
    is_correction: Optional[bool] = Field(
        default=False,
        description="정정 여부. None이면 원본과 정정 이력을 모두 조회합니다.",
    )
    basis: Optional[str] = Field(
        default=None,
        description="재무 기준(연결 또는 별도)",
    )
    report_nm_contains: Optional[list[str]] = Field(
        default=None,
        description="보고서명에 포함되어야 하는 문자열 목록",
    )
