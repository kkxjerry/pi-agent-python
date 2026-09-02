from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypeAlias

from pi_agent.ai import CancellationToken

from .result import Result

FileKind: TypeAlias = Literal["file", "directory", "symlink"]
FileErrorCode: TypeAlias = Literal[
    "aborted",
    "not_found",
    "permission_denied",
    "not_directory",
    "is_directory",
    "invalid",
    "not_supported",
    "unknown",
]
ExecutionErrorCode: TypeAlias = Literal[
    "aborted",
    "timeout",
    "shell_unavailable",
    "spawn_error",
    "callback_error",
    "unknown",
]
CompactionErrorCode: TypeAlias = Literal["aborted", "summarization_failed"]
BranchSummaryErrorCode: TypeAlias = Literal["aborted", "summarization_failed"]
StreamName: TypeAlias = Literal["stdout", "stderr"]
ChunkCallback: TypeAlias = Callable[[str], Awaitable[None] | None]
ShellOutputCallback: TypeAlias = Callable[[StreamName, str], Awaitable[None] | None]


class FileError(Exception):
    def __init__(
        self,
        code: FileErrorCode,
        message: str,
        path: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.__cause__ = cause


class ExecutionError(Exception):
    def __init__(
        self,
        code: ExecutionErrorCode,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.__cause__ = cause


class CompactionError(Exception):
    def __init__(
        self,
        code: CompactionErrorCode,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.__cause__ = cause


class BranchSummaryError(Exception):
    def __init__(
        self,
        code: BranchSummaryErrorCode,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.__cause__ = cause


@dataclass(frozen=True, slots=True)
class FileInfo:
    name: str
    path: str
    kind: FileKind
    size: int
    mtime_ms: float

    @property
    def modified_ns(self) -> int:
        return int(self.mtime_ms * 1_000_000)


@dataclass(slots=True)
class ShellExecOptions:
    cwd: str | Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    inherit_env: bool = True
    timeout: float | None = None
    abort_signal: CancellationToken | None = None
    signal: CancellationToken | None = None
    on_stdout: ChunkCallback | None = None
    on_stderr: ChunkCallback | None = None
    on_output: ShellOutputCallback | None = None
    shell: str | None = None

    @property
    def active_signal(self) -> CancellationToken | None:
        return self.abort_signal or self.signal


@dataclass(frozen=True, slots=True)
class ShellResult:
    stdout: str
    stderr: str
    exit_code: int
    command: str = ""
    duration_seconds: float = 0.0

    @property
    def output(self) -> str:
        if self.stdout and self.stderr:
            return f"{self.stdout}\n{self.stderr}"
        return self.stdout or self.stderr


class FileSystem(Protocol):
    cwd: str

    async def absolute_path(
        self, path: str, signal: CancellationToken | None = None
    ) -> Result[str, FileError]: ...

    async def join_path(
        self, parts: list[str], signal: CancellationToken | None = None
    ) -> Result[str, FileError]: ...

    async def read_text_file(
        self, path: str, signal: CancellationToken | None = None
    ) -> Result[str, FileError]: ...

    async def read_text_lines(
        self,
        path: str,
        *,
        max_lines: int | None = None,
        signal: CancellationToken | None = None,
    ) -> Result[list[str], FileError]: ...

    async def read_binary_file(
        self, path: str, signal: CancellationToken | None = None
    ) -> Result[bytes, FileError]: ...

    async def write_file(
        self,
        path: str,
        content: str | bytes,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]: ...

    async def append_file(
        self,
        path: str,
        content: str | bytes,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]: ...

    async def rename_file(
        self,
        source_path: str,
        destination_path: str,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]: ...

    async def file_info(
        self, path: str, signal: CancellationToken | None = None
    ) -> Result[FileInfo, FileError]: ...

    async def list_dir(
        self, path: str, signal: CancellationToken | None = None
    ) -> Result[list[FileInfo], FileError]: ...

    async def canonical_path(
        self, path: str, signal: CancellationToken | None = None
    ) -> Result[str, FileError]: ...

    async def exists(
        self, path: str, signal: CancellationToken | None = None
    ) -> Result[bool, FileError]: ...

    async def create_dir(
        self,
        path: str,
        *,
        recursive: bool = True,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]: ...

    async def remove(
        self,
        path: str,
        *,
        recursive: bool = False,
        force: bool = False,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]: ...

    async def create_temp_dir(
        self,
        *,
        prefix: str = "tmp-",
        signal: CancellationToken | None = None,
    ) -> Result[str, FileError]: ...

    async def create_temp_file(
        self,
        *,
        prefix: str = "",
        suffix: str = "",
        signal: CancellationToken | None = None,
    ) -> Result[str, FileError]: ...

    async def cleanup(self) -> None: ...


class Shell(Protocol):
    async def exec(
        self,
        command: str,
        options: ShellExecOptions | None = None,
    ) -> Result[ShellResult, ExecutionError]: ...

    async def cleanup(self) -> None: ...


class ExecutionEnv(FileSystem, Shell):
    @classmethod
    def local(cls, cwd: str | Path | None = None) -> ExecutionEnv:
        from .environment import LocalExecutionEnv

        return LocalExecutionEnv(cwd)

    def resolve(self, value: str) -> Path:
        raise NotImplementedError
