try:
    from langchain.tools import tool
except ImportError:  # langchain-classic is optional for the integration runtime
    from langchain_core.tools import tool
from pydantic import BaseModel
from app.schemas.calculator_args import CalculateArgs, Operation


@tool(args_schema=CalculateArgs)
def calculator(a: int, b: int, operation: Operation) -> str:
    """
    간단한 계산기 도구입니다.

    Args:
        a: 첫 번째 숫자
        b: 두 번째 숫자
        operation: 연산 종류 (add, subtract, multiply, divide)
    """
    if operation == Operation.add:
        result = a + b
    elif operation == Operation.subract:
        result = a - b
    elif operation == Operation.multiply:
        result = a * b
    elif operation == Operation.divide:
        result = a / b if b != 0 else "0으로 나눌 수 없습니다"
    else:
        return f"지원하지 않는 연산: {operation}"

    return f"{a} {operation} {b} = {result}"


# 도구 목록
tools = [calculator]
