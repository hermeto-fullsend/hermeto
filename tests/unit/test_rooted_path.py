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
    rp = RootedPath(test_path)
    result = rp / "subpath"
    assert result.path == test_path / "subpath"
    assert result.root == test_path


def test_truediv_chained(test_path: Path) -> None:
    rp = RootedPath(test_path)
    result = rp / "subpath" / "symlink-to-parent"
    assert result.path == test_path
    assert result.root == test_path


def test_truediv_prevents_escape(test_path: Path) -> None:
    rp = RootedPath(test_path)
    with pytest.raises(PathOutsideRoot):
        rp / ".."


def test_name_property() -> None:
    rp = RootedPath("/some/directory")
    assert rp.name == "directory"

    child = rp.join_within_root("file.txt")
    assert child.name == "file.txt"


def test_suffix_property() -> None:
    rp = RootedPath("/some/directory")
    child = rp.join_within_root("archive.tar.gz")
    assert child.suffix == ".gz"


def test_stem_property() -> None:
    rp = RootedPath("/some/directory")
    child = rp.join_within_root("archive.tar.gz")
    assert child.stem == "archive.tar"


def test_parts_property() -> None:
    rp = RootedPath("/some/directory")
    assert rp.parts == ("/", "some", "directory")


def test_parent_within_root(test_path: Path) -> None:
    rp = RootedPath(test_path)
    child = rp.join_within_root("subpath")
    parent = child.parent
    assert parent.path == test_path
    assert parent.root == test_path


def test_parent_at_root_stays_at_root(test_path: Path) -> None:
    rp = RootedPath(test_path)
    parent = rp.parent
    assert parent.path == test_path
    assert parent.root == test_path


def test_parent_preserves_root(test_path: Path) -> None:
    rp = RootedPath(test_path)
    deep = rp.join_within_root("subpath")
    parent = deep.parent
    assert parent.root == test_path


def test_exists(tmp_path: Path) -> None:
    rp = RootedPath(tmp_path)
    assert rp.exists()
    assert not rp.join_within_root("no-such-file").exists()


def test_is_file(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hi")
    rp = RootedPath(tmp_path)
    assert rp.join_within_root("hello.txt").is_file()
    assert not rp.is_file()


def test_is_dir(tmp_path: Path) -> None:
    rp = RootedPath(tmp_path)
    assert rp.is_dir()
    (tmp_path / "hello.txt").write_text("hi")
    assert not rp.join_within_root("hello.txt").is_dir()


def test_iterdir(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    rp = RootedPath(tmp_path)
    names = sorted(p.name for p in rp.iterdir())
    assert names == ["a.txt", "b.txt"]


def test_read_text(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello world")
    rp = RootedPath(tmp_path)
    assert rp.join_within_root("hello.txt").read_text() == "hello world"


def test_read_bytes(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"\x00\x01\x02")
    rp = RootedPath(tmp_path)
    assert rp.join_within_root("data.bin").read_bytes() == b"\x00\x01\x02"


def test_stat(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello")
    rp = RootedPath(tmp_path)
    st = rp.join_within_root("hello.txt").stat()
    assert st.st_size == 5


def test_open(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hello")
    rp = RootedPath(tmp_path)
    with rp.join_within_root("hello.txt").open() as f:
        assert f.read() == "hello"


def test_open_write(tmp_path: Path) -> None:
    rp = RootedPath(tmp_path)
    with rp.join_within_root("output.txt").open("w") as f:
        f.write("written")
    assert (tmp_path / "output.txt").read_text() == "written"


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
