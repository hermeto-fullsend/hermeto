# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest
import tomlkit

from hermeto.core.models.output import ProjectFile
from hermeto.core.package_managers.python.pip.rust import (
    _consolidate_vendor_dirs,
    _get_rust_root_dir,
    _merge_cargo_config_files,
    _shortest_path_parent,
)
from hermeto.core.rooted_path import RootedPath


@pytest.mark.parametrize(
    "cargo_files,expected_rust_root_dir",
    [
        pytest.param(
            (Path("/tmp/foo/Cargo.toml"), Path("/tmp/bar/baz/Cargo.toml")),
            Path("/tmp/foo"),
            id="simple_ordering",
        ),
        pytest.param(
            (Path("/tmp/bar/baz/Cargo.toml"), Path("/tmp/foo/Cargo.toml")),
            Path("/tmp/foo"),
            id="reversed_simple_ordering",
        ),
        pytest.param(
            (
                Path("/tmp/bar/baz/Cargo.toml"),
                Path("/tmp/foo/Cargo.toml"),
                Path("/tmp/foo/quux/Cargo.toml"),
            ),
            Path("/tmp/foo"),
            id="tricky_ordering",
        ),
    ],
)
def test_the_shortest_path_in_cargo_package_is_inferred_as_root(
    cargo_files: tuple, expected_rust_root_dir: Path
) -> None:
    inferred_rust_root_dir = _shortest_path_parent(cargo_files)
    assert inferred_rust_root_dir == expected_rust_root_dir


def test_get_rust_root_dir_returns_none_if_no_rust_files_exist(tmp_path: Path) -> None:
    assert _get_rust_root_dir(tmp_path) is None


def test_get_rust_root_dir_falls_back_to_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").touch()
    assert _get_rust_root_dir(tmp_path) == tmp_path


def test_get_rust_root_dir_prefers_cargo_lock_over_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").touch()

    subdir = tmp_path / "workspace-package"
    subdir.mkdir()

    (subdir / "Cargo.lock").touch()
    (subdir / "Cargo.toml").touch()
    assert _get_rust_root_dir(tmp_path) == subdir


def test_merge_cargo_config_files() -> None:
    # The cargo backend creates per-package vendor directories (deps/cargo/0,
    # deps/cargo/1, …). The merge function normalises the vendor path back to
    # deps/cargo because _consolidate_vendor_dirs has already flattened them.
    config1 = """
    [source.crates-io]
    replace-with = "vendored-sources"

    [source."git+https://github.com/org1/repo1.git?tag=0.1.0"]
    git = "https://github.com/org1/repo1.git"
    tag = "0.1.0"
    replace-with = "vendored-sources"

    [source.vendored-sources]
    directory = "${output_dir}/deps/cargo/0"
    """

    config2 = """
    [source.crates-io]
    replace-with = "vendored-sources"

    [source."git+https://github.com/org2/repo2.git?tag=0.2.0"]
    git = "https://github.com/org2/repo2.git"
    tag = "0.2.0"
    replace-with = "vendored-sources"

    [source.vendored-sources]
    directory = "${output_dir}/deps/cargo/1"
    """

    expected_config = """
    [source.crates-io]
    replace-with = "vendored-sources"

    [source."git+https://github.com/org1/repo1.git?tag=0.1.0"]
    git = "https://github.com/org1/repo1.git"
    tag = "0.1.0"
    replace-with = "vendored-sources"

    [source."git+https://github.com/org2/repo2.git?tag=0.2.0"]
    git = "https://github.com/org2/repo2.git"
    tag = "0.2.0"
    replace-with = "vendored-sources"

    [source.vendored-sources]
    directory = "${output_dir}/deps/cargo"
    """

    expected = tomlkit.parse(expected_config)

    # Make sure the order of the project files does not matter.
    for variation in ((config1, config2), (config2, config1)):
        pfs = [
            ProjectFile(abspath=Path("/does/not/matter"), template=template)
            for template in variation
        ]
        assert tomlkit.parse(_merge_cargo_config_files(pfs)) == expected


def test_consolidate_vendor_dirs(tmp_path: Path) -> None:
    """Per-package vendor subdirs are flattened into the parent deps/cargo dir."""
    output_dir = RootedPath(tmp_path)
    cargo_dir = tmp_path / "deps" / "cargo"

    # Simulate two per-package vendor directories with different crates
    pkg0 = cargo_dir / "0"
    pkg1 = cargo_dir / "1"

    (pkg0 / "crate-a-1.0").mkdir(parents=True)
    (pkg0 / "crate-a-1.0" / ".cargo-checksum.json").write_text('{"package":"abc"}')

    (pkg1 / "crate-b-2.0").mkdir(parents=True)
    (pkg1 / "crate-b-2.0" / ".cargo-checksum.json").write_text('{"package":"def"}')

    _consolidate_vendor_dirs(output_dir, 2)

    # Per-package subdirs should be removed
    assert not pkg0.exists()
    assert not pkg1.exists()

    # Crate dirs should be directly under deps/cargo
    assert (cargo_dir / "crate-a-1.0" / ".cargo-checksum.json").read_text() == '{"package":"abc"}'
    assert (cargo_dir / "crate-b-2.0" / ".cargo-checksum.json").read_text() == '{"package":"def"}'


def test_consolidate_vendor_dirs_keeps_first_on_collision(tmp_path: Path) -> None:
    """When two packages vendor the same crate, the first one is kept."""
    output_dir = RootedPath(tmp_path)
    cargo_dir = tmp_path / "deps" / "cargo"

    pkg0 = cargo_dir / "0"
    pkg1 = cargo_dir / "1"

    # Same crate in both per-package dirs with different checksums
    (pkg0 / "crate-x-1.0").mkdir(parents=True)
    (pkg0 / "crate-x-1.0" / ".cargo-checksum.json").write_text('{"package":"registry"}')

    (pkg1 / "crate-x-1.0").mkdir(parents=True)
    (pkg1 / "crate-x-1.0" / ".cargo-checksum.json").write_text('{"package":null}')

    _consolidate_vendor_dirs(output_dir, 2)

    # First package's version should be preserved
    assert (cargo_dir / "crate-x-1.0" / ".cargo-checksum.json").read_text() == (
        '{"package":"registry"}'
    )
