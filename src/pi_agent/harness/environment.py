from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import signal as process_signal
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from pi_agent.ai import CancellationToken

from .result import Result, err, get_or_raise, ok, to_error
from .types import (
    ExecutionEnv,
    ExecutionError,
    FileError,
    FileInfo,
    FileKind,
    ShellExecOptions,
    ShellOutputCallback,
    ShellResult,
    StreamName,
)

T = TypeVar("T")


class LocalExecutionEnv(ExecutionEnv):
    """Node-style local execution environment with structured failures.

    Filesystem and shell operations never expose expected OS failures as thrown
    exceptions. They return ``Result`` values and retain temporary/process
    resources for best-effort cleanup.
    """

    def __init__(self, cwd: str | Path | None = None) -> None:
        self.cwd = str(Path(cwd or Path.cwd()).expanduser().absolute())
        self._temp_paths: set[Path] = set()
        self._processes: set[asyncio.subprocess.Process] = set()

    @property
    def filesystem(self) -> LocalFileSystem:
        return LocalFileSystem(self)

    @property
    def shell(self) -> LocalShell:
        return LocalShell(self)

    def resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(self.cwd) / path
        return Path(os.path.abspath(os.path.normpath(path)))

    async def absolute_path(
        self,
        path: str,
        signal: CancellationToken | None = None,
    ) -> Result[str, FileError]:
        return await self._file_operation(path, signal, lambda: str(self.resolve(path)))

    async def join_path(
        self,
        parts: list[str],
        signal: CancellationToken | None = None,
    ) -> Result[str, FileError]:
        joined = os.path.join(*parts) if parts else self.cwd
        return await self.absolute_path(joined, signal)

    async def read_text_file(
        self,
        path: str,
        signal: CancellationToken | None = None,
    ) -> Result[str, FileError]:
        async def operation() -> str:
            data = await asyncio.to_thread(self.resolve(path).read_bytes)
            return data.decode("utf-8", errors="replace")

        return await self._file_operation_async(path, signal, operation)

    async def read_text_lines(
        self,
        path: str,
        *,
        max_lines: int | None = None,
        signal: CancellationToken | None = None,
    ) -> Result[list[str], FileError]:
        def operation() -> list[str]:
            lines: list[str] = []
            with self.resolve(path).open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    lines.append(line.rstrip("\r\n"))
                    if max_lines is not None and len(lines) >= max_lines:
                        break
            return lines

        return await self._file_operation(path, signal, operation)

    async def read_binary_file(
        self,
        path: str,
        signal: CancellationToken | None = None,
    ) -> Result[bytes, FileError]:
        return await self._file_operation(path, signal, lambda: self.resolve(path).read_bytes())

    async def write_file(
        self,
        path: str,
        content: str | bytes,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]:
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)

        def operation() -> None:
            target = self.resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
            temporary_path = Path(temporary)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, target)
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                raise

        return await self._file_operation(path, signal, operation)

    async def append_file(
        self,
        path: str,
        content: str | bytes,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]:
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)

        def operation() -> None:
            target = self.resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("ab") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())

        return await self._file_operation(path, signal, operation)

    async def rename_file(
        self,
        source_path: str,
        destination_path: str,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]:
        def operation() -> None:
            source = self.resolve(source_path)
            destination = self.resolve(destination_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)

        return await self._file_operation(source_path, signal, operation)

    async def file_info(
        self,
        path: str,
        signal: CancellationToken | None = None,
    ) -> Result[FileInfo, FileError]:
        return await self._file_operation(path, signal, lambda: self._file_info(self.resolve(path)))

    async def list_dir(
        self,
        path: str,
        signal: CancellationToken | None = None,
    ) -> Result[list[FileInfo], FileError]:
        def operation() -> list[FileInfo]:
            root = self.resolve(path)
            return sorted(
                (self._file_info(item) for item in root.iterdir()), key=lambda item: item.name
            )

        return await self._file_operation(path, signal, operation)

    async def canonical_path(
        self,
        path: str,
        signal: CancellationToken | None = None,
    ) -> Result[str, FileError]:
        return await self._file_operation(
            path,
            signal,
            lambda: str(self.resolve(path).resolve(strict=True)),
        )

    async def exists(
        self,
        path: str,
        signal: CancellationToken | None = None,
    ) -> Result[bool, FileError]:
        if signal is not None and signal.cancelled:
            return err(FileError("aborted", signal.reason, str(self.resolve(path))))
        try:
            return ok(await asyncio.to_thread(os.path.lexists, self.resolve(path)))
        except Exception as exc:
            return err(self._file_error(exc, path))

    async def create_dir(
        self,
        path: str,
        *,
        recursive: bool = True,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]:
        return await self._file_operation(
            path,
            signal,
            lambda: self.resolve(path).mkdir(parents=recursive, exist_ok=recursive),
        )

    async def remove(
        self,
        path: str,
        *,
        recursive: bool = False,
        force: bool = False,
        signal: CancellationToken | None = None,
    ) -> Result[None, FileError]:
        def operation() -> None:
            target = self.resolve(path)
            if not os.path.lexists(target):
                if force:
                    return
                raise FileNotFoundError(str(target))
            if target.is_dir() and not target.is_symlink():
                if recursive:
                    shutil.rmtree(target)
                else:
                    target.rmdir()
            else:
                target.unlink()

        return await self._file_operation(path, signal, operation)

    async def create_temp_dir(
        self,
        *,
        prefix: str = "tmp-",
        signal: CancellationToken | None = None,
    ) -> Result[str, FileError]:
        result = await self._file_operation(
            self.cwd,
            signal,
            lambda: tempfile.mkdtemp(prefix=prefix),
        )
        if result.ok:
            self._temp_paths.add(Path(result.value))
        return result

    async def create_temp_file(
        self,
        *,
        prefix: str = "",
        suffix: str = "",
        signal: CancellationToken | None = None,
    ) -> Result[str, FileError]:
        def operation() -> str:
            descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
            os.close(descriptor)
            return name

        result = await self._file_operation(self.cwd, signal, operation)
        if result.ok:
            self._temp_paths.add(Path(result.value))
        return result

    async def exec(
        self,
        command: str,
        options: ShellExecOptions | None = None,
    ) -> Result[ShellResult, ExecutionError]:
        opts = options or ShellExecOptions()
        token = opts.active_signal
        if token is not None and token.cancelled:
            return err(ExecutionError("aborted", token.reason))
        cwd = self.resolve(str(opts.cwd)) if opts.cwd is not None else Path(self.cwd)
        environment = os.environ.copy() if opts.inherit_env else {}
        environment.update(opts.env)
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=environment,
                executable=opts.shell,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
        except FileNotFoundError as exc:
            return err(ExecutionError("shell_unavailable", str(exc), exc))
        except Exception as exc:
            return err(ExecutionError("spawn_error", str(exc), exc))

        self._processes.add(process)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        stdout_task = asyncio.create_task(
            self._pump(
                process.stdout,
                "stdout",
                stdout_parts,
                opts.on_stdout,
                opts.on_output,
            )
        )
        stderr_task = asyncio.create_task(
            self._pump(
                process.stderr,
                "stderr",
                stderr_parts,
                opts.on_stderr,
                opts.on_output,
            )
        )
        wait_task = asyncio.create_task(process.wait())
        cancel_task = asyncio.create_task(token.wait()) if token is not None else None
        deadline = time.monotonic() + opts.timeout if opts.timeout is not None else None

        try:
            while True:
                timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
                waiters: set[asyncio.Task[Any]] = {wait_task}
                for task in (stdout_task, stderr_task, cancel_task):
                    if task is not None and not task.done():
                        waiters.add(task)
                done, _ = await asyncio.wait(
                    waiters,
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    await self._terminate(process)
                    return err(
                        ExecutionError(
                            "timeout",
                            f"Command timed out after {opts.timeout:g}s"
                            if opts.timeout is not None
                            else "Command timed out",
                        )
                    )
                if cancel_task is not None and cancel_task in done:
                    await self._terminate(process)
                    return err(
                        ExecutionError("aborted", token.reason if token is not None else "aborted")
                    )
                for task in (stdout_task, stderr_task):
                    if task in done:
                        callback_error = task.exception()
                        if callback_error is not None:
                            await self._terminate(process)
                            return err(
                                ExecutionError(
                                    "callback_error",
                                    str(callback_error),
                                    to_error(callback_error),
                                )
                            )
                if wait_task in done:
                    await asyncio.gather(stdout_task, stderr_task)
                    return ok(
                        ShellResult(
                            stdout="".join(stdout_parts),
                            stderr="".join(stderr_parts),
                            exit_code=wait_task.result(),
                            command=command,
                            duration_seconds=time.monotonic() - started,
                        )
                    )
        except asyncio.CancelledError as exc:
            await self._terminate(process)
            return err(ExecutionError("aborted", str(exc) or "Operation aborted", exc))
        except Exception as exc:
            await self._terminate(process)
            return err(ExecutionError("unknown", str(exc), exc))
        finally:
            for pending_task in (stdout_task, stderr_task, wait_task, cancel_task):
                if pending_task is not None and not pending_task.done():
                    pending_task.cancel()
            await asyncio.gather(
                *(
                    pending_task
                    for pending_task in (stdout_task, stderr_task, wait_task, cancel_task)
                    if pending_task is not None
                ),
                return_exceptions=True,
            )
            self._processes.discard(process)

    async def cleanup(self) -> None:
        for process in tuple(self._processes):
            await self._terminate(process)
        self._processes.clear()
        for path in tuple(self._temp_paths):
            try:
                if path.is_dir() and not path.is_symlink():
                    await asyncio.to_thread(shutil.rmtree, path, True)
                else:
                    await asyncio.to_thread(path.unlink, missing_ok=True)
            except Exception:
                pass
        self._temp_paths.clear()

    async def _file_operation(
        self,
        path: str,
        signal: CancellationToken | None,
        operation: Callable[[], T],
    ) -> Result[T, FileError]:
        if signal is not None and signal.cancelled:
            return err(FileError("aborted", signal.reason, str(self.resolve(path))))
        try:
            value = await asyncio.to_thread(operation)
            if signal is not None:
                signal.raise_if_cancelled()
            return ok(value)
        except asyncio.CancelledError as exc:
            return err(
                FileError("aborted", str(exc) or "Operation aborted", str(self.resolve(path)))
            )
        except Exception as exc:
            return err(self._file_error(exc, path))

    async def _file_operation_async(
        self,
        path: str,
        signal: CancellationToken | None,
        operation: Callable[[], Awaitable[T]],
    ) -> Result[T, FileError]:
        if signal is not None and signal.cancelled:
            return err(FileError("aborted", signal.reason, str(self.resolve(path))))
        try:
            value = await operation()
            if signal is not None:
                signal.raise_if_cancelled()
            return ok(value)
        except asyncio.CancelledError as exc:
            return err(
                FileError("aborted", str(exc) or "Operation aborted", str(self.resolve(path)))
            )
        except Exception as exc:
            return err(self._file_error(exc, path))

    @staticmethod
    def _file_info(path: Path) -> FileInfo:
        stat_result = path.lstat()
        kind: FileKind
        if path.is_symlink():
            kind = "symlink"
        elif path.is_file():
            kind = "file"
        elif path.is_dir():
            kind = "directory"
        else:
            raise OSError(f"Unsupported filesystem object: {path}")
        return FileInfo(
            name=path.name,
            path=str(path),
            kind=kind,
            size=stat_result.st_size,
            mtime_ms=stat_result.st_mtime * 1000,
        )

    def _file_error(self, error: Exception, path: str) -> FileError:
        resolved = str(self.resolve(path))
        if isinstance(error, FileNotFoundError):
            return FileError("not_found", str(error), resolved, error)
        if isinstance(error, PermissionError):
            return FileError("permission_denied", str(error), resolved, error)
        if isinstance(error, NotADirectoryError):
            return FileError("not_directory", str(error), resolved, error)
        if isinstance(error, IsADirectoryError):
            return FileError("is_directory", str(error), resolved, error)
        if isinstance(error, (ValueError, TypeError)):
            return FileError("invalid", str(error), resolved, error)
        if isinstance(error, NotImplementedError):
            return FileError("not_supported", str(error), resolved, error)
        return FileError("unknown", str(error), resolved, error)

    @staticmethod
    async def _pump(
        stream: asyncio.StreamReader | None,
        stream_name: StreamName,
        target: list[str],
        callback: Callable[[str], Awaitable[None] | None] | None,
        combined_callback: ShellOutputCallback | None,
    ) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace")
            target.append(text)
            if callback is not None:
                await _maybe_await(callback(text))
            if combined_callback is not None:
                await _maybe_await(combined_callback(stream_name, text))

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, process_signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
        except PermissionError:
            try:
                process.terminate()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
            return
        except TimeoutError:
            pass
        try:
            if os.name == "posix":
                os.killpg(process.pid, process_signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        except PermissionError:
            try:
                process.kill()
            except ProcessLookupError:
                return
        await process.wait()


class LocalFileSystem:
    """Compatibility adapter for the early Phase 11 filesystem surface."""

    def __init__(self, env: LocalExecutionEnv | str | Path | None = None) -> None:
        self.env = env if isinstance(env, LocalExecutionEnv) else LocalExecutionEnv(env)

    async def read_bytes(self, path: Path) -> bytes:
        return get_or_raise(await self.env.read_binary_file(str(path)))

    async def write_bytes_atomic(self, path: Path, data: bytes) -> None:
        get_or_raise(await self.env.write_file(str(path), data))

    async def stat(self, path: Path) -> FileInfo:
        return get_or_raise(await self.env.file_info(str(path)))

    async def exists(self, path: Path) -> bool:
        return get_or_raise(await self.env.exists(str(path)))


class LocalShell:
    """Compatibility adapter exposing the earlier ``execute`` method."""

    def __init__(self, env: LocalExecutionEnv | str | Path | None = None) -> None:
        self.env = env if isinstance(env, LocalExecutionEnv) else LocalExecutionEnv(env)

    async def execute(self, command: str, options: ShellExecOptions) -> ShellResult:
        return get_or_raise(await self.env.exec(command, options))


def create_local_execution_env(cwd: str | Path | None = None) -> ExecutionEnv:
    return LocalExecutionEnv(cwd)


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value
