# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest

from tests.integration.utils import _find_rpm_repos_dir


class TestFindRpmReposDir:
    """Tests for dynamic RPM repos.d directory discovery."""

    def test_returns_none_when_no_rpm_dir(self, tmp_path: Path) -> None:
        """Return None when there is no deps/rpm directory at all."""
        assert _find_rpm_repos_dir(tmp_path) is None

    def test_returns_none_when_rpm_dir_has_no_repos_d(self, tmp_path: Path) -> None:
        """Return None when deps/rpm exists but no arch has a repos.d subdir."""
        rpm_dir = tmp_path / "hermeto-output" / "deps" / "rpm" / "x86_64"
        rpm_dir.mkdir(parents=True)
        assert _find_rpm_repos_dir(tmp_path) is None

    def test_finds_x86_64_repos_d(self, tmp_path: Path) -> None:
        """Find repos.d under x86_64 architecture directory."""
        repos_d = tmp_path / "hermeto-output" / "deps" / "rpm" / "x86_64" / "repos.d"
        repos_d.mkdir(parents=True)
        assert _find_rpm_repos_dir(tmp_path) == repos_d

    def test_finds_aarch64_repos_d(self, tmp_path: Path) -> None:
        """Find repos.d under aarch64 architecture directory."""
        repos_d = tmp_path / "hermeto-output" / "deps" / "rpm" / "aarch64" / "repos.d"
        repos_d.mkdir(parents=True)
        assert _find_rpm_repos_dir(tmp_path) == repos_d

    @pytest.mark.parametrize(
        "arch",
        ["ppc64le", "s390x"],
    )
    def test_finds_repos_d_for_any_arch(self, tmp_path: Path, arch: str) -> None:
        """Find repos.d regardless of the architecture name."""
        repos_d = tmp_path / "hermeto-output" / "deps" / "rpm" / arch / "repos.d"
        repos_d.mkdir(parents=True)
        assert _find_rpm_repos_dir(tmp_path) == repos_d

    def test_returns_first_repos_d_sorted(self, tmp_path: Path) -> None:
        """When multiple arches have repos.d, return the first one (sorted)."""
        rpm_dir = tmp_path / "hermeto-output" / "deps" / "rpm"
        for arch in ("x86_64", "aarch64"):
            (rpm_dir / arch / "repos.d").mkdir(parents=True)

        result = _find_rpm_repos_dir(tmp_path)
        # aarch64 sorts before x86_64
        assert result == rpm_dir / "aarch64" / "repos.d"

    def test_skips_files_in_rpm_dir(self, tmp_path: Path) -> None:
        """Ignore regular files in the rpm directory, only check subdirectories."""
        rpm_dir = tmp_path / "hermeto-output" / "deps" / "rpm"
        rpm_dir.mkdir(parents=True)
        (rpm_dir / "some_file.txt").touch()

        repos_d = rpm_dir / "x86_64" / "repos.d"
        repos_d.mkdir(parents=True)
        assert _find_rpm_repos_dir(tmp_path) == repos_d
