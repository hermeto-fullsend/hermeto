# SPDX-License-Identifier: GPL-3.0-only
"""Shared fixtures for the behavior scenarios.

Step definitions live in tests/bdd/steps/ and bind the Gherkin phrases in
features/ to assertions on observable behavior. Keep setup here rather than
in step bodies, and add no per-feature conftest files.
"""

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest import mock

import git
import pytest
import typer.testing

from hermeto.interface.cli import app

# Step modules are not named test_*, so pytest does not rewrite their asserts
# unless asked. Without this a failing scenario reports a bare AssertionError
# instead of naming the packages that are missing.
pytest.register_assert_rewrite("tests.bdd.steps.pnpm_lockfiles")

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_ACTOR = git.Actor("hermeto behavior tests", "noreply@example.com")


async def _write_empty_tarballs(
    files_without_auth: dict[str, Path],
    files_with_auth: dict[str, Path],
    auth: Any = None,
) -> None:
    """Stand in for the real downloader, creating each destination file locally."""
    for destination in (*files_without_auth.values(), *files_with_auth.values()):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"")


@pytest.fixture(autouse=True)
def no_network() -> Iterator[None]:
    """Keep every scenario offline.

    Downloads are mocked at the boundary tests/unit/package_managers/javascript/
    pnpm/test_main.py already mocks. Checksums go with them: the stand-in writes
    empty files, which cannot match the integrity hashes the fixtures declare.
    """
    base = "hermeto.core.package_managers.javascript.pnpm.main"
    with (
        mock.patch(f"{base}.async_download_with_auth", new=_write_empty_tarballs),
        mock.patch(f"{base}.must_match_any_checksum"),
    ):
        yield


@pytest.fixture
def scenario_context() -> dict[str, Any]:
    """Carry state between the steps of a single scenario."""
    return {}


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """Return the directory hermeto writes its output to."""
    return tmp_path / "output"


@pytest.fixture
def make_pnpm_project(tmp_path: Path) -> Callable[[str], Path]:
    """Return a factory building a git-backed pnpm project from a committed fixture."""

    def _make(shape: str) -> Path:
        source = tmp_path / "source"
        source.mkdir()
        lockfile = FIXTURES_DIR / f"pnpm12-{shape}.yaml"
        source.joinpath("pnpm-lock.yaml").write_bytes(lockfile.read_bytes())
        source.joinpath("package.json").write_text(
            json.dumps({"name": "hermeto-bdd", "version": "1.0.0"}) + "\n"
        )
        repo = git.Repo.init(source)
        repo.create_remote("origin", "https://example.com/hermeto-bdd.git")
        repo.index.add(["package.json", "pnpm-lock.yaml"])
        repo.index.commit("initial", author=_ACTOR, committer=_ACTOR)
        return source

    return _make


@contextmanager
def _logging_detached_from_pytest() -> Iterator[None]:
    """Keep the CLI's log records away from pytest and from stale streams.

    pytest's live-log handler sits on the root logger and suspends and resumes
    global capture whenever a record reaches it. Inside CliRunner.invoke that
    closes the streams click is capturing into, and reading the result then
    fails with "I/O operation on closed file" -- so any scenario whose command
    logs at or above the live-log level dies for a reason that has nothing to
    do with the behavior it specifies.

    The NullHandler is what keeps setup_logging from attaching one of its own.
    That handler binds to sys.stderr as it is at construction time, and the
    hermeto logger outlives the invocation, so the next scenario would inherit
    a handler aimed at a stream click has already closed.

    Scenarios assert on what the CLI prints, not on what it logs, so this hides
    nothing they are allowed to see.
    """
    logger = logging.getLogger("hermeto")
    handlers, propagate = logger.handlers[:], logger.propagate
    logger.handlers, logger.propagate = [logging.NullHandler()], False
    try:
        yield
    finally:
        logger.handlers, logger.propagate = handlers, propagate


@pytest.fixture
def run_hermeto() -> Callable[..., Any]:
    """Return a callable invoking the hermeto CLI in process."""
    runner = typer.testing.CliRunner()

    def _run(*args: str) -> Any:
        with _logging_detached_from_pytest():
            return runner.invoke(app, list(args), catch_exceptions=False)

    return _run
