import asyncio
from pathlib import Path

import pytest

from agent.llm import ToolCall
from agent.tools import (
    ApprovalRequest,
    Grant,
    Permissions,
    Tool,
    ToolErrorCode,
    ToolEvent,
    ToolFailure,
    ToolGateway,
    ToolInput,
    ToolRegistry,
)


class EchoInput(ToolInput):
    text: str


class EchoTool(Tool):
    name = "test.echo"
    description = "Echo text"
    input_model = EchoInput

    async def run(self, args: EchoInput) -> str:
        return args.text


class PathInput(ToolInput):
    path: str


class PathTool(Tool):
    name = "test.path"
    description = "Touches a path"
    input_model = PathInput

    def resources(self, args: PathInput) -> list[Path]:
        return [Path(args.path)]

    async def run(self, args: PathInput) -> str:
        return f"touched {args.path}"


class DangerTool(Tool):
    name = "test.danger"
    description = "Needs approval"
    input_model = EchoInput
    requires_approval = True

    def __init__(self) -> None:
        self.ran = False

    async def run(self, args: EchoInput) -> str:
        self.ran = True
        return "done"


class SlowTool(Tool):
    name = "test.slow"
    description = "Sleeps"
    input_model = EchoInput
    timeout_seconds = 0.05

    async def run(self, args: EchoInput) -> str:
        await asyncio.sleep(5)
        return "never"


class FailingTool(Tool):
    name = "test.fail"
    description = "Raises ToolFailure"
    input_model = EchoInput

    async def run(self, args: EchoInput) -> str:
        raise ToolFailure("expected failure")


class CrashingTool(Tool):
    name = "test.crash"
    description = "Raises an unexpected error"
    input_model = EchoInput

    async def run(self, args: EchoInput) -> str:
        raise RuntimeError("kaboom")


def make_gateway(tools, grants, **kwargs):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    events: list[ToolEvent] = []
    gateway = ToolGateway(
        registry,
        Permissions(grants),
        on_event=events.append,
        secrets=kwargs.pop("secrets", []),
        **kwargs,
    )
    return gateway, events


def call(name, **arguments):
    return ToolCall(id="c1", name=name, arguments=arguments)


async def test_success_returns_structured_result_and_events():
    gateway, events = make_gateway([EchoTool()], [Grant("test.echo")])

    result = await gateway.invoke(call("test.echo", text="hi"))

    assert result.ok and result.output == "hi" and result.error_code is None
    assert result.invocation_id
    assert [e.kind for e in events] == ["started", "finished"]
    assert events[-1].ok is True and events[-1].detail == "granted"
    assert events[-1].invocation_id == result.invocation_id


async def test_unknown_tool():
    gateway, events = make_gateway([EchoTool()], [Grant("test.echo")])

    result = await gateway.invoke(call("test.nope"))

    assert not result.ok and result.error_code is ToolErrorCode.UNKNOWN_TOOL
    assert [e.kind for e in events] == ["finished"]


@pytest.mark.parametrize(
    "arguments", [{}, {"text": 5}, {"text": "x", "extra": 1}], ids=["missing", "wrong-type", "extra"]
)
async def test_invalid_arguments(arguments):
    gateway, _ = make_gateway([EchoTool()], [Grant("test.echo")])

    result = await gateway.invoke(ToolCall(id="c", name="test.echo", arguments=arguments))

    assert not result.ok and result.error_code is ToolErrorCode.INVALID_ARGUMENTS
    assert "text" in result.output or "extra" in result.output


async def test_registered_but_not_granted_tool_is_denied_and_hidden():
    gateway, events = make_gateway([EchoTool(), PathTool()], [Grant("test.echo")])

    assert [d.name for d in gateway.definitions()] == ["test.echo"]
    result = await gateway.invoke(call("test.path", path="/tmp/x"))

    assert result.error_code is ToolErrorCode.NOT_PERMITTED
    assert events[-1].detail.startswith("denied")


async def test_path_inside_scope_allowed(tmp_path):
    gateway, _ = make_gateway([PathTool()], [Grant("test.path", roots=(tmp_path,))])

    result = await gateway.invoke(call("test.path", path=str(tmp_path / "a.txt")))

    assert result.ok


async def test_path_outside_scope_denied(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    gateway, _ = make_gateway([PathTool()], [Grant("test.path", roots=(root,))])

    result = await gateway.invoke(call("test.path", path=str(tmp_path / "other.txt")))

    assert result.error_code is ToolErrorCode.NOT_PERMITTED


async def test_dotdot_escape_denied(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    gateway, _ = make_gateway([PathTool()], [Grant("test.path", roots=(root,))])

    result = await gateway.invoke(call("test.path", path=str(root / ".." / "secret.txt")))

    assert result.error_code is ToolErrorCode.NOT_PERMITTED


async def test_symlink_escape_denied(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    gateway, _ = make_gateway([PathTool()], [Grant("test.path", roots=(root,))])

    result = await gateway.invoke(call("test.path", path=str(root / "link" / "secret.txt")))

    assert result.error_code is ToolErrorCode.NOT_PERMITTED


async def test_sibling_directory_with_shared_prefix_denied(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    sibling = tmp_path / "work-evil"
    sibling.mkdir()
    gateway, _ = make_gateway([PathTool()], [Grant("test.path", roots=(root,))])

    result = await gateway.invoke(call("test.path", path=str(sibling / "x")))

    assert result.error_code is ToolErrorCode.NOT_PERMITTED


async def test_approval_required_without_approver_is_denied():
    tool = DangerTool()
    gateway, _ = make_gateway([tool], [Grant("test.danger")])

    result = await gateway.invoke(call("test.danger", text="x"))

    assert result.error_code is ToolErrorCode.DENIED_BY_USER and not tool.ran


async def test_user_declines():
    tool = DangerTool()
    seen: list[ApprovalRequest] = []

    async def decline(request: ApprovalRequest) -> bool:
        seen.append(request)
        return False

    gateway, _ = make_gateway([tool], [Grant("test.danger")], approver=decline)

    result = await gateway.invoke(call("test.danger", text="x"))

    assert result.error_code is ToolErrorCode.DENIED_BY_USER and not tool.ran
    assert seen[0].tool == "test.danger" and "x" in seen[0].summary


async def test_user_approves():
    tool = DangerTool()

    async def approve(request: ApprovalRequest) -> bool:
        return True

    gateway, events = make_gateway([tool], [Grant("test.danger")], approver=approve)

    result = await gateway.invoke(call("test.danger", text="x"))

    assert result.ok and tool.ran
    assert events[-1].detail == "granted, approved by user"


async def test_approver_error_counts_as_denial():
    tool = DangerTool()

    async def broken(request: ApprovalRequest) -> bool:
        raise RuntimeError("prompt failed")

    gateway, _ = make_gateway([tool], [Grant("test.danger")], approver=broken)

    result = await gateway.invoke(call("test.danger", text="x"))

    assert result.error_code is ToolErrorCode.DENIED_BY_USER and not tool.ran


async def test_permission_checked_before_approval_is_requested(tmp_path):
    asked = []

    async def approver(request: ApprovalRequest) -> bool:
        asked.append(request)
        return True

    gateway, _ = make_gateway([DangerTool()], [], approver=approver)

    result = await gateway.invoke(call("test.danger", text="x"))

    assert result.error_code is ToolErrorCode.NOT_PERMITTED and not asked


async def test_timeout():
    gateway, _ = make_gateway([SlowTool()], [Grant("test.slow")])

    result = await gateway.invoke(call("test.slow", text="x"))

    assert result.error_code is ToolErrorCode.TIMEOUT


async def test_tool_failure_is_execution_error_with_message():
    gateway, _ = make_gateway([FailingTool()], [Grant("test.fail")])

    result = await gateway.invoke(call("test.fail", text="x"))

    assert result.error_code is ToolErrorCode.EXECUTION_ERROR
    assert result.output == "expected failure"


async def test_unexpected_exception_does_not_escape():
    gateway, events = make_gateway([CrashingTool()], [Grant("test.crash")])

    result = await gateway.invoke(call("test.crash", text="x"))

    assert result.error_code is ToolErrorCode.EXECUTION_ERROR
    assert "RuntimeError" in result.output and "kaboom" in result.output
    assert events[-1].ok is False


async def test_output_is_truncated_and_flagged():
    gateway, _ = make_gateway([EchoTool()], [Grant("test.echo")], max_output_chars=10)

    result = await gateway.invoke(call("test.echo", text="x" * 100))

    assert result.ok and result.truncated
    assert result.output.startswith("x" * 10) and "truncated" in result.output


async def test_secrets_redacted_from_output_errors_and_events():
    secret = "sk-supersecret-1234"
    gateway, events = make_gateway(
        [EchoTool(), FailingTool()],
        [Grant("test.echo"), Grant("test.fail")],
        secrets=[secret],
    )

    ok = await gateway.invoke(call("test.echo", text=f"key is {secret}"))
    unknown = await gateway.invoke(call(f"test.{secret}"))

    assert secret not in ok.output and "[REDACTED]" in ok.output
    assert secret not in unknown.output
    assert all(secret not in e.arguments for e in events)


async def test_secret_split_by_truncation_does_not_leak():
    secret = "sk-supersecret-1234"
    gateway, _ = make_gateway(
        [EchoTool()], [Grant("test.echo")], secrets=[secret], max_output_chars=12
    )

    result = await gateway.invoke(call("test.echo", text="abc " + secret))

    assert "sk-super" not in result.output


async def test_every_invocation_emits_exactly_one_finished_event():
    gateway, events = make_gateway(
        [EchoTool(), FailingTool()], [Grant("test.echo"), Grant("test.fail")]
    )

    results = [
        await gateway.invoke(call("test.echo", text="a")),
        await gateway.invoke(call("test.fail", text="a")),
        await gateway.invoke(call("test.missing")),
        await gateway.invoke(ToolCall(id="c", name="test.echo", arguments={})),
    ]

    finished = [e for e in events if e.kind == "finished"]
    assert [e.invocation_id for e in finished] == [r.invocation_id for r in results]
    assert len({r.invocation_id for r in results}) == 4


def test_duplicate_registration_rejected():
    registry = ToolRegistry()
    registry.register(EchoTool())
    with pytest.raises(ValueError):
        registry.register(EchoTool())


def test_definition_comes_from_input_model():
    definition = EchoTool().definition()

    assert definition.name == "test.echo"
    assert definition.input_schema["properties"]["text"]["type"] == "string"
    assert definition.input_schema["required"] == ["text"]
