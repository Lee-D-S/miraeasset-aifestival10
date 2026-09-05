"""DART disclosure parsers (XML / HTML / PDF) -> section-tagged elements.

Vendored from the reference ``dart_preprocessing`` package.  Behaviour is
unchanged; only the import path, the removal of a script demo block, and a
lazy ``pdfplumber`` import (so an XML/HTML-only corpus needs no PDF stack)
differ from the original.  Each parser returns a list of
``{section_name, chunk_type, text_content, raw_json_content}`` dicts that
:mod:`stage2.ingestion.dart.chunker` turns into chunk rows.
"""

from abc import ABC, abstractmethod
from pathlib import Path
import re
from bs4 import BeautifulSoup
from stage2.ingestion.dart.converters import reconstruct_table


def clean_unescaped_ampersands(xml_string: str) -> str:
    return re.sub(r'&(?!#?\w+;)', '&amp;', xml_string)


_STRAY_LT_RE = re.compile(r'<(?![A-Za-z_/!?])')


def clean_unescaped_angle_brackets(xml_string: str) -> str:
    """Escape ``<`` that DART filers use as a bare decorative caption marker.

    Some filings write a caption like ``< TV 시장점유율 추이 >`` directly as text
    instead of escaping it (seen literally as ``<P>< TV 시장점유율 추이 ></P>`` and
    as table-cell captions like ``<이사ㆍ감사 전체의 보수현황>``). A real XML tag
    name always starts with a letter, ``_``, ``/`` (closing tag), ``!``
    (comment/CDATA/doctype) or ``?`` (processing instruction) — anything else
    right after ``<`` is not a tag. lxml's XML parser fails to recover cleanly
    from that malformed markup: it can silently drop large stretches of the
    surrounding tree (observed: a document's parse tree lost ~96% of its
    elements — 3,960 of an expected ~89,000 — including entire chapters,
    because of a handful of these captions).
    """

    return _STRAY_LT_RE.sub('&lt;', xml_string)


class BaseParser(ABC):

    @abstractmethod
    def parse(self, file_path: str) -> list[dict]:
        """파싱 결과로 [{section_name, chunk_type, text_content, raw_json_content}, ...] 반환"""
        pass


class XmlParser(BaseParser):

    def parse(self, file_path: str) -> list[dict]:
        parsed_elements = []
        content = ""

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

        cleaned_xml = clean_unescaped_angle_brackets(clean_unescaped_ampersands(content))
        soup = BeautifulSoup(cleaned_xml, "lxml-xml")

        current_section = "본문"

        # 트리 전체를 순차적으로 방문하도록 변경 (BFS/DFS 개념 적용)
        for element in soup.find_all(True):  # 모든 태그 순회
            tag_name = element.name.lower()

            # 1. 섹션/제목 업데이트 (단, 부모 노드 위주)
            if tag_name in ["title", "cover-title"] or re.match(r"^section-\d+$", tag_name):
                title_text = element.get_text(strip=True)
                # 하위 태그 텍스트 오염 방지 및 길이 조건
                if title_text and len(title_text) < 150:
                    current_section = title_text

            # 2. 표(Table) 처리 (부모 태그가 최상위 table일 때만)
            elif tag_name == "table":
                # 자식 테이블이 중복 추출되는 것 방지
                if element.find_parent("table"):
                    continue
                
                try:
                    table_md = reconstruct_table(element, "md")
                    table_json = reconstruct_table(element, "json")

                    if table_md:
                        parsed_elements.append({
                            "section_name": current_section,
                            "chunk_type": "table",
                            "text_content": f"[{current_section}]\n{table_md}",
                            "raw_json_content": table_json,
                        })
                except Exception as e:
                    print(f"Table reconstruction error in {file_path}: {e}")

            # 3. 텍스트(P) 처리
            elif tag_name == "p":
                # P 내부 또는 상위에 Table이 포함된 경우 중복 파싱 방지
                if element.find_parent("table") or element.find(["p", "table"]):
                    continue

                text = element.get_text(strip=True)
                if len(text) > 15:
                    parsed_elements.append({
                        "section_name": current_section,
                        "chunk_type": "text",
                        "text_content": f"[{current_section}] {text}",
                        "raw_json_content": None,
                    })

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
        import pdfplumber

        parsed_elements = []
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    section_name = f"PDF Page {page_num}"
                    
                    # 1. PDF 내 표(Table) 우선 추출
                    tables = page.extract_tables()
                    for table_idx, table in enumerate(tables):
                        # 리스트 형태의 표 데이터를 텍스트/Markdown 변환
                        table_str = "\n".join([" | ".join([cell if cell else "" for cell in row]) for row in table])
                        if len(table_str.strip()) > 10:
                            parsed_elements.append({
                                'section_name': section_name,
                                'chunk_type': 'table',
                                'text_content': f'[{section_name} Table {table_idx+1}]\n{table_str}',
                                'raw_json_content': table,
                            })

                    # 2. 일반 텍스트 추출
                    text = page.extract_text()
                    if text and len(text.strip()) > 30:
                        parsed_elements.append({
                            'section_name': section_name,
                            'chunk_type': 'text',
                            'text_content': f'[{section_name}] {text.strip()}',
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


