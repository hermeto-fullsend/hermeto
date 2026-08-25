# SPDX-License-Identifier: GPL-3.0-only
import os
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any, TypeVar

from pydantic_core import CoreSchema, core_schema

from hermeto.core.errors import PathOutsideRoot
from hermeto.core.type_aliases import StrPath

RootedPathT = TypeVar("RootedPathT", bound="RootedPath")


class RootedPath(os.PathLike[str]):
    """A safer way to handle subpaths.

    Get a subpath, guaranteeing that it really is a subpath:

    >>> rooted_path = RootedPath("/some/directory")
    >>> rooted_path.join_within_root("..")
    Traceback (most recent call last):
    ...
    hermeto.core.errors.PathOutsideRoot: ...

    >>> rooted_path.join_within_root("/abspath")
    Traceback (most recent call last):
    ...
    hermeto.core.errors.PathOutsideRoot: ...

    >>> rooted_path = RootedPath("/some/directory")
    >>> rooted_path.join_within_root("vendor", "modules.txt").path
    PosixPath('/some/directory/vendor/modules.txt')

    Supports the ``/`` operator for path joining (delegates to join_within_root):

    >>> rooted_path = RootedPath("/some/directory")
    >>> (rooted_path / "subpath").path
    PosixPath('/some/directory/subpath')

    The join_within_root method remembers the original root. See the join_within_root
    and re_root docstrings for more details.

    Implements the PathLike interface -> most stdlib methods that accept paths will work
    with a RootedPath as well.

    Exposes commonly-used pathlib.Path properties and methods as convenience delegates.
    Mutating filesystem methods (rename, unlink, symlink_to, etc.) are intentionally
    not delegated to preserve safety guarantees.

    Implements __get_validators__ for pydantic integration.
    """

    def __init__(self, path: StrPath) -> None:
        """Create a RootedPath.

        :param path: the path (which also becomes the root of the RootedPath)
        """
        self._root = Path(path)
        self._path = self.root
        if not self._path.is_absolute():
            raise ValueError(f"path must be absolute: {path}")

    @property
    def root(self) -> Path:
        """Get the root directory which this path is not allowed to leave."""
        return self._root

    @property
    def path(self) -> Path:
        """Get the current path, which is guaranteed to be at or below the root."""
        return self._path

    @property
    def subpath_from_root(self) -> Path:
        """Get the path relative to the root."""
        return self._path.relative_to(self._root)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RootedPath):
            # NotImplemented is a special value which should be returned by the binary special methods
            # (e.g. __eq__(), __lt__(), __add__(), __rsub__(), etc.)  to indicate that the operation is
            # not implemented with respect to the other type - https://docs.python.org/3/library/constants.html#NotImplemented
            return NotImplemented

        return self.path == other.path and self.root == other.root

    def __fspath__(self) -> str:
        return self.path.__fspath__()

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        typename = type(self).__name__
        subpath_from_root = self.path.relative_to(self.root)
        return f"<{typename} root={str(self.root)!r} subpath={str(subpath_from_root)!r}>"

    def __hash__(self) -> int:
        return hash((self._path, self._root))

    def re_root(self: RootedPathT, *other: StrPath) -> RootedPathT:
        """Safely join other path components and make the result the new root.

        >>> rooted_path = RootedPath("/some/directory")
        >>> rooted_path.re_root("subpath").join_within_root("..")
        Traceback (most recent call last):
        ...
        hermeto.core.errors.PathOutsideRoot: ...

        :raises PathOutsideRoot: if the resulting path is not a subpath of the root
        """
        subpath = self.path.joinpath(*other).resolve()
        if not subpath.is_relative_to(self.root):
            raise PathOutsideRoot(
                s_self=str(Path(*other)),
                s_other=str(self.path),
                s_root=str(self.root),
            )
        cls = type(self)
        return cls(subpath)

    def join_within_root(self: RootedPathT, *other: StrPath) -> RootedPathT:
        """Safely join other path components but remember the original root.

        >>> rooted_path = RootedPath("/some/directory")
        >>> rooted_path.join_within_root("subpath").join_within_root("..")
        <RootedPath root='/some/directory' subpath='.'>

        :raises PathOutsideRoot: if the resulting path is not a subpath of the root
        """
        new = self.re_root(*other)
        new._root = self.root
        return new

    def __truediv__(self: RootedPathT, other: StrPath) -> RootedPathT:
        """Join paths using the ``/`` operator, delegating to join_within_root.

        :raises PathOutsideRoot: if the resulting path is not a subpath of the root
        """
        return self.join_within_root(other)

    # -- pathlib.Path property delegates --

    @property
    def name(self) -> str:
        """The final component of the path."""
        return self._path.name

    @property
    def suffix(self) -> str:
        """The file extension of the final component."""
        return self._path.suffix

    @property
    def stem(self) -> str:
        """The final component without its suffix."""
        return self._path.stem

    @property
    def parts(self) -> tuple[str, ...]:
        """The components of the path as a tuple."""
        return self._path.parts

    @property
    def parent(self: RootedPathT) -> RootedPathT:
        """Get the parent directory, clamped at the root boundary.

        Unlike pathlib.Path.parent, this will never return a path above
        the root — repeated calls to .parent stop at the root.
        """
        parent_path = self._path.parent
        if not parent_path.is_relative_to(self._root):
            parent_path = self._root
        cls = type(self)
        new = cls.__new__(cls)
        new._root = self._root
        new._path = parent_path
        return new

    # -- pathlib.Path method delegates --

    def exists(self) -> bool:
        """Whether the path points to an existing file or directory."""
        return self._path.exists()

    def is_file(self) -> bool:
        """Whether the path points to a regular file."""
        return self._path.is_file()

    def is_dir(self) -> bool:
        """Whether the path points to a directory."""
        return self._path.is_dir()

    def is_symlink(self) -> bool:
        """Whether the path is a symbolic link."""
        return self._path.is_symlink()

    def iterdir(self: RootedPathT) -> Iterator[RootedPathT]:
        """Iterate over directory contents, yielding RootedPath objects.

        Each yielded item preserves the root boundary of this RootedPath.
        """
        for child in self._path.iterdir():
            yield self.join_within_root(child.name)

    def read_text(self, encoding: str | None = None, errors: str | None = None) -> str:
        """Read the file contents as a string."""
        return self._path.read_text(encoding=encoding, errors=errors)

    def read_bytes(self) -> bytes:
        """Read the file contents as bytes."""
        return self._path.read_bytes()

    def write_text(
        self,
        data: str,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        """Write string data to the file."""
        return self._path.write_text(data, encoding=encoding, errors=errors, newline=newline)

    def stat(self) -> os.stat_result:
        """Return the result of os.stat() on this path."""
        return self._path.stat()

    def open(
        self,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[Any]:
        """Open the file pointed to by the path.

        Supports all modes — the safety guarantee of RootedPath is about
        path traversal prevention, not I/O mode restriction.
        """
        return self._path.open(
            mode=mode, buffering=buffering, encoding=encoding, errors=errors, newline=newline
        )

    def mkdir(
        self,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        """Create the directory at this path."""
        self._path.mkdir(mode=mode, parents=parents, exist_ok=exist_ok)

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> CoreSchema:
        return core_schema.no_info_before_validator_function(
            cls._validate, core_schema.any_schema()
        )

    @staticmethod
    def _validate(value: Any) -> "RootedPath":
        if not isinstance(value, (str, os.PathLike)):
            raise ValueError(f"expected str or os.PathLike, got {type(value).__name__}")
        return RootedPath(path=value)
