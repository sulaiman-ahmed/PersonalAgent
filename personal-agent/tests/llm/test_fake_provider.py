import pytest

from agent.llm import (
    ModelCapabilities,
    ModelError,
    ModelErrorCategory,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolDefinition,
    validate_request,
)
from tests.fakes import FakeProvider


def request(**overrides):
    base = dict(
        provider="fake",
        model="m",
        messages=[ModelMessage(role="user", content="hi")],
    )
    return ModelRequest(**{**base, **overrides})


def response(text="ok"):
    return ModelResponse(
        message=ModelMessage(role="assistant", content=text),
        stop_reason="end",
        provider="fake",
        model="m",
    )


async def test_returns_scripted_items_in_order_and_records_requests():
    provider = FakeProvider([response("one"), response("two")])

    first = await provider.generate(request())
    second = await provider.generate(request())

    assert [first.message.content, second.message.content] == ["one", "two"]
    assert len(provider.requests) == 2


async def test_raises_scripted_error():
    provider = FakeProvider([ModelError(ModelErrorCategory.RATE_LIMIT, "slow down")])

    with pytest.raises(ModelError) as exc:
        await provider.generate(request())
    assert exc.value.retryable


async def test_exhausted_script_fails_loudly():
    with pytest.raises(AssertionError):
        await FakeProvider([]).generate(request())


async def test_tools_rejected_when_model_lacks_tool_calling():
    provider = FakeProvider([response()], capabilities=ModelCapabilities(tool_calling=False))
    tool = ToolDefinition(name="file.read", description="d", input_schema={})

    with pytest.raises(ModelError) as exc:
        await provider.generate(request(tools=[tool]))
    assert exc.value.category is ModelErrorCategory.UNSUPPORTED_CAPABILITY


def test_validate_request_allows_text_only_without_tool_calling():
    provider = FakeProvider([], capabilities=ModelCapabilities(tool_calling=False))
    validate_request(provider, request())


def test_model_error_retryable_flags():
    C = ModelErrorCategory
    retryable = {C.RATE_LIMIT, C.TIMEOUT, C.UNAVAILABLE}
    for category in C:
        assert ModelError(category, "x").retryable is (category in retryable)
