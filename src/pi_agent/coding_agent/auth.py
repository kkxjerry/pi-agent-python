from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

CredentialKind = Literal["api_key", "oauth"]
RefreshCredential = Callable[["Credential"], "Credential | Awaitable[Credential]"]


class AuthStorageError(RuntimeError):
    """Raised when the credential store cannot be read or updated safely."""


@dataclass(frozen=True, slots=True)
class Credential:
    provider: str
    kind: CredentialKind
    api_key: str | None = field(default=None, repr=False)
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: float | None = None
    scopes: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("credential provider must not be empty")
        if self.kind == "api_key" and not self.api_key:
            raise ValueError("api_key credentials require api_key")
        if self.kind == "oauth" and not self.access_token:
            raise ValueError("oauth credentials require access_token")
        if self.expires_at is not None and self.expires_at < 0:
            raise ValueError("expires_at must be non-negative")
        object.__setattr__(self, "scopes", tuple(self.scopes))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def is_expired(self, *, now: float | None = None, skew_seconds: float = 30.0) -> bool:
        if self.expires_at is None:
            return False
        current = time.time() if now is None else now
        return self.expires_at <= current + max(0.0, skew_seconds)

    def secret(self) -> str:
        value = self.api_key if self.kind == "api_key" else self.access_token
        if value is None:  # guarded by __post_init__, kept defensive for decoded data
            raise AuthStorageError(f"credential for {self.provider!r} has no usable secret")
        return value

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "provider": self.provider,
            "kind": self.kind,
            "scopes": list(self.scopes),
            "metadata": dict(self.metadata),
        }
        if self.api_key is not None:
            value["apiKey"] = self.api_key
        if self.access_token is not None:
            value["accessToken"] = self.access_token
        if self.refresh_token is not None:
            value["refreshToken"] = self.refresh_token
        if self.expires_at is not None:
            value["expiresAt"] = self.expires_at
        return value

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "kind": self.kind,
            "expiresAt": self.expires_at,
            "expired": self.is_expired(),
            "scopes": list(self.scopes),
            "metadata": dict(self.metadata),
            "hasRefreshToken": self.refresh_token is not None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Credential:
        provider = value.get("provider")
        kind = value.get("kind")
        if not isinstance(provider, str) or not provider:
            raise AuthStorageError("credential.provider must be a non-empty string")
        if kind not in {"api_key", "oauth"}:
            raise AuthStorageError(f"credential.kind is invalid for provider {provider!r}")
        scopes = value.get("scopes", [])
        metadata = value.get("metadata", {})
        if not isinstance(scopes, list) or any(not isinstance(item, str) for item in scopes):
            raise AuthStorageError(f"credential.scopes must be strings for provider {provider!r}")
        if not isinstance(metadata, dict):
            raise AuthStorageError(
                f"credential.metadata must be an object for provider {provider!r}"
            )
        expires_at = value.get("expiresAt")
        if expires_at is not None and (
            isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
        ):
            raise AuthStorageError(f"credential.expiresAt must be a number for {provider!r}")
        try:
            return cls(
                provider=provider,
                kind=cast(CredentialKind, kind),
                api_key=_optional_string(value.get("apiKey"), "apiKey", provider),
                access_token=_optional_string(value.get("accessToken"), "accessToken", provider),
                refresh_token=_optional_string(value.get("refreshToken"), "refreshToken", provider),
                expires_at=float(expires_at) if expires_at is not None else None,
                scopes=tuple(scopes),
                metadata=dict(metadata),
            )
        except ValueError as exc:
            raise AuthStorageError(str(exc)) from exc


class AuthStorage:
    """Atomic, process-safe local credential storage.

    The file is plaintext by design, matching common CLI credential stores. On
    POSIX it is created with mode ``0600``. Callers that require OS keychain
    protection can inject a different credential resolver at the product layer.
    """

    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.RLock()
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    async def get(self, provider: str) -> Credential | None:
        async with self._async_lock:
            values = await asyncio.to_thread(self._load_sync)
            return values.get(provider)

    async def list(self) -> tuple[Credential, ...]:
        async with self._async_lock:
            values = await asyncio.to_thread(self._load_sync)
            return tuple(values[name] for name in sorted(values))

    async def set(self, credential: Credential) -> None:
        async with self._async_lock:
            await asyncio.to_thread(self._set_sync, credential)

    async def remove(self, provider: str) -> bool:
        async with self._async_lock:
            return await asyncio.to_thread(self._remove_sync, provider)

    async def resolve(
        self,
        provider: str,
        *,
        refresh: RefreshCredential | None = None,
        skew_seconds: float = 30.0,
    ) -> Credential | None:
        credential = await self.get(provider)
        if credential is None or not credential.is_expired(skew_seconds=skew_seconds):
            return credential
        if credential.kind != "oauth" or credential.refresh_token is None or refresh is None:
            return credential
        lock = self._refresh_locks.setdefault(provider, asyncio.Lock())
        async with lock:
            latest = await self.get(provider)
            if latest is None or not latest.is_expired(skew_seconds=skew_seconds):
                return latest
            refreshed_value = refresh(latest)
            refreshed = (
                await refreshed_value if inspect.isawaitable(refreshed_value) else refreshed_value
            )
            if refreshed.provider != provider:
                raise AuthStorageError(
                    f"refresh callback returned provider {refreshed.provider!r}; "
                    f"expected {provider!r}"
                )
            await self.set(refreshed)
            return refreshed

    def _load_sync(self) -> dict[str, Credential]:
        with self._thread_lock, _file_lock(self._lock_path):
            return self._read_unlocked()

    def _set_sync(self, credential: Credential) -> None:
        with self._thread_lock, _file_lock(self._lock_path):
            values = self._read_unlocked()
            values[credential.provider] = credential
            self._write_unlocked(values)

    def _remove_sync(self, provider: str) -> bool:
        with self._thread_lock, _file_lock(self._lock_path):
            values = self._read_unlocked()
            if provider not in values:
                return False
            del values[provider]
            self._write_unlocked(values)
            return True

    def _read_unlocked(self) -> dict[str, Credential]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AuthStorageError(f"could not read auth store {self.path}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != self.VERSION:
            raise AuthStorageError(f"unsupported auth store format: {self.path}")
        entries = raw.get("credentials", {})
        if not isinstance(entries, dict):
            raise AuthStorageError("auth store credentials must be an object")
        result: dict[str, Credential] = {}
        for provider, item in entries.items():
            if not isinstance(provider, str) or not isinstance(item, dict):
                raise AuthStorageError("auth store contains an invalid credential entry")
            credential = Credential.from_dict(item)
            if credential.provider != provider:
                raise AuthStorageError(
                    f"credential key {provider!r} does not match provider {credential.provider!r}"
                )
            result[provider] = credential
        return result

    def _write_unlocked(self, values: Mapping[str, Credential]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "credentials": {name: values[name].to_dict() for name in sorted(values)},
        }
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, sort_keys=True, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
        except Exception:
            with suppress(OSError):
                os.close(fd)
            temporary.unlink(missing_ok=True)
            raise


@dataclass(slots=True)
class CredentialResolver:
    storage: AuthStorage | None = None
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ)
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    async def api_key(
        self,
        provider: str,
        *,
        explicit: str | None = None,
        refresh: RefreshCredential | None = None,
    ) -> str | None:
        if explicit:
            return explicit
        for key in self.aliases.get(provider, _default_environment_keys(provider)):
            value = self.environment.get(key)
            if value:
                return value
        if self.storage is None:
            return None
        credential = await self.storage.resolve(provider, refresh=refresh)
        return None if credential is None else credential.secret()


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        if file.tell() == 0:
            file.write(b"\0")
            file.flush()
        file.seek(0)
        if os.name == "nt":  # pragma: no cover - exercised in Windows CI
            import msvcrt

            api = cast(Any, msvcrt)
            api.locking(file.fileno(), api.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":  # pragma: no cover - exercised in Windows CI
                import msvcrt

                api = cast(Any, msvcrt)
                api.locking(file.fileno(), api.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _optional_string(value: Any, field_name: str, provider: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise AuthStorageError(f"credential.{field_name} must be a string for {provider!r}")
    return value


def _default_environment_keys(provider: str) -> tuple[str, ...]:
    normalized = "".join(
        character if character.isalnum() else "_" for character in provider
    ).upper()
    common = {
        "OPENAI": ("OPENAI_API_KEY",),
        "ANTHROPIC": ("ANTHROPIC_API_KEY",),
        "GOOGLE": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    }
    return common.get(normalized, (f"{normalized}_API_KEY",))


__all__ = [
    "AuthStorage",
    "AuthStorageError",
    "Credential",
    "CredentialKind",
    "CredentialResolver",
    "RefreshCredential",
]
