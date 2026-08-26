from pydantic import BaseModel, Field
from enum import IntEnum

class Operation(IntEnum):
    add = 1
    subract = 2
    multiply = 3
    divide = 4

class CalculateArgs(BaseModel):
    a:int = Field(description="첫 번째 피연산자")
    b:int = Field(description="두 번째 피연산자")
    operation:Operation = Field(description="연산자")
