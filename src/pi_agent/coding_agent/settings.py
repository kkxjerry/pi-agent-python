from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SettingsLayer = Literal["default", "global", "project", "environment", "cli", "runtime"]


class SettingsError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class SettingOrigin:
    layer: SettingsLayer
    location: str
    key: str


@dataclass(slots=True, frozen=True)
class SettingsWarning:
    code: str
    message: str
    location: str | None = None


@dataclass(slots=True, frozen=True)
class SettingSpec:
    default: Any
    coerce: Callable[[Any], Any]
    validate: Callable[[Any], bool] = lambda _value: True
    description: str = ""


@dataclass(slots=True)
class SettingsSnapshot:
    _values: dict[str, Any]
    origins: dict[str, SettingOrigin]
    warnings: tuple[SettingsWarning, ...] = ()

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]

    def source(self, key: str) -> SettingOrigin:
        try:
            return self.origins[key]
        except KeyError as exc:
            raise KeyError(f"unknown setting: {key}") from exc

    def flat(self) -> dict[str, Any]:
        return dict(self._values)

    def nested(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in self._values.items():
            _assign_nested(result, key.split("."), value)
        return result


DEFAULT_ENV_KEYS: dict[str, str] = {
    "PI_MODEL_PROVIDER": "model.provider",
    "PI_MODEL": "model.id",
    "PI_THINKING_LEVEL": "thinking.level",
    "PI_PROVIDER_TRANSPORT": "provider.transport",
    "PI_PROVIDER_TIMEOUT": "provider.timeout_seconds",
    "PI_PROVIDER_STREAM_IDLE_TIMEOUT": "provider.stream_idle_timeout_seconds",
    "PI_PROVIDER_MAX_RETRIES": "provider.max_retries",
    "PI_COMPACTION_ENABLED": "compaction.enabled",
    "PI_COMPACTION_RESERVE_TOKENS": "compaction.reserve_tokens",
    "PI_COMPACTION_KEEP_RECENT_TOKENS": "compaction.keep_recent_tokens",
    "PI_TOOLS": "tools.enabled",
    "PI_TOOL_TIMEOUT": "tools.timeout_seconds",
    "PI_RESOURCE_USER_ROOT": "resources.user_root",
    "PI_RESOURCE_PROJECT_DIR": "resources.project_dir_name",
    "PI_SESSION_DIR": "session.directory",
    "PI_SESSION_ENABLED": "session.enabled",
    "PI_THEME": "ui.theme",
    "PI_EXTENSIONS_ENABLED": "extensions.enabled",
    "PI_PACKAGES_ROOT": "packages.root",
    "PI_PACKAGES_AUTO_LOAD": "packages.auto_load",
    "PI_TELEMETRY_ENABLED": "telemetry.enabled",
}


def _optional_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SettingsError("must be a string or null")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise SettingsError("must be a non-empty string")
    return value


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise SettingsError("must be a boolean")


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        raise SettingsError("must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError("must be an integer") from exc
    return parsed


def _float(value: Any) -> float:
    if isinstance(value, bool):
        raise SettingsError("must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError("must be a number") from exc
    return parsed


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return _float(value)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise SettingsError("must be a string list")


def _string_map(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SettingsError("must be a JSON object of strings") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise SettingsError("must be an object of strings")
    return dict(value)


def _one_of(*allowed: Any) -> Callable[[Any], bool]:
    return lambda value: value in allowed


DEFAULT_SPECS: dict[str, SettingSpec] = {
    "model.provider": SettingSpec(None, _optional_string),
    "model.id": SettingSpec(None, _optional_string),
    "thinking.level": SettingSpec(
        "off", _string, _one_of("off", "minimal", "low", "medium", "high", "xhigh", "max")
    ),
    "provider.transport": SettingSpec(
        "auto", _string, _one_of("auto", "sse", "websocket", "websocket-cached")
    ),
    "provider.timeout_seconds": SettingSpec(600.0, _float, lambda value: value > 0),
    "provider.stream_idle_timeout_seconds": SettingSpec(
        None, _optional_float, lambda value: value is None or value > 0
    ),
    "provider.max_retries": SettingSpec(2, _integer, lambda value: value >= 0),
    "provider.max_retry_delay_seconds": SettingSpec(60.0, _float, lambda value: value >= 0),
    "compaction.enabled": SettingSpec(True, _boolean),
    "compaction.reserve_tokens": SettingSpec(16_384, _integer, lambda value: value >= 0),
    "compaction.keep_recent_tokens": SettingSpec(20_000, _integer, lambda value: value > 0),
    "tools.enabled": SettingSpec(("read", "write", "edit", "bash"), _string_tuple),
    "tools.timeout_seconds": SettingSpec(120.0, _float, lambda value: value > 0),
    "resources.user_root": SettingSpec("~/.pi/agent", _string),
    "resources.project_dir_name": SettingSpec(".pi", _string),
    "resources.include": SettingSpec((), _string_tuple),
    "resources.exclude": SettingSpec(("**/.git/**", "**/__pycache__/**"), _string_tuple),
    "resources.follow_symlinks": SettingSpec(False, _boolean),
    "packages.enabled": SettingSpec((), _string_tuple),
    "packages.root": SettingSpec("~/.pi/agent", _string),
    "packages.auto_load": SettingSpec(True, _boolean),
    # Compatibility-only paths retained for callers from the early product API.
    # The canonical Phase 21 runtime injects auth/model services directly.
    "auth.file": SettingSpec("~/.pi/agent/auth.json", _string),
    "models.snapshot_file": SettingSpec("~/.pi/agent/models.json", _string),
    "extensions.enabled": SettingSpec(True, _boolean),
    "extensions.trusted_project": SettingSpec((), _string_tuple),
    "session.enabled": SettingSpec(True, _boolean),
    "session.directory": SettingSpec("~/.pi/agent/sessions", _string),
    "session.fsync": SettingSpec(False, _boolean),
    "ui.theme": SettingSpec(None, _optional_string),
    "ui.keybindings": SettingSpec({}, _string_map),
    "telemetry.enabled": SettingSpec(False, _boolean),
    "telemetry.exporter": SettingSpec("none", _string),
}


class SettingsResolver:
    def __init__(
        self,
        specs: Mapping[str, SettingSpec] | None = None,
        *,
        env_keys: Mapping[str, str] | None = None,
    ) -> None:
        self.specs = dict(specs or DEFAULT_SPECS)
        self.env_keys = dict(env_keys or DEFAULT_ENV_KEYS)

    def resolve(
        self,
        *,
        global_path: str | Path | None = None,
        project_path: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        cli: Mapping[str, Any] | None = None,
        runtime: Mapping[str, Any] | None = None,
        strict_unknown: bool = True,
    ) -> SettingsSnapshot:
        values: dict[str, Any] = {}
        origins: dict[str, SettingOrigin] = {}
        warnings: list[SettingsWarning] = []
        for key, spec in self.specs.items():
            values[key] = _clone_value(spec.default)
            origins[key] = SettingOrigin("default", "built-in", key)

        if global_path is not None:
            self._apply_file(
                values,
                origins,
                warnings,
                Path(global_path),
                "global",
                strict_unknown,
            )
        if project_path is not None:
            self._apply_file(
                values,
                origins,
                warnings,
                Path(project_path),
                "project",
                strict_unknown,
            )
        self._apply_environment(
            values,
            origins,
            warnings,
            environ if environ is not None else os.environ,
            strict_unknown,
        )
        if cli:
            self._apply_mapping(
                values,
                origins,
                warnings,
                _flatten(cli),
                "cli",
                "command line",
                strict_unknown,
            )
        if runtime:
            self._apply_mapping(
                values,
                origins,
                warnings,
                _flatten(runtime),
                "runtime",
                "runtime override",
                strict_unknown,
            )
        self._validate_cross_fields(values)
        return SettingsSnapshot(values, origins, tuple(warnings))

    def _apply_file(
        self,
        values: dict[str, Any],
        origins: dict[str, SettingOrigin],
        warnings: list[SettingsWarning],
        path: Path,
        layer: Literal["global", "project"],
        strict_unknown: bool,
    ) -> None:
        if not path.exists():
            return
        mapping = load_settings_file(path)
        self._apply_mapping(
            values,
            origins,
            warnings,
            _flatten(mapping),
            layer,
            str(path),
            strict_unknown,
        )

    def _apply_environment(
        self,
        values: dict[str, Any],
        origins: dict[str, SettingOrigin],
        warnings: list[SettingsWarning],
        environ: Mapping[str, str],
        strict_unknown: bool,
    ) -> None:
        mapped = {key: environ[name] for name, key in self.env_keys.items() if name in environ}
        self._apply_mapping(
            values,
            origins,
            warnings,
            mapped,
            "environment",
            "environment",
            strict_unknown,
        )

    def _apply_mapping(
        self,
        values: dict[str, Any],
        origins: dict[str, SettingOrigin],
        warnings: list[SettingsWarning],
        mapping: Mapping[str, Any],
        layer: SettingsLayer,
        location: str,
        strict_unknown: bool,
    ) -> None:
        errors: list[str] = []
        for key, raw in mapping.items():
            spec = self.specs.get(key)
            if spec is None:
                message = f"unknown setting {key!r} in {location}"
                if strict_unknown:
                    errors.append(message)
                else:
                    warnings.append(SettingsWarning("unknown_setting", message, location))
                continue
            try:
                value = spec.coerce(raw)
            except SettingsError as exc:
                errors.append(f"{key} in {location} {exc}")
                continue
            if not spec.validate(value):
                errors.append(f"{key} in {location} has an invalid value: {raw!r}")
                continue
            values[key] = value
            origins[key] = SettingOrigin(layer, location, key)
        if errors:
            raise SettingsError("; ".join(errors))

    @staticmethod
    def _validate_cross_fields(values: Mapping[str, Any]) -> None:
        if values["compaction.keep_recent_tokens"] <= values["compaction.reserve_tokens"]:
            # This is allowed by some models, but is almost always a configuration
            # mistake because no stable recent tail can fit inside the reserve.
            raise SettingsError(
                "compaction.keep_recent_tokens must be greater than compaction.reserve_tokens"
            )


class SettingsStore:
    """Explicit persistence targets; runtime overrides are never saved implicitly."""

    def __init__(self, *, global_path: str | Path, project_path: str | Path) -> None:
        self.global_path = Path(global_path)
        self.project_path = Path(project_path)

    def save_global(self, values: Mapping[str, Any]) -> None:
        save_settings_file(self.global_path, values)

    def save_project(self, values: Mapping[str, Any]) -> None:
        save_settings_file(self.project_path, values)


def load_settings_file(path: str | Path) -> dict[str, Any]:
    actual = Path(path)
    try:
        data = actual.read_bytes()
    except OSError as exc:
        raise SettingsError(f"could not read settings file {actual}: {exc}") from exc
    try:
        if actual.suffix.lower() == ".toml":
            value = tomllib.loads(data.decode("utf-8"))
        else:
            value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SettingsError(f"invalid settings file {actual}: {exc}") from exc
    if not isinstance(value, dict):
        raise SettingsError(f"settings file {actual} must contain an object")
    return value


def save_settings_file(path: str | Path, values: Mapping[str, Any]) -> None:
    actual = Path(path)
    actual.parent.mkdir(parents=True, exist_ok=True)
    nested = _nested_from_input(values)
    if actual.suffix.lower() == ".toml":
        content = _toml_document(nested)
    else:
        content = json.dumps(nested, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    temporary = actual.with_name(f".{actual.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, actual)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping) and full not in DEFAULT_SPECS:
            result.update(_flatten(item, full))
        else:
            result[full] = item
    return result


def _assign_nested(target: dict[str, Any], parts: list[str], value: Any) -> None:
    current = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = _serializable_value(value)


def _nested_from_input(values: Mapping[str, Any]) -> dict[str, Any]:
    if any("." in key for key in values):
        result: dict[str, Any] = {}
        for key, value in values.items():
            _assign_nested(result, key.split("."), value)
        return result
    return {str(key): _serializable_value(value) for key, value in values.items()}


def _serializable_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_serializable_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serializable_value(item) for key, item in value.items()}
    return value


def _clone_value(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return tuple(value)
    return value


def _toml_document(value: Mapping[str, Any]) -> str:
    lines: list[str] = []
    scalars = {key: item for key, item in value.items() if not isinstance(item, Mapping)}
    nested = {key: item for key, item in value.items() if isinstance(item, Mapping)}
    for key, item in scalars.items():
        lines.append(f"{key} = {_toml_value(item)}")
    for section, mapping in nested.items():
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, item in mapping.items():
            if isinstance(item, Mapping):
                raise SettingsError("nested TOML tables deeper than one level are not supported")
            lines.append(f"{key} = {_toml_value(item)}")
    return "\n".join(lines) + "\n"


def _toml_value(value: Any) -> str:
    if value is None:
        raise SettingsError("TOML cannot persist null values")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        parts = [f"{key} = {_toml_value(item)}" for key, item in value.items()]
        return "{ " + ", ".join(parts) + " }"
    raise SettingsError(f"cannot serialize TOML value {value!r}")
