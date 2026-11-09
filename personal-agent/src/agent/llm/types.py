from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ModelMessage(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    # Set on assistant messages that request tools.
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # Set on tool-result messages.
    tool_call_id: str | None = None


class ModelRequest(BaseModel):
    provider: str
    model: str
    instructions: str | None = None
    messages: list[ModelMessage]
    tools: list[ToolDefinition] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None


class Usage(BaseModel):
    # None means unknown; it is never treated as zero.
    input_tokens: int | None = None
    output_tokens: int | None = None


StopReason = Literal["end", "tool_calls", "length", "other"]


class ModelResponse(BaseModel):
    message: ModelMessage
    stop_reason: StopReason
    usage: Usage | None = None
    provider: str
    model: str
    latency_ms: float | None = None
    # Adapter-owned state; opaque to the runtime.
    continuation: dict[str, Any] | None = None


class ModelCapabilities(BaseModel):
    tool_calling: bool = False
    parallel_tool_calls: bool = False
    structured_output: bool = False
    streaming: bool = False
    multimodal: bool = False
    context_window: int | None = None
