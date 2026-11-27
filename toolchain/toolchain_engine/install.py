"""Install semantics (design 3.3): verify before extract, harden extraction, atomic and idempotent."""
import hashlib
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import archive
from .errors import ArchiveError, FetchError, InstallError
from .manifest import Tool

STAMP = ".toolchain-stamp"
CHUNK = 1 << 20


@dataclass
class Result:
    changed: int = 0
    skipped: int = 0
    failed: int = 0


def tool_dir(prefix, tool):
    return prefix / "tools" / tool.name / tool.version


def link_path(prefix, binary):
    return prefix / "bin" / PurePosixPath(binary).name


def expected_link(prefix, tool, binary):
    return os.path.relpath(tool_dir(prefix, tool) / binary, prefix / "bin")


def links_ok(prefix, tool):
    for binary in tool.binaries:
        link = link_path(prefix, binary)
        if not link.is_symlink() or os.readlink(link) != expected_link(prefix, tool, binary) or not link.exists():
            return False
    return True


def stamp_matches(prefix, tool, sha256):
    stamp = tool_dir(prefix, tool) / STAMP
    try:
        return stamp.read_text().strip() == f"sha256={sha256}" and all((tool_dir(prefix, tool) / b).is_file() for b in tool.binaries)
    except OSError:
        return False


def _link(prefix, tool, binary):
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = link_path(prefix, binary)
    temp = bin_dir / f".new-{link.name}"
    if temp.is_symlink() or temp.exists():
        temp.unlink()
    os.symlink(expected_link(prefix, tool, binary), temp)
    os.replace(temp, link)


def _download(fetcher, url, expected, max_bytes, target):
    digest = hashlib.sha256()
    size = 0
    stream = fetcher(url, max_bytes)
    try:
        with open(target, "wb") as sink:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise InstallError("download exceeds the size cap (max_bytes)")
                digest.update(chunk)
                sink.write(chunk)
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    if digest.hexdigest() != expected:
        raise InstallError("checksum mismatch (the artifact differs from the manifest)")


def _install_download(prefix, tool, artifact, fetcher):
    prefix.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=prefix))
    try:
        blob = staging / "artifact"
        try:
            _download(fetcher, artifact.url, artifact.sha256, tool.max_bytes, blob)
        except FetchError as error:
            raise InstallError(f"download failed: {error}") from None
        content = staging / "content"
        content.mkdir()
        try:
            if tool.format == "binary":
                shutil.move(str(blob), content / tool.binaries[0])
                os.chmod(content / tool.binaries[0], 0o755)
            else:
                archive.extract(blob, tool.format, content, tool.strip, archive.Limits())
        except ArchiveError as error:
            raise InstallError(f"extraction rejected: {error}") from None
        for binary in tool.binaries:
            path = content / binary
            if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
                raise InstallError(f"declared binary is missing or not executable: {binary}")
        (content / STAMP).write_text(f"sha256={artifact.sha256}\n")
        target = tool_dir(prefix, tool)
        target.parent.mkdir(parents=True, exist_ok=True)
        old = staging / "previous"
        if target.exists():
            os.rename(target, old)
        try:
            os.rename(content, target)
        except OSError:
            if old.exists():
                os.rename(old, target)
            raise InstallError("could not move the new version into place") from None
        for binary in tool.binaries:
            _link(prefix, tool, binary)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _plan_download(prefix, tool, arch):
    artifact = tool.artifacts[arch]
    if stamp_matches(prefix, tool, artifact.sha256):
        return artifact, ("skip" if links_ok(prefix, tool) else "relink")
    return artifact, "install"


def _install_pip(prefix, tool, root, runner, state, dry_run, out):
    lock = Path(root) / tool.lockfile
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    venv = prefix / "venv"
    stamp = venv / ".lock-sha256"
    done = state.setdefault("pip", {})
    up_to_date = stamp.is_file() and stamp.read_text().strip() == digest
    links = all(link_path(prefix, b).is_symlink() and os.readlink(link_path(prefix, b)) == f"../venv/bin/{b}" and link_path(prefix, b).exists()
                for b in tool.binaries)
    if up_to_date and links:
        return "skip", None
    if dry_run:
        return "install", None
    if lock not in done or done[lock] != digest:
        if not up_to_date:
            if not venv.is_dir():
                prefix.mkdir(parents=True, exist_ok=True)
                code, _ = runner([sys.executable, "-m", "venv", str(venv)], None, 300)
                if code != 0:
                    raise InstallError("could not create the virtual environment")
            code, output = runner([str(venv / "bin" / "python"), "-m", "pip", "install", "--require-hashes", "--only-binary", ":all:",
                                   "--no-deps", "--disable-pip-version-check", "-r", str(lock)], None, 900)
            if code != 0:
                first = (output.strip().splitlines() or [""])[-1][:120]
                raise InstallError(f"pip install failed (exit {code}): {first}")
            stamp.write_text(digest + "\n")
        done[lock] = digest
    for binary in tool.binaries:
        if not (venv / "bin" / binary).exists():
            raise InstallError(f"expected binary not found in the virtual environment: {binary}")
        bin_dir = prefix / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        link = link_path(prefix, binary)
        temp = bin_dir / f".new-{binary}"
        if temp.is_symlink():
            temp.unlink()
        os.symlink(f"../venv/bin/{binary}", temp)
        os.replace(temp, link)
    return "install", None


def install(manifest, profile, prefix, arch, fetcher, runner, root, only, dry_run, out, source=None):
    prefix = Path(prefix)
    result = Result()
    state = {}
    for tool in manifest.for_profile(profile):
        if only and tool.name != only:
            continue
        if source and tool.source != source:
            continue
        label = f"{tool.name} {tool.version}" if tool.version else tool.name
        try:
            if tool.source == "apt":
                print(f"{tool.name} (apt {tool.package}): not installed by this engine (the host provides apt packages)", file=out)
                result.skipped += 1
                continue
            if tool.source == "pip":
                action, _ = _install_pip(prefix, tool, root, runner, state, dry_run, out)
                if action == "skip":
                    print(f"{tool.name}: {'would skip' if dry_run else 'skipped'} (up to date)", file=out)
                    result.skipped += 1
                else:
                    print(f"{tool.name}: {'would install from the lock file' if dry_run else 'installed'}", file=out)
                    result.changed += 1
                continue
            artifact, action = _plan_download(prefix, tool, arch)
            if action == "skip":
                print(f"{label}: {'would skip' if dry_run else 'skipped'} (up to date)", file=out)
                result.skipped += 1
            elif dry_run:
                verb = "would install" if action == "install" else "would relink"
                print(f"{label}: {verb} from {artifact.url} to {tool_dir(prefix, tool)}", file=out)
                result.changed += 1
            elif action == "relink":
                for binary in tool.binaries:
                    _link(prefix, tool, binary)
                print(f"{label}: relinked", file=out)
                result.changed += 1
            else:
                _install_download(prefix, tool, artifact, fetcher)
                print(f"{label}: installed", file=out)
                result.changed += 1
        except InstallError as error:
            print(f"{label}: FAILED at install: {error}", file=out)
            result.failed += 1
    return result
