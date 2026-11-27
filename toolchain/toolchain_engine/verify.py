"""verify: smoke commands against the install prefix, and upstream checksum re-verification."""
import hashlib
import os
from pathlib import Path

from .errors import FetchError
from .install import CHUNK, link_path

SMOKE_TIMEOUT = 30


def _smoke(tool, prefix, runner):
    """Returns (ok, detail)."""
    if tool.source in ("download", "pip"):
        for binary in tool.binaries:
            link = link_path(prefix, binary)
            if not link.is_symlink() or not link.exists():
                return False, f"binary not installed: {link.name}"
    env = dict(os.environ)
    env["PATH"] = f"{prefix / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    code, output = runner(list(tool.smoke), env, SMOKE_TIMEOUT)
    if code != 0:
        return False, f"smoke command exited {code}"
    if tool.smoke_match and tool.smoke_match not in output:
        return False, f"smoke output does not contain the expected version text (expected '{tool.smoke_match}')"
    first = (output.strip().splitlines() or [""])[0][:80]
    return True, first


def _apt_version(tool, runner):
    code, installed = runner(["dpkg-query", "-W", "-f=${Version}", tool.package], None, SMOKE_TIMEOUT)
    if code != 0:
        return None, "installed version not checked (dpkg-query unavailable or package missing)"
    installed = installed.strip()
    code, _ = runner(["dpkg", "--compare-versions", installed, "ge", tool.version_min], None, SMOKE_TIMEOUT)
    if code != 0:
        return False, f"package {tool.package} {installed} is older than the minimum {tool.version_min}"
    return True, f"package {tool.package} {installed} >= {tool.version_min}"


def _checksum(tool, arch, fetcher):
    artifact = tool.artifacts[arch]
    digest = hashlib.sha256()
    size = 0
    try:
        stream = fetcher(artifact.url, tool.max_bytes)
        try:
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > tool.max_bytes:
                    return False, "download exceeds the size cap (max_bytes)"
                digest.update(chunk)
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
    except FetchError as error:
        return False, str(error)
    if digest.hexdigest() != artifact.sha256:
        return False, "checksum mismatch (upstream differs from the manifest)"
    return True, "checksum ok"


def verify(manifest, profile, prefix, arch, download, skip_smoke, fetcher, runner, out, skip_apt=False, source=None):
    prefix = Path(prefix)
    ok = failed = 0
    for tool in manifest.for_profile(profile):
        label = f"{tool.name} {tool.version}" if tool.version else tool.name
        if skip_apt and tool.source == "apt":
            continue
        if source and tool.source != source:
            continue
        if not skip_smoke:
            good, detail = _smoke(tool, prefix, runner)
            if good and tool.source == "apt":
                version_ok, version_detail = _apt_version(tool, runner)
                if version_ok is False:
                    good, detail = False, version_detail
                else:
                    detail = f"{detail}; {version_detail}"
            if good:
                print(f"{label}: ok ({detail})", file=out)
                ok += 1
            else:
                print(f"{label}: FAILED: {detail}", file=out)
                failed += 1
        if download and tool.source == "download":
            good, detail = _checksum(tool, arch, fetcher)
            if good:
                print(f"{label} {arch}: {detail}", file=out)
                ok += 1
            else:
                print(f"{label} {arch}: FAILED: {detail}", file=out)
                failed += 1
    print(f"toolchain verify: {ok} ok, {failed} failed", file=out)
    return failed
