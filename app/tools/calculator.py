"""Arithmetic tool.

Operations are explicit functions. This module does not evaluate arbitrary
text from the model.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.tools.base import Tool, ToolResult


class ArithmeticOperation(str, Enum):
    """Operations the calculator can perform."""

    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"


class CalculatorInput(BaseModel):
    """Two numbers and one named operation."""

    operation: ArithmeticOperation
    left: float = Field(description="The first number.")
    right: float = Field(description="The second number.")

    @field_validator("left", "right", mode="before")
    @classmethod
    def numbers_must_be_numeric(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        return value


class CalculatorTool(Tool):
    """Add, subtract, multiply, or divide two numbers."""

    name = "calculator"
    description = (
        "Perform addition, subtraction, multiplication, or division on two "
        "numbers. Arguments: operation (add, subtract, multiply, divide), "
        "left, and right."
    )
    input_model = CalculatorInput

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            data = CalculatorInput.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(success=False, error=_validation_error(exc))
        try:
            value = _calculate(data.operation, data.left, data.right)
        except ZeroDivisionError:
            return ToolResult(success=False, error="Cannot divide by zero.")
        return ToolResult(success=True, output=_format_number(value))


def _calculate(operation: ArithmeticOperation, left: float, right: float) -> float:
    if operation is ArithmeticOperation.ADD:
        return left + right
    if operation is ArithmeticOperation.SUBTRACT:
        return left - right
    if operation is ArithmeticOperation.MULTIPLY:
        return left * right
    if right == 0:
        raise ZeroDivisionError
    return left / right


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


def _validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "input"
        parts.append(f"{location}: {error['msg']}")
    return "Invalid calculator input. " + "; ".join(parts)
