# chunk_index SQL 인덱스 추가 (2026-09-04)

Retriever 검색의 메타데이터 필터(`filter_candidates`)가 매 질의마다 5.55M행·21GB
`chunk_index` 테이블을 전체 스캔하던 문제를 인덱스로 해결한 기록.

## 배경

`scripts/bench_retriever.py` 측정 결과:

- 전체 `/answer` 실행 시간의 **93~100%가 Retriever 검색**이었다.
- 그 검색 시간의 대부분은 `keyword_search`/`vector_search`가 아니라
  **`filter_candidates`의 SQL 한 줄**이었다.
  - SQL 필터: median 57,615ms, 콜드 p90 167,682ms
  - 키워드만/벡터만/합집합의 차이는 그 위에 얹힌 약 100ms 수준

원인: `chunk_index`에 기본키 autoindex 하나뿐이고, WHERE 절이 거는
`base_year` / `doc_group` / `doc_subtype` / `is_correction` / `rcept_dt` 중
인덱스가 있는 컬럼이 없었다. → SQLite 풀스캔.

## 추가한 인덱스 (로컬 DB, `data/local_db/chunk_index.db`)

```sql
PRAGMA temp_store_directory='<db가 있는 디렉터리>';
CREATE INDEX ix_ci_year_group_sub      ON chunk_index(base_year, doc_group, doc_subtype, is_correction);
CREATE INDEX ix_ci_rcept               ON chunk_index(rcept_dt);
CREATE INDEX ix_ci_corp_year_group_sub ON chunk_index(corp_name, base_year, doc_group, doc_subtype);
ANALYZE;
```

| 인덱스 | 대상 컬럼 | 용도 |
|---|---|---|
| `ix_ci_year_group_sub` | `(base_year, doc_group, doc_subtype, is_correction)` | 정기공시 질의의 등가/`IN` 조건 |
| `ix_ci_rcept` | `(rcept_dt)` | `rcept_from`/`rcept_to` 범위 조건 |
| `ix_ci_corp_year_group_sub` | `(corp_name, base_year, doc_group, doc_subtype)` | `corp_name` 정확 일치 질의 (아래 "남은 작업" 참고) |

- 파일 크기: 21,023,379,456 → 21,515,288,576 바이트 (+491 MB)
- 소요: 인덱스 3개 각 1~2분, 총 5분 미만
- `journal_mode`는 `delete`(롤백 저널) 유지. WAL 아님.
- 인덱스 이름은 로컬·서버 동일하게 `ix_ci_*`를 쓴다.

## 효과 (로컬, 워밍 상태)

| 질의 형태 | 이전 | 이후 | 사용 인덱스 |
|---|---:|---:|---|
| `base_year`+`doc_group`+`doc_subtype`+`is_correction` 모두 지정 | ~58,000ms | **292ms** | `ix_ci_year_group_sub` (4열 전부) |
| `삼성전자 2024`, `corp_name = '삼성전자'` (정확 일치) | ~19,000ms | **0.4ms** | `ix_ci_corp_year_group_sub` |
| `카카오 2025`, `corp_name = '카카오'` (정확 일치) | ~21,000ms | **21ms** | `ix_ci_corp_year_group_sub` |
| `카카오 2025`, `corp_name LIKE '%카카오%'`, `doc_subtype` 없음 | ~21,000ms | 2,300ms (콜드는 여전히 ~20s) | 없음 — 풀스캔 |

`EXPLAIN QUERY PLAN` 확인:

```
SEARCH chunk_index USING INDEX ix_ci_year_group_sub (base_year=? AND doc_group=? AND doc_subtype=? AND is_correction=?)
SEARCH chunk_index USING INDEX ix_ci_corp_year_group_sub (corp_name=? AND base_year=? AND doc_group=?)
```

## 코드 수정 — `corp_name` 정확 일치 (적용 완료)

`retriever/local_store.py`의 `build_manifest_where_and_params`에서 `corp_name`을
`LIKE '%이름%'` → `= :key` 로 바꿨다. 앞뒤 와일드카드는 어떤 인덱스도 못 쓴다.

```python
# 변경 전 (retriever/local_store.py:83 부근)
ors.append(f"corp_name LIKE :{key}")
params[key] = f"%{name}%"

# 변경 후
ors.append(f"corp_name = :{key}")
params[key] = name
```

- Interpreter의 `manifest_filter.corp_names`에는 이미 정규화된 정확한 회사명이 들어온다
  (예: "현대차" → "현대자동차", "KT" → "케이티", "엔씨소프트" → "NC").
- `EXPLAIN QUERY PLAN` 확인: `SEARCH chunk_index USING INDEX ix_ci_corp_year_group_sub (corp_name=? AND base_year=? AND doc_group=?)`.
- 실측: `카카오 2025` (`doc_subtype` 없음) 약 21,000ms → **28ms**.
- `tests/test_local_store.py`, `tests/test_retriever.py` 통과.
- `sector`, `report_nm`의 `LIKE`는 그대로 둔다(부분 매칭이 의도로 보임).

## 서버 적용 절차

read-only 마운트이므로 컨테이너 안에서는 불가. 호스트에서 실행한다.

```bash
docker compose stop app
which sqlite3 || sudo apt-get install -y sqlite3
df -h /data/local_db                     # 여유 1GB+ 확인

time sqlite3 /data/local_db/chunk_index.db <<'SQL'
PRAGMA journal_mode=DELETE;
PRAGMA temp_store_directory='/data/local_db';
CREATE INDEX IF NOT EXISTS ix_ci_year_group_sub      ON chunk_index(base_year, doc_group, doc_subtype, is_correction);
CREATE INDEX IF NOT EXISTS ix_ci_rcept               ON chunk_index(rcept_dt);
CREATE INDEX IF NOT EXISTS ix_ci_corp_year_group_sub ON chunk_index(corp_name, base_year, doc_group, doc_subtype);
ANALYZE;
SQL

docker compose start app
curl -i http://localhost:8000/ready
```

`scripts/build_chunk_index.py`가 빌드 시 이 인덱스를 만들도록 고치면
매번 수동 추가할 필요가 없다.

## 되돌리기

```sql
DROP INDEX IF EXISTS ix_ci_year_group_sub;
DROP INDEX IF EXISTS ix_ci_rcept;
DROP INDEX IF EXISTS ix_ci_corp_year_group_sub;
```

파일 내용(행 데이터)은 변경되지 않았다. 인덱스만 추가·삭제된다.

## 참고 — 문서/코드 불일치

`retriever/local_store.py:346`은 `ORDER BY id ASC`인데, CLAUDE.md에는
"candidates are ordered latest-disclosure-first (`rcept_dt DESC, rcept_no DESC`)"로
적혀 있다. 어느 쪽이 의도인지 확인 필요. `id ASC`가 맞다면 `ix_ci_rcept`는
범위 조건용으로만 쓰인다.
