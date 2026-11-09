import json
import time
from typing import Any

import groq

from .errors import ModelError, ModelErrorCategory
from .provider import validate_request
from .types import (
    ModelCapabilities,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    StopReason,
    ToolCall,
    Usage,
)

# Groq only accepts [a-zA-Z0-9_-] in function names, so "file.read" is sent as
# "file__read" and mapped back on the way in.
_DOT = "__"


def _to_wire_name(name: str) -> str:
    return name.replace(".", _DOT)


def _from_wire_name(name: str) -> str:
    return name.replace(_DOT, ".")


_STOP_REASONS: dict[str, StopReason] = {
    "stop": "end",
    "tool_calls": "tool_calls",
    "length": "length",
}


class GroqProvider:
    name = "groq"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or groq.AsyncGroq()

    def capabilities(self, model: str) -> ModelCapabilities:
        # Conservative default; refine per model as we learn what each supports.
        return ModelCapabilities(tool_calling=True, streaming=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        validate_request(self, request)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": self._messages(request),
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": _to_wire_name(t.name),
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        start = time.perf_counter()
        try:
            raw = await self._client.chat.completions.create(**kwargs)
        except groq.GroqError as exc:
            raise _map_error(exc) from exc
        latency_ms = (time.perf_counter() - start) * 1000

        return self._response(raw, request, latency_ms)

    @staticmethod
    def _messages(request: ModelRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if request.instructions:
            out.append({"role": "system", "content": request.instructions})
        for m in request.messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content or "",
                    }
                )
                continue
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": _to_wire_name(tc.name),
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            out.append(msg)
        return out

    @staticmethod
    def _response(raw: Any, request: ModelRequest, latency_ms: float) -> ModelResponse:
        if not raw.choices:
            raise ModelError(ModelErrorCategory.MALFORMED_RESPONSE, "response has no choices")
        choice = raw.choices[0]

        tool_calls: list[ToolCall] = []
        for tc in choice.message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise ModelError(
                    ModelErrorCategory.MALFORMED_RESPONSE,
                    f"tool call {tc.function.name!r} has invalid JSON arguments: {exc}",
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelError(
                    ModelErrorCategory.MALFORMED_RESPONSE,
                    f"tool call {tc.function.name!r} arguments are not a JSON object",
                )
            tool_calls.append(
                ToolCall(
                    id=tc.id,
                    name=_from_wire_name(tc.function.name),
                    arguments=arguments,
                )
            )

        usage = None
        if raw.usage is not None:
            usage = Usage(
                input_tokens=raw.usage.prompt_tokens,
                output_tokens=raw.usage.completion_tokens,
            )

        return ModelResponse(
            message=ModelMessage(
                role="assistant",
                content=choice.message.content,
                tool_calls=tool_calls,
            ),
            stop_reason=_STOP_REASONS.get(choice.finish_reason, "other"),
            usage=usage,
            provider="groq",
            model=raw.model or request.model,
            latency_ms=latency_ms,
        )


def _map_error(exc: groq.GroqError) -> ModelError:
    C = ModelErrorCategory
    message = str(exc)
    if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
        return ModelError(C.AUTHENTICATION, message)
    if isinstance(exc, groq.RateLimitError):
        return ModelError(C.RATE_LIMIT, message)
    if isinstance(exc, groq.APITimeoutError):
        return ModelError(C.TIMEOUT, message)
    if isinstance(exc, (groq.APIConnectionError, groq.InternalServerError)):
        return ModelError(C.UNAVAILABLE, message)
    if isinstance(exc, groq.APIResponseValidationError):
        return ModelError(C.MALFORMED_RESPONSE, message)
    if isinstance(exc, groq.BadRequestError) and "context" in message.lower():
        return ModelError(C.CONTEXT_LIMIT, message)
    return ModelError(C.OTHER, message)
