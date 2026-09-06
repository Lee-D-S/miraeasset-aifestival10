"""파싱 결과(parsers.py)를 DB 적재용 청크로 쪼갠다.

일반 텍스트: max_chunk_len(1000자) + 100자 overlap 슬라이싱.
표(table): 행 단위로 여러 청크로 분할 — 조각마다 [문서 Header]+[표 헤더 행]을 반복해
컬럼 맥락을 유지하고, raw_json_content도 같은 행 구간으로 잘라 재직렬화한다. 표를 안 자르면
병리적 공시(중첩표가 한 <tr>에 flatten돼 들어오는 경우 등) 하나가 ~1GB 청크를 만들어
preprocesser.py의 to_sql에서 SQLite 단일 값 한도(1e9 bytes)를 넘겨 전처리가 죽는다.
"""
import json


def _detect_basis(text: str) -> str:
    """청크 본문에서 연결/별도 재무제표 기준을 감지한다.
    문서(리포트) 단위가 아니라 청크 단위 속성이다 — 사업보고서 하나에도
    연결재무제표 섹션과 별도재무제표 섹션이 함께 들어있기 때문에, 여기서
    "연결"/"별도" 문자열을 직접 찾는 방식(reasoner fact_extraction._basis()와
    동일한 휴리스틱)만 청크별로 정확하다.
    """
    if '연결' in text:
        return '연결'
    if '별도' in text:
        return '별도'
    return ''


def _split_markdown_table(table_md: str) -> tuple[list[str], list[str]]:
    """converters.MarkdownConverter가 만든 표 문자열을
    (헤더 라인, 데이터 라인)으로 분리한다.

        | c1 | c2 |
        | --- | --- |
        | ... |

    두 번째 줄이 구분선이면 헤더 2줄, 아니면 첫 줄만 헤더로 본다.
    """
    lines = [ln for ln in table_md.split('\n') if ln.strip()]
    if not lines:
        return [], []
    core = lines[1].replace('|', '').replace(' ', '') if len(lines) >= 2 else ''
    if core and set(core) <= {'-', ':'}:
        return lines[:2], lines[2:]
    return lines[:1], lines[1:]


def _coerce_json_rows(raw) -> list:
    """raw_json_content(JSON 문자열 / 파이썬 리스트 / None)를 행 리스트로 정규화한다.
    XmlParser·HtmlParser는 JSON 문자열, PdfParser는 리스트를 넣기 때문에 둘 다 받는다.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _iter_table_chunks(
    header: str,
    md_header_lines: list[str],
    md_data_lines: list[str],
    json_rows: list,
    max_chunk_len: int,
    hard_cap: int = 20_000,
    max_json_chars: int = 20_000,
):
    """표를 행 단위로 묶어 (text_content, raw_json_content) 튜플을 순서대로 내보낸다.

    - 각 청크 상단에 [문서 Header] + [표 헤더 행 + 구분선]을 반복해서 붙여
      어느 조각을 봐도 컬럼 맥락이 유지되도록 한다.
    - 데이터 행을 max_chunk_len 예산 안에서 그리디하게 그룹핑한다(그룹당 최소 1행).
    - 한 행 자체가 예산보다 크면(중첩표가 한 <tr>에 뭉쳐 들어온 경우 등)
      그 행을 다시 문자 단위로 슬라이싱한다 — 잘라 버리지 않고 여러 청크로 나눈다.
    - hard_cap은 어떤 경우에도 넘지 않도록 하는 마지막 안전장치다
      (SQLite 단일 값 1GB 한도·임베딩 토큰 한도 방지).
    - 조각 JSON이 max_json_chars를 넘으면 raw_json_content를 None으로 둔다
      (RDB는 메타데이터 필터용이라 표 원문 전체를 보관할 필요가 없다).
    """
    md_header_block = '\n'.join(md_header_lines)
    # 헤더 행 자체가 비정상적으로 큰 경우(중첩표가 첫 <tr>에 통째로 뭉쳐 들어온 경우 등)
    # 매 청크에 반복되는 prefix가 폭발하지 않도록 잘라 낸다.
    head_budget = max(max_chunk_len - len(header), 200)
    if len(md_header_block) > head_budget:
        md_header_block = md_header_block[:head_budget]
    prefix = f'{header}{md_header_block}\n' if md_header_block else header
    budget = max(max_chunk_len - len(prefix), 200)

    n = len(md_data_lines)
    aligned = len(json_rows) == n  # md 데이터 행수 == json 행수일 때만 조각별 매핑

    def _dump(rows: list):
        s = json.dumps(rows, ensure_ascii=False)
        return s if len(s) <= max_json_chars else None

    if n == 0:
        # 데이터 행이 없는 표(헤더만 존재) — 헤더 블록만 단일 청크로
        if md_header_block:
            text = prefix if len(prefix) <= hard_cap else prefix[:hard_cap]
            yield text, _dump(json_rows)
        return

    i = 0
    first = True
    while i < n:
        row = md_data_lines[i]

        # (a) 한 행이 통째로 예산을 초과 → 그 행을 문자 단위로 재분할
        if len(row) + 1 > budget:
            row_rows = [json_rows[i]] if aligned else (json_rows if first else [])
            for j in range(0, len(row), budget):
                text = (prefix + row[j : j + budget])[:hard_cap]
                yield text, _dump(row_rows if j == 0 else [])
            i += 1
            first = False
            continue

        # (b) 예산 안에서 여러 행 묶기(그룹당 최소 1행)
        start = i
        group: list[str] = []
        cur = 0
        while i < n:
            add = len(md_data_lines[i]) + 1
            if group and (add > budget or cur + add > budget):
                break
            group.append(md_data_lines[i])
            cur += add
            i += 1

        text = (prefix + '\n'.join(group))[:hard_cap]
        if aligned:
            sub_json = _dump(json_rows[start:i])
        else:
            # md/json 행수가 어긋나면 조각 매핑이 불가능하므로
            # 전체 JSON을 첫 청크에만 best-effort로 싣고 나머지는 비운다.
            sub_json = _dump(json_rows) if first else None

        yield text, sub_json
        first = False


def split_to_chunks(
    parsed_elements: list[dict],
    doc_meta: dict,
    max_chunk_len: int = 1000,
) -> list[dict]:
    """파싱된 요소들에 [기업명 | 공시명 | 목차] Header를 상단에 합성하고,
    1,000자가 넘는 긴 텍스트는 100자 오버랩(Overlap)을 적용하여 슬라이싱함.
    표(Table)도 행 단위로 묶어 여러 청크로 분할한다(헤더 행은 조각마다 반복).
    """
    final_chunks = []

    # 1. Header 생성을 위한 공통 정보 추출
    corp_name = doc_meta.get('corp_name', '')
    report_nm = doc_meta.get('report_nm', '')

    for item in parsed_elements:
        section_name = item.get('section_name', '본문')

        # [기업명 | 공시명 | 목차] 표준 Header 생성
        header = f'[{corp_name} | {report_nm} | {section_name}]\n'

        # 2. 표(Table)는 행 단위로 묶어 여러 청크로 분할
        if item['chunk_type'] == 'table':
            # XmlParser에서 임시로 붙었던 [section_name] 중복 태그 제거
            raw_table_md = item['text_content'].replace(
                f'[{section_name}]\n', ''
            )
            md_header_lines, md_data_lines = _split_markdown_table(raw_table_md)
            json_rows = _coerce_json_rows(item.get('raw_json_content'))

            for sub_text, sub_json in _iter_table_chunks(
                header, md_header_lines, md_data_lines, json_rows, max_chunk_len
            ):
                sub_item = item.copy()
                sub_item['text_content'] = sub_text
                sub_item['raw_json_content'] = sub_json
                sub_item['basis'] = _detect_basis(sub_text)

                chunk_row = {**doc_meta, **sub_item}
                final_chunks.append(chunk_row)

        # 3. 일반 텍스트 청킹 (Header 포함 길이에 맞춰 분할)
        else:
            # XmlParser에서 임시로 붙었던 [section_name] 중복 태그 제거
            raw_text = item['text_content'].replace(f'[{section_name}] ', '')
            full_text = header + raw_text

            # 3-1. 텍스트가 지정한 길이(1,000자) 이하인 경우 바로 저장
            if len(full_text) <= max_chunk_len:
                sub_item = item.copy()
                sub_item['text_content'] = full_text
                sub_item['basis'] = _detect_basis(full_text)

                chunk_row = {**doc_meta, **sub_item}
                final_chunks.append(chunk_row)

            # 3-2. 텍스트가 1,000자를 초과할 경우 100자 Overlap 적용하여 슬라이싱
            else:
                overlap = 100
                # Header 길이를 뺀 실제 본문 슬라이싱 단위 계산
                step_size = max_chunk_len - len(header) - overlap

                for i in range(0, len(raw_text), step_size):
                    # 100자 오버랩을 적용하여 본문 자르기
                    sub_raw_text = raw_text[i : i + step_size + overlap]

                    sub_item = item.copy()
                    # 매 조각(Chunk)마다 맨 앞에 Header를 강제로 다시 결합
                    sub_item['text_content'] = header + sub_raw_text
                    sub_item['basis'] = _detect_basis(sub_item['text_content'])

                    chunk_row = {**doc_meta, **sub_item}
                    final_chunks.append(chunk_row)

    return final_chunks
