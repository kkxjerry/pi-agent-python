from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SettingValidator = Callable[[Any], Any]


@dataclass(frozen=True, slots=True)
class SettingSource:
    layer: str
    location: str
    key: str


@dataclass(frozen=True, slots=True)
class SettingSpec:
    default: Any
    validator: SettingValidator


class Settings:
    def __init__(
        self,
        values: dict[str, Any],
        sources: dict[str, SettingSource],
        specs: dict[str, SettingSpec],
    ) -> None:
        self._values = dict(values)
        self._sources = dict(sources)
        self._specs = dict(specs)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]

    def source(self, key: str) -> SettingSource | None:
        return self._sources.get(key)

    def as_flat_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def as_dict(self) -> dict[str, Any]:
        return unflatten(self._values)

    def with_runtime(self, values: dict[str, Any]) -> Settings:
        merged = dict(self._values)
        sources = dict(self._sources)
        for key, value in flatten(values).items():
            spec = self._specs.get(key)
            if spec is None:
                raise ValueError(f"Unknown setting: {key}")
            merged[key] = spec.validator(value)
            sources[key] = SettingSource("runtime", "runtime", key)
        return Settings(merged, sources, self._specs)


DEFAULT_SPECS: dict[str, SettingSpec] = {
    "model.provider": SettingSpec("openai", _string),
    "model.id": SettingSpec("gpt-5.6", _string),
    "model.api": SettingSpec("openai-completions", _string),
    "model.base_url": SettingSpec("https://api.openai.com/v1", _string),
    "model.context_window": SettingSpec(128_000, _positive_int),
    "model.max_tokens": SettingSpec(16_384, _positive_int),
    "thinking.level": SettingSpec("off", _thinking_level),
    "provider.transport": SettingSpec("sse", _transport),
    "provider.timeout": SettingSpec(600.0, _positive_number),
    "provider.stream_idle_timeout": SettingSpec(600.0, _positive_number),
    "provider.max_retries": SettingSpec(2, _non_negative_int),
    "provider.max_retry_delay": SettingSpec(60.0, _non_negative_number),
    "compaction.enabled": SettingSpec(True, _boolean),
    "compaction.reserve_tokens": SettingSpec(16_384, _non_negative_int),
    "compaction.keep_recent_tokens": SettingSpec(20_000, _non_negative_int),
    "compaction.retry_count": SettingSpec(1, _non_negative_int),
    "tools.enabled": SettingSpec(["read", "write", "edit", "bash"], _string_list),
    "tools.timeout": SettingSpec(120.0, _positive_number),
    "resources.paths": SettingSpec([], _string_list),
    "resources.include": SettingSpec(["**"], _string_list),
    "resources.exclude": SettingSpec([], _string_list),
    "resources.follow_symlinks": SettingSpec(False, _boolean),
    "packages.enabled": SettingSpec([], _string_list),
    "session.enabled": SettingSpec(True, _boolean),
    "session.directory": SettingSpec("~/.pi/sessions", _string),
    "session.fsync": SettingSpec(False, _boolean),
    "theme": SettingSpec("default", _string),
    "keybindings": SettingSpec({}, _dict),
    "telemetry.enabled": SettingSpec(False, _boolean),
}

_ENV_KEYS = {
    "PI_MODEL_PROVIDER": "model.provider",
    "PI_MODEL": "model.id",
    "PI_MODEL_API": "model.api",
    "PI_BASE_URL": "model.base_url",
    "PI_CONTEXT_WINDOW": "model.context_window",
    "PI_MAX_TOKENS": "model.max_tokens",
    "PI_THINKING_LEVEL": "thinking.level",
    "PI_TRANSPORT": "provider.transport",
    "PI_TIMEOUT": "provider.timeout",
    "PI_STREAM_IDLE_TIMEOUT": "provider.stream_idle_timeout",
    "PI_MAX_RETRIES": "provider.max_retries",
    "PI_MAX_RETRY_DELAY": "provider.max_retry_delay",
    "PI_SESSION_DIR": "session.directory",
    "PI_THEME": "theme",
    "PI_TELEMETRY": "telemetry.enabled",
}


class SettingsResolver:
    def __init__(self, specs: dict[str, SettingSpec] | None = None) -> None:
        self.specs = dict(specs or DEFAULT_SPECS)

    def resolve(
        self,
        *,
        global_path: str | Path | None = None,
        project_path: str | Path | None = None,
        environment: dict[str, str] | None = None,
        cli: dict[str, Any] | None = None,
        runtime: dict[str, Any] | None = None,
    ) -> Settings:
        values = {key: spec.validator(spec.default) for key, spec in self.specs.items()}
        sources = {key: SettingSource("default", "built-in", key) for key in self.specs}
        layers: list[tuple[str, str, dict[str, Any]]] = []
        for layer, path_value in (("global", global_path), ("project", project_path)):
            if path_value is None:
                continue
            path = Path(path_value).expanduser().resolve()
            if path.exists():
                layers.append((layer, str(path), load_settings_file(path)))
        layers.append(("environment", "environment", self._environment_values(environment)))
        if cli:
            layers.append(("cli", "command line", cli))
        if runtime:
            layers.append(("runtime", "runtime", runtime))
        for layer, location, payload in layers:
            self._apply(values, sources, payload, layer=layer, location=location)
        self._validate_cross_fields(values)
        return Settings(values, sources, self.specs)

    def _apply(
        self,
        values: dict[str, Any],
        sources: dict[str, SettingSource],
        payload: dict[str, Any],
        *,
        layer: str,
        location: str,
    ) -> None:
        for key, raw in flatten(payload).items():
            spec = self.specs.get(key)
            if spec is None:
                raise ValueError(f"Unknown setting {key!r} in {location}")
            try:
                value = spec.validator(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid setting {key!r} in {location}: {exc}") from exc
            values[key] = value
            sources[key] = SettingSource(layer, location, key)

    def _environment_values(self, environment: dict[str, str] | None) -> dict[str, Any]:
        env = environment if environment is not None else dict(os.environ)
        result: dict[str, Any] = {}
        for variable, key in _ENV_KEYS.items():
            if variable in env:
                result[key] = _parse_environment_value(env[variable])
        for variable, value in env.items():
            if not variable.startswith("PI_SETTING__"):
                continue
            key = variable[len("PI_SETTING__") :].lower().replace("__", ".")
            result[key] = _parse_environment_value(value)
        return result

    @staticmethod
    def _validate_cross_fields(values: dict[str, Any]) -> None:
        reserve = int(values["compaction.reserve_tokens"])
        keep = int(values["compaction.keep_recent_tokens"])
        context = int(values["model.context_window"])
        if reserve >= context:
            raise ValueError("compaction.reserve_tokens must be smaller than model.context_window")
        if keep >= context:
            raise ValueError(
                "compaction.keep_recent_tokens must be smaller than model.context_window"
            )


class SettingsStore:
    def __init__(
        self,
        *,
        global_path: str | Path,
        project_path: str | Path,
    ) -> None:
        self.global_path = Path(global_path).expanduser().resolve()
        self.project_path = Path(project_path).expanduser().resolve()

    def save_global(self, values: Settings | dict[str, Any]) -> Path:
        return save_settings_file(self.global_path, _settings_payload(values))

    def save_project(self, values: Settings | dict[str, Any]) -> Path:
        return save_settings_file(self.project_path, _settings_payload(values))


def load_settings_file(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    raw = source.read_bytes()
    if source.suffix.lower() == ".toml":
        value = tomllib.loads(raw.decode("utf-8"))
    else:
        value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Settings file must contain an object: {source}")
    return value


def save_settings_file(path: str | Path, values: dict[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".toml":
        content = _render_toml(values)
    else:
        content = json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)
    return target


def flatten(values: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and full not in DEFAULT_SPECS:
            result.update(flatten(value, full))
        else:
            result[full] = value
    return result


def unflatten(values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        cursor = result
        parts = key.split(".")
        for part in parts[:-1]:
            child = cursor.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"Setting path collision at {key!r}")
            cursor = child
        cursor[parts[-1]] = value
    return result


def _settings_payload(values: Settings | dict[str, Any]) -> dict[str, Any]:
    if isinstance(values, Settings):
        return values.as_dict()
    return values


def _render_toml(values: dict[str, Any]) -> str:
    lines: list[str] = []
    flat = flatten(values)
    grouped: dict[str, dict[str, Any]] = {}
    root: dict[str, Any] = {}
    for key, value in flat.items():
        if "." in key:
            section, field = key.rsplit(".", 1)
            grouped.setdefault(section, {})[field] = value
        else:
            root[key] = value
    for key, value in sorted(root.items()):
        lines.append(f"{key} = {_toml_value(value)}")
    for section, fields in sorted(grouped.items()):
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in sorted(fields.items()):
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def _toml_value(value: Any) -> str:
    if value is None:
        raise ValueError("TOML settings cannot store null values")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        pairs = ", ".join(f"{key} = {_toml_value(item)}" for key, item in sorted(value.items()))
        return "{ " + pairs + " }"
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")


def _parse_environment_value(value: str) -> Any:
    stripped = value.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def _string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "1", "yes", "on"}:
        return True
    if isinstance(value, str) and value.lower() in {"false", "0", "no", "off"}:
        return False
    raise ValueError("must be a boolean")


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("must be a positive integer")
    return value


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("must be a non-negative integer")
    return value


def _positive_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("must be a positive number")
    return float(value)


def _non_negative_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError("must be a non-negative number")
    return float(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("must be an array of strings")
    return list(value)


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("must be an object")
    return dict(value)


def _thinking_level(value: Any) -> str:
    value = _string(value)
    if value not in {"off", "minimal", "low", "medium", "high", "xhigh", "max"}:
        raise ValueError("must be a supported thinking level")
    return value


def _transport(value: Any) -> str:
    value = _string(value)
    if value not in {"sse", "websocket", "websocket-cached", "auto"}:
        raise ValueError("must be sse, websocket, websocket-cached, or auto")
    return value
