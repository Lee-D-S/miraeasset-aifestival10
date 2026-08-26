# from pydantic import Field, BaseModel
# from langchain.tools import tool


# class ExecPython(BaseModel):
#     code: str = Field(description="임포트 구문을 포함한 코드 블록")

# class WriteFile(BaseModel):
#     file_path: str = Field(description="생성/수정할 파일의 경로")
#     content: str = Field(description="파일에 작성할 내용")

# @tool(args_schema=ExecPython)
# def python_exec_tool(
#     code:str
# ) -> str:
#     """
#     파이썬 코드를 실행하는 도구입니다. 만약 코드 실행에 실패하면 에러 메시지를 반환합니다.
#     실행 결과를 확인하고 싶다면 `print(...)`를 사용하여 출력해야 합니다.

#     Args:
#         imports: 임포트 구문
#         code: 임포트 구문을 제외한 코드 블록

#     Returns:
#         실행 결과 또는 에러 메시지
#     """ # [ 2 ]
#     # [ 3 ] Check imports
#     # try:
#     #     exec(imports)
#     # except Exception as e:
#     #     return f"모듈을 임포트하는 데 실패했습니다. ERROR: {repr(e)}"


#     # [ 4 ] Check execution
#     try:
#         exec(code)
#     except Exception as e:
#         return f"코드 실행에 실패했습니다. ERROR: {repr(e)}"

#     result_str = f"성공적으로 코드가 실행되었습니다. :\n```python\n{code}\n```"
#     # 근데 이거 토큰 낭비 아닌/ 아 이건 파이썬 print구나.
#     return result_str


# @tool(args_schema=WriteFile)
# def file_write_tool(
#     file_path:str, content:str
# ) -> str:
#     """
#     파일을 생성하거나 내용을 작성하는 도구입니다.

#     Args:
#         file_path: 생성/수정할 파일의 경로
#         content: 파일에 작성할 내용

#     Returns:
#         성공/실패 메시지
#     """
#     try:
#         with open(file_path, 'w', encoding='utf-8') as f:
#             f.write(content)
#         return f"파일 '{file_path}'에 성공적으로 작성했습니다."
#     except Exception as e:
#         return f"파일 작성 실패: {repr(e)}"