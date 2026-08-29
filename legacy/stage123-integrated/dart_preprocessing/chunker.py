def split_to_chunks(
    parsed_elements: list[dict],
    doc_meta: dict,
    max_chunk_len: int = 1000,
) -> list[dict]:
    """파싱된 요소들에 [기업명 | 공시명 | 목차] Header를 상단에 합성하고,
    1,000자가 넘는 긴 텍스트는 100자 오버랩(Overlap)을 적용하여 슬라이싱함
    """
    final_chunks = []

    # 1. Header 생성을 위한 공통 정보 추출
    corp_name = doc_meta.get('corp_name', '')
    report_nm = doc_meta.get('report_nm', '')

    for item in parsed_elements:
        section_name = item.get('section_name', '본문')

        # [기업명 | 공시명 | 목차] 표준 Header 생성
        header = f'[{corp_name} | {report_nm} | {section_name}]\n'

        # 2. 표(Table)는 자르지 않고 Header만 붙여서 단일 청크로 저장
        if item['chunk_type'] == 'table':
            # XmlParser에서 임시로 붙었던 [section_name] 중복 태그 제거
            raw_table_md = item['text_content'].replace(
                f'[{section_name}]\n', ''
            )

            sub_item = item.copy()
            sub_item['text_content'] = header + raw_table_md

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

                    chunk_row = {**doc_meta, **sub_item}
                    final_chunks.append(chunk_row)

    return final_chunks