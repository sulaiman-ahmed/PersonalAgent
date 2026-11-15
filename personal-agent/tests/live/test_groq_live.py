import groq
import pytest

from agent.llm import (
    GroqProvider,
    ModelError,
    ModelErrorCategory,
    ModelMessage,
    ModelRequest,
    ToolDefinition,
)

pytestmark = pytest.mark.live

READ_TOOL = ToolDefinition(
    name="file.read",
    description="Read the contents of a file at the given path.",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path"}},
        "required": ["path"],
    },
)


async def test_text_round_trip(groq_model):
    provider = GroqProvider()
    response = await provider.generate(
        ModelRequest(
            provider="groq",
            model=groq_model,
            messages=[ModelMessage(role="user", content="Reply with the single word: pong")],
            max_tokens=200,
        )
    )

    assert response.provider == "groq"
    assert response.message.role == "assistant"
    assert response.stop_reason in ("end", "length")
    assert response.usage is not None
    assert response.usage.input_tokens and response.usage.output_tokens
    assert response.latency_ms > 0


async def test_tool_call_round_trip(groq_model):
    provider = GroqProvider()
    user = ModelMessage(
        role="user",
        content="Use the file.read tool to read notes.txt, then tell me what it says.",
    )

    first = await provider.generate(
        ModelRequest(provider="groq", model=groq_model, messages=[user], tools=[READ_TOOL])
    )

    # The model chooses whether to call the tool, so a failure here can be model
    # behavior rather than a provider bug. The message shows which.
    assert first.message.tool_calls, (
        f"model did not call a tool (stop_reason={first.stop_reason}, "
        f"content={first.message.content!r})"
    )
    call = first.message.tool_calls[0]
    assert call.name == "file.read"  # "file__read" on the wire, mapped back
    assert isinstance(call.arguments.get("path"), str)

    second = await provider.generate(
        ModelRequest(
            provider="groq",
            model=groq_model,
            messages=[
                user,
                first.message,
                ModelMessage(
                    role="tool",
                    tool_call_id=call.id,
                    content="The launch code is 4271.",
                ),
            ],
            tools=[READ_TOOL],
        )
    )

    assert second.message.content
    assert "4271" in second.message.content


async def test_invalid_api_key_is_authentication_error(groq_model):
    provider = GroqProvider(client=groq.AsyncGroq(api_key="gsk_invalid"))

    with pytest.raises(ModelError) as exc:
        await provider.generate(
            ModelRequest(
                provider="groq",
                model=groq_model,
                messages=[ModelMessage(role="user", content="hi")],
            )
        )
    assert exc.value.category is ModelErrorCategory.AUTHENTICATION
    assert not exc.value.retryable


async def test_unknown_model_is_normalized_error(groq_model):
    provider = GroqProvider()

    with pytest.raises(ModelError) as exc:
        await provider.generate(
            ModelRequest(
                provider="groq",
                model="no-such-model-xyz",
                messages=[ModelMessage(role="user", content="hi")],
            )
        )
    # Records what Groq's real error maps to; useful for tuning _map_error.
    print(f"unknown model -> {exc.value}")
    assert not exc.value.retryable
