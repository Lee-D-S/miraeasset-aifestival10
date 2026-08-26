# 경로
import os
import sys
from pathlib import Path
## 현재 파일(preprocesser.py) 기준 프로젝트 루트를 모듈 검색 경로에 등록
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.config import SQLITE_URL

import pandas as pd
# 내부 임포트
from chunker import split_to_chunks
from parsers import parse_docs
from app.agent.db import vectorstore

from langchain_core.documents import Document
# from langchain_naver import ClovaXEmbeddings

from dotenv import load_dotenv

from sqlalchemy import create_engine

# .env 파일에서 환경 변수 로드
load_dotenv()

def load_master_data(base_dir: str):
    """universe.csv 와 manifest.jsonl 조인하여 통합 문서 메타데이터 구축"""
    universe_df = pd.read_csv(
        os.path.join(base_dir, 'universe.csv'),
        dtype={'corp_code': str, 'stock_code': str},
    )
    manifest_df = pd.read_json(
        os.path.join(base_dir, 'manifest.jsonl'),
        lines=True,
        dtype={'corp_code': str, 'stock_code': str},
    )
     # 중복 컬럼들 제외
    common_cols = [
        "corp_name",
        "listed_name",
        "stock_code",
        "industry",
        "sector",
    ]  # corp_code 제외 중복 컬럼
    universe_df_clean = universe_df.drop(columns=common_cols)

    # universe 전체를 manifestp에 left join
    merged_df = pd.merge(
        manifest_df,
        universe_df_clean,
        on='corp_code',
        how='left',
    )
    return merged_df


def process_doc_to_db():
    base_directory = PROJECT_ROOT / 'data' / '3.gongsi' / 'corpus'

    master_df = load_master_data(base_directory) # 매니페스트는 한 행당 하나의 문서
    master_docs = master_df.to_dict(orient='records') # 한 행 당 한 개의 '딕셔너리(키는 컬럼명)의 리스트'로 만듦.

    print(f'Total Documents to Process: {len(master_docs)}')

    df_item_list = []
    BATCH_SIZE = 50  # 문서 파일 (defalt: 50개 문서파일) 배치단위 수정

    # DB engine
    rdb_engine = create_engine(SQLITE_URL)

    # debugging
    # debugging 집계 카운터
    stats = {
        "path_not_found": 0,
        "no_target_files": 0,
        "empty_chunks": 0,
        "success_docs": 0,
    }

    # 임베딩 모델 인스턴스
    #embedding_model = OpenAIEmbeddings(model='text-embedding-3-small')
    # embedding_model = embedding_model
    
    for doc_idx, doc_meta in enumerate(master_docs): # 같은 문서에서 온 청크는 같은 메타데이터(doc_meta)를 붙임.
        #print("사용 가능한 keys:", doc_meta.keys())
        rel_path = doc_meta['file_path']  # 매니페스트.jsonl에 써있던 컬럼값
        full_dir_path = Path(base_directory) / rel_path
        # debugging log
        if doc_idx == 0:
            print(f"[DEBUG] base_directory: {base_directory}")
            print(f"[DEBUG] rel_path: {rel_path}")
            print(f"[DEBUG] full_dir_path: {full_dir_path}")

        if not full_dir_path.exists():
            stats["path_not_found"] += 1
            if doc_idx < 10:
                print(f"[{doc_idx}] 경로 없음: {full_dir_path}")
            continue

        all_files = list(full_dir_path.glob("*"))
        target_files = [
            f
            for f in all_files
            if f.suffix.lower() in [".xml", ".html", ".pdf", ".xhtml"]
        ]
        if not target_files:
            stats["no_target_files"] += 1
            if doc_idx < 10:
                filenames = [f.name for f in all_files]
                print(
                    f"[{doc_idx}] 대상 파일 없음: {full_dir_path} (폴더 내 파일: {filenames})"
                )
            continue

        # 파일 확장자 분리
        target_file = target_files[0]
        file_ext = target_file.suffix.replace('.', '').lower() 

        # 1. 파싱
        parsed_elements = parse_docs(str(target_file), file_ext)

        # 2. 청킹 및 메타데이터 합성
        doc_chunks = split_to_chunks(parsed_elements, doc_meta) # [chunk1, chunk2, chunk3, ...]
        if not doc_chunks:
            stats["empty_chunks"] += 1
            if doc_idx < 10:
                print(
                    f"[{doc_idx}] 청크 생성 실패: {target_file.name}"
                )
            continue
        ## chunk id를 rdb, vectordb에 둘 다 추가해서 양쪽을 연동해서 사용할 수 있도록.
        for c_idx, chunk in enumerate(doc_chunks):
            # 접수번호(rcept_no) + 순번(c_idx) 조합 (예: 20240516000123_0, 20240516000123_1)
            rcept_no = chunk.get('rcept_no', 'doc_id') # rcept_no없으면 doc_id로
            chunk['chunk_id'] = f'{rcept_no}_{c_idx}'
        df_item_list.extend(doc_chunks) # 추가 청크들을 꺼내서 리스트에 잘 넣어줌:  [chunk1, chunk2, ..., chunk_n]

        # vectorDB instance 한 번 intialized
        # vectorstore = vectorstore
        # 3. 배치 처리 (DataFrame 생성 및 DB 적재)
        if (doc_idx + 1) % BATCH_SIZE == 0 or (doc_idx + 1) == len(master_docs):
            if df_item_list:
                batch_df = pd.DataFrame(df_item_list)
                print(
                    f'[{doc_idx + 1}/{len(master_docs)}] Processed Batch Chunks: {len(batch_df)}'
                )

                ## 3-1. RDB 
                # SQLite db생성 (test)
                
                batch_df.to_sql(
                    name='finance_db',
                    con=rdb_engine,  # SQLAlchemy engine
                    if_exists='append',
                    index=False,
                )
                print(f"{doc_idx} sqlite db에 저장완료")
                # 실제로는 아래 코드로 교체.
                # from sqlalchemy import create_engine

                # # PostgreSQL 접속 정보 (postgresql://사용자:비밀번호@호스트:포트/DB이름)
                # db_url = 'postgresql://postgres:password@localhost:5432/dart_db'
                # rdb_engine = create_engine(db_url)

                # # 데이터 적재
                # batch_df.to_sql(name='chunks', con=rdb_engine, if_exists='append', index=False)

                ## 3-2. 벡터DB
                docs = []
                for row in batch_df.to_dict(orient='records'):
                    # 1. 원본 텍스트 추출
                    page_content = row.pop('text_content')

                    # 2. 필터링용 Metadata 생성 - None 체크, metadata dictionary 구성
                    metadata = {
                        k: (v if v is not None else '')
                        for k, v in row.items()
                        if k != 'raw_json_content'
                    }

                    if row.get('raw_json_content'):
                        metadata['raw_json_content'] = str(row['raw_json_content'])

                    docs.append(Document(page_content=page_content, metadata=metadata))

                # ChromaDB 저장
                # 30만 토큰 초과 방지를 위해 docs를 50개 단위로 쪼개서 add_documents 호출
                SUB_BATCH_SIZE = 500
                for i in range(0, len(docs), SUB_BATCH_SIZE):
                    sub_docs = docs[i : i + SUB_BATCH_SIZE]
                    vectorstore.add_documents(sub_docs)
                            
                print(f"{doc_idx} chroma db에 저장완료")
                df_item_list = []  # 배치 후 메모리 비우기

# 최종 요약 보고서 출력
    print("=" * 60)
    print("[전처리 진단 요약 리포트]")
    print(f"- 전체 대상 문서 수: {len(master_docs)}")
    print(f"- 파싱/청킹 성공 문서 수: {stats['success_docs']}")
    print(f"- [스킵 원인1] 폴더 경로 부재: {stats['path_not_found']}")
    print(f"- [스킵 원인2] 지원 파일(.xml/.html 등) 없음: {stats['no_target_files']}")
    print(f"- [스킵 원인3] 청크 0개 생성: {stats['empty_chunks']}")
    print("=" * 60)

if __name__ == '__main__':
    process_doc_to_db()
