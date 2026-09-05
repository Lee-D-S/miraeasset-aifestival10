# 경쟁대회 테스트 케이스

`competition-round-template.json`을 복사해 회차별 케이스 파일을 만들고, 질문별로 기대 사실과 자동 비교할 문구를 작성합니다.

`required_phrases`와 `forbidden_phrases`는 기계적으로 판정할 최소 조건입니다. 표현이 달라도 같은 답으로 인정해야 하는 질문은 `expected_answer` 또는 `expected_facts`에 사람이 읽을 기준을 남기고, 필요한 숫자·기간·단위만 `required_phrases`에 넣습니다.

실패한 case에 `followups`를 넣으면, 실행기가 본 질문이 실패했을 때만 꼬리 질문을 서버에 보내고 각 응답을 같은 case 폴더에 저장합니다.
