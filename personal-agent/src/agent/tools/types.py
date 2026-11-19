from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from agent.llm import ToolDefinition


class ToolInput(BaseModel):
    # Unknown arguments are rejected rather than silently ignored.
    model_config = ConfigDict(extra="forbid")


class ToolErrorCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_PERMITTED = "not_permitted"
    DENIED_BY_USER = "denied_by_user"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"


class ToolResult(BaseModel):
    invocation_id: str
    tool: str
    ok: bool
    # Tool output on success, error message on failure. Bounded and redacted.
    output: str = ""
    error_code: ToolErrorCode | None = None
    truncated: bool = False


class ToolEvent(BaseModel):
    invocation_id: str
    tool: str
    # "started" is emitted after authorization and before execution.
    # "finished" is emitted for every invocation, including denied ones.
    kind: Literal["started", "finished"]
    arguments: str
    ok: bool | None = None
    error_code: ToolErrorCode | None = None
    detail: str | None = None
    duration_ms: float | None = None


class ToolFailure(Exception):
    # Raised by tools for expected, model-actionable failures.
    pass


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[ToolInput]]
    requires_approval: ClassVar[bool] = False
    timeout_seconds: ClassVar[float] = 30.0

    def resources(self, args: Any) -> list[Path]:
        # Paths this call would touch; the gateway checks them against grants.
        return []

    def describe(self, args: Any) -> str:
        # Human-readable summary shown when approval is requested.
        return f"{self.name} {args.model_dump_json()}"

    @abstractmethod
    async def run(self, args: Any) -> str: ...

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
        )
