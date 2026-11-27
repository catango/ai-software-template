"""Archive extraction with validation before anything is written (design 3.3).

Rejected: absolute paths, '..' components, links that leave the destination, hard links,
device and other special files, setuid/setgid/sticky bits, and archives over the limits.
"""
import posixpath
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .errors import ArchiveError


@dataclass(frozen=True)
class Limits:
    max_files: int = 20000
    max_bytes: int = 2 * 1024 * 1024 * 1024


def _parts(name, strip):
    if name.startswith("/"):
        raise ArchiveError("absolute path in archive")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ArchiveError("path component '..' in archive")
    return parts[strip:]


def _check_link(rel, target):
    if target.startswith("/") or "\\" in target:
        raise ArchiveError("link target escapes the destination")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        raise ArchiveError("link target escapes the destination")


def extract(src, fmt, dest, strip, limits):
    """Extract SRC into DEST. Every failure is an ArchiveError: a member listed twice, or a file and a directory of the same name,
    must end as a rejected archive, not as a traceback of the engine."""
    try:
        if fmt in ("tar.gz", "tar.xz"):
            _extract_tar(Path(src), "r:gz" if fmt == "tar.gz" else "r:xz", Path(dest), strip, limits)
        elif fmt == "zip":
            _extract_zip(Path(src), Path(dest), strip, limits)
        else:
            raise ArchiveError("unsupported archive format")
    except ArchiveError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError, ValueError):
        raise ArchiveError("members of the archive collide or cannot be written") from None


def _extract_tar(src, mode, dest, strip, limits):
    try:
        archive = tarfile.open(src, mode)
    except (tarfile.TarError, EOFError, OSError):
        raise ArchiveError("not a valid archive") from None
    with archive:
        try:
            members = archive.getmembers()
        except (tarfile.TarError, EOFError, OSError):
            raise ArchiveError("not a valid archive") from None
        plan, total = [], 0
        for member in members:
            parts = _parts(member.name, strip)
            if not parts:
                continue
            rel = "/".join(parts)
            if member.islnk():
                raise ArchiveError("hard links are not allowed")
            if member.issym():
                _check_link(rel, member.linkname)
            elif not (member.isreg() or member.isdir()):
                raise ArchiveError("special file in archive")
            if member.mode & 0o7000:
                raise ArchiveError("unsafe mode bits in archive")
            total += member.size if member.isreg() else 0
            plan.append((member, rel))
            if len(plan) > limits.max_files or total > limits.max_bytes:
                raise ArchiveError("archive exceeds the file count or size limit")
        for member, rel in plan:
            archive.extract(member.replace(name=rel, deep=False), dest, filter="data")


def _extract_zip(src, dest, strip, limits):
    try:
        archive = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError):
        raise ArchiveError("not a valid archive") from None
    with archive:
        plan, total = [], 0
        for info in archive.infolist():
            parts = _parts(info.filename, strip)
            if not parts:
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ArchiveError("links are not allowed in zip archives")
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ArchiveError("special file in archive")
            if mode & 0o7000:
                raise ArchiveError("unsafe mode bits in archive")
            total += info.file_size
            plan.append((info, "/".join(parts), mode))
            if len(plan) > limits.max_files or total > limits.max_bytes:
                raise ArchiveError("archive exceeds the file count or size limit")
        written = 0
        for info, rel, mode in plan:
            target = dest / rel
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "xb") as sink:
                while True:
                    chunk = source.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limits.max_bytes:
                        raise ArchiveError("archive exceeds the file count or size limit")
                    sink.write(chunk)
            target.chmod((mode & 0o755) or 0o644)
