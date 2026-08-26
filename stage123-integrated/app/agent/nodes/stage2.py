from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate
# clova
from langchain_naver import ChatClovaX
from langgraph.prebuilt import ToolNode

from state import AgentState
from app.tools.hybrid_db_tools import dart_hybrid_search_tool
from app.tools.math_tools import calculator

load_dotenv()

tools = [dart_hybrid_search_tool, calculator]
llm = ChatClovaX(model="HCX-005", temperature=0.1)


def chatbot(state: AgentState):
    """사용자의 질문을 분석하여 하이브리드 검색 툴에 전달할 인자(Arguments)를 추출하고 Tool Call을 생성합니다.
    """
    intent = state["intent"]
    question = state["question"]

    system_prompt = f"""당신은 공시 및 재무 데이터 분석 전문 에이전트입니다.
    사용자의 질문에 대해 이미 파싱된 핵심 조건(Intent)은 다음과 같습니다:
    - 기업 목록: {intent.get('corps')}
    - 공시/지표: {intent.get('metric')} ({intent.get('doc')})
    - 대상 연도/기간: {intent.get('time')}
    - 재무 기준: {intent.get('doc', {}).get('basis', '연결')}

    제공된 Tool(SQL 호출 Tool 등)을 활용하여 정확한 데이터를 조회하고 답변하세요.\n
    - 검색 결과가 없더라도 임의로 다른 기업을 검색하지 마시고 조회 날짜 범위를 더 넓혀보세요.\n
    - 질문이 특정 기업명이 아니라 '~업종/섹터 기업 중'처럼 업종 단위로 여러 기업을 비교·나열하라고 요구하면,
    corp_name을 비워두고 sector 인자(예: '2차전지')에 업종명을 넣어 해당 업종의 기업들을 함께 조회하세요.\n
    - 질문에 '~를 제외하고'처럼 특정 기업을 배제하라는 조건이 있으면 exclude_corp_name 인자에 그 기업명을 넣으세요."""


    print("----- [CHATBOT] -----")
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=question)
    ]

    llm_with_tools =llm.bind_tools(tools)
    response = llm_with_tools.invoke(messages)

    return {"messages": [response], "question": messages[-1].content}

# retreiver
tool_node = ToolNode(tools=tools)


def context_organizer(state: AgentState):
    """검색된 결과를 정리합니다."""
    print("----- [CONTEXT ORGANIZER] -----")
    # 1. state["messages"]의 뒤에서부터 ToolMessage 들의 content를 추출하여 합침
    tool_contents = []
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage):
            tool_contents.append(msg.content)
        else:
            # ToolMessage 영역이 끝나면 중단
            break

    # 최신 순서대로 정렬 후 하나의 텍스트로 병합
    context = "\n\n".join(reversed(tool_contents)) if tool_contents else state.get("context", "")

    if not context.strip():
        context = "검색된 공시 데이터가 없습니다."

    context_organizer_prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """당신은 검색증강생성(RAG)을 위한 검색문서를 정리하는 전문가입니다.
아래의 검색된 결과 문서를 확인하고, LLM이 해당 문서를 정리된 형태로 참고할 수 있도록
문서의 불필요한 공백 등을 삭제하거나 정렬을 다시하여 정리된 형태로 반환해주세요.
내용을 삭제하는 것을 최소로 합니다. 페이지 번호 정보를 절대 삭제하지 마세요.""",
        ),
        (
            "user",
            """
검색 결과: {context}
""",
        ),
    ])

    context_organizer = context_organizer_prompt | llm
    organized_context = context_organizer.invoke({"context": context})

    return {
        "context": organized_context.content,
        "messages": [AIMessage(content=organized_context.content)],
    }


def query_transformer(state: AgentState):
    """검색 실패 시 기존 질문 및 검색 조건(인자)을 분석하여 하이브리드 검색에 최적화된 새로운 인자(Arguments)를 재생성합니다."""
    print("----- [TRANSFORM QUERY] -----")
    question = state["question"]
    context = state.get("context", "")

    system = "당신은 RAG 검색 쿼리 최적화 전문가입니다. 불필요한 설명 없이 검색 키워드만 짧게 작성하세요.\
        검색 실패 시 기존 질문 및 검색 조건(인자)을 분석하여 하이브리드 검색에 최적화된 새로운 인자(Arguments)를 재생성합니다."
    re_write_prompt = ChatPromptTemplate.from_messages([
        ("system", system),
        ("user", "초기 질문: {question}\n이전 맥락: {context}\n\n검색용 핵심 키워드:"),
    ])

    question_rewriter = re_write_prompt | llm
    better_question = question_rewriter.invoke(
        {"question": question, "context": context}
    )
    new_query_text = better_question.content

    prev_tool_args = {}
    for msg in reversed(state["messages"]):
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            prev_tool_args = msg.tool_calls[0]["args"].copy()
            break

    # 쿼리 문구 갱신 및 불필요한 날짜 제한 해제
    clean_query = new_query_text.replace("-", "").replace("\n", " ").strip()
    prev_tool_args["query"] = clean_query
    prev_tool_args.pop("start_date", None)

    new_tool_calls = [{
        "name": "dart_hybrid_search_tool",
        "args": prev_tool_args,
        "id": "retry_tool_call"
    }]

    # tool_calls를 가진 AIMessage 객체 생성
    transformed_message = AIMessage(
        content=f"개선된 쿼리로 재검색을 시도합니다: {new_query_text}",
        tool_calls=new_tool_calls,
    )

    retry_cnt = state.get("retry_num", 0) + 1

    return {
        "question": new_query_text,
        "messages": [transformed_message],
        "retry_num": retry_cnt,
    }


def response_generator(state: AgentState):
    """검색된 문서와 질문을 기반으로 답변을 생성합니다."""
    print("----- [GENERATE] -----")
    question = state["question"]
    context = state["context"]
    retry_num = state.get("retry_num", 0)

    if retry_num >= 3:
        rag_prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """당신은 검색된 문서를 통해 해결할 수 있는 질문을 추출하는 어시스턴트입니다.
사용자가 해결하고자 한 질문이 있었으나 검색 컨텍스트가 충분하지 않은 상황이므로, 주어진 검색 결과 내에서 답변할 수 있는 질문을 새롭게 작성해 나열하세요.
사용자에게 질문에 대한 답변을 하지 못함에 양해를 구하고, 다른 질문의 기회와 선택지를 제공하는 친절한 가이드를 하세요.
""",
            ),
            (
                "user",
                "질문: {question} \n\n검색 결과: {context} \n\n답변:",
            ),
        ])
    else:
        rag_prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """당신은 질문-답변 업무를 수행하는 어시스턴트입니다. 검색된 컨텍스트를 사용하여 질문에 답변하세요.
답변을 모르는 경우, 모른다고 말하세요.
답변은 간결하게 작성하고, 반드시 답변의 출처(페이지 번호)를 함께 명시해주세요.
""",
            ),
            (
                "user",
                "질문: {question} \n\n검색 결과: {context} \n\n답변:",
            ),
        ])

    rag_chain = rag_prompt | llm
    response = rag_chain.invoke({"question": question, "context": context})
    return {
        "question": question,
        "answer": response.content,
        "messages": [response],
    }
