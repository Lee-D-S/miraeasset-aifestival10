# =============================================================================
#  team-feature2 · chunk_index 빌더 (Google Colab) — 셀 2개로 나눠 실행
# =============================================================================
#  이 파일 = Colab 셀 '2개'.  아래 구분선에서 잘라 각각 다른 셀에 붙여넣는다:
#
#    [셀 1]  "===== [셀 1] 끝 =====" 까지  →  설치 + Drive 마운트 + GPU 확인.
#            런타임을 새로 켤 때마다 딱 한 번만 실행.
#    [셀 2]  "===== [셀 2] 시작 =====" 부터 파일 끝까지  →  실제 빌드
#            (파싱 → 임베딩 → zip).  런타임이 끊기면 [셀 2] 만 다시 실행하면
#            마지막 체크포인트부터 재개된다(RESUME).
#
#  · repo 를 clone/import 하지 않는다. Drive 코퍼스(universe.csv + manifest.jsonl
#    + raw/)만 읽어 chunk_index.db + chunk_index_chroma/ 를 만들고 zip 으로 낸다.
#  · 임베딩: intfloat/multilingual-e5-large.  '문서 측' 은 "passage: " 프리픽스 + mean
#    pooling + L2 정규화 — 서빙(retriever, fastembed e5)의 "query: " 측과 같은 비대칭 쌍,
#    같은 1024-dim 벡터 공간.  Colab 에서는 sentence-transformers(torch) 로 돌린다:
#    onnxruntime-gpu 의 CUDA 라이브러리 버전 지옥을 피하고 Colab 이 보장하는 torch+CUDA
#    를 그대로 쓰기 위함(서빙 이미지는 여전히 torch 없이 fastembed).
#  · 만든 DB 서빙: STAGE2_MODE=local · STAGE2_EMBEDDING=e5 ·
#    STAGE2_INDEX_PATH=<unzip>/chunk_index.db ·
#    STAGE2_CHROMA_PATH=<unzip>/chunk_index_chroma ·
#    STAGE2_CHROMA_COLLECTION=<COLLECTION 과 동일>
#  · "vendored" 구역은 retriever/ingestion/dart/*.py 손복사본. 원본 파싱·청킹이
#    바뀌면 같이 고치고 pytest tests/test_colab_cell.py 로 확인한다.
# =============================================================================


# ============================ [셀 1] 시작 ====================================
#  설정을 고치고, 이 블록 전체(===== [셀 1] 끝 ===== 까지)를 셀 하나에 붙여넣어
#  실행한다. 런타임당 한 번. (pip 설치 + Drive 마운트 + GPU 실제 동작 확인)
# ---------------------------------------------------------------------------
CORPUS_DIR    = "/content/drive/MyDrive/corpus"              # universe.csv·manifest.jsonl·raw/
OUT_DIR       = "/content/drive/MyDrive/team-feature2_build" # ⚠ Drive 영구 경로 (재개 스냅샷 보관처. 빌드 자체는 로컬 /content 에서)
SELECTION     = ""          # "" = 전체 manifest, 또는 selected_documents.json 경로
COLLECTION    = "chunk_vectors"
MAX_CHUNK_LEN = 1000
E5_BATCH_SIZE = 128         # A100 이면 128~256 도 여유. 임베딩 처리량 ↑
FP16          = True        # GPU 에서 half precision 임베딩 (A100 ~2배, 검색 품질 차이 무시 가능)
PARSE_WORKERS = 0           # 파싱·청킹 병렬 프로세스 수. 0 = CPU 코어 수 자동 / 1 = 단일 / N = N개
SNAPSHOT_EVERY = 300000     # 임베딩 이 chunk 수마다 로컬 빌드 → Drive 로 rsync 스냅샷(재개 지점)
RESUME        = True        # True = OUT_DIR 의 마지막 체크포인트부터 이어서. False = 처음부터 새로
USE_GPU       = True        # True = torch CUDA 로 임베딩(Colab GPU 런타임). False = 처음부터 CPU
REQUIRE_GPU   = True        # True = GPU 확인 실패 시 정지 (CPU 로 몇 시간 낭비 방지)
DRIVE_COPY    = "/content/drive/MyDrive/team-feature2_chunk_index.zip"   # "" 면 생략
# ---------------------------------------------------------------------------

import subprocess, sys


def _sh(*args):
    """pip 를 서브프로세스로 호출 (이 셀은 repo import 없이 독립 실행하므로 직접 설치)."""
    print("$", sys.executable.split("/")[-1], "-m pip", *args, flush=True)
    subprocess.run([sys.executable, "-m", "pip", *args], check=False)


# (1) 설치. torch 는 Colab 에 이미 있고 CUDA 가 동작한다(Colab 이 유지보수하는 유일한
#     GPU 스택). sentence-transformers 는 그 torch 를 그대로 재사용한다.
_sh("install", "-q", "beautifulsoup4", "lxml", "pdfplumber", "chromadb", "sentence-transformers")

# (2) Drive 마운트
from google.colab import drive
drive.mount("/content/drive")

# (3) 임베딩이 실제로 GPU(torch CUDA)에 붙는지 '별도 프로세스로' 확인한다.
#     - 메인 커널에서 CUDA 를 초기화하지 않음 → [셀 2] 의 병렬 파싱 fork 가 깨끗함
#     - e5 모델도 이때 내려받아 HuggingFace 캐시에 저장 → [셀 2] 가 바로 시작
_GPU_PROBE = (
    "import torch\n"
    "from sentence_transformers import SentenceTransformer\n"
    "ok = torch.cuda.is_available()\n"
    "name = torch.cuda.get_device_name(0) if ok else '-'\n"
    "print('INFO=torch %s / CUDA %s / %s' % (torch.__version__, torch.version.cuda, name))\n"
    "m = SentenceTransformer('intfloat/multilingual-e5-large', device='cuda' if ok else 'cpu')\n"
    "v = m.encode(['passage: warmup'], normalize_embeddings=True)\n"
    "print('DIM=%d' % len(v[0]))\n"
    "print('CUDA_OK' if ok else 'CUDA_NO')\n"
)
if USE_GPU:
    _r = subprocess.run([sys.executable, "-c", _GPU_PROBE], capture_output=True, text=True)
    _ok = "CUDA_OK" in _r.stdout
    print("=" * 60)
    for _ln in _r.stdout.strip().splitlines():
        if _ln.startswith(("INFO=", "DIM=")):
            print("  " + _ln)
    print("  GPU(torch CUDA) 확인 :", "OK" if _ok else "실패")
    if not _ok:
        for _ln in (_r.stderr or "").strip().splitlines()[-8:]:
            print("   " + _ln)
    print("=" * 60)
    if not _ok:
        _msg = (
            "\n[정지] torch 가 GPU(CUDA)를 못 씁니다.\n"
            "  · 런타임이 GPU 인가?  [런타임] > [런타임 유형 변경] > 하드웨어 가속기 = GPU\n"
            "  · 맞다면 [런타임] > [세션 다시 시작] 후 [셀 1] 을 다시 실행\n"
            "  · CPU 로 진행하려면 REQUIRE_GPU=False (체크포인트가 있어 중단돼도 재개됨)\n"
        )
        if REQUIRE_GPU:
            raise SystemExit(_msg)
        print(_msg + "→ REQUIRE_GPU=False 라 [셀 2] 는 CPU 로 진행합니다.", flush=True)

print("\n>>> [셀 1] 완료. 이제 [셀 2] 를 붙여넣고 실행하세요.", flush=True)
# ============================ [셀 1] 끝 ======================================


# ============================ [셀 2] 시작 ====================================
#  "===== [셀 1] 끝 =====" 아래부터 파일 끝까지를 '다른' 셀에 붙여넣어 실행.
#  런타임이 끊기면 이 [셀 2] 만 다시 실행 → 마지막 체크포인트부터 재개.
# ---------------------------------------------------------------------------
try:
    CORPUS_DIR, OUT_DIR, RESUME, USE_GPU, FP16, SNAPSHOT_EVERY  # [셀 1] 을 먼저 실행했는지 확인
except NameError:
    raise SystemExit("먼저 [셀 1] 을 붙여넣고 실행하세요 (설정·설치·GPU 준비가 거기 있습니다).")

import csv, json, math, multiprocessing, os, re, shutil, sqlite3
from pathlib import Path

from google.colab import files


# =============================================================================
#  vendored — retriever/ingestion/dart/converters.py
# =============================================================================
from abc import ABC, abstractmethod
import json
import re
from bs4 import Tag

# DART 공시 XML은 태그가 대문자(<TR>, <TD>)로 오는 경우가 많고,
# lxml-xml 파서는 태그 대소문자를 그대로 보존하므로 대소문자 무관 매칭이 필요함.
TR_PATTERN = re.compile(r'^tr$', re.I)
CELL_PATTERN = re.compile(r'^(td|th)$', re.I)


class BaseConverter(ABC):

    @abstractmethod
    def convertTab(self, table_tag: Tag) -> str:
        pass


class JsonConverter(BaseConverter):

    def convertTab(self, table_tag: Tag) -> str:
        """BS4 Table 태그를 읽어 JSON 문자열로 변환"""
        rows = table_tag.find_all(TR_PATTERN)
        if not rows:
            return json.dumps([])

        table_data = []
        header = []

        for i, row in enumerate(rows):
            cols = [
                ele.text.strip().replace('\n', ' ')
                for ele in row.find_all(CELL_PATTERN)
            ]
            if not cols or not any(cols):
                continue

            if i == 0 or not header:
                header = cols
            else:
                row_dict = {
                    header[j]
                    if j < len(header)
                    else f'col_{j}': cols[j]
                    for j in range(len(cols))
                }
                table_data.append(row_dict)

        return json.dumps(table_data, ensure_ascii=False)


class MarkdownConverter(BaseConverter):

    def convertTab(self, table_tag: Tag) -> str:
        """BS4 Table 태그를 읽어 Markdown 표 문자열로 변환"""
        rows = table_tag.find_all(TR_PATTERN)
        if not rows:
            return ''

        md_lines = []
        for i, row in enumerate(rows):
            cols = [
                ele.text.strip().replace('\n', ' ')
                for ele in row.find_all(CELL_PATTERN)
            ]
            if not cols or not any(cols):
                continue

            md_lines.append('| ' + ' | '.join(cols) + ' |')
            if i == 0:
                md_lines.append('| ' + ' | '.join(['---'] * len(cols)) + ' |')

        return '\n'.join(md_lines)


class ConverterFactory:

    _converters = {
        'md': MarkdownConverter,
        'json': JsonConverter,
    }

    @classmethod
    def get_converter(cls, tab_type: str) -> BaseConverter:
        converter_cls = cls._converters.get(tab_type.lower())
        if not converter_cls:
            raise ValueError(f'지원하지 않는 표 포맷: {tab_type}')
        return converter_cls()


def reconstruct_table(table_tag: Tag, tab_type: str) -> str:
    converter = ConverterFactory.get_converter(tab_type)
    return converter.convertTab(table_tag)


# =============================================================================
#  vendored — retriever/ingestion/dart/parsers.py
# =============================================================================
"""DART disclosure parsers (XML / HTML / PDF) -> section-tagged elements.

Vendored from the reference ``dart_preprocessing`` package.  Behaviour is
unchanged; only the import path, the removal of a script demo block, and a
lazy ``pdfplumber`` import (so an XML/HTML-only corpus needs no PDF stack)
differ from the original.  Each parser returns a list of
``{section_name, chunk_type, text_content, raw_json_content}`` dicts that
:mod:`retriever.ingestion.dart.chunker` turns into chunk rows.
"""

from abc import ABC, abstractmethod
from pathlib import Path
import re
from bs4 import BeautifulSoup


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


# =============================================================================
#  vendored — retriever/ingestion/dart/chunker.py
# =============================================================================
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


# =============================================================================
#  vendored — retriever/ingestion/dart/rows.py  (build_dart_chunk_rows)
# =============================================================================
"""DART corpus -> :class:`retriever.contracts.ChunkRow` list.

This is the single entry point for the disclosure-aware ingestion path.  It
ports ``dart_preprocessing/preprocesser.py``'s document loop -- join
``universe.csv`` + ``manifest.jsonl``, locate each document's source file,
parse, chunk -- but stops at the chunk-row contract instead of writing to a
DB.  Persistence is handled by the local SQLite/Chroma index builder after
this function returns the chunk-row contract.

The master-data join uses the standard library (``csv`` + ``json``); only
:mod:`retriever.ingestion.dart.parsers` pulls the heavy parsing dependencies.
"""


import csv
import json
import os
import warnings
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any


_SOURCE_SUFFIXES = {".xml": "xml", ".xhtml": "html", ".html": "html", ".htm": "html", ".pdf": "pdf"}

# universe.csv columns that manifest.jsonl already carries -- drop them from the
# universe side so the manifest value wins on join (matches preprocesser.py).
_UNIVERSE_DUPLICATE_COLUMNS = ("corp_name", "listed_name", "stock_code", "industry", "sector")


def _load_universe(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: dict[str, dict[str, Any]] = {}
        for record in reader:
            code = str(record.get("corp_code") or "").strip()
            if not code:
                continue
            rows[code] = {
                key: value
                for key, value in record.items()
                if key and key not in _UNIVERSE_DUPLICATE_COLUMNS
            }
    return rows


def load_master_records(corpus_dir: str | Path) -> list[dict[str, Any]]:
    """Return one merged metadata record per manifest document (manifest LEFT JOIN universe)."""

    root = Path(corpus_dir)
    universe = _load_universe(root / "universe.csv")
    records: list[dict[str, Any]] = []
    with (root / "manifest.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            doc = json.loads(line)
            code = str(doc.get("corp_code") or "").strip()
            merged = {**universe.get(code, {}), **doc}
            records.append(merged)
    return records


def _selected_doc_ids(doc_ids: Iterable[str] | None, selection_path: str | Path | None) -> set[str] | None:
    ids: set[str] = set()
    if doc_ids:
        ids.update(str(value).strip() for value in doc_ids if str(value).strip())
    if selection_path:
        payload = json.loads(Path(selection_path).read_text(encoding="utf-8"))
        for entry in payload.get("documents", []):
            if isinstance(entry, dict) and entry.get("doc_id"):
                ids.add(str(entry["doc_id"]).strip())
    return ids or None


def _find_source_file(document_dir: Path) -> tuple[Path, str] | None:
    if not document_dir.is_dir():
        return None
    for path in sorted(document_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES:
            return path, _SOURCE_SUFFIXES[path.suffix.lower()]
    return None


def _chunk_row(
    doc_meta: dict[str, Any],
    chunk: dict[str, Any],
    index: int,
    embedder: Callable[[str], Sequence[float]] | None,
) -> dict:
    rcept_no = str(chunk.get("rcept_no") or doc_meta.get("rcept_no") or "doc").strip()
    chunk_id = f"{rcept_no}_{index}"
    text = str(chunk.get("text_content") or "")
    raw_json = chunk.get("raw_json_content")

    metadata = {key: value for key, value in doc_meta.items() if key != "file_path"}
    metadata.update(
        {
            "section_name": chunk.get("section_name") or "",
            "chunk_type": chunk.get("chunk_type") or "",
            "basis": chunk.get("basis") or "",
            "rcept_no": rcept_no,
        }
    )

    row = {
        "id": chunk_id,
        "doc_id": str(doc_meta.get("doc_id") or ""),
        "chunk_id": chunk_id,
        "text": text,
        "source_path": str(doc_meta.get("file_path") or ""),
        "raw_json_content": None if raw_json is None else str(raw_json),
        "metadata": metadata,
    }
    if embedder is not None:
        row["embedding"] = [float(value) for value in embedder(text)]
    return row


def _rows_for_document(
    doc_meta: dict[str, Any],
    root_str: str,
    max_chunk_len: int,
    on_missing: str,
    embedder: Callable[[str], Sequence[float]] | None,
) -> list:
    """Locate + parse + chunk a single manifest document.

    Self-contained (only module-level helpers + stdlib/parsing deps) so it can
    run in a :class:`~concurrent.futures.ProcessPoolExecutor` worker.
    """

    root = Path(root_str)
    doc_id = str(doc_meta.get("doc_id") or "")
    located = _find_source_file(root / str(doc_meta.get("file_path") or ""))
    if located is None:
        if on_missing == "raise":
            raise FileNotFoundError(f"no source file for {doc_id} under {doc_meta.get('file_path')}")
        return []
    source_file, file_format = located

    parsed = parse_docs(str(source_file), file_format)
    chunks = split_to_chunks(parsed, doc_meta, max_chunk_len=max_chunk_len)
    return [_chunk_row(doc_meta, chunk, index, embedder) for index, chunk in enumerate(chunks)]


def _rows_for_document_task(task: tuple) -> list:
    """``ProcessPoolExecutor.map`` shim — unpacks the argument tuple."""

    return _rows_for_document(*task)


def _resolve_workers(max_workers: int | None) -> int:
    if max_workers is None:
        return os.cpu_count() or 1
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1 or None")
    return max_workers


def build_dart_chunk_rows(
    corpus_dir: str | Path,
    *,
    doc_ids: Iterable[str] | None = None,
    selection_path: str | Path | None = None,
    max_chunk_len: int = 1000,
    embedder: Callable[[str], Sequence[float]] | None = None,
    on_missing: str = "skip",
    max_workers: int | None = 1,
) -> list:
    """Parse + chunk the DART corpus into :class:`ChunkRow` records.

    ``corpus_dir`` must contain ``universe.csv``, ``manifest.jsonl`` and the
    ``raw/`` document tree.  Pass ``doc_ids`` or ``selection_path`` to index a
    subset; both unset indexes every manifest document.  ``on_missing`` is
    ``"skip"`` (default) or ``"raise"`` for documents whose source file is
    absent.

    ``max_workers`` controls the parse/chunk fan-out over documents (each one is
    independent): ``1`` (default) stays fully sequential, ``None`` uses every CPU
    core, ``N`` uses ``N`` processes.  It is a no-op when ``embedder`` is set,
    since embedders are generally not picklable — embed after the fact instead
    (``LocalHybridRetriever.write_rows`` / the Colab writer both do).
    """

    root = Path(corpus_dir)
    wanted = _selected_doc_ids(doc_ids, selection_path)
    records = load_master_records(root)

    todo = [
        record
        for record in records
        if wanted is None or str(record.get("doc_id") or "") in wanted
    ]
    seen_docs = len(todo)

    workers = _resolve_workers(max_workers)
    if workers != 1 and embedder is not None:
        warnings.warn(
            "build_dart_chunk_rows: max_workers is ignored when embedder is set; parsing sequentially",
            stacklevel=2,
        )
        workers = 1

    rows = []
    if workers == 1:
        for doc_meta in todo:
            rows.extend(_rows_for_document(doc_meta, str(root), max_chunk_len, on_missing, embedder))
    else:
        from concurrent.futures import ProcessPoolExecutor

        tasks = [(doc_meta, str(root), max_chunk_len, on_missing, None) for doc_meta in todo]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for doc_rows in pool.map(_rows_for_document_task, tasks, chunksize=8):
                rows.extend(doc_rows)

    if wanted is not None and seen_docs < len(wanted):
        missing = wanted - {str(record.get("doc_id") or "") for record in records}
        if missing and on_missing == "raise":
            raise KeyError(f"selected doc_ids not in manifest: {sorted(missing)}")
    return rows


def iter_dart_chunk_rows(
    corpus_dir: str | Path,
    *,
    doc_ids: Iterable[str] | None = None,
    selection_path: str | Path | None = None,
    max_chunk_len: int = 1000,
    on_missing: str = "skip",
    max_workers: int | None = 1,
) -> Iterator[tuple[str, list]]:
    """Yield ``(doc_id, [ChunkRow, ...])`` one manifest document at a time.

    Same join / parse / chunk as :func:`build_dart_chunk_rows`, but streamed so a
    caller can checkpoint after each document (e.g. the Colab builder writing to
    SQLite as it goes).  No ``embedder`` hook — embed downstream.  ``max_workers``
    fans the per-document parse out over processes exactly as
    :func:`build_dart_chunk_rows` does; results are still yielded in manifest
    order.
    """

    root = Path(corpus_dir)
    wanted = _selected_doc_ids(doc_ids, selection_path)
    records = load_master_records(root)
    todo = [
        record
        for record in records
        if wanted is None or str(record.get("doc_id") or "") in wanted
    ]

    workers = _resolve_workers(max_workers)
    if workers == 1:
        for doc_meta in todo:
            yield (
                str(doc_meta.get("doc_id") or ""),
                _rows_for_document(doc_meta, str(root), max_chunk_len, on_missing, None),
            )
    else:
        from concurrent.futures import ProcessPoolExecutor

        tasks = [(doc_meta, str(root), max_chunk_len, on_missing, None) for doc_meta in todo]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for doc_meta, doc_rows in zip(todo, pool.map(_rows_for_document_task, tasks, chunksize=8)):
                yield str(doc_meta.get("doc_id") or ""), doc_rows

    if wanted is not None:
        missing = wanted - {str(record.get("doc_id") or "") for record in records}
        if missing and on_missing == "raise":
            raise KeyError(f"selected doc_ids not in manifest: {sorted(missing)}")


__all__ = ["build_dart_chunk_rows", "iter_dart_chunk_rows", "load_master_records"]


# =============================================================================
#  chunk_index writer — SQLite chunk_index + Chroma, 체크포인트/재개 지원
#  (스키마는 retriever/local_store.py 와 1:1, Chroma id == chunk_id)
# =============================================================================
CHUNK_TABLE = "chunk_index"

_DDL = """CREATE TABLE IF NOT EXISTS chunk_index (
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
    text TEXT NOT NULL, source_path TEXT NOT NULL,
    corp_name TEXT, sector TEXT, doc_group TEXT, doc_subtype TEXT,
    base_year INTEGER, base_month INTEGER, rcept_dt TEXT, rcept_no TEXT,
    is_correction INTEGER, report_nm TEXT, basis TEXT, section_name TEXT,
    raw_json_content TEXT, metadata_json TEXT NOT NULL
)"""

# 진행 상태(설정 지문 · 파싱 완료 플래그)를 같은 SQLite 파일에 둔다 — 서빙은 무시.
_STATE_DDL = "CREATE TABLE IF NOT EXISTS _build_state (key TEXT PRIMARY KEY, value TEXT)"

_COLS = ["id", "doc_id", "chunk_id", "text", "source_path", "corp_name", "sector",
         "doc_group", "doc_subtype", "base_year", "base_month", "rcept_dt", "rcept_no",
         "is_correction", "report_nm", "basis", "section_name", "raw_json_content",
         "metadata_json"]

_INSERT = ("INSERT OR REPLACE INTO chunk_index (" + ", ".join(_COLS) + ") "
           "VALUES (" + ", ".join(":" + _c for _c in _COLS) + ")")


def _txt(value):
    text = str(value).strip() if value is not None else ""
    return text or None


def _int(value):
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _row_to_sql(row):
    """ChunkRow dict -> chunk_index 행 dict (모든 _COLS 키 채움)."""
    meta = dict(row.get("metadata") or {})
    doc_id = str(row.get("doc_id") or row["id"])
    chunk_id = str(row.get("chunk_id") or row["id"])
    raw = row.get("raw_json_content")
    raw = str(raw) if raw else None
    return {
        "id": str(row["id"]), "doc_id": doc_id, "chunk_id": chunk_id,
        "text": str(row.get("text") or ""), "source_path": str(row.get("source_path") or ""),
        "corp_name": _txt(meta.get("corp_name")), "sector": _txt(meta.get("sector")),
        "doc_group": _txt(meta.get("doc_group")), "doc_subtype": _txt(meta.get("doc_subtype")),
        "base_year": _int(meta.get("base_year")), "base_month": _int(meta.get("base_month")),
        "rcept_dt": _txt(meta.get("rcept_dt")), "rcept_no": _txt(meta.get("rcept_no")),
        "is_correction": 1 if meta.get("is_correction") else 0,
        "report_nm": _txt(meta.get("report_nm")), "basis": _txt(meta.get("basis")),
        "section_name": _txt(meta.get("section_name")), "raw_json_content": raw,
        "metadata_json": json.dumps(meta, ensure_ascii=False),
    }


def _chroma_meta(chunk_id, doc_id, source_path, metadata_json, raw):
    """chunk_index 행에서 Chroma 메타데이터를 복원한다 (write_chunk_index 원본과 동일 규칙)."""
    meta = json.loads(metadata_json) if metadata_json else {}
    cm = {key: ("" if val is None else val) for key, val in meta.items()}
    cm.update({"chunk_id": chunk_id, "doc_id": doc_id, "source_path": source_path})
    if raw is not None:
        cm["raw_json_content"] = raw
    return cm


def _connect(sqlite_path):
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(sqlite_path))
    cx.execute(_DDL)
    cx.execute(_STATE_DDL)
    cx.commit()
    return cx


def parse_to_sqlite(corpus_dir, sqlite_path, *, selection, max_chunk_len, workers, resume):
    """코퍼스를 파싱·청킹해 chunk_index 테이블에 '문서 단위'로 커밋한다.

    - resume=True: 이미 테이블에 들어간 doc_id 는 건너뛴다 → 런타임이 끊겨도 재개.
    - 설정(코퍼스/셀렉션/청크길이)이 지난 체크포인트와 다르면 테이블을 비우고 새로 시작.
    - 한 문서의 청크는 한 번의 executemany 로 통째로 커밋되므로, 중간에 죽어도
      '완결된 문서'까지는 항상 온전하다.
    """
    import time

    cx = _connect(sqlite_path)
    try:
        fingerprint = json.dumps(
            {"corpus": str(corpus_dir), "selection": selection or "", "max_chunk_len": max_chunk_len},
            sort_keys=True, ensure_ascii=False,
        )
        state = dict(cx.execute("SELECT key, value FROM _build_state").fetchall())

        if not resume:
            cx.execute("DELETE FROM chunk_index")
            cx.execute("DELETE FROM _build_state")
            cx.commit()
            state = {}
        elif state.get("fingerprint") not in (None, fingerprint):
            print(">>> 설정이 지난 체크포인트와 달라 chunk_index 를 비우고 새로 시작합니다.", flush=True)
            cx.execute("DELETE FROM chunk_index")
            cx.execute("DELETE FROM _build_state")
            cx.commit()
            state = {}

        cx.execute("INSERT OR REPLACE INTO _build_state VALUES ('fingerprint', ?)", (fingerprint,))
        cx.commit()

        done_docs = {r[0] for r in cx.execute("SELECT DISTINCT doc_id FROM chunk_index")}
        have_rows = cx.execute("SELECT COUNT(*) FROM chunk_index").fetchone()[0]
        if resume and state.get("parse_done") == "1":
            print(f"파싱 생략 — 체크포인트에 chunk {have_rows:,} / 문서 {len(done_docs):,} 이미 있음", flush=True)
            return

        sel_ids = _selected_doc_ids(None, selection)
        all_ids = [str(m.get("doc_id") or "") for m in load_master_records(corpus_dir)]
        remaining = [
            d for d in all_ids
            if d and d not in done_docs and (sel_ids is None or d in sel_ids)
        ]
        if not remaining:
            cx.execute("INSERT OR REPLACE INTO _build_state VALUES ('parse_done', '1')")
            cx.commit()
            print(f"파싱 완료(재개) — chunk {have_rows:,} / 문서 {len(done_docs):,}", flush=True)
            return

        print(f"파싱·청킹 중... 남은 문서 {len(remaining):,}개 "
              f"(완료 {len(done_docs):,}) · worker {workers}개", flush=True)
        pend, n_docs, n_rows, t0 = [], 0, 0, time.time()
        for _doc_id, doc_rows in iter_dart_chunk_rows(
            corpus_dir, doc_ids=remaining, max_chunk_len=max_chunk_len, max_workers=workers,
        ):
            pend.extend(_row_to_sql(r) for r in doc_rows)
            n_docs += 1
            n_rows += len(doc_rows)
            if len(pend) >= 2000 or n_docs % 200 == 0:
                if pend:
                    cx.executemany(_INSERT, pend)
                    cx.commit()
                    pend.clear()
                rate = n_docs / max(time.time() - t0, 1e-9)
                eta = (len(remaining) - n_docs) / max(rate, 1e-9) / 60
                print(f"  파싱 {n_docs:,}/{len(remaining):,} 문서 · 누적 chunk {n_rows:,} · "
                      f"ETA {eta:.1f}분", flush=True)
        if pend:
            cx.executemany(_INSERT, pend)
            cx.commit()
        cx.execute("INSERT OR REPLACE INTO _build_state VALUES ('parse_done', '1')")
        cx.commit()
        total_rows = cx.execute("SELECT COUNT(*) FROM chunk_index").fetchone()[0]
        print(f"파싱 완료 — 신규 chunk {n_rows:,} / 문서 {n_docs:,} · 전체 chunk {total_rows:,}", flush=True)
    finally:
        cx.close()


def embed_to_chroma(sqlite_path, chroma_dir, collection, embed_texts, *, resume,
                    batch=1000, checkpoint=None, checkpoint_every=300_000):
    """chunk_index 의 텍스트를 임베딩해 Chroma 에 배치 단위로 upsert 한다.

    - resume=True: Chroma 에 이미 있는 chunk_id 는 건너뛴다.
    - chunk_dir 은 '로컬 디스크' 여야 한다. Chroma 를 Google Drive(FUSE) 위에 두면
      배치마다 fsync 가 수십 배 느려져 A100 도 ~30 chunk/s 로 떨어진다.
    - checkpoint(): checkpoint_every chunk 마다(+ 마지막에) 호출 — 로컬 빌드를
      Drive 로 스냅샷하는 용도. 이게 곧 재개 지점이다.
    """
    import time
    import chromadb

    Path(chroma_dir).mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(sqlite_path))
    try:
        recs = cx.execute(
            "SELECT chunk_id, text, metadata_json, raw_json_content, doc_id, source_path "
            "FROM chunk_index ORDER BY id"
        ).fetchall()
    finally:
        cx.close()
    assert recs, "chunk_index 가 비어있음 — 파싱 단계 확인"
    total = len(recs)

    client = chromadb.PersistentClient(path=str(chroma_dir))
    if not resume:
        try:
            client.delete_collection(collection)
        except Exception:
            pass
    col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})
    done = set(col.get(include=[])["ids"]) if resume else set()

    todo = [r for r in recs if r[0] not in done]
    print(f"임베딩 대상 {len(todo):,} / 전체 {total:,} chunk (이미 {len(done):,} 완료)", flush=True)

    t0 = time.time()
    since_ckpt = 0
    for start in range(0, len(todo), batch):
        grp = todo[start:start + batch]
        ids = [r[0] for r in grp]
        texts = [r[1] for r in grp]
        metas = [_chroma_meta(r[0], r[4], r[5], r[2], r[3]) for r in grp]
        vectors = embed_texts(texts)
        col.upsert(ids=ids, embeddings=vectors, documents=texts, metadatas=metas)
        seen = min(start + batch, len(todo))
        since_ckpt += len(grp)
        rate = seen / max(time.time() - t0, 1e-9)
        eta = (len(todo) - seen) / max(rate, 1e-9) / 60
        print(f"  임베딩 {seen:,}/{len(todo):,} · {rate:.0f} chunk/s · ETA {eta:.1f}분", flush=True)
        if checkpoint is not None and since_ckpt >= checkpoint_every and seen < len(todo):
            print("  ↳ Drive 스냅샷...", flush=True)
            checkpoint()
            since_ckpt = 0

    stored = set(col.get(include=[])["ids"])
    wanted = {r[0] for r in recs}
    assert wanted <= stored, f"Chroma 누락 {len(wanted - stored):,} chunk"
    if checkpoint is not None:
        checkpoint()
    return {
        "rows": total,
        "documents": len({r[4] for r in recs}),
        "embedded_now": len(todo),
        "in_chroma": len(stored),
    }


# =============================================================================
#  실행
# =============================================================================
assert Path(CORPUS_DIR, "universe.csv").exists(), f"universe.csv 없음: {CORPUS_DIR}"
assert Path(CORPUS_DIR, "manifest.jsonl").exists(), f"manifest.jsonl 없음: {CORPUS_DIR}"

# 빌드는 '로컬 디스크'(/content) 에서 한다 — SQLite/Chroma 쓰기가 Drive(FUSE)보다
# 수십~수백 배 빠르다. 진행분은 주기적으로 OUT_DIR(Drive) 로 rsync 스냅샷하고,
# 재개 시 Drive 스냅샷을 로컬로 복원한다.
_DRIVE_OUT = OUT_DIR
_OUT = "/content/_chunk_index_build"
_SQLITE = Path(_OUT, "chunk_index.db")
_CHROMA = Path(_OUT, "chunk_index_chroma")
_WORKERS = PARSE_WORKERS or (multiprocessing.cpu_count() or 1)


def _snapshot():
    subprocess.run(["rsync", "-a", "--delete", "--inplace",
                    _OUT + "/", _DRIVE_OUT + "/"], check=False)


if not RESUME:
    shutil.rmtree(_OUT, ignore_errors=True)
    shutil.rmtree(_DRIVE_OUT, ignore_errors=True)
Path(_OUT).mkdir(parents=True, exist_ok=True)
Path(_DRIVE_OUT).mkdir(parents=True, exist_ok=True)

# 재개: Drive 스냅샷 → 로컬 복원 (로컬이 비었고 Drive 에 진행분이 있을 때만)
if RESUME and any(Path(_DRIVE_OUT).iterdir()) and not any(Path(_OUT).iterdir()):
    print("Drive 스냅샷 → 로컬 복원 중... (한 번, 수 분)", flush=True)
    subprocess.run(["rsync", "-a", "--inplace", _DRIVE_OUT + "/", _OUT + "/"], check=False)

# ── 1) 파싱·청킹 → SQLite (문서 단위 체크포인트) ──────────────────────────────
#   임베딩 모델(CUDA)보다 '먼저' 실행: ProcessPoolExecutor 가 fork 하는 시점에
#   부모가 CUDA 컨텍스트를 갖고 있지 않도록. 워커는 순수 CPU 파싱만 한다.
parse_to_sqlite(CORPUS_DIR, _SQLITE, selection=(SELECTION or None),
                max_chunk_len=MAX_CHUNK_LEN, workers=_WORKERS, resume=RESUME)
_snapshot()  # 파싱 결과(SQLite)를 Drive 로 1회 스냅샷

# ── 2) 임베딩 모델 로드 ([셀 1] 에서 설치·확인 완료 상태여야 함) ────────────────
import torch
from sentence_transformers import SentenceTransformer

_device = "cuda" if (USE_GPU and torch.cuda.is_available()) else "cpu"
print("=" * 64)
print("  임베딩 장치 :", _device.upper(),
      f"({torch.cuda.get_device_name(0)})" if _device == "cuda" else "")
print("=" * 64)
if USE_GPU and REQUIRE_GPU and _device != "cuda":
    raise SystemExit(
        "\n[정지] torch 가 GPU(CUDA)를 못 씁니다. [셀 1] 을 실행해 "
        "'GPU(torch CUDA) 확인 : OK' 가 나오는지 먼저 확인하세요.\n"
        "CPU 로 진행하려면 REQUIRE_GPU=False (체크포인트가 있어 중단돼도 재개됨).\n"
    )

_model = SentenceTransformer("intfloat/multilingual-e5-large", device=_device)  # 1024-dim
if FP16 and _device == "cuda":
    _model = _model.half()


def embed_texts(batch):
    # e5 문서 측 = "passage: " 프리픽스 + mean pooling + L2 정규화.
    # 서빙(retriever)의 query 임베딩("query: ") 과 같은 비대칭 쌍·같은 벡터 공간.
    vectors = _model.encode(
        ["passage: " + str(text) for text in batch],
        normalize_embeddings=True,
        batch_size=E5_BATCH_SIZE,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [vector.tolist() for vector in vectors]


# ── 3) 임베딩 → Chroma (로컬 빌드 · 주기적으로 Drive 스냅샷) ──────────────────
stats = embed_to_chroma(_SQLITE, _CHROMA, COLLECTION, embed_texts, resume=RESUME,
                        checkpoint=_snapshot, checkpoint_every=SNAPSHOT_EVERY)
print("OK", stats, "| collection =", COLLECTION)

# ── 4) zip & 다운로드 (로컬에서 압축 → Drive 사본) ───────────────────────────
zip_path = shutil.make_archive("/content/team-feature2_chunk_index", "zip", _OUT)
print(f"zip: {zip_path} ({os.path.getsize(zip_path) / 1e6:.1f} MB)")
if DRIVE_COPY:
    shutil.copy(zip_path, DRIVE_COPY)
    print("Drive 사본:", DRIVE_COPY)
files.download(zip_path)
