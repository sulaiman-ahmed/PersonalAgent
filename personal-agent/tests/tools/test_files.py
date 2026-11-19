import hashlib
import os
import re
import stat

import pytest

from agent.llm import ToolCall
from agent.tools import (
    FileEdit,
    FileRead,
    FileSearch,
    Grant,
    Permissions,
    ToolErrorCode,
    ToolGateway,
    ToolRegistry,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def gateway(ws):
    registry = ToolRegistry()
    for tool in (FileRead(ws), FileSearch(ws), FileEdit(ws)):
        registry.register(tool)
    grants = [Grant(name, roots=(ws,)) for name in ("file.read", "file.search", "file.edit")]
    return ToolGateway(registry, Permissions(grants), secrets=[])


async def call(gateway, name, **arguments):
    return await gateway.invoke(ToolCall(id="c", name=name, arguments=arguments))


def sha_from(output: str) -> str:
    return re.search(r"sha256: ([0-9a-f]{64})", output).group(1)


# ---------------------------------------------------------------- file.read


async def test_read_returns_hash_and_numbered_lines(gateway, ws):
    (ws / "a.txt").write_text("one\ntwo\nthree\n")

    result = await call(gateway, "file.read", path="a.txt")

    assert result.ok
    assert sha_from(result.output) == sha("one\ntwo\nthree\n")
    assert "lines 1-3 of 3" in result.output
    assert "     2\ttwo" in result.output


async def test_read_offset_and_limit(gateway, ws):
    (ws / "a.txt").write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")

    result = await call(gateway, "file.read", path="a.txt", offset=4, limit=2)

    assert "lines 4-5 of 10" in result.output
    assert "line4" in result.output and "line5" in result.output and "line6" not in result.output


async def test_read_offset_past_end(gateway, ws):
    (ws / "a.txt").write_text("one\n")

    result = await call(gateway, "file.read", path="a.txt", offset=5)

    assert result.error_code is ToolErrorCode.EXECUTION_ERROR and "past the end" in result.output


async def test_read_empty_file(gateway, ws):
    (ws / "empty.txt").write_text("")

    result = await call(gateway, "file.read", path="empty.txt")

    assert result.ok and "(empty file)" in result.output
    assert sha_from(result.output) == sha("")


async def test_read_missing_file(gateway):
    result = await call(gateway, "file.read", path="nope.txt")

    assert result.error_code is ToolErrorCode.EXECUTION_ERROR and "not found" in result.output


async def test_read_directory(gateway, ws):
    (ws / "d").mkdir()

    result = await call(gateway, "file.read", path="d")

    assert not result.ok and "directory" in result.output


async def test_read_binary_and_non_utf8_rejected(gateway, ws):
    (ws / "bin").write_bytes(b"ab\x00cd")
    (ws / "latin").write_bytes(b"caf\xe9")

    assert "binary" in (await call(gateway, "file.read", path="bin")).output
    assert "UTF-8" in (await call(gateway, "file.read", path="latin")).output


async def test_read_oversized_file_rejected(gateway, ws, monkeypatch):
    monkeypatch.setattr("agent.tools.files.MAX_FILE_BYTES", 10)
    (ws / "big.txt").write_text("x" * 100)

    result = await call(gateway, "file.read", path="big.txt")

    assert not result.ok and "too large" in result.output


async def test_read_outside_workspace_denied(gateway, ws, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")

    for path in ("../secret.txt", str(tmp_path / "secret.txt")):
        result = await call(gateway, "file.read", path=path)
        assert result.error_code is ToolErrorCode.NOT_PERMITTED


async def test_read_through_symlink_out_of_workspace_denied(gateway, ws, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    (ws / "link.txt").symlink_to(tmp_path / "secret.txt")

    result = await call(gateway, "file.read", path="link.txt")

    assert result.error_code is ToolErrorCode.NOT_PERMITTED


async def test_read_symlink_within_workspace_allowed(gateway, ws):
    (ws / "real.txt").write_text("hello\n")
    (ws / "link.txt").symlink_to(ws / "real.txt")

    assert (await call(gateway, "file.read", path="link.txt")).ok


async def test_read_rejects_bad_arguments(gateway):
    for args in ({"path": "a", "offset": 0}, {"path": "a", "limit": 0}, {}):
        result = await call(gateway, "file.read", **args)
        assert result.error_code is ToolErrorCode.INVALID_ARGUMENTS


# -------------------------------------------------------------- file.search


async def test_glob_by_name_and_by_path(gateway, ws):
    (ws / "src").mkdir()
    (ws / "src" / "a.py").write_text("")
    (ws / "src" / "b.txt").write_text("")
    (ws / "top.py").write_text("")

    by_name = await call(gateway, "file.search", mode="glob", pattern="*.py")
    by_path = await call(gateway, "file.search", mode="glob", pattern="src/**/*.py")

    assert by_name.output.splitlines() == ["src/a.py", "top.py"]
    assert by_path.output.splitlines() == ["src/a.py"]


async def test_grep_finds_lines_literally(gateway, ws):
    (ws / "a.py").write_text("def f(x):\n    return x + 1\n")
    (ws / "b.py").write_text("print('f(x)')\n")

    result = await call(gateway, "file.search", mode="grep", pattern="f(x)")

    assert result.output.splitlines() == ["a.py:1: def f(x):", "b.py:1: print('f(x)')"]


async def test_grep_regex_syntax_is_not_interpreted(gateway, ws):
    (ws / "a.txt").write_text("aaaa\n(a+)+$\n")

    result = await call(gateway, "file.search", mode="grep", pattern="(a+)+$")

    assert result.output == "a.txt:2: (a+)+$"


async def test_grep_ignore_case_and_include(gateway, ws):
    (ws / "a.py").write_text("Hello\n")
    (ws / "a.txt").write_text("hello\n")

    result = await call(
        gateway, "file.search", mode="grep", pattern="HELLO", ignore_case=True, include="*.py"
    )

    assert result.output == "a.py:1: Hello"


async def test_search_no_matches(gateway):
    result = await call(gateway, "file.search", mode="grep", pattern="zzz")

    assert result.ok and result.output == "no matches"


async def test_search_skips_vendor_dirs_and_binary_files(gateway, ws):
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("needle\n")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x.js").write_text("needle\n")
    (ws / "blob.bin").write_bytes(b"\x00needle")
    (ws / "ok.txt").write_text("needle\n")

    result = await call(gateway, "file.search", mode="grep", pattern="needle")

    assert result.output == "ok.txt:1: needle"


async def test_search_result_limit(gateway, ws, monkeypatch):
    monkeypatch.setattr("agent.tools.files.MAX_SEARCH_RESULTS", 3)
    for i in range(10):
        (ws / f"f{i}.txt").write_text("")

    result = await call(gateway, "file.search", mode="glob", pattern="*.txt")

    assert len(result.output.splitlines()) == 4 and "result limit" in result.output


async def test_search_long_match_lines_are_clipped(gateway, ws):
    (ws / "a.txt").write_text("needle" + "x" * 5000 + "\n")

    result = await call(gateway, "file.search", mode="grep", pattern="needle")

    assert len(result.output) < 400


async def test_search_does_not_follow_symlinks_out_of_scope(gateway, ws, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.txt").write_text("needle\n")
    (ws / "linkdir").symlink_to(outside)
    (ws / "linkfile.txt").symlink_to(outside / "leak.txt")

    result = await call(gateway, "file.search", mode="grep", pattern="needle")

    assert result.output == "no matches"


async def test_search_outside_workspace_denied_and_missing_dir(gateway):
    denied = await call(gateway, "file.search", mode="glob", pattern="*", path="..")
    missing = await call(gateway, "file.search", mode="glob", pattern="*", path="nope")

    assert denied.error_code is ToolErrorCode.NOT_PERMITTED
    assert missing.error_code is ToolErrorCode.EXECUTION_ERROR


async def test_search_time_limit(gateway, ws, monkeypatch):
    monkeypatch.setattr("agent.tools.files.SEARCH_DEADLINE_SECONDS", -1)
    (ws / "a.txt").write_text("x")

    result = await call(gateway, "file.search", mode="glob", pattern="*")

    assert "time limit" in result.output


# ---------------------------------------------------------------- file.edit


async def test_edit_round_trip_with_chained_hash(gateway, ws):
    path = ws / "a.txt"
    path.write_text("alpha\nbeta\n")
    read = await call(gateway, "file.read", path="a.txt")

    first = await call(
        gateway, "file.edit", path="a.txt", old_string="alpha", new_string="ALPHA",
        expected_sha256=sha_from(read.output),
    )
    second = await call(
        gateway, "file.edit", path="a.txt", old_string="beta", new_string="BETA",
        expected_sha256=sha_from(first.output),
    )

    assert first.ok and second.ok
    assert path.read_text() == "ALPHA\nBETA\n"
    assert sha_from(second.output) == sha("ALPHA\nBETA\n")


async def test_edit_stale_hash_refused_and_file_untouched(gateway, ws):
    path = ws / "a.txt"
    path.write_text("alpha\n")
    stale = sha("alpha\n")
    path.write_text("alpha changed elsewhere\n")

    result = await call(
        gateway, "file.edit", path="a.txt", old_string="alpha", new_string="X",
        expected_sha256=stale,
    )

    assert result.error_code is ToolErrorCode.EXECUTION_ERROR and "changed" in result.output
    assert path.read_text() == "alpha changed elsewhere\n"


async def test_edit_requires_hash_for_existing_file(gateway, ws):
    (ws / "a.txt").write_text("alpha\n")

    result = await call(gateway, "file.edit", path="a.txt", old_string="alpha", new_string="X")

    assert not result.ok and "expected_sha256 is required" in result.output
    assert (ws / "a.txt").read_text() == "alpha\n"


async def test_edit_old_string_must_match_exactly_once(gateway, ws):
    (ws / "a.txt").write_text("dup dup\nuniq\n")
    digest = sha("dup dup\nuniq\n")

    none = await call(gateway, "file.edit", path="a.txt", old_string="zzz", new_string="x",
                      expected_sha256=digest)
    many = await call(gateway, "file.edit", path="a.txt", old_string="dup", new_string="x",
                      expected_sha256=digest)
    empty = await call(gateway, "file.edit", path="a.txt", old_string="", new_string="x",
                       expected_sha256=digest)
    same = await call(gateway, "file.edit", path="a.txt", old_string="uniq", new_string="uniq",
                      expected_sha256=digest)

    assert "not found" in none.output
    assert "2 places" in many.output
    assert "must not be empty" in empty.output
    assert "identical" in same.output
    assert (ws / "a.txt").read_text() == "dup dup\nuniq\n"


async def test_edit_deletion_via_empty_new_string(gateway, ws):
    (ws / "a.txt").write_text("keep\nremove me\n")

    result = await call(gateway, "file.edit", path="a.txt", old_string="remove me\n",
                        new_string="", expected_sha256=sha("keep\nremove me\n"))

    assert result.ok and (ws / "a.txt").read_text() == "keep\n"


async def test_edit_preserves_file_mode(gateway, ws):
    path = ws / "run.sh"
    path.write_text("echo hi\n")
    path.chmod(0o755)

    await call(gateway, "file.edit", path="run.sh", old_string="hi", new_string="bye",
               expected_sha256=sha("echo hi\n"))

    assert stat.S_IMODE(path.stat().st_mode) == 0o755


async def test_edit_leaves_no_temp_files(gateway, ws):
    (ws / "a.txt").write_text("alpha\n")

    await call(gateway, "file.edit", path="a.txt", old_string="alpha", new_string="B",
               expected_sha256=sha("alpha\n"))
    await call(gateway, "file.edit", path="a.txt", old_string="zzz", new_string="B",
               expected_sha256=sha("B\n"))
    await call(gateway, "file.edit", path="new.txt", old_string="", new_string="hello")

    assert sorted(p.name for p in ws.iterdir()) == ["a.txt", "new.txt"]


async def test_edit_creates_new_file(gateway, ws):
    result = await call(gateway, "file.edit", path="new.txt", old_string="", new_string="hello\n")

    assert result.ok and (ws / "new.txt").read_text() == "hello\n"
    assert sha_from(result.output) == sha("hello\n")


async def test_create_refuses_to_overwrite_existing_file(gateway, ws):
    (ws / "a.txt").write_text("precious\n")

    result = await call(gateway, "file.edit", path="a.txt", old_string="", new_string="clobber")

    assert not result.ok and "already exists" in result.output
    assert (ws / "a.txt").read_text() == "precious\n"


async def test_create_through_dangling_symlink_outside_workspace_denied(gateway, ws, tmp_path):
    (ws / "link.txt").symlink_to(tmp_path / "planted.txt")

    result = await call(gateway, "file.edit", path="link.txt", old_string="", new_string="x")

    assert result.error_code is ToolErrorCode.NOT_PERMITTED
    assert not (tmp_path / "planted.txt").exists()


async def test_create_through_dangling_symlink_inside_workspace_writes_target(gateway, ws):
    # The authorized path is the resolved path, so the link's target is what gets created.
    (ws / "link.txt").symlink_to(ws / "target.txt")

    result = await call(gateway, "file.edit", path="link.txt", old_string="", new_string="x")

    assert result.ok and (ws / "target.txt").read_text() == "x"


async def test_create_requires_existing_parent_and_empty_old_string(gateway, ws):
    no_parent = await call(gateway, "file.edit", path="nodir/x.txt", old_string="", new_string="x")
    with_old = await call(gateway, "file.edit", path="new.txt", old_string="abc", new_string="x")

    assert "parent directory" in no_parent.output
    assert "expected_sha256 is required" in with_old.output


async def test_edit_outside_workspace_denied_and_nothing_written(gateway, ws, tmp_path):
    (tmp_path / "victim.txt").write_text("original")
    (ws / "link.txt").symlink_to(tmp_path / "victim.txt")

    via_dotdot = await call(gateway, "file.edit", path="../new.txt", old_string="", new_string="x")
    via_link = await call(gateway, "file.edit", path="link.txt", old_string="original",
                          new_string="pwned", expected_sha256=sha("original"))

    assert via_dotdot.error_code is ToolErrorCode.NOT_PERMITTED
    assert via_link.error_code is ToolErrorCode.NOT_PERMITTED
    assert (tmp_path / "victim.txt").read_text() == "original"
    assert not (tmp_path / "new.txt").exists()


async def test_edit_size_limit(gateway, ws, monkeypatch):
    monkeypatch.setattr("agent.tools.files.MAX_FILE_BYTES", 20)
    (ws / "a.txt").write_text("small\n")

    grow = await call(gateway, "file.edit", path="a.txt", old_string="small",
                      new_string="x" * 100, expected_sha256=sha("small\n"))
    create = await call(gateway, "file.edit", path="big.txt", old_string="", new_string="x" * 100)

    assert not grow.ok and not create.ok
    assert (ws / "a.txt").read_text() == "small\n" and not (ws / "big.txt").exists()


async def test_edit_failure_after_temp_write_cleans_up(gateway, ws, monkeypatch):
    (ws / "a.txt").write_text("alpha\n")

    def boom(src, dst):
        raise OSError("disk exploded")

    monkeypatch.setattr(os, "replace", boom)

    result = await call(gateway, "file.edit", path="a.txt", old_string="alpha", new_string="B",
                        expected_sha256=sha("alpha\n"))

    assert not result.ok
    assert sorted(p.name for p in ws.iterdir()) == ["a.txt"]
    assert (ws / "a.txt").read_text() == "alpha\n"


def test_tool_definitions_have_usable_schemas(ws):
    for tool in (FileRead(ws), FileSearch(ws), FileEdit(ws)):
        definition = tool.definition()
        assert definition.description and definition.input_schema["type"] == "object"
        assert "properties" in definition.input_schema
