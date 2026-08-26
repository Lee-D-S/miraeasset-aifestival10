from langgraph.graph import MessagesState
from typing import TypedDict, Annotated, List, Dict, Any
from langchain_core.messages import BaseMessage
import operator

from typing import Dict, Any, Optional
from langgraph.graph import MessagesState

class AgentState(MessagesState):
    # 1. 사용자 입/출력 관련
    question: str                         # 원문 질문
    answer: Optional[str]                 # 최종 생성된 답변
    context: Optional[str]                # DB/SQL 조회 결과 또는 컨텍스트 데이터
    
    # 2. Stage1 (질의 이해) 파이프라인 결과
    intent: Optional[Dict[str, Any]]      # Stage1에서 추출된 Intent JSON
    route: Optional[str]                  # ok, need_clarify, unanswerable, unsafe
    
    # 3. 제어 및 상태 관리
    retry_num: int                        # 재시도 횟수 (기본값 관리용)
    
    # Note: messages 필드는 MessagesState 상속으로 자동 포함되어 있음
