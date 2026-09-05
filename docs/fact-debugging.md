# 범위형 Fact 진단

실제 배포 DB에서 사업부문·부문·제품군 등 범위형 질문의 Fact 선택을 확인하려면 앱 환경 변수에 다음을 설정한다.

```bash
DIS164_DEBUG_FACTS=true
```

설정하면 Stage3 로그에 매칭 후보, numeric/table 우선순위 후보, 최종 선택 Fact의 문서 ID·종류·값·집계 범위·표 문맥이 기록된다. 기본값은 `false`이며, 진단이 끝나면 해제한다.

범위형 질문뿐 아니라 모든 답변 질문에서 Stage3 입력 Fact 전체도 `answer_input` 단계로 기록된다. 기본 기록 상한은 500개이며, 필요하면 `DIS164_DEBUG_FACT_LIMIT`으로 조정한다.

```bash
docker compose logs --tail=300 app
```

로그를 통해 올바른 Fact가 검색 후보에 들어왔는지, 범위 판정에서 탈락했는지, 최종 선택 단계에서 잘못 선택됐는지를 구분할 수 있다.
