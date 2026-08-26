from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field
from langchain_naver import ChatClovaX
from langchain_core.prompts import ChatPromptTemplate

from app.clova_config import DEFAULT_CLOVA_CHAT_MODEL, get_clova_api_key
from app.agent.state import AgentState

llm = ChatClovaX(
    model=DEFAULT_CLOVA_CHAT_MODEL,
    temperature=0.1,
    api_key=get_clova_api_key(),
    disabled_params={"parallel_tool_calls": None},
)


def route_decision(state: AgentState) -> str:
    """Stage1이 매긴 route를 그래프 분기로 변환합니다."""
    print("----- ROUTE DECISION -----")
    route = state.get("route", "unanswerable")
    if route == "ok":
        return "chatbot_node"
    elif route == "need_clarify":
        return "clarify_node"
    elif route == "unsafe":
        return "unsafe_node"
    # unanswerable 및 알 수 없는 값은 모두 unanswerable_node로 보낸다.
    return "unanswerable_node"


class Grade(BaseModel):
    """관련성 확인을 위한 점수 스키마"""

    binary_score: str = Field(description="문서가 질문과 관련이 있는지 여부, 'yes' 또는 'no'")


def decide_to_generate(state: AgentState) -> str:
    """검색된 컨텍스트가 질문과 관련이 있는지 평가하여 다음 노드를 결정합니다."""
    print("----- ASSESS GRADED DOCUMENTS -----")
    question = state["question"]
    context = state.get("context", "")
    retry_num = state.get("retry_num", 0)

    # 1. 안전장치: 재시도 횟수가 2회 이상이면 더 이상 쿼리를 바꾸지 않고 답변 생성으로 강제 이동
    if retry_num >= 2:
        print(f"---MAX RETRY REACHED ({retry_num}), FORCE GENERATE RESPONSE---")
        return "response_generator"

    # 2. 컨텍스트가 비어있는 경우 query_transformer로 이동
    if not context or "검색된 공시 데이터가 없습니다" in context:
        print("---DECISION: RETRIEVED DOCUMENT IS EMPTY, TRANSFORM QUERY---")
        return "query_transformer"

    # 3. 문서 유용성 평가 (ClovaX 호환 일반 텍스트 기반)
    system = """당신은 검색된 문서가 사용자의 질문에 답변하기에 유용한 정보인지 평가하는 평가자입니다.
검색된 문서가 질문과 관련이 있고 조금이라도 답변에 도움이 된다면 'yes', 전혀 상관없거나 완전한 오답이면 'no'라고만 답하세요.
다른 설명 없이 오직 'yes' 또는 'no'로만 답변하세요."""

    grade_prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("user", "질문: {question}\n\n검색된 문서:\n{context}\n\n평가(yes/no):"),
    ])

    evaluator_chain = grade_prompt | llm | StrOutputParser()
    
    try:
        response = evaluator_chain.invoke({"question": question, "context": context}).strip().lower()
        
        if "yes" in response:
            print("---DECISION: DOCS RELEVANT, GENERATE RESPONSE---")
            return "response_generator"
        else:
            print("---DECISION: DOCS NOT RELEVANT, TRANSFORM QUERY---")
            return "query_transformer"
            
    except Exception as e:
        print(f"---EVALUATION ERROR ({e}): FALLBACK TO RESPONSE GENERATOR---")
        return "response_generator"


class GradeHallucinations(BaseModel):
    """생성된 답변의 환각 여부를 판단하기 위한 점수 스키마"""

    binary_score: str = Field(
        description="답변이 사실에 근거하고 있는지 여부, 'yes' 또는 'no'"
    )

def check_hallucinations(state):
    """
    생성된 답변이 문서에 근거하고 질문에 답하는지 판단합니다.
    """

    print("----- CHECK HALLUCINATIONS -----")
    question = state["question"]
    context = state["context"]
    answer = state["answer"]

    structured_llm = llm.with_structured_output(GradeHallucinations)

    system = """당신은 LLM이 생성한 답변이 검색된 사실들에 근거하고 있는지 평가하는 평가자입니다.
    'yes' 또는 'no'의 이진 점수를 제공하세요. 'yes'는 답변이 사실들에 근거하고 있음을 의미합니다."""
    hallucination_prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system),
            ("user", "질문: {question} \n\n 사실 집합: \n\n {context} \n\n LLM 생성 답변: {generation}"),
        ]
    )

    hallucination_grader = hallucination_prompt | structured_llm

    score = hallucination_grader.invoke(
        {"question": question, "context": context, "generation": answer}
    )
    grade = score.binary_score
    if grade == "yes":
        print("---DECISION: GENERATION IS GROUNDED IN DOCUMENTS---")
        return "support"
    else:
        print("---DECISION: GENERATION IS NOT GROUNDED IN DOCUMENTS, RE-TRY---")
        return "not supported"
