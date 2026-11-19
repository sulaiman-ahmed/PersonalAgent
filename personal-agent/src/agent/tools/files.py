import asyncio
import fnmatch
import hashlib
import os
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal

from pydantic import Field

from .permissions import resolve
from .types import Tool, ToolFailure, ToolInput

MAX_FILE_BYTES = 5_000_000
MAX_GREP_FILE_BYTES = 2_000_000
MAX_SEARCH_RESULTS = 200
MAX_MATCH_LINE_CHARS = 300
SEARCH_DEADLINE_SECONDS = 20.0

_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
     ".pytest_cache", ".ruff_cache", ".tox"}
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_file(path: Path) -> tuple[bytes, str]:
    # Returns the raw bytes (for hashing) and the decoded text.
    try:
        if path.is_dir():
            raise ToolFailure(f"not a file (it is a directory): {path}")
        size = path.stat().st_size
    except FileNotFoundError:
        raise ToolFailure(f"file not found: {path}") from None
    except OSError as exc:
        raise ToolFailure(f"cannot access {path}: {exc.strerror}") from None
    if size > MAX_FILE_BYTES:
        raise ToolFailure(f"file is too large ({size} bytes; limit {MAX_FILE_BYTES}): {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolFailure(f"cannot read {path}: {exc.strerror}") from None
    if b"\x00" in data:
        raise ToolFailure(f"binary file, not supported: {path}")
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolFailure(f"file is not valid UTF-8: {path}") from None


class _FileTool(Tool):
    def __init__(self, workspace: Path | str) -> None:
        self._workspace = resolve(workspace)

    def _resolve(self, raw: str) -> Path:
        # Relative paths are relative to the workspace. realpath follows symlinks,
        # so the path the gateway authorizes is the path the tool operates on.
        return resolve(self._workspace / raw)


# ---------------------------------------------------------------- file.read


class ReadInput(ToolInput):
    path: str = Field(description="File path, absolute or relative to the workspace.")
    offset: int = Field(default=1, ge=1, description="1-based line number to start from.")
    limit: int = Field(default=2000, ge=1, le=10_000, description="Maximum lines to return.")


class FileRead(_FileTool):
    name: ClassVar[str] = "file.read"
    description: ClassVar[str] = (
        "Read a UTF-8 text file. Output starts with the file's sha256 (of the whole file), "
        "then numbered lines ('<number><TAB><text>'; the numbers are not part of the file). "
        "Pass the sha256 to file.edit as expected_sha256."
    )
    input_model: ClassVar[type[ToolInput]] = ReadInput

    def resources(self, args: ReadInput) -> list[Path]:
        return [self._resolve(args.path)]

    async def run(self, args: ReadInput) -> str:
        return await asyncio.to_thread(self._read, self._resolve(args.path), args)

    @staticmethod
    def _read(path: Path, args: ReadInput) -> str:
        data, text = _read_file(path)
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        total = len(lines)
        header = f"path: {path}\nsha256: {_sha256(data)}\n"
        if total == 0:
            return header + "(empty file)"
        if args.offset > total:
            raise ToolFailure(f"offset {args.offset} is past the end of the file ({total} lines)")
        end = min(total, args.offset - 1 + args.limit)
        body = "\n".join(f"{n:>6}\t{lines[n - 1]}" for n in range(args.offset, end + 1))
        return f"{header}lines {args.offset}-{end} of {total}\n{body}"


# -------------------------------------------------------------- file.search


class SearchInput(ToolInput):
    mode: Literal["glob", "grep"] = Field(
        description="'glob' finds file paths; 'grep' finds lines containing the text."
    )
    pattern: str = Field(
        min_length=1,
        description=(
            "For glob: a path pattern such as '*.py' (matches file names) or 'src/**/*.py' "
            "(matches paths relative to the search directory). "
            "For grep: literal text to find. It is NOT a regular expression."
        ),
    )
    path: str = Field(default=".", description="Directory to search; default is the workspace.")
    include: str | None = Field(
        default=None, description="grep only: glob restricting which files are searched."
    )
    ignore_case: bool = Field(default=False, description="grep only: case-insensitive match.")


def _glob_match(rel: str, name: str, pattern: str) -> bool:
    if "/" in pattern:
        return PurePosixPath(rel).full_match(pattern)
    return fnmatch.fnmatchcase(name, pattern)


class FileSearch(_FileTool):
    name: ClassVar[str] = "file.search"
    description: ClassVar[str] = (
        "Search files under a directory. mode='glob' lists matching file paths; "
        "mode='grep' lists 'path:line: text' for lines containing literal text. "
        "Skips .git, .venv, node_modules and similar directories. Results are capped."
    )
    input_model: ClassVar[type[ToolInput]] = SearchInput

    def resources(self, args: SearchInput) -> list[Path]:
        return [self._resolve(args.path)]

    async def run(self, args: SearchInput) -> str:
        return await asyncio.to_thread(self._search, self._resolve(args.path), args)

    @staticmethod
    def _search(root: Path, args: SearchInput) -> str:
        if not root.is_dir():
            raise ToolFailure(f"not a directory: {root}")
        deadline = time.monotonic() + SEARCH_DEADLINE_SECONDS
        needle = args.pattern.lower() if args.ignore_case else args.pattern
        results: list[str] = []
        stopped = ""

        for dirpath, dirnames, filenames in os.walk(root):  # does not follow symlinked dirs
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            for name in sorted(filenames):
                if time.monotonic() > deadline:
                    stopped = "time limit reached"
                    break
                full = Path(dirpath) / name
                if not resolve(full).is_relative_to(root):
                    continue  # symlink pointing outside the search root
                rel = full.relative_to(root).as_posix()
                if args.mode == "glob":
                    if _glob_match(rel, name, args.pattern):
                        results.append(rel)
                else:
                    if args.include and not _glob_match(rel, name, args.include):
                        continue
                    results.extend(_grep_file(full, rel, needle, args.ignore_case))
                if len(results) >= MAX_SEARCH_RESULTS:
                    stopped = f"result limit ({MAX_SEARCH_RESULTS}) reached"
                    break
            if stopped:
                break

        results = results[:MAX_SEARCH_RESULTS]
        if args.mode == "glob":
            results.sort()
        if not results:
            return "no matches" + (f" [search stopped: {stopped}]" if stopped else "")
        out = "\n".join(results)
        return out + (f"\n[search stopped: {stopped}]" if stopped else "")


def _grep_file(path: Path, rel: str, needle: str, ignore_case: bool) -> list[str]:
    try:
        if path.stat().st_size > MAX_GREP_FILE_BYTES:
            return []
        data = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in data[:8192]:
        return []
    matches = []
    for number, line in enumerate(data.decode("utf-8", errors="replace").split("\n"), start=1):
        if needle in (line.lower() if ignore_case else line):
            matches.append(f"{rel}:{number}: {line[:MAX_MATCH_LINE_CHARS]}")
    return matches


# ---------------------------------------------------------------- file.edit


class EditInput(ToolInput):
    path: str = Field(description="File path, absolute or relative to the workspace.")
    old_string: str = Field(
        description=(
            "Exact text to replace; must occur exactly once in the file. "
            "Leave empty only when creating a new file."
        )
    )
    new_string: str = Field(description="Replacement text (or the full content of a new file).")
    expected_sha256: str | None = Field(
        default=None,
        description=(
            "The sha256 reported by file.read for the version you are editing. "
            "Required for existing files; omit only when creating a new file."
        ),
    )


class FileEdit(_FileTool):
    name: ClassVar[str] = "file.edit"
    description: ClassVar[str] = (
        "Edit a UTF-8 text file by replacing one exact occurrence of old_string with new_string. "
        "Existing files require expected_sha256 from file.read; if the file changed since, the "
        "edit is refused and you must read it again. To create a new file, omit expected_sha256 "
        "and leave old_string empty. Returns the new sha256, which can be used for a follow-up edit."
    )
    input_model: ClassVar[type[ToolInput]] = EditInput

    def resources(self, args: EditInput) -> list[Path]:
        return [self._resolve(args.path)]

    def describe(self, args: EditInput) -> str:
        return f"edit {self._resolve(args.path)}"

    async def run(self, args: EditInput) -> str:
        return await asyncio.to_thread(self._edit, self._resolve(args.path), args)

    @staticmethod
    def _edit(path: Path, args: EditInput) -> str:
        if args.expected_sha256 is None:
            return _create(path, args)

        if args.old_string == "":
            raise ToolFailure("old_string must not be empty when editing an existing file")
        data, text = _read_file(path)
        actual = _sha256(data)
        if actual != args.expected_sha256.strip().lower():
            raise ToolFailure(
                f"file has changed since it was read (current sha256 {actual}); "
                "read it again before editing"
            )
        count = text.count(args.old_string)
        if count == 0:
            raise ToolFailure("old_string was not found in the file")
        if count > 1:
            raise ToolFailure(
                f"old_string matches {count} places; include more surrounding text to make it unique"
            )
        if args.old_string == args.new_string:
            raise ToolFailure("old_string and new_string are identical; nothing to change")

        new_bytes = text.replace(args.old_string, args.new_string, 1).encode("utf-8")
        if len(new_bytes) > MAX_FILE_BYTES:
            raise ToolFailure(f"result would exceed the {MAX_FILE_BYTES} byte file size limit")
        _replace_atomically(path, new_bytes, expected_sha256=actual)
        return f"edited {path}\nsha256: {_sha256(new_bytes)}"


def _create(path: Path, args: EditInput) -> str:
    if args.old_string != "":
        raise ToolFailure(
            "expected_sha256 is required to edit an existing file (get it from file.read); "
            "to create a new file, leave old_string empty"
        )
    if os.path.lexists(path):
        raise ToolFailure(
            f"file already exists: {path}; read it and pass its sha256 as expected_sha256 to edit it"
        )
    if not path.parent.is_dir():
        raise ToolFailure(f"parent directory does not exist: {path.parent}")
    new_bytes = args.new_string.encode("utf-8")
    if len(new_bytes) > MAX_FILE_BYTES:
        raise ToolFailure(f"content exceeds the {MAX_FILE_BYTES} byte file size limit")

    tmp = _write_temp(path.parent, new_bytes, mode=0o644)
    try:
        # A hard link fails if the target exists, so this cannot overwrite a file
        # that appeared after the check above.
        os.link(tmp, path)
    except FileExistsError:
        raise ToolFailure(f"file already exists: {path}") from None
    except OSError as exc:
        raise ToolFailure(f"cannot create {path}: {exc.strerror}") from None
    finally:
        os.unlink(tmp)
    return f"created {path}\nsha256: {_sha256(new_bytes)}"


def _write_temp(directory: Path, data: bytes, mode: int) -> str:
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".agent-edit-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp


def _replace_atomically(path: Path, data: bytes, *, expected_sha256: str) -> None:
    try:
        mode = path.stat().st_mode & 0o7777
        tmp = _write_temp(path.parent, data, mode)
    except OSError as exc:
        raise ToolFailure(f"cannot write {path}: {exc.strerror}") from None
    try:
        # Re-verify immediately before the swap to narrow the window for a concurrent change.
        if _sha256(path.read_bytes()) != expected_sha256:
            raise ToolFailure("file changed while editing; read it again and retry")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
