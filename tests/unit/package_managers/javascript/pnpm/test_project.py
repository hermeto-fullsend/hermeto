# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Any

import pytest

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, UnsupportedFeature
from hermeto.core.package_managers.javascript.pnpm.project import PnpmLock, parse_packages

# Since pnpm 12, a lockfile may be a stream of two documents: a leading "env"
# document for the project's config dependencies and pinned package manager,
# followed by the project's own dependency graph. https://pnpm.io/lockfile
TWO_DOCUMENT_LOCKFILE = """\
---
lockfileVersion: '9.0'
importers:
  .:
    packageManagerDependencies:
      pnpm:
        specifier: 12.11.2
        version: 12.11.2
packages:
  pnpm@12.11.2:
    resolution: {integrity: sha512-pnpm}
snapshots:
  pnpm@12.11.2: {}
---
lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      is-number:
        specifier: 7.0.0
        version: 7.0.0
packages:
  is-number@7.0.0:
    resolution: {integrity: sha512-isnumber}
snapshots:
  is-number@7.0.0: {}
"""

SINGLE_DOCUMENT_LOCKFILE = """\
lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      is-number:
        specifier: 7.0.0
        version: 7.0.0
packages:
  is-number@7.0.0:
    resolution: {integrity: sha512-isnumber}
snapshots:
  is-number@7.0.0: {}
"""

MARKED_SINGLE_DOCUMENT_LOCKFILE = f"---\n{SINGLE_DOCUMENT_LOCKFILE}"

THREE_DOCUMENT_LOCKFILE = (
    "---\nlockfileVersion: '9.0'\npackages:\n  middle@1.0.0:\n"
    "    resolution: {integrity: sha512-middle}\n" + TWO_DOCUMENT_LOCKFILE
)


def write_lockfile(tmp_path: Path, content: str) -> Path:
    lockfile = tmp_path / "pnpm-lock.yaml"
    lockfile.write_text(content)
    return lockfile


class TestPnpmLock:
    def test_from_dir_fails_with_missing_pnpm_lock(self, tmp_path: Path) -> None:
        with pytest.raises(LockfileNotFound):
            PnpmLock.from_dir(tmp_path)

    def test_from_file_fails_with_invalid_yaml(self, tmp_path: Path) -> None:
        lockfile = tmp_path / "pnpm-lock.yaml"
        lockfile.write_text(":")
        with pytest.raises(InvalidLockfileFormat):
            PnpmLock.from_file(lockfile)

    @pytest.mark.parametrize(
        ("data", "exc"),
        [
            pytest.param(
                {"lockfileVersion": "1000.0"}, UnsupportedFeature, id="unsupported_version_str"
            ),
            pytest.param(
                {"lockfileVersion": 1000.0}, UnsupportedFeature, id="unsupported_version_float"
            ),
            pytest.param({}, InvalidLockfileFormat, id="missing_version"),
        ],
    )
    def test_pnpm_lock_version_validation_fails(
        self, tmp_path: Path, data: dict[str, Any], exc: type[Exception]
    ) -> None:
        with pytest.raises(exc):
            PnpmLock(path=tmp_path, data=data)

    def test_from_file_fails_with_an_empty_lockfile(self, tmp_path: Path) -> None:
        # Reading the stream rather than a single document must not turn the
        # empty file into a crash on a missing key. The actionable part of the
        # message lives in .solution, which str(exc) does not include.
        with pytest.raises(InvalidLockfileFormat) as exc_info:
            PnpmLock.from_file(write_lockfile(tmp_path, ""))
        assert exc_info.value.solution == (
            "The pnpm-lock.yaml file must contain a lockfileVersion field."
        )

    @pytest.mark.parametrize(
        ("content", "expected_ids"),
        [
            pytest.param(SINGLE_DOCUMENT_LOCKFILE, {"is-number@7.0.0"}, id="single_document"),
            pytest.param(
                TWO_DOCUMENT_LOCKFILE,
                {"pnpm@12.11.2", "is-number@7.0.0"},
                id="two_documents",
            ),
        ],
    )
    def test_from_file_reads_packages_from_every_document(
        self, tmp_path: Path, content: str, expected_ids: set[str]
    ) -> None:
        lockfile = PnpmLock.from_file(write_lockfile(tmp_path, content))
        assert set(lockfile.packages) == expected_ids

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(SINGLE_DOCUMENT_LOCKFILE, id="single_document"),
            pytest.param(TWO_DOCUMENT_LOCKFILE, id="two_documents"),
        ],
    )
    def test_from_file_takes_the_dependency_graph_from_the_last_document(
        self, tmp_path: Path, content: str
    ) -> None:
        lockfile = PnpmLock.from_file(write_lockfile(tmp_path, content))
        assert lockfile.direct_dependencies == ["is-number@7.0.0"]

    def test_from_file_keeps_the_env_document(self, tmp_path: Path) -> None:
        lockfile = PnpmLock.from_file(write_lockfile(tmp_path, TWO_DOCUMENT_LOCKFILE))
        assert lockfile.env["importers"]["."]["packageManagerDependencies"] == {
            "pnpm": {"specifier": "12.11.2", "version": "12.11.2"}
        }

    def test_from_file_has_no_env_document_for_a_single_document_lockfile(
        self, tmp_path: Path
    ) -> None:
        lockfile = PnpmLock.from_file(write_lockfile(tmp_path, SINGLE_DOCUMENT_LOCKFILE))
        assert lockfile.env == {}

    def test_from_file_rejects_more_documents_than_pnpm_writes(self, tmp_path: Path) -> None:
        # Keeping only the first and the last document would drop the middle one
        # from the prefetch, the SBOM and the rewritten lockfile without a word.
        with pytest.raises(UnsupportedFeature):
            PnpmLock.from_file(write_lockfile(tmp_path, THREE_DOCUMENT_LOCKFILE))

    def test_from_file_suggests_regenerating_a_lockfile_with_too_many_documents(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(UnsupportedFeature) as exc_info:
            PnpmLock.from_file(write_lockfile(tmp_path, THREE_DOCUMENT_LOCKFILE))
        assert "pnpm install" in (exc_info.value.solution or "")

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            pytest.param(SINGLE_DOCUMENT_LOCKFILE, False, id="single_document"),
            pytest.param(MARKED_SINGLE_DOCUMENT_LOCKFILE, True, id="marked_single_document"),
            pytest.param(TWO_DOCUMENT_LOCKFILE, True, id="two_documents"),
        ],
    )
    def test_from_file_records_the_document_start_marker(
        self, tmp_path: Path, content: str, expected: bool
    ) -> None:
        # Whether the file opens with '---' is a property of the file, not of
        # how many documents it holds. pnpm reads a missing marker as a reason to
        # re-resolve and then fails the hermetic build.
        lockfile = PnpmLock.from_file(write_lockfile(tmp_path, content))
        assert lockfile.explicit_start is expected

    def test_from_file_rejects_an_unsupported_version_in_the_env_document(
        self, tmp_path: Path
    ) -> None:
        # Only the last document used to pass the version gate, so an env document
        # in a format hermeto does not know would have been parsed as if it were 9.0.
        content = TWO_DOCUMENT_LOCKFILE.replace(
            "lockfileVersion: '9.0'", "lockfileVersion: '10.0'", 1
        )
        with pytest.raises(UnsupportedFeature):
            PnpmLock.from_file(write_lockfile(tmp_path, content))

    def test_rejects_documents_disagreeing_on_a_checksum(self, tmp_path: Path) -> None:
        # Merging the documents resolves the duplicate silently, so the env
        # document's checksum would never be verified nor reported.
        with pytest.raises(InvalidLockfileFormat):
            PnpmLock(
                path=tmp_path / "pnpm-lock.yaml",
                data={
                    "lockfileVersion": "9.0",
                    "packages": {"pnpm@12.11.2": {"resolution": {"integrity": "sha512-graph"}}},
                },
                env={
                    "lockfileVersion": "9.0",
                    "packages": {"pnpm@12.11.2": {"resolution": {"integrity": "sha512-env"}}},
                },
            )

    def test_suggests_regenerating_a_lockfile_disagreeing_on_a_checksum(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(InvalidLockfileFormat) as exc_info:
            PnpmLock(
                path=tmp_path / "pnpm-lock.yaml",
                data={
                    "lockfileVersion": "9.0",
                    "packages": {"pnpm@12.11.2": {"resolution": {"integrity": "sha512-graph"}}},
                },
                env={
                    "lockfileVersion": "9.0",
                    "packages": {"pnpm@12.11.2": {"resolution": {"integrity": "sha512-env"}}},
                },
            )
        assert "pnpm install" in (exc_info.value.solution or "")

    def test_accepts_documents_agreeing_on_a_shared_package(self, tmp_path: Path) -> None:
        shared = {"resolution": {"integrity": "sha512-pnpm"}}
        lockfile = PnpmLock(
            path=tmp_path / "pnpm-lock.yaml",
            data={"lockfileVersion": "9.0", "packages": {"pnpm@12.11.2": shared}},
            env={"lockfileVersion": "9.0", "packages": {"pnpm@12.11.2": dict(shared)}},
        )
        assert set(lockfile.packages) == {"pnpm@12.11.2"}

    def test_parse_packages_version_field_overrides_version_from_id(self, tmp_path: Path) -> None:
        lockfile = PnpmLock(
            path=tmp_path / "pnpm-lock.yaml",
            data={
                "lockfileVersion": "9.0",
                "packages": {
                    "repo@https://codeload.github.com/org/repo/tar.gz/abc123": {
                        "version": "1.0.0",
                        "resolution": {
                            "tarball": "https://codeload.github.com/org/repo/tar.gz/abc123"
                        },
                    }
                },
            },
        )
        packages = parse_packages(lockfile)
        assert packages[0].version == "1.0.0"
        assert packages[0].url == "https://codeload.github.com/org/repo/tar.gz/abc123"
