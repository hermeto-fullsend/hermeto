# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from typing import Literal

import pydantic
import pytest

from hermeto.core.rooted_path import PathOutsideRoot, RootedPath


@pytest.fixture
def test_path(tmp_path: Path) -> Path:
    tmp_path.joinpath("symlink-to-parent").symlink_to("..")
    tmp_path.joinpath("subpath").mkdir()
    tmp_path.joinpath("subpath/symlink-to-parent").symlink_to("..")
    tmp_path.joinpath("subpath/symlink-to-abspath").symlink_to("/abspath")
    return tmp_path


def assert_attrs(rooted_path: RootedPath, *, path: Path, root: Path) -> None:
    assert rooted_path.path == path
    assert rooted_path.root == root


def test_path_must_be_absolute() -> None:
    with pytest.raises(ValueError):
        RootedPath("foo")


def test_rooted_path_init() -> None:
    rooted_path = RootedPath("/some/directory")
    assert_attrs(rooted_path, path=Path("/some/directory"), root=Path("/some/directory"))


def test_join_within_root(test_path: Path) -> None:
    rooted_path = RootedPath(test_path)

    assert_attrs(
        rooted_path.join_within_root("nonexistent-subpath"),
        path=test_path / "nonexistent-subpath",
        root=test_path,
    )
    assert_attrs(
        rooted_path.join_within_root("nonexistent-subpath/.."),
        path=test_path,
        root=test_path,
    )
    assert_attrs(
        rooted_path.join_within_root("nonexistent-subpath", ".."),
        path=test_path,
        root=test_path,
    )
    assert_attrs(
        rooted_path.join_within_root("nonexistent-subpath").join_within_root(".."),
        path=test_path,
        root=test_path,
    )
    assert_attrs(
        rooted_path.join_within_root("subpath").join_within_root("symlink-to-parent"),
        path=test_path,
        root=test_path,
    )


def test_re_root(test_path: Path) -> None:
    rooted_path = RootedPath(test_path)

    assert_attrs(
        rooted_path.re_root("subpath"),
        path=test_path / "subpath",
        root=test_path / "subpath",
    )
    assert_attrs(
        rooted_path.re_root("nonexistent-subpath"),
        path=test_path / "nonexistent-subpath",
        root=test_path / "nonexistent-subpath",
    )


@pytest.mark.parametrize("join_method", ["re_root", "join_within_root"])
def test_dont_leave_root(
    join_method: Literal["re_root", "join_within_root"], test_path: Path
) -> None:
    rooted_path = RootedPath(test_path)

    if join_method == "re_root":
        join = RootedPath.re_root
    else:
        join = RootedPath.join_within_root

    # root/..
    with pytest.raises(PathOutsideRoot):
        join(rooted_path, "..")

    # root/symlink-to-parent
    with pytest.raises(PathOutsideRoot):
        join(rooted_path, "symlink-to-parent")

    # root/subpath/../..
    with pytest.raises(PathOutsideRoot):
        join(rooted_path.join_within_root("subpath"), "../..")

    # root/subpath/symlink-to-abspath
    with pytest.raises(PathOutsideRoot):
        join(rooted_path.join_within_root("subpath"), "symlink-to-abspath")

    # root/ /abspath
    with pytest.raises(PathOutsideRoot):
        join(rooted_path, "/abspath")

    # (root/subpath)/..
    with pytest.raises(PathOutsideRoot):
        join(rooted_path.re_root("subpath"), "..")


def test_rooted_path_eq() -> None:
    assert RootedPath("/some/directory") == RootedPath("/some/directory")
    assert RootedPath("/some/directory").re_root("subpath") == RootedPath("/some/directory/subpath")

    a = RootedPath("/some/directory").join_within_root("subpath")
    assert a != RootedPath("/some/directory")
    assert a != RootedPath("/some/directory/subpath")
    assert a == RootedPath("/some/directory").join_within_root("subpath")


def test_truediv(test_path: Path) -> None:
    rooted_path = RootedPath(test_path)
    result = rooted_path / "subpath"
    assert result.path == test_path / "subpath"
    assert result.root == test_path


def test_truediv_prevents_escape(test_path: Path) -> None:
    rooted_path = RootedPath(test_path)
    with pytest.raises(PathOutsideRoot):
        rooted_path / ".."


def test_name_property() -> None:
    rp = RootedPath("/some/directory")
    child = rp.join_within_root("file.txt")
    assert child.name == "file.txt"


def test_suffix_property() -> None:
    rp = RootedPath("/some/directory")
    child = rp.join_within_root("file.tar.gz")
    assert child.suffix == ".gz"


def test_stem_property() -> None:
    rp = RootedPath("/some/directory")
    child = rp.join_within_root("file.txt")
    assert child.stem == "file"


def test_parts_property() -> None:
    rp = RootedPath("/some/directory")
    child = rp.join_within_root("sub")
    assert child.parts == ("/", "some", "directory", "sub")


def test_parent_within_root(test_path: Path) -> None:
    rp = RootedPath(test_path)
    child = rp.join_within_root("subpath")
    parent = child.parent
    assert isinstance(parent, RootedPath)
    assert parent.path == test_path
    assert parent.root == test_path


def test_parent_chain_clamps_at_root(test_path: Path) -> None:
    rp = RootedPath(test_path)
    deep = rp.join_within_root("subpath")
    # Going up from subpath reaches root, then stays there
    assert deep.parent.parent.parent.path == test_path
    assert deep.parent.parent.parent.root == test_path


def test_parent_at_root_stays_at_root() -> None:
    rp = RootedPath("/some/directory")
    assert rp.parent.path == Path("/some/directory")
    assert rp.parent.root == Path("/some/directory")


def test_exists(test_path: Path) -> None:
    rp = RootedPath(test_path)
    assert rp.exists()
    assert not rp.join_within_root("nonexistent").exists()


def test_is_file(test_path: Path) -> None:
    rp = RootedPath(test_path)
    (test_path / "afile.txt").write_text("hello")
    assert rp.join_within_root("afile.txt").is_file()
    assert not rp.is_file()


def test_is_dir(test_path: Path) -> None:
    rp = RootedPath(test_path)
    assert rp.is_dir()
    assert rp.join_within_root("subpath").is_dir()


def test_is_symlink(test_path: Path) -> None:
    rp = RootedPath(test_path)
    # join_within_root resolves symlinks, so the resolved path is not a symlink
    assert not rp.join_within_root("subpath").is_symlink()
    assert not rp.is_symlink()


def test_iterdir_returns_rooted_paths(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").touch()
    rp = RootedPath(tmp_path)
    results = list(rp.iterdir())
    assert all(isinstance(r, RootedPath) for r in results)
    assert all(r.root == tmp_path for r in results)
    names = sorted(r.name for r in results)
    assert names == ["a", "b"]


def test_iterdir_skips_symlinks_outside_root(tmp_path: Path) -> None:
    (tmp_path / "regular").touch()
    (tmp_path / "escape").symlink_to("/")
    rp = RootedPath(tmp_path)
    results = list(rp.iterdir())
    names = sorted(r.name for r in results)
    # The symlink resolving outside root should be silently skipped
    assert "escape" not in names
    assert "regular" in names


def test_read_text(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("world")
    rp = RootedPath(tmp_path)
    assert rp.join_within_root("hello.txt").read_text() == "world"


def test_read_bytes(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")
    rp = RootedPath(tmp_path)
    assert rp.join_within_root("data.bin").read_bytes() == b"\x00\x01\x02"


def test_write_text(tmp_path: Path) -> None:
    rp = RootedPath(tmp_path)
    rp.join_within_root("out.txt").write_text("content")
    assert (tmp_path / "out.txt").read_text() == "content"


def test_stat(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    rp = RootedPath(tmp_path)
    st = rp.join_within_root("file.txt").stat()
    assert st.st_size == 1


def test_open_read(tmp_path: Path) -> None:
    (tmp_path / "readable.txt").write_text("hello")
    rp = RootedPath(tmp_path)
    with rp.join_within_root("readable.txt").open() as f:
        assert f.read() == "hello"


def test_open_write(tmp_path: Path) -> None:
    rp = RootedPath(tmp_path)
    with rp.join_within_root("writable.txt").open("w") as f:
        f.write("written")
    assert (tmp_path / "writable.txt").read_text() == "written"


def test_mkdir(tmp_path: Path) -> None:
    rp = RootedPath(tmp_path)
    rp.join_within_root("newdir").mkdir()
    assert (tmp_path / "newdir").is_dir()


def test_mkdir_parents(tmp_path: Path) -> None:
    rp = RootedPath(tmp_path)
    rp.join_within_root("a/b/c").mkdir(parents=True, exist_ok=True)
    assert (tmp_path / "a" / "b" / "c").is_dir()


def test_pydantic_integration() -> None:
    class SomeModel(pydantic.BaseModel):
        path: RootedPath

    x = SomeModel.model_validate({"path": "/foo"})
    assert isinstance(x.path, RootedPath)
    assert_attrs(x.path, root=Path("/foo"), path=Path("/foo"))

    with pytest.raises(pydantic.ValidationError, match="expected str or os.PathLike, got bytes"):
        SomeModel.model_validate({"path": b"/foo"})

    with pytest.raises(pydantic.ValidationError, match="path must be absolute: foo/bar"):
        SomeModel.model_validate({"path": "foo/bar"})
