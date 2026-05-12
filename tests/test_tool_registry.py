import pytest
from pydantic import BaseModel

from core.tool_registry import DEFAULT_REGISTRY, ToolRegistry, ToolSpec


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
