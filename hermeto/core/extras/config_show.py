# SPDX-License-Identifier: GPL-3.0-only
"""Configuration introspection utilities.

Provides functions to dump the current effective configuration, generate
corresponding environment variable names, compute differences against
default values, and determine which source provided each value.
"""

import os
from pathlib import Path
from typing import Any

import yaml

from hermeto.core.config import CONFIG_FILE_PATHS, Config, normalize_config_data

# Type aliases for configuration diff and source-tracking structures.
ConfigValue = str | int | float | bool | None | dict[str, Any] | list[Any]
FieldDiff = tuple[ConfigValue, ConfigValue]  # (current_value, default_value)
# Recursive: a section maps field names to FieldDiffs, or to further nested sections.
ConfigDiff = dict[str, "FieldDiff | ConfigDiff"]

# Recursive: leaf values are source label strings, nested dicts mirror config structure.
ConfigSources = dict[str, "str | ConfigSources"]


def _get_env_var_name(*parts: str) -> str:
    """Reconstruct the environment variable name from config key parts.

    Derives the prefix and nested delimiter from Config.model_config rather than
    hardcoding them, so that changes to the configuration schema are automatically
    reflected.

    >>> _get_env_var_name("gomod", "proxy_url")
    'HERMETO_GOMOD__PROXY_URL'
    >>> _get_env_var_name("runtime", "concurrency_limit")
    'HERMETO_RUNTIME__CONCURRENCY_LIMIT'
    """
    prefix = Config.model_config.get("env_prefix") or ""
    delimiter = Config.model_config.get("env_nested_delimiter") or "__"
    return f"{prefix}{delimiter.join(part.upper() for part in parts)}"


def get_effective_config(config: Config, *, raw: bool = False) -> dict[str, Any]:
    """Get the current effective configuration as a nested dict.

    Uses Pydantic's model_dump(mode='json') for serialization, which handles
    SecretStr redaction, Enum conversion, HttpUrl stringification, etc.
    SecretStr fields are redacted unless raw=True.
    """
    context = {"reveal": True} if raw else {}
    return config.model_dump(mode="json", context=context)


def get_default_config() -> dict[str, Any]:
    """Get the default configuration values.

    Uses field.default from Config.model_fields rather than Config() because
    Config extends BaseSettings, so Config() would read from env vars and
    config files instead of returning pure schema defaults.
    """
    result: dict[str, Any] = {}
    for name, field in Config.model_fields.items():
        default = field.default
        if hasattr(type(default), "model_fields"):
            result[name] = default.model_dump(mode="json")
        else:
            result[name] = default.value if hasattr(default, "value") else default
    return result


def get_config_diff(
    effective: dict[str, Any],
    defaults: dict[str, Any],
) -> ConfigDiff:
    """Compare effective config against defaults.

    Recursively walks nested dicts and returns only values that differ.
    For nested dicts, produces nested diff dicts.
    For leaf values, produces a FieldDiff tuple of (current, default).
    """
    diff: ConfigDiff = {}

    for key, current_value in effective.items():
        default_value = defaults.get(key)

        if isinstance(current_value, dict) and isinstance(default_value, dict):
            sub_diff = get_config_diff(current_value, default_value)
            if sub_diff:
                diff[key] = sub_diff
        elif current_value != default_value:
            diff[key] = (current_value, default_value)

    return diff


def _collect_fields_from_dict(
    data: dict[str, Any],
    label: str,
    result: dict[tuple[str, ...], str],
    prefix: tuple[str, ...] = (),
) -> None:
    """Recursively collect leaf-field paths from a nested dict.

    Each leaf is recorded as ``result[path_tuple] = label``.
    """
    for key, value in data.items():
        current = prefix + (key,)
        if isinstance(value, dict):
            _collect_fields_from_dict(value, label, result, current)
        else:
            result[current] = label


def get_config_sources(
    effective: dict[str, Any],
    config_file_path: Path | None = None,
) -> ConfigSources:
    """Determine which source provided each effective config value.

    Checks sources in descending priority order (environment variables
    first, then config files, then schema defaults) and returns a nested
    dict that mirrors the structure of *effective* with source label
    strings at the leaves.

    Source labels:

    * ``"default"`` -- the value comes from the schema default.
    * ``"env"`` -- the value was set via an environment variable.
    * ``"file: <path>"`` -- the value was read from a YAML config file.

    >>> sources = get_config_sources({"runtime": {"concurrency_limit": 5}})
    >>> sources["runtime"]["concurrency_limit"]
    'default'
    """
    # --- environment variables -------------------------------------------------
    prefix = Config.model_config.get("env_prefix") or ""
    delimiter = Config.model_config.get("env_nested_delimiter") or "__"
    known_sections = set(Config.model_fields)
    env_fields: dict[tuple[str, ...], str] = {}
    for key in os.environ:
        if key.startswith(prefix):
            remainder = key[len(prefix) :]
            parts = tuple(p.lower() for p in remainder.split(delimiter))
            if parts and parts[0] not in known_sections:
                continue
            env_fields[parts] = key

    # --- config files (ascending priority, later entries overwrite earlier) -----
    file_fields: dict[tuple[str, ...], str] = {}
    for path_str in CONFIG_FILE_PATHS:
        path = Path(path_str).expanduser()
        if path.exists():
            try:
                raw = yaml.safe_load(path.read_text())
                if isinstance(raw, dict):
                    normalized = normalize_config_data(dict(raw))
                    _collect_fields_from_dict(normalized, path_str, file_fields)
            except Exception:  # noqa: S112
                # Skip unreadable files; the main Config() path will report them
                continue

    if config_file_path and config_file_path.exists():
        try:
            raw = yaml.safe_load(config_file_path.read_text())
            if isinstance(raw, dict):
                normalized = normalize_config_data(dict(raw))
                _collect_fields_from_dict(normalized, str(config_file_path), file_fields)
        except Exception:  # noqa: S110
            pass  # Skip unreadable CLI config; the main Config() path will report it

    # --- walk effective config and assign sources ------------------------------
    def _walk(
        data: dict[str, Any],
        path: tuple[str, ...],
    ) -> ConfigSources:
        sources: ConfigSources = {}
        for key, value in data.items():
            current = path + (key,)
            # Non-empty dicts are config sections; recurse into them.
            # Empty dicts and all other types are leaf values.
            if isinstance(value, dict) and value:
                sources[key] = _walk(value, current)
            elif current in env_fields:
                sources[key] = "env"
            elif current in file_fields:
                sources[key] = f"file: {file_fields[current]}"
            else:
                sources[key] = "default"
        return sources

    return _walk(effective, ())


def format_yaml_output(
    effective: dict[str, Any],
    defaults: dict[str, Any],
    sources: ConfigSources | None = None,
) -> str:
    """Format effective config as YAML with env var comments and diff markers.

    Produces valid, parseable YAML. Env var names are shown as comments above
    each field. Values that differ from defaults are marked with ``# (*)``.
    When *sources* is provided, each env-var comment also shows the source
    that provided the value (``[default]``, ``[env]``, or ``[file: ...]``).

    The output can be piped to a file and parsed by a YAML processor.
    """
    if sources is not None:
        lines: list[str] = [
            "# Current effective configuration",
            "# Values marked with (*) differ from defaults",
            "# Source shown in brackets: [default], [env], [file: <path>]",
            "",
        ]
    else:
        lines = [
            "# Current effective configuration",
            "# Values marked with (*) differ from defaults",
            "# Environment variables shown in comments",
            "",
        ]

    lines = _walk_yaml_lines(
        effective,
        defaults,
        lines,
        depth=0,
        env_parts=[],
        sources=sources,
    )

    return "\n".join(lines)


def _walk_yaml_lines(
    data: dict[str, Any],
    defaults: dict[str, Any],
    lines: list[str],
    depth: int,
    env_parts: list[str],
    sources: ConfigSources | None = None,
) -> list[str]:
    """Recursively build YAML output lines with env var comments and diff markers."""
    indent = "  " * depth

    for key, value in data.items():
        default_value = defaults.get(key)
        current_env_parts = env_parts + [key]

        if isinstance(value, dict) and value:
            lines.append(f"{indent}{key}:")
            sub_defaults = default_value if isinstance(default_value, dict) else {}
            sub_sources_raw = sources.get(key) if sources else None
            sub_sources: ConfigSources | None = (
                sub_sources_raw if isinstance(sub_sources_raw, dict) else None
            )
            lines = _walk_yaml_lines(
                value,
                sub_defaults,
                lines,
                depth + 1,
                current_env_parts,
                sub_sources,
            )
            if depth == 0:
                lines.append("")
        else:
            env_var = _get_env_var_name(*current_env_parts)
            source_label = ""
            if sources is not None:
                src = sources.get(key, "default")
                if isinstance(src, str):
                    source_label = f"  [{src}]"
            lines.append(f"{indent}# {env_var}{source_label}")
            yaml_value = _format_yaml_value(value)
            if value != default_value:
                lines.append(f"{indent}{key}: {yaml_value}  # (*)")
            else:
                lines.append(f"{indent}{key}: {yaml_value}")
            if depth == 0:
                lines.append("")

    return lines


def format_diff_output(
    diff: ConfigDiff,
) -> str:
    """Format only changed values, showing current and default values.

    Produces valid, parseable YAML with default values shown in comments.

    >>> format_diff_output({"http": {"read_timeout": (600, 300)}})
    '# Only showing values that differ from defaults\\n\\nhttp:\\n  read_timeout: 600  # default: 300\\n'
    >>> format_diff_output({})
    '# All values are at their defaults'
    """
    if not diff:
        return "# All values are at their defaults"

    lines: list[str] = [
        "# Only showing values that differ from defaults",
        "",
    ]

    lines = _walk_diff_lines(diff, lines, depth=0)

    return "\n".join(lines)


def _walk_diff_lines(
    diff: ConfigDiff,
    lines: list[str],
    depth: int,
) -> list[str]:
    """Recursively build diff output lines."""
    indent = "  " * depth

    for key, value in diff.items():
        if isinstance(value, tuple):
            current_value, default_value = value
            yaml_current = _format_yaml_value(current_value)
            yaml_default = _format_yaml_value(default_value)
            lines.append(f"{indent}{key}: {yaml_current}  # default: {yaml_default}")
            if depth == 0:
                lines.append("")
        else:
            lines.append(f"{indent}{key}:")
            lines = _walk_diff_lines(value, lines, depth + 1)
            if depth == 0:
                lines.append("")

    return lines


def _format_yaml_value(value: ConfigValue) -> str:
    """Format a single value for inline YAML representation.

    Uses yaml.dump for correctness (handles quoting, special chars, etc.)
    and strips trailing newlines/document markers.
    """
    match value:
        case None:
            return "null"
        case bool():
            return str(value).lower()
        case dict() | list():
            if not value:
                return "{}" if isinstance(value, dict) else "[]"
            return yaml.dump(value, default_flow_style=True, width=float("inf")).strip()
        case str():
            # Use yaml.dump to handle quoting correctly
            dumped = yaml.dump(value, default_flow_style=True, width=float("inf"))
            # yaml.dump adds "...\n" for simple strings, strip document end marker
            dumped = dumped.removesuffix("...\n").strip()
            return dumped
        case _:
            return str(value)
