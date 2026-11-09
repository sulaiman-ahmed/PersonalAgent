from types import SimpleNamespace as NS

import groq
import httpx
import pytest

from agent.llm import (
    GroqProvider,
    ModelError,
    ModelErrorCategory,
    ModelMessage,
    ModelRequest,
    ToolCall,
    ToolDefinition,
)

READ_TOOL = ToolDefinition(
    name="file.read",
    description="Read a file",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
)


def make_raw(*, content="hi", tool_calls=None, finish_reason="stop", usage=True):
    return NS(
        model="test-model",
        choices=[
            NS(
                finish_reason=finish_reason,
                message=NS(content=content, tool_calls=tool_calls),
            )
        ],
        usage=NS(prompt_tokens=3, completion_tokens=5) if usage else None,
    )


class StubClient:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def request(**overrides):
    base = dict(
        provider="groq",
        model="test-model",
        messages=[ModelMessage(role="user", content="hello")],
    )
    return ModelRequest(**{**base, **overrides})


async def test_text_response():
    client = StubClient(make_raw())
    resp = await GroqProvider(client).generate(request(instructions="be brief"))

    assert resp.message.content == "hi"
    assert resp.stop_reason == "end"
    assert resp.usage.input_tokens == 3 and resp.usage.output_tokens == 5
    assert resp.provider == "groq" and resp.latency_ms is not None
    assert client.calls[0]["messages"][0] == {"role": "system", "content": "be brief"}
    assert "tools" not in client.calls[0]


async def test_missing_usage_is_none():
    resp = await GroqProvider(StubClient(make_raw(usage=False))).generate(request())
    assert resp.usage is None


async def test_tool_name_round_trip_and_argument_parsing():
    raw_call = NS(id="c1", function=NS(name="file__read", arguments='{"path": "a.txt"}'))
    client = StubClient(make_raw(content=None, tool_calls=[raw_call], finish_reason="tool_calls"))

    resp = await GroqProvider(client).generate(request(tools=[READ_TOOL]))

    assert client.calls[0]["tools"][0]["function"]["name"] == "file__read"
    assert resp.stop_reason == "tool_calls"
    assert resp.message.tool_calls == [
        ToolCall(id="c1", name="file.read", arguments={"path": "a.txt"})
    ]


async def test_history_with_tool_call_and_result_is_translated():
    messages = [
        ModelMessage(role="user", content="read a.txt"),
        ModelMessage(
            role="assistant",
            tool_calls=[ToolCall(id="c1", name="file.read", arguments={"path": "a.txt"})],
        ),
        ModelMessage(role="tool", tool_call_id="c1", content="contents"),
    ]
    client = StubClient(make_raw())
    await GroqProvider(client).generate(request(messages=messages, tools=[READ_TOOL]))

    sent = client.calls[0]["messages"]
    assert sent[1]["tool_calls"][0]["function"] == {
        "name": "file__read",
        "arguments": '{"path": "a.txt"}',
    }
    assert sent[2] == {"role": "tool", "tool_call_id": "c1", "content": "contents"}


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
async def test_bad_tool_arguments_are_malformed_response(arguments):
    raw_call = NS(id="c1", function=NS(name="file__read", arguments=arguments))
    client = StubClient(make_raw(tool_calls=[raw_call], finish_reason="tool_calls"))

    with pytest.raises(ModelError) as exc:
        await GroqProvider(client).generate(request(tools=[READ_TOOL]))
    assert exc.value.category is ModelErrorCategory.MALFORMED_RESPONSE


async def test_no_choices_is_malformed_response():
    raw = NS(model="m", choices=[], usage=None)
    with pytest.raises(ModelError) as exc:
        await GroqProvider(StubClient(raw)).generate(request())
    assert exc.value.category is ModelErrorCategory.MALFORMED_RESPONSE


def _status_error(cls, status, message="boom"):
    req = httpx.Request("POST", "https://api.groq.com")
    return cls(message, response=httpx.Response(status, request=req), body=None)


_REQ = httpx.Request("POST", "https://api.groq.com")


@pytest.mark.parametrize(
    ("exc", "category", "retryable"),
    [
        (_status_error(groq.AuthenticationError, 401), ModelErrorCategory.AUTHENTICATION, False),
        (_status_error(groq.RateLimitError, 429), ModelErrorCategory.RATE_LIMIT, True),
        (_status_error(groq.InternalServerError, 500), ModelErrorCategory.UNAVAILABLE, True),
        (
            _status_error(groq.BadRequestError, 400, "maximum context length exceeded"),
            ModelErrorCategory.CONTEXT_LIMIT,
            False,
        ),
        (_status_error(groq.BadRequestError, 400, "bad param"), ModelErrorCategory.OTHER, False),
        (groq.APITimeoutError(_REQ), ModelErrorCategory.TIMEOUT, True),
        (groq.APIConnectionError(request=_REQ), ModelErrorCategory.UNAVAILABLE, True),
    ],
)
async def test_sdk_errors_are_normalized(exc, category, retryable):
    with pytest.raises(ModelError) as caught:
        await GroqProvider(StubClient(exc)).generate(request())
    assert caught.value.category is category
    assert caught.value.retryable is retryable
