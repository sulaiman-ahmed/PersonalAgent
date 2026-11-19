import asyncio
import json
import time
import uuid
from collections.abc import Callable

from pydantic import ValidationError

from agent.llm import ToolCall, ToolDefinition

from .permissions import ApprovalRequest, Approver, Permissions
from .redact import redact, secret_values
from .registry import ToolRegistry
from .types import ToolErrorCode, ToolEvent, ToolFailure, ToolResult

EventSink = Callable[[ToolEvent], None]

_ARGS_LOG_LIMIT = 2000


class ToolGateway:
    # The only path by which a tool call executes. invoke() never raises for
    # tool-level problems; every outcome is a ToolResult the model can read.
    def __init__(
        self,
        registry: ToolRegistry,
        permissions: Permissions,
        *,
        approver: Approver | None = None,
        on_event: EventSink | None = None,
        max_output_chars: int = 20_000,
        secrets: list[str] | None = None,
    ) -> None:
        self._registry = registry
        self._permissions = permissions
        self._approver = approver
        self._on_event = on_event or (lambda event: None)
        self._max_output_chars = max_output_chars
        self._secrets = secret_values() if secrets is None else secrets

    def definitions(self) -> list[ToolDefinition]:
        # Only tools granted to this run are exposed to the model.
        return [t.definition() for t in self._registry if self._permissions.allows_tool(t.name)]

    async def invoke(self, call: ToolCall) -> ToolResult:
        invocation_id = uuid.uuid4().hex
        started = time.perf_counter()
        arguments = self._bound(json.dumps(call.arguments, default=str), _ARGS_LOG_LIMIT)[0]
        decision = "not evaluated"

        def finish(
            ok: bool, output: str, code: ToolErrorCode | None = None
        ) -> ToolResult:
            bounded, truncated = self._bound(output, self._max_output_chars)
            self._on_event(
                ToolEvent(
                    invocation_id=invocation_id,
                    tool=call.name,
                    kind="finished",
                    arguments=arguments,
                    ok=ok,
                    error_code=code,
                    detail=decision,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            )
            return ToolResult(
                invocation_id=invocation_id,
                tool=call.name,
                ok=ok,
                output=bounded,
                error_code=code,
                truncated=truncated,
            )

        def fail(code: ToolErrorCode, message: str) -> ToolResult:
            return finish(False, message, code)

        tool = self._registry.get(call.name)
        if tool is None:
            return fail(ToolErrorCode.UNKNOWN_TOOL, f"unknown tool: {call.name}")

        try:
            args = tool.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}" for e in exc.errors()
            )
            return fail(ToolErrorCode.INVALID_ARGUMENTS, f"invalid arguments: {problems}")

        try:
            paths = tool.resources(args)
        except Exception as exc:
            return fail(ToolErrorCode.INVALID_ARGUMENTS, f"invalid arguments: {exc}")

        denial = self._permissions.check(tool.name, paths)
        if denial is not None:
            decision = f"denied: {denial}"
            return fail(ToolErrorCode.NOT_PERMITTED, denial)
        decision = "granted"

        if tool.requires_approval:
            if self._approver is None:
                decision = "denied: approval required but no approver configured"
                return fail(
                    ToolErrorCode.DENIED_BY_USER,
                    "this tool requires user approval and none is available",
                )
            request = ApprovalRequest(
                invocation_id=invocation_id,
                tool=tool.name,
                summary=self._bound(tool.describe(args), _ARGS_LOG_LIMIT)[0],
            )
            try:
                approved = await self._approver(request)
            except Exception:
                approved = False
            if not approved:
                decision = "denied: user declined"
                return fail(ToolErrorCode.DENIED_BY_USER, "the user declined this action")
            decision = "granted, approved by user"

        self._on_event(
            ToolEvent(
                invocation_id=invocation_id,
                tool=tool.name,
                kind="started",
                arguments=arguments,
                detail=decision,
            )
        )

        try:
            async with asyncio.timeout(tool.timeout_seconds):
                output = await tool.run(args)
        except TimeoutError:
            return fail(
                ToolErrorCode.TIMEOUT, f"timed out after {tool.timeout_seconds:g} seconds"
            )
        except ToolFailure as exc:
            return fail(ToolErrorCode.EXECUTION_ERROR, str(exc))
        except Exception as exc:
            return fail(ToolErrorCode.EXECUTION_ERROR, f"unexpected error: {type(exc).__name__}: {exc}")

        return finish(True, output)

    def _bound(self, text: str, limit: int) -> tuple[str, bool]:
        # Redact first so a secret cut by truncation cannot partially survive.
        text = redact(text, self._secrets)
        if len(text) <= limit:
            return text, False
        omitted = len(text) - limit
        return f"{text[:limit]}\n[output truncated: {omitted} more characters]", True
