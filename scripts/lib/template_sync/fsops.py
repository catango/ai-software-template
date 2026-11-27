"""Paths and writes inside the repository: a path is relative, plain, and outside `.git`; a write never leaves the root and never follows a link."""
import os
import unicodedata
from pathlib import Path

MAX_PATH = 240
_BAD = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


def check_path(rel):
    """ValueError unless `rel` is a relative path with `/` separators, no empty, `.`, `..` or `.git` segment, and no control character."""
    if not isinstance(rel, str) or not rel or len(rel) > MAX_PATH:
        raise ValueError("path must be a non-empty relative path of at most %d characters" % MAX_PATH)
    if "\\" in rel or rel.startswith("/") or any(unicodedata.category(ch) in _BAD for ch in rel):
        raise ValueError("path must be a relative path with '/' separators and no control character")
    for segment in rel.split("/"):
        if segment in ("", ".", "..") or segment.lower() == ".git":
            raise ValueError("path has an empty, '.', '..' or '.git' segment")
    return rel


def _inside(root, path):
    return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root))


def _check_parents(root, rel):
    """No component below the root is a symbolic link, and every existing one is a directory inside the root."""
    current = Path(root)
    for part in rel.split("/")[:-1]:
        current = current / part
        if current.is_symlink():
            raise OSError(f"{rel}: a parent directory is a symbolic link")
        if current.exists() and not current.is_dir():
            raise OSError(f"{rel}: a parent is not a directory")
    if not _inside(root, current):
        raise OSError(f"{rel}: parent directory leaves the repository")


def safe_write(root, rel, data, mode):
    """Write `data` to root/rel (a file or a link there is replaced); the executable bit follows `mode`."""
    check_path(rel)
    root = Path(root)
    _check_parents(root, rel)
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not _inside(root, target.parent):
        raise OSError(f"{rel}: parent directory leaves the repository")
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        raise OSError(f"{rel}: a directory is in the way")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    target.chmod(0o755 if mode & 0o111 else 0o644)


def safe_remove(root, rel):
    """Remove root/rel (a file or a link) and the directories that this leaves empty."""
    check_path(rel)
    root = Path(root)
    _check_parents(root, rel)
    target = root / rel
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        raise OSError(f"{rel}: a directory is in the way")
    parent = target.parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
