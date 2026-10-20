# SPDX-License-Identifier: GPL-3.0-only
from collections import UserDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import yaml
from semver import Version

from hermeto.core.errors import InvalidLockfileFormat, LockfileNotFound, UnsupportedFeature
from hermeto.core.package_managers.javascript.npm import NPM_REGISTRY_URL

JSR_REGISTRY_URL = "https://npm.jsr.io"
JSR_REGISTRY_PREFIX = "@jsr/"

# Since pnpm 12 a pnpm-lock.yaml may be a stream of two documents: an optional
# leading "env" document recording the project's config dependencies and pinned
# package manager, followed by the project's dependency graph, which is always
# last. For pnpm <= 11 the dependency graph is the only document.
# https://pnpm.io/lockfile
MAX_LOCKFILE_DOCUMENTS = 2


class PnpmLock(UserDict):
    """Class representing a pnpm-lock.yaml file."""

    def __init__(
        self,
        path: Path,
        data: dict[str, Any],
        env: dict[str, Any] | None = None,
        explicit_start: bool | None = None,
    ) -> None:
        """Initialize a PnpmLock object.

        :param data: the project's dependency graph, the last document in the file
        :param env: the leading env document, when the lockfile has one
        :param explicit_start: whether the file opened with a '---' document start
            marker. Only the reader knows; callers that construct a lockfile by hand
            get the marker pnpm writes for the number of documents they passed.
        """
        self.path = path
        self.env = env or {}
        self.explicit_start = bool(self.env) if explicit_start is None else explicit_start
        super().__init__(data)
        _ensure_lockfile_version_is_supported(self)
        _ensure_documents_agree_on_shared_packages(self)

    @classmethod
    def from_file(cls, path: Path) -> "PnpmLock":
        """Create a PnpmLock object from a pnpm-lock.yaml file."""
        if not path.exists():
            raise LockfileNotFound(
                path, solution="Run 'pnpm install' to generate the pnpm-lock.yaml file."
            )

        content = path.read_text()
        try:
            documents = [doc for doc in yaml.safe_load_all(content) if doc is not None]
        except yaml.YAMLError as e:
            raise InvalidLockfileFormat(
                path,
                err_details=str(e),
                solution="The pnpm-lock.yaml file must contain valid YAML.",
            )

        if len(documents) > MAX_LOCKFILE_DOCUMENTS:
            # Keeping only the first and the last would drop everything between them
            # from the prefetch, the SBOM and the rewritten lockfile without a word.
            raise UnsupportedFeature(
                f"Unsupported pnpm-lock.yaml: Hermeto reads at most "
                f"{MAX_LOCKFILE_DOCUMENTS} YAML documents, this one holds {len(documents)}",
                solution=(
                    "Regenerate the lockfile with 'pnpm install'. A pnpm lockfile is an "
                    "optional leading env document followed by the project's dependency "
                    "graph -- see docs/pnpm.md. If pnpm itself wrote this file, please "
                    "report it to the Hermeto maintainers."
                ),
            )

        # Fall back to an empty mapping for an empty file to preserve the old behavior:
        # the missing lockfileVersion is reported instead of crashing on the lookup below.
        return cls(
            path,
            documents[-1] if documents else {},
            env=documents[0] if len(documents) > 1 else None,
            explicit_start=_starts_with_document_marker(content),
        )

    @classmethod
    def from_dir(cls, path: Path) -> "PnpmLock":
        """Create a PnpmLock object from a directory containing a pnpm-lock.yaml file."""
        return cls.from_file(path.joinpath("pnpm-lock.yaml"))

    @property
    def packages(self) -> dict[str, dict[str, Any]]:
        """Return the 'packages' from every document in the pnpm-lock.yaml file.

        pnpm installs the env document's packages too -- the pinned package manager
        and its platform binaries -- so they must be fetched and reported like any
        other dependency. https://pnpm.io/lockfile
        """
        return {**self.env.get("packages", {}), **self.get("packages", {})}

    @property
    def snapshots(self) -> dict[str, dict[str, Any]]:
        """Return the 'snapshots' key from the pnpm-lock.yaml file."""
        return self.get("snapshots", {})

    @property
    def direct_dependencies(self) -> list[str]:
        """Return the direct dependencies from the pnpm-lock.yaml file."""
        importers: dict[str, dict[str, Any]] = self.get("importers", {})

        ids = []
        for importer in importers.values():
            dependencies = importer.get("dependencies", {})
            optional = importer.get("optionalDependencies", {})

            for name, data in {**dependencies, **optional}.items():
                version = data["version"]
                # For JSR dependencies, version is not a semver, it's the full lockfile package ID.
                if version.startswith(JSR_REGISTRY_PREFIX):
                    ids.append(version)
                else:
                    ids.append(f"{name}@{version}")

        return ids


@dataclass(frozen=True)
class PnpmPackage:
    """Class representing a package from a pnpm-lock.yaml file."""

    id: str
    scope: str
    name: str
    version: str
    url: str
    # FIXME: Non-registry dependencies may lack the integrity field in lockfiles generated by pnpm v10.
    # By running various commands, pnpm can "magically" drop it:
    # - https://github.com/pnpm/pnpm/issues/12067
    # - https://github.com/pnpm/pnpm/issues/12001
    #
    # This broken logic has been fixed in pnpm 11.5.1: https://github.com/pnpm/pnpm/releases/tag/v11.5.1
    # Once pnpm v10 is EOL and unsupported by Hermeto, integrity should be required.
    integrity: str | None = None

    @property
    def full_name(self) -> str:
        """Return the full package name with the scope prefix if present."""
        if self.scope:
            return f"@{self.scope}/{self.name}"

        return self.name

    @property
    def tarball_filename(self) -> str:
        """Return the tarball filename."""
        if self.scope:
            return f"{self.scope}-{self.name}-{self.version}.tgz"

        return f"{self.name}-{self.version}.tgz"

    @property
    def is_from_npm_registry(self) -> bool:
        """Return True if the package is from the npm registry."""
        return self.url.startswith(NPM_REGISTRY_URL)

    @property
    def is_from_jsr_registry(self) -> bool:
        """Return True if the package is from the JSR registry."""
        return self.url.startswith(JSR_REGISTRY_URL)


def _starts_with_document_marker(content: str) -> bool:
    """Return True if a pnpm-lock.yaml opens with a YAML document start marker.

    pnpm writes the marker itself and will not accept a lockfile that lost it: the
    stream still parses, but pnpm re-runs the package manager dependency resolution
    and then fails the hermetic build with
    ERR_PNPM_FROZEN_LOCKFILE_WITH_OUTDATED_LOCKFILE. Record what the file had rather
    than inferring it from the document count, so the rewrite hands pnpm back the
    same stream it was given.

    >>> _starts_with_document_marker("---\\nlockfileVersion: '9.0'\\n")
    True
    >>> _starts_with_document_marker("lockfileVersion: '9.0'\\n")
    False
    """
    for line in content.splitlines():
        if not line.strip():
            continue
        return line.rstrip() == "---" or line.startswith("--- ")

    return False


def _ensure_lockfile_version_is_supported(lockfile: PnpmLock) -> None:
    """Ensure Hermeto supports the lockfile version of every document in the file.

    Each document carries its own lockfileVersion. pnpm writes the same version to
    both today, but nothing in the format promises that, and parsing an unknown
    format as if it were 9.0 would quietly mis-report the SBOM.
    """
    labeled_documents: list[tuple[str | None, dict[str, Any]]] = (
        [("the env document", lockfile.env)] if lockfile.env else []
    )
    labeled_documents.append((None, lockfile.data))

    for where, document in labeled_documents:
        location = f" in {where}" if where else ""

        raw_version = str(document.get("lockfileVersion", ""))
        if not raw_version:
            raise InvalidLockfileFormat(
                lockfile.path,
                err_details=None,
                solution=(
                    f"The pnpm-lock.yaml file must contain a lockfileVersion field{location}."
                ),
            )

        version = Version.parse(raw_version, optional_minor_and_patch=True)
        if version.major != 9:
            raise UnsupportedFeature(f"Unsupported lockfileVersion: '{raw_version}'{location}")


def _ensure_documents_agree_on_shared_packages(lockfile: PnpmLock) -> None:
    """Reject a package the documents declare with different checksums.

    PnpmLock.packages merges the documents, so a duplicate silently resolves to the
    dependency graph's entry and the env document's checksum is never verified nor
    reported. Hermeto cannot tell which tarball pnpm would install, so it refuses
    to pick one.
    """
    graph_packages = lockfile.get("packages", {})

    for package_id, env_package in lockfile.env.get("packages", {}).items():
        graph_package = graph_packages.get(package_id)
        if graph_package is None:
            continue

        env_integrity = env_package.get("resolution", {}).get("integrity")
        graph_integrity = graph_package.get("resolution", {}).get("integrity")
        if env_integrity != graph_integrity:
            raise InvalidLockfileFormat(
                lockfile.path,
                err_details=f"the documents declare different checksums for '{package_id}'",
                solution=(
                    "Regenerate the lockfile with 'pnpm install'. Hermeto cannot tell "
                    "which of the two tarballs pnpm would install, so it will not guess."
                ),
            )


def parse_packages(lockfile: PnpmLock) -> list[PnpmPackage]:
    """Parse the packages from the pnpm-lock.yaml file."""
    result: list[PnpmPackage] = []

    for id, data in lockfile.packages.items():
        scope, name, version_from_id = _parse_pnpm_package(id)
        # Non-registry dependencies have an extra 'version' field.
        version = data.get("version") or version_from_id
        resolution = data.get("resolution", {})
        url = _resolve_package_tarball_url(scope, name, version, resolution)
        integrity = resolution.get("integrity")
        result.append(PnpmPackage(id, scope, name, version, url, integrity))

    return result


class ParsedPackageID(NamedTuple):
    """Parsed package ID from a pnpm-lock.yaml file."""

    scope: str
    name: str
    version: str


def _parse_pnpm_package(id: str) -> ParsedPackageID:
    """
    Parse the package scope, name, and version from the package ID.

    >>> _parse_pnpm_package("foo@1.0.0")
    ParsedPackageID(scope='', name='foo', version='1.0.0')
    >>> _parse_pnpm_package("@foo/bar@1.0.0")
    ParsedPackageID(scope='foo', name='bar', version='1.0.0')
    >>> _parse_pnpm_package("@jsr/foo__bar@1.0.0")
    ParsedPackageID(scope='foo', name='bar', version='1.0.0')
    >>> _parse_pnpm_package("@jsr/foo@1.0.0")
    ParsedPackageID(scope='', name='foo', version='1.0.0')
    """
    # JSR format
    if id.startswith(JSR_REGISTRY_PREFIX):
        full_name, version = id.removeprefix(JSR_REGISTRY_PREFIX).split("@", maxsplit=1)
        if "__" in full_name:
            scope, name = full_name.split("__", 1)
        else:
            scope = ""
            name = full_name

        return ParsedPackageID(scope, name, version)

    # Scoped format
    if id.startswith("@"):
        scope, full_name = id.split("/", maxsplit=1)
        name, version = full_name.split("@", maxsplit=1)
        return ParsedPackageID(scope.removeprefix("@"), name, version)

    name, version = id.split("@", maxsplit=1)
    return ParsedPackageID("", name, version)


def _resolve_package_tarball_url(
    scope: str, name: str, version: str, resolution: dict[str, str]
) -> str:
    return resolution.get("tarball") or _construct_npm_registry_tarball_url(scope, name, version)


def _construct_npm_registry_tarball_url(scope: str, name: str, version: str) -> str:
    """
    Construct the tarball URL for a package from the npm registry.

    >>> _construct_npm_registry_tarball_url("", "vue", "1.0.0")
    'https://registry.npmjs.org/vue/-/vue-1.0.0.tgz'
    >>> _construct_npm_registry_tarball_url("vue", "core", "1.0.0")
    'https://registry.npmjs.org/@vue/core/-/core-1.0.0.tgz'
    """
    if scope:
        # The URL for scoped packages must include the '@' prefix.
        return f"{NPM_REGISTRY_URL}/@{scope}/{name}/-/{name}-{version}.tgz"

    return f"{NPM_REGISTRY_URL}/{name}/-/{name}-{version}.tgz"
