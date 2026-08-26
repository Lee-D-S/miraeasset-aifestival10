from abc import ABC, abstractmethod
from pathlib import Path
import re
from bs4 import BeautifulSoup
import pdfplumber
from converters import reconstruct_table


def clean_unescaped_ampersands(xml_string: str) -> str:
    return re.sub(r'&(?!#?\w+;)', '&amp;', xml_string)


class BaseParser(ABC):

    @abstractmethod
    def parse(self, file_path: str) -> list[dict]:
        """파싱 결과로 [{section_name, chunk_type, text_content, raw_json_content}, ...] 반환"""
        pass


class XmlParser(BaseParser):

    def parse(self, file_path: str) -> list[dict]:
        parsed_elements = []
        content = ""

        # 1. 인코딩 읽기 (utf-8, utf-8-sig, euc-kr, cp949)
        for enc in ["utf-8", "utf-8-sig", "euc-kr", "cp949"]:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    content = f.read()
                break
            except Exception:
                continue

        if not content:
            print(f"Error reading XML {file_path}: Unable to decode content.")
            return []

        cleaned_xml = clean_unescaped_ampersands(content)

        # 2. DART XML 특성을 고려하여 파서 적용
        soup = BeautifulSoup(cleaned_xml, "lxml-xml")

        current_section = "본문"

        # 3. DART XML 태그 탐색 (대소문자 및 DART 확장 태그 대응)
        # SECTION-1, SECTION-2, COVER, TABLE, P, TITLE, COVER-TITLE 등
        target_tags = soup.find_all(
            re.compile(
                r"^(title|cover-title|section-\d+|p|table|cover)$", re.I
            )
        )

        for element in target_tags:
            tag_name = element.name.lower()

            # A. 제목/섹션 태그 처리
            if tag_name in ["title", "cover-title"] or tag_name.startswith(
                "section-"
            ):
                title_text = element.get_text(strip=True)
                # 제목용 단문만 섹션명으로 채택
                if title_text and len(title_text) < 150:
                    current_section = title_text

            # B. 표(Table) 처리
            elif tag_name == "table":
                table_md = reconstruct_table(element, "md")
                table_json = reconstruct_table(element, "json")

                if table_md:
                    parsed_elements.append(
                        {
                            "section_name": current_section,
                            "chunk_type": "table",
                            "text_content": f"[{current_section}]\n{table_md}",
                            "raw_json_content": table_json,
                        }
                    )

            # C. 문단(P, COVER 등) 텍스트 처리
            elif tag_name in ["p", "cover"]:
                # 내부에 하위 p나 table이 중복 포함되어 있다면 상위 태그 스킵
                if element.find(["p", "P", "table", "TABLE"]):
                    continue

                text = element.get_text(strip=True)
                if len(text) > 15:  # 유의미한 길이의 텍스트만 추출
                    parsed_elements.append(
                        {
                            "section_name": current_section,
                            "chunk_type": "text",
                            "text_content": f"[{current_section}] {text}",
                            "raw_json_content": None,
                        }
                    )

        # Fallback: 위 태그 조건으로 아무것도 안 잡혔을 경우 전체 본문 추출
        if not parsed_elements:
            body = soup.find(re.compile(r"^(body|document)$", re.I)) or soup
            fallback_text = body.get_text(separator="\n", strip=True)
            if len(fallback_text) > 30:
                parsed_elements.append(
                    {
                        "section_name": "본문 전체",
                        "chunk_type": "text",
                        "text_content": f"[본문] {fallback_text[:2000]}",  # 
                        "raw_json_content": None,
                    }
                )

        return parsed_elements


class HtmlParser(BaseParser):

    def parse(self, file_path: str) -> list[dict]:
        parsed_elements = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                soup = BeautifulSoup(f.read(), 'html.parser')
        except Exception as e:
            print(f'Error reading HTML {file_path}: {e}')
            return []

        current_section = '대체수집 본문'

        for element in soup.find_all(
            ['h1', 'h2', 'h3', 'h4', 'title', 'p', 'table', 'div']
        ):
            if element.name in ['h1', 'h2', 'h3', 'h4', 'title']:
                title_text = element.text.strip()
                if title_text and len(title_text) < 100:
                    current_section = title_text

            elif element.name == 'table':
                table_md = reconstruct_table(element, 'md')
                table_json = reconstruct_table(element, 'json')

                if table_md:
                    parsed_elements.append({
                        'section_name': current_section,
                        'chunk_type': 'table',
                        'text_content': f'[{current_section}]\n{table_md}',
                        'raw_json_content': table_json,
                    })

            elif element.name in ['p', 'div']:
                if element.find(['table', 'div', 'p']):
                    continue
                text = element.text.strip()
                if len(text) > 20:
                    parsed_elements.append({
                        'section_name': current_section,
                        'chunk_type': 'text',
                        'text_content': f'[{current_section}] {text}',
                        'raw_json_content': None,
                    })

        return parsed_elements


class PdfParser(BaseParser):

    def parse(self, file_path: str) -> list[dict]:
        parsed_elements = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text()
                    if text and len(text.strip()) > 30:
                        parsed_elements.append({
                            'section_name': f'PDF Page {page_num}',
                            'chunk_type': 'text',
                            'text_content': f'[PDF Page {page_num}] {text.strip()}',
                            'raw_json_content': None,
                        })
        except Exception as e:
            print(f'Error reading PDF {file_path}: {e}')

        return parsed_elements


class JsonParser(BaseParser):

    def parse(self, file_path: str) -> list[dict]:
        # DART 메타데이터 list_*.json 등의 원본 텍스트 추출용
        return []


class ParserFactory:

    _parsers = {
        'html': HtmlParser,
        'pdf': PdfParser,
        'json': JsonParser,
        'xml': XmlParser,
    }

    @classmethod
    def get_parser(cls, doc_type: str) -> BaseParser:
        parser_cls = cls._parsers.get(doc_type.lower())
        if not parser_cls:
            raise ValueError(f'지원하지 않는 파서 포맷: {doc_type}')
        return parser_cls()


def parse_docs(file_path: str, file_format: str) -> list[dict]:
    parser = ParserFactory.get_parser(file_format)
    return parser.parse(file_path=file_path)


if __name__ == "__main__":
    import os
    from pathlib import Path
    from parsers import parse_docs
    import traceback

    # 프로젝트 최상위 루트 디렉토리 기준 절대 경로 자동 계산
    BASE_DIR = Path(__file__).resolve().parent.parent
    test_file = (
        BASE_DIR
        / "data"
        / "3.gongsi"
        / "corpus"
        / "raw"
        / "periodic"
        / "삼성전자"
        / "20230515002335_quarter_2023_03"
        / "20230515002335.xml"
    )
    # 삼성전자 0번 XML 파일 경로

    if test_file.exists():
        print(f"파일 존재 확인: {test_file}")
        try:
            elements = parse_docs(str(test_file), "xml")
            print(f"파싱 결과 요소 개수: {len(elements)}")
            if elements:
                print(f"첫번째 요소 샘플: {elements[0]}")
        except Exception as e:
            print(f"파싱 중 예외 발생: {e}")
    else:
        print("테스트 파일을 찾을 수 없습니다.")

    # 1. 파일 원본 텍스트 및 인코딩 확인
    content = ""
    for enc in ["euc-kr", "cp949", "utf-8", "utf-8-sig"]:
        try:
            with open(test_file, "r", encoding=enc) as f:
                content = f.read()
            print(f"인코딩 읽기 성공: {enc} (총 길이: {len(content)}자)")
            break
        except Exception:
            pass

    if content:
        print("\n=== XML 파일 앞부분 500자 ===")
        print(content[:500])
        print("=============================\n")

        # 2. BeautifulSoup으로 태그 파싱 실험
        soup = BeautifulSoup(content, "lxml-xml")
        all_tags = set(tag.name for tag in soup.find_all())
        print(f"발견된 XML 주요 태그 목록 (최대 20개): {list(all_tags)[:20]}")

        # 3. 본문 텍스트 추출 가능 여부 테스트
        body_text = soup.get_text(strip=True)
        print(f"전체 추출 텍스트 길이: {len(body_text)}자")