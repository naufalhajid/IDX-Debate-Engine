import pytest
from pydantic import BaseModel

from core.tool_registry import (
    DEFAULT_REGISTRY,
    ToolExecutionRecord,
    ToolRegistry,
    ToolSpec,
    execute_tool,
)


class ExampleInput(BaseModel):
    value: int


class ExampleOutput(BaseModel):
    value: int


def example_tool(input_data: ExampleInput) -> ExampleOutput:
    return ExampleOutput(value=input_data.value)


def test_register_and_retrieve_tool() -> None:
    registry = ToolRegistry()
    spec = ToolSpec(
        name="ExampleTool",
        description="Example typed tool.",
        input_model=ExampleInput,
        output_model=ExampleOutput,
        callable=example_tool,
    )

    registry.register(spec)

    retrieved = registry.get("ExampleTool")
    assert retrieved is spec
    assert retrieved.input_model is ExampleInput
    assert retrieved.output_model is ExampleOutput
    assert retrieved.callable(ExampleInput(value=3)) == ExampleOutput(value=3)


def test_get_unknown_tool_raises_key_error() -> None:
    registry = ToolRegistry()

    with pytest.raises(KeyError):
        registry.get("MissingTool")


def test_list_tools_returns_default_tool_names() -> None:
    assert DEFAULT_REGISTRY.list_tools() == [
        "FetchPriceTool",
        "FetchFundamentalsTool",
        "FairValueTool",
        "PositionSizeTool",
    ]


def test_execute_tool_validates_input_output_and_records_success() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="ExampleTool",
            description="Example typed tool.",
            input_model=ExampleInput,
            output_model=ExampleOutput,
            callable=example_tool,
        )
    )
    ledger: list[ToolExecutionRecord] = []

    result = execute_tool(
        registry,
        "ExampleTool",
        {"value": 7},
        run_id="run-1",
        ledger=ledger,
    )

    assert result.status == "success"
    assert result.run_id == "run-1"
    assert result.input_payload == {"value": 7}
    assert result.output_payload == {"value": 7}
    assert result.error is None
    assert ledger == [result]


def test_execute_tool_unknown_tool_returns_failure() -> None:
    result = execute_tool(ToolRegistry(), "MissingTool", {"value": 1})

    assert result.status == "failed"
    assert result.error == "Unknown tool: MissingTool"


def test_execute_tool_invalid_input_fails_before_callable() -> None:
    called = False

    def should_not_run(_input: ExampleInput) -> ExampleOutput:
        nonlocal called
        called = True
        return ExampleOutput(value=1)

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="ExampleTool",
            description="Example typed tool.",
            input_model=ExampleInput,
            output_model=ExampleOutput,
            callable=should_not_run,
        )
    )

    result = execute_tool(registry, "ExampleTool", {})

    assert result.status == "failed"
    assert "Input validation failed" in str(result.error)
    assert called is False


def test_execute_tool_invalid_output_is_contract_failure() -> None:
    def bad_output(_input: ExampleInput) -> dict:
        return {"wrong": 1}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="ExampleTool",
            description="Example typed tool.",
            input_model=ExampleInput,
            output_model=ExampleOutput,
            callable=bad_output,
        )
    )

    result = execute_tool(registry, "ExampleTool", {"value": 1})

    assert result.status == "failed"
    assert "Output validation failed" in str(result.error)
