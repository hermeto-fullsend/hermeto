# SPDX-License-Identifier: GPL-3.0-only
"""Steps for features/pnpm-multi-document-lockfiles.feature."""

import json
from pathlib import Path
from typing import Any, Callable

import yaml
from packageurl import PackageURL
from pytest_bdd import given, parsers, then, when

DOCUMENTS_PER_SHAPE = {"single-document": 1, "two-document": 2}


def _documents(source: str) -> list[dict[str, Any]]:
    """Return every YAML document in a lockfile, in the order it appears."""
    # Same filter as PnpmLock.from_file: drop only the empty documents yaml
    # yields as None, so a document that is present but empty still counts.
    return [document for document in yaml.safe_load_all(source) if document is not None]


def _env_document(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the leading env document, which pnpm writes only when there is one."""
    return documents[0] if len(documents) > 1 else {}


def _declared_packages(documents: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Return the name and version of every package id across all documents."""
    declared = set()
    for document in documents:
        for package_id in document.get("packages", {}):
            name, _, version = package_id.rpartition("@")
            declared.add((name, version))
    return declared


def _pinned_package_managers(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the packageManagerDependencies the env document pins, if any."""
    importers = _env_document(documents).get("importers", {})
    return importers.get(".", {}).get("packageManagerDependencies", {})


def _sbom_packages(output_dir: Path) -> set[tuple[str, str]]:
    """Return the name and version of every component in the generated SBOM.

    Read them off the purl rather than the name field: a scoped package keeps its
    scope in the purl namespace, so the name field alone says "exe.linux-x64"
    where the lockfile says "@pnpm/exe.linux-x64".
    """
    sbom = json.loads(output_dir.joinpath("bom.json").read_text())
    packages: set[tuple[str, str]] = set()
    for component in sbom["components"]:
        purl = PackageURL.from_string(component["purl"])
        name = f"{purl.namespace}/{purl.name}" if purl.namespace else purl.name
        packages.add((name, purl.version or ""))
    return packages


def _planned_lockfile(output_dir: Path) -> list[dict[str, Any]]:
    """Return the lockfile fetch-deps recorded for injection, before it is written out."""
    build_config = json.loads(output_dir.joinpath(".build-config.json").read_text())
    for project_file in build_config["project_files"]:
        if Path(project_file["abspath"]).name == "pnpm-lock.yaml":
            return _documents(project_file["template"])
    raise AssertionError("fetch-deps recorded no pnpm-lock.yaml for injection")


def _rewritten_lockfile(project_dir: Path) -> list[dict[str, Any]]:
    """Return every document of the lockfile hermeto wrote back into the project."""
    return _documents(project_dir.joinpath("pnpm-lock.yaml").read_text())


def _reported_error(scenario_context: dict[str, Any]) -> str:
    """Return what the failed fetch told the user, wherever click put it."""
    result = scenario_context["fetch"]
    try:
        return result.output + result.stderr
    except ValueError:
        # click raises rather than returning when it did not capture the two
        # streams separately, in which case .output already holds both.
        return str(result.output)


@given(
    "a project with a pnpm lockfile holding an env document and a dependency graph",
    target_fixture="project_dir",
)
def two_document_project(
    make_pnpm_project: Callable[[str], Path], scenario_context: dict[str, Any]
) -> Path:
    return shaped_project("two-document", make_pnpm_project, scenario_context)


@given(parsers.parse("a project with a {shape} pnpm lockfile"), target_fixture="project_dir")
def shaped_project(
    shape: str, make_pnpm_project: Callable[[str], Path], scenario_context: dict[str, Any]
) -> Path:
    project_dir = make_pnpm_project(shape)
    scenario_context["shape"] = shape
    scenario_context["documents"] = _documents(project_dir.joinpath("pnpm-lock.yaml").read_text())
    return project_dir


@given(
    "a project with a pnpm lockfile declaring one package in both documents",
    target_fixture="project_dir",
)
def shared_package_project(
    make_pnpm_project: Callable[[str], Path], scenario_context: dict[str, Any]
) -> Path:
    return shaped_project("shared-package", make_pnpm_project, scenario_context)


@given(
    "a project with a pnpm lockfile whose documents disagree on a checksum",
    target_fixture="project_dir",
)
def conflicting_duplicate_project(
    make_pnpm_project: Callable[[str], Path], scenario_context: dict[str, Any]
) -> Path:
    return shaped_project("conflicting-duplicate", make_pnpm_project, scenario_context)


@given(
    "a project with a pnpm lockfile whose env document declares lockfileVersion 10.0",
    target_fixture="project_dir",
)
def unsupported_env_version_project(
    make_pnpm_project: Callable[[str], Path], scenario_context: dict[str, Any]
) -> Path:
    return shaped_project("unsupported-env-version", make_pnpm_project, scenario_context)


@when("hermeto fetches the pnpm dependencies")
def fetch_dependencies(
    project_dir: Path,
    output_dir: Path,
    run_hermeto: Callable[..., Any],
    scenario_context: dict[str, Any],
) -> None:
    scenario_context["fetch"] = run_hermeto(
        "fetch-deps", "--source", str(project_dir), "--output", str(output_dir), "pnpm"
    )


@when("hermeto injects the files for the hermetic build")
def inject_files(output_dir: Path, run_hermeto: Callable[..., Any]) -> None:
    result = run_hermeto("inject-files", str(output_dir), "--for-output-dir", str(output_dir))
    assert result.exit_code == 0, result.output


@then("the fetch succeeds")
def fetch_succeeds(scenario_context: dict[str, Any]) -> None:
    result = scenario_context["fetch"]
    assert result.exit_code == 0, result.output


@then("the SBOM lists every package declared in the lockfile")
def sbom_lists_every_package(output_dir: Path, scenario_context: dict[str, Any]) -> None:
    declared = _declared_packages(scenario_context["documents"])
    assert declared - _sbom_packages(output_dir) == set()


@then("the SBOM lists the pinned package manager declared in the env document")
def sbom_lists_pinned_package_manager(output_dir: Path, scenario_context: dict[str, Any]) -> None:
    pinned = _pinned_package_managers(scenario_context["documents"])
    expected = {(name, pin["version"]) for name, pin in pinned.items()}
    assert expected - _sbom_packages(output_dir) == set()


@then("every package declared in the env document resolves to a local tarball")
def env_packages_resolve_to_local_tarballs(
    output_dir: Path, scenario_context: dict[str, Any]
) -> None:
    declared = _env_document(scenario_context["documents"]).get("packages", {})
    rewritten = _env_document(_planned_lockfile(output_dir)).get("packages", {})
    registry_dir = output_dir.joinpath("deps", "pnpm", "npm")
    resolved = {
        package_id
        for package_id in declared
        if (tarball := rewritten.get(package_id, {}).get("resolution", {}).get("tarball"))
        and registry_dir.joinpath(tarball).is_file()
    }
    assert declared.keys() - resolved == set()


@then("the rewritten lockfile still declares the pinned package manager")
def rewritten_lockfile_keeps_pinned_package_manager(
    project_dir: Path, scenario_context: dict[str, Any]
) -> None:
    rewritten = _documents(project_dir.joinpath("pnpm-lock.yaml").read_text())
    assert _pinned_package_managers(rewritten) == _pinned_package_managers(
        scenario_context["documents"]
    )


@then("the rewritten lockfile begins with a document start marker")
def rewritten_lockfile_begins_with_document_start_marker(project_dir: Path) -> None:
    # Assert on the raw text. A parsed lockfile looks the same with or without the
    # marker -- both yield two documents -- but pnpm reads its absence as a reason to
    # re-resolve, and then fails the hermetic build.
    assert project_dir.joinpath("pnpm-lock.yaml").read_text().startswith("---\n")


@then("the rewritten lockfile begins with no document start marker")
def rewritten_lockfile_begins_with_no_document_start_marker(project_dir: Path) -> None:
    assert not project_dir.joinpath("pnpm-lock.yaml").read_text().startswith("---")


@then(parsers.parse("the rewritten lockfile is a {shape} stream"))
def rewritten_lockfile_keeps_its_shape(shape: str, project_dir: Path) -> None:
    rewritten = _documents(project_dir.joinpath("pnpm-lock.yaml").read_text())
    assert len(rewritten) == DOCUMENTS_PER_SHAPE[shape]


@then("every package in the rewritten lockfile resolves to a local tarball")
def every_package_resolves_to_a_local_tarball(project_dir: Path, output_dir: Path) -> None:
    # A package both documents declare has to be rewritten in both. Patching only
    # the copy hermeto happens to find first leaves the other pointing at the
    # upstream registry, and pnpm then reaches the network mid-build.
    registry_dir = output_dir.joinpath("deps", "pnpm", "npm")
    unresolved = {
        package_id
        for document in _rewritten_lockfile(project_dir)
        for package_id, package in document.get("packages", {}).items()
        if not registry_dir.joinpath(package.get("resolution", {}).get("tarball", "")).is_file()
    }
    assert unresolved == set()


@then("the fetch fails")
def fetch_fails(scenario_context: dict[str, Any]) -> None:
    result = scenario_context["fetch"]
    assert result.exit_code != 0, result.output


@then("the error reports how many documents the lockfile holds")
def error_reports_document_count(scenario_context: dict[str, Any]) -> None:
    assert "holds 3" in _reported_error(scenario_context)


@then("the error names the package the documents disagree on")
def error_names_the_disputed_package(scenario_context: dict[str, Any]) -> None:
    assert "pnpm@12.11.2" in _reported_error(scenario_context)


@then("the error suggests regenerating the lockfile")
def error_suggests_regenerating_the_lockfile(scenario_context: dict[str, Any]) -> None:
    assert "pnpm install" in _reported_error(scenario_context)


@then("the error names the unsupported lockfileVersion")
def error_names_the_unsupported_lockfile_version(scenario_context: dict[str, Any]) -> None:
    assert "'10.0'" in _reported_error(scenario_context)


@then("the error points at the env document")
def error_points_at_the_env_document(scenario_context: dict[str, Any]) -> None:
    assert "env document" in _reported_error(scenario_context)
