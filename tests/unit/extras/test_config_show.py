# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest
import yaml

from hermeto.core.config import Config
from hermeto.core.extras.config_show import (
    ConfigDiff,
    _get_env_var_name,
    format_diff_output,
    format_yaml_output,
    get_config_diff,
    get_config_sources,
    get_default_config,
    get_effective_config,
)


class TestGetEnvVarName:
    """Tests for environment variable name reconstruction."""

    @pytest.mark.parametrize(
        "section, field, expected",
        [
            ("gomod", "proxy_url", "HERMETO_GOMOD__PROXY_URL"),
            ("gomod", "download_max_tries", "HERMETO_GOMOD__DOWNLOAD_MAX_TRIES"),
            ("http", "connect_timeout", "HERMETO_HTTP__CONNECT_TIMEOUT"),
            ("http", "read_timeout", "HERMETO_HTTP__READ_TIMEOUT"),
            ("runtime", "subprocess_timeout", "HERMETO_RUNTIME__SUBPROCESS_TIMEOUT"),
            ("runtime", "concurrency_limit", "HERMETO_RUNTIME__CONCURRENCY_LIMIT"),
            ("pip", "ignore_dependencies_crates", "HERMETO_PIP__IGNORE_DEPENDENCIES_CRATES"),
            ("yarn", "enabled", "HERMETO_YARN__ENABLED"),
            ("npm", "proxy_url", "HERMETO_NPM__PROXY_URL"),
            ("npm", "proxy_login", "HERMETO_NPM__PROXY_LOGIN"),
            ("npm", "proxy_password", "HERMETO_NPM__PROXY_PASSWORD"),
        ],
    )
    def test_env_var_name_generation(self, section: str, field: str, expected: str) -> None:
        assert _get_env_var_name(section, field) == expected


class TestGetEffectiveConfig:
    """Tests for dumping current effective configuration."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_returns_all_sections(self) -> None:
        config = Config()
        effective = get_effective_config(config)

        assert set(effective.keys()) == set(Config.model_fields.keys())

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_section_order_matches_model_definition(self) -> None:
        """Output order must match Config model field order for readability."""
        config = Config()
        effective = get_effective_config(config)

        expected_order = list(Config.model_fields.keys())
        actual_order = list(effective.keys())
        assert actual_order == expected_order

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_field_order_within_sections_matches_model(self) -> None:
        """Field order within each section must match the settings class definition."""
        config = Config()
        effective = get_effective_config(config)

        for section_name in Config.model_fields:
            section_obj = getattr(config, section_name)
            if not hasattr(type(section_obj), "model_fields"):
                continue
            expected_fields = list(type(section_obj).model_fields.keys())
            actual_fields = list(effective[section_name].keys())
            assert actual_fields == expected_fields, (
                f"Field order mismatch in {section_name}: "
                f"expected {expected_fields}, got {actual_fields}"
            )


class TestGetDefaultConfig:
    """Tests for default configuration retrieval."""

    def test_returns_all_sections(self) -> None:
        defaults = get_default_config()
        assert set(defaults.keys()) == set(Config.model_fields.keys())

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_matches_effective_when_no_overrides(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()
        assert effective == defaults


class TestGetConfigDiff:
    """Tests for configuration diff computation."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_no_diff_with_defaults(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()
        diff = get_config_diff(effective, defaults)
        assert diff == {}

    def test_detects_changed_values(self) -> None:
        effective = {
            "gomod": {"proxy_url": "https://custom-proxy.example.com", "download_max_tries": 5},
            "http": {"connect_timeout": 30, "read_timeout": 600},
        }
        defaults = {
            "gomod": {"proxy_url": "https://proxy.golang.org,direct", "download_max_tries": 5},
            "http": {"connect_timeout": 30, "read_timeout": 300},
        }

        diff = get_config_diff(effective, defaults)

        assert "gomod" in diff
        gomod_diff = diff["gomod"]
        assert isinstance(gomod_diff, dict)
        assert "proxy_url" in gomod_diff
        assert gomod_diff["proxy_url"] == (
            "https://custom-proxy.example.com",
            "https://proxy.golang.org,direct",
        )

        assert "http" in diff
        http_diff = diff["http"]
        assert isinstance(http_diff, dict)
        assert "read_timeout" in http_diff
        assert http_diff["read_timeout"] == (600, 300)

    def test_unchanged_values_not_in_diff(self) -> None:
        effective = {"gomod": {"proxy_url": "same", "download_max_tries": 5}}
        defaults = {"gomod": {"proxy_url": "same", "download_max_tries": 5}}

        diff = get_config_diff(effective, defaults)
        assert diff == {}


class TestFormatYamlOutput:
    """Tests for YAML output formatting."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_yaml_roundtrip_matches_effective_config(self) -> None:
        """Dumped YAML can be parsed back and matches the effective config."""
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()

        output = format_yaml_output(effective, defaults)

        # yaml.safe_load natively ignores YAML comments
        parsed = yaml.safe_load(output)
        assert parsed == effective

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_contains_env_var_comments(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()

        output = format_yaml_output(effective, defaults)
        assert "# HERMETO_GOMOD__PROXY_URL" in output
        assert "# HERMETO_HTTP__CONNECT_TIMEOUT" in output
        assert "# HERMETO_RUNTIME__CONCURRENCY_LIMIT" in output

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_no_star_markers_when_all_defaults(self) -> None:
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()

        output = format_yaml_output(effective, defaults)
        assert "# (*)" not in output

    def test_star_markers_on_changed_values(self) -> None:
        effective = {
            "gomod": {"proxy_url": "https://custom-proxy.example.com", "download_max_tries": 5},
        }
        defaults = {
            "gomod": {"proxy_url": "https://proxy.golang.org,direct", "download_max_tries": 5},
        }

        output = format_yaml_output(effective, defaults)
        value_lines = [
            line
            for line in output.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        star_lines = [line for line in value_lines if "# (*)" in line]
        assert len(star_lines) == 1
        assert "proxy_url" in star_lines[0]


class TestFormatDiffOutput:
    """Tests for diff output formatting."""

    def test_empty_diff(self) -> None:
        output = format_diff_output({})
        assert "All values are at their defaults" in output

    def test_non_empty_diff_is_valid_yaml(self) -> None:
        """Non-empty diff output is parseable YAML showing current values."""
        diff: ConfigDiff = {
            "gomod": {
                "proxy_url": ("https://custom.example.com", "https://proxy.golang.org,direct")
            },
            "http": {"read_timeout": (600, 300)},
        }
        output = format_diff_output(diff)
        # yaml.safe_load natively ignores YAML comments
        parsed = yaml.safe_load(output)
        assert parsed["gomod"]["proxy_url"] == "https://custom.example.com"
        assert parsed["http"]["read_timeout"] == 600


class TestSecretStrRedaction:
    """Tests for SecretStr-based redaction in config output."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_proxy_password_redacted_by_default(self) -> None:
        config = Config(
            gomod={
                "proxy_url": "https://proxy.example.com",
                "proxy_login": "user",
                "proxy_password": "s3cret",
            },  # noqa: S106
        )
        effective = get_effective_config(config)
        assert effective["gomod"]["proxy_password"] == "**********"  # noqa: S105

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_proxy_password_revealed_with_raw(self) -> None:
        config = Config(
            gomod={
                "proxy_url": "https://proxy.example.com",
                "proxy_login": "user",
                "proxy_password": "s3cret",
            },  # noqa: S106
        )
        effective = get_effective_config(config, raw=True)
        assert effective["gomod"]["proxy_password"] == "s3cret"  # noqa: S105

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_raw_flag_does_not_affect_non_sensitive_fields(self) -> None:
        """The raw flag should only reveal SecretStr fields, not change other values."""
        config = Config(
            gomod={
                "proxy_url": "https://proxy.example.com",
                "proxy_login": "user",
                "proxy_password": "s3cret",
            },  # noqa: S106
        )
        default_output = get_effective_config(config)
        raw_output = get_effective_config(config, raw=True)
        assert default_output["gomod"]["proxy_login"] == raw_output["gomod"]["proxy_login"]
        assert default_output["gomod"]["proxy_url"] == raw_output["gomod"]["proxy_url"]


class TestGetConfigSources:
    """Tests for configuration source tracking."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_all_defaults_when_no_overrides(self) -> None:
        """Every field should report 'default' when nothing is overridden."""
        config = Config()
        effective = get_effective_config(config)
        sources = get_config_sources(effective)

        for section_name, section in sources.items():
            if isinstance(section, dict):
                for field_name, source in section.items():
                    assert source == "default", (
                        f"{section_name}.{field_name} should be 'default', got {source!r}"
                    )
            else:
                assert section == "default", f"{section_name} should be 'default', got {section!r}"

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_env_var_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An environment variable override should be reported as 'env'."""
        monkeypatch.setenv("HERMETO_RUNTIME__CONCURRENCY_LIMIT", "10")
        config = Config()
        effective = get_effective_config(config)
        sources = get_config_sources(effective)

        assert isinstance(sources["runtime"], dict)
        assert sources["runtime"]["concurrency_limit"] == "env"
        # Other fields in the same section remain defaults
        assert sources["runtime"]["subprocess_timeout"] == "default"

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_config_file_detected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Values from a YAML config file should report 'file: <path>'."""
        config_file = tmp_path / "hermeto.yaml"
        config_file.write_text(yaml.safe_dump({"http": {"read_timeout": 600}}))

        monkeypatch.setattr(
            "hermeto.core.extras.config_show.CONFIG_FILE_PATHS",
            [str(config_file)],
        )

        effective = {"http": {"connect_timeout": 30, "read_timeout": 600, "max_retries": 5}}
        sources = get_config_sources(effective)

        assert isinstance(sources["http"], dict)
        assert sources["http"]["read_timeout"] == f"file: {config_file}"
        assert sources["http"]["connect_timeout"] == "default"

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_env_takes_priority_over_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When both env var and file provide a value, env should win."""
        config_file = tmp_path / "hermeto.yaml"
        config_file.write_text(yaml.safe_dump({"runtime": {"concurrency_limit": 8}}))

        monkeypatch.setattr(
            "hermeto.core.extras.config_show.CONFIG_FILE_PATHS",
            [str(config_file)],
        )
        monkeypatch.setenv("HERMETO_RUNTIME__CONCURRENCY_LIMIT", "10")

        effective = {"runtime": {"concurrency_limit": 10, "subprocess_timeout": 3600}}
        sources = get_config_sources(effective)

        assert isinstance(sources["runtime"], dict)
        assert sources["runtime"]["concurrency_limit"] == "env"

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_cli_config_file_detected(self, tmp_path: Path) -> None:
        """Values from a CLI-provided config file should report that file."""
        cli_config = tmp_path / "custom.yaml"
        cli_config.write_text(yaml.safe_dump({"gomod": {"download_max_tries": 10}}))

        effective = {
            "gomod": {
                "proxy_url": "https://proxy.golang.org,direct",
                "proxy_login": None,
                "proxy_password": None,
                "download_max_tries": 10,
                "environment_variables": {},
            },
        }
        sources = get_config_sources(effective, config_file_path=cli_config)

        assert isinstance(sources["gomod"], dict)
        assert sources["gomod"]["download_max_tries"] == f"file: {cli_config}"
        assert sources["gomod"]["proxy_url"] == "default"

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_legacy_field_migration_tracked(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legacy flat fields in config files should be tracked after migration."""
        config_file = tmp_path / "hermeto.yaml"
        # Legacy field name that gets migrated to gomod.proxy_url
        config_file.write_text(yaml.safe_dump({"goproxy_url": "https://custom.proxy"}))

        monkeypatch.setattr(
            "hermeto.core.extras.config_show.CONFIG_FILE_PATHS",
            [str(config_file)],
        )

        effective = {
            "gomod": {
                "proxy_url": "https://custom.proxy",
                "proxy_login": None,
                "proxy_password": None,
                "download_max_tries": 5,
                "environment_variables": {},
            },
        }
        sources = get_config_sources(effective)

        assert isinstance(sources["gomod"], dict)
        assert sources["gomod"]["proxy_url"] == f"file: {config_file}"


class TestFormatYamlOutputWithSources:
    """Tests for YAML output with source annotations."""

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_source_annotations_in_output(self) -> None:
        """Source labels should appear in comments when sources are provided."""
        effective = {"runtime": {"concurrency_limit": 10, "subprocess_timeout": 3600}}
        defaults = {"runtime": {"concurrency_limit": 5, "subprocess_timeout": 3600}}
        sources = {"runtime": {"concurrency_limit": "env", "subprocess_timeout": "default"}}

        output = format_yaml_output(effective, defaults, sources=sources)

        assert "[env]" in output
        assert "[default]" in output

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_yaml_still_parseable_with_sources(self) -> None:
        """Source annotations must not break YAML parsing."""
        config = Config()
        effective = get_effective_config(config)
        defaults = get_default_config()
        sources = get_config_sources(effective)

        output = format_yaml_output(effective, defaults, sources=sources)
        parsed = yaml.safe_load(output)
        assert parsed == effective

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_header_mentions_source_brackets(self) -> None:
        """Header comment should explain the bracket notation."""
        effective = {"mode": "strict"}
        defaults = {"mode": "strict"}
        sources = {"mode": "default"}

        output = format_yaml_output(effective, defaults, sources=sources)
        assert "[default]" in output
        assert "Source shown in brackets" in output

    @pytest.mark.usefixtures("_clean_hermeto_env")
    def test_no_source_annotations_when_sources_none(self) -> None:
        """When sources is None, output should use the legacy header."""
        effective = {"mode": "strict"}
        defaults = {"mode": "strict"}

        output = format_yaml_output(effective, defaults, sources=None)
        assert "[default]" not in output
        assert "Environment variables shown in comments" in output
