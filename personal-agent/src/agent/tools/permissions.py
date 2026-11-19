import os
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


def resolve(path: str | os.PathLike[str]) -> Path:
    # realpath follows symlinks, so a link pointing out of a root is caught.
    return Path(os.path.realpath(path))


@dataclass(frozen=True)
class Grant:
    tool: str
    roots: tuple[Path, ...] = ()


class Permissions:
    def __init__(self, grants: Iterable[Grant]) -> None:
        self._roots: dict[str, list[Path]] = {}
        for grant in grants:
            self._roots.setdefault(grant.tool, []).extend(resolve(r) for r in grant.roots)

    def allows_tool(self, tool: str) -> bool:
        return tool in self._roots

    def check(self, tool: str, paths: Iterable[Path]) -> str | None:
        # Returns a denial reason, or None if every path is within a granted root.
        if tool not in self._roots:
            return f"tool not granted for this run: {tool}"
        roots = self._roots[tool]
        for path in paths:
            resolved = resolve(path)
            if not any(resolved.is_relative_to(root) for root in roots):
                return f"path is outside the granted scope: {resolved}"
        return None


@dataclass(frozen=True)
class ApprovalRequest:
    invocation_id: str
    tool: str
    summary: str


Approver = Callable[[ApprovalRequest], Awaitable[bool]]
