"""Unit tests for the tool registry and calculator."""

import ast
from pathlib import Path

import pytest

from app.tools import (
    CalculatorTool,
    DuplicateToolError,
    ToolRegistry,
    ToolResult,
    UnknownToolError,
)


def test_registry_registers_and_lists_a_tool() -> None:
    registry = ToolRegistry()
    tool = CalculatorTool()

    registry.register(tool)

    assert registry.get("calculator") is tool
    assert registry.list_tools() == (tool,)


def test_registry_rejects_duplicate_registration() -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())

    with pytest.raises(DuplicateToolError, match="already registered"):
        registry.register(CalculatorTool())


def test_registry_rejects_unknown_tool_lookup() -> None:
    registry = ToolRegistry()

    with pytest.raises(UnknownToolError, match="web_search"):
        registry.get("web_search")


@pytest.mark.parametrize(
    ("arguments", "output"),
    [
        ({"operation": "add", "left": 2, "right": 3}, "5"),
        ({"operation": "subtract", "left": 5, "right": 2}, "3"),
        ({"operation": "multiply", "left": 125, "right": 48}, "6000"),
        ({"operation": "divide", "left": 10, "right": 4}, "2.5"),
    ],
)
def test_calculator_operations(arguments: dict[str, object], output: str) -> None:
    result = CalculatorTool().execute(arguments)

    assert result == ToolResult(success=True, output=output)


def test_calculator_rejects_division_by_zero() -> None:
    result = CalculatorTool().execute(
        {"operation": "divide", "left": 1, "right": 0}
    )

    assert result.success is False
    assert result.error == "Cannot divide by zero."


def test_calculator_rejects_an_invalid_operation() -> None:
    result = CalculatorTool().execute(
        {"operation": "power", "left": 2, "right": 3}
    )

    assert result.success is False
    assert result.error is not None
    assert "operation" in result.error


def test_calculator_rejects_invalid_numbers() -> None:
    result = CalculatorTool().execute(
        {"operation": "add", "left": "five", "right": 1}
    )

    assert result.success is False
    assert result.error is not None
    assert "left" in result.error


def test_calculator_does_not_evaluate_arbitrary_code() -> None:
    source = Path("app/tools/calculator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"eval", "exec"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden


def test_tool_package_does_not_import_the_openai_sdk() -> None:
    for path in Path("app/tools").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = _imported_modules(node)
            assert all(not module.startswith("openai") for module in modules)
            assert all(not module.startswith("app.llm") for module in modules)


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""]
    return []
