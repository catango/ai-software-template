"""Command line: install [--manifest FILE] [--root DIR] check | list | install | verify.

Exit codes: 0 success, 1 verification or install failure (check: invalid manifest), 2 usage or manifest error.
"""
import argparse
import json
import platform
import sys
from pathlib import Path

from . import manifest as manifest_module
from .errors import ManifestError, UsageError
from .fetch import HttpsFetcher
from .install import install
from .manifest import ARCH_NAME, PROFILE_NAME, SOURCES
from .runner import run_command
from .verify import verify

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def detect_arch(machines, machine=None):
    """The declared architecture whose machine names include this host's (platform.machine(), the value of uname -m)."""
    machine = platform.machine() if machine is None else machine
    for name, names in machines.items():
        if machine in names:
            return name
    raise UsageError("this host's machine type is not listed under architectures in the manifest")


def _profile(value):
    if not PROFILE_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid profile name")
    return value


def _arch(value):
    if not ARCH_NAME.fullmatch(value):
        raise argparse.ArgumentTypeError("invalid architecture name")
    return value


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError("invalid usage: install [--manifest FILE] [--root DIR] check|list|install|verify")


def _parser():
    parser = _Parser(prog="install", add_help=False)
    parser.add_argument("--manifest")
    parser.add_argument("--root")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    sub.add_parser("check")
    listing = sub.add_parser("list")
    listing.add_argument("--profile", required=True, type=_profile)
    listing.add_argument("--arch", type=_arch)
    listing.add_argument("--source", choices=SOURCES)
    listing.add_argument("--format", choices=("text", "json"), default="text")
    inst = sub.add_parser("install")
    inst.add_argument("--profile", required=True, type=_profile)
    inst.add_argument("--prefix", required=True)
    inst.add_argument("--arch", type=_arch)
    inst.add_argument("--only")
    inst.add_argument("--source", choices=SOURCES)
    inst.add_argument("--dry-run", action="store_true")
    ver = sub.add_parser("verify")
    ver.add_argument("--profile", required=True, type=_profile)
    ver.add_argument("--prefix", required=True)
    ver.add_argument("--arch", type=_arch)
    ver.add_argument("--download", action="store_true")
    ver.add_argument("--skip-smoke", action="store_true")
    ver.add_argument("--skip-apt", action="store_true", help="leave apt tools to the host-profiles job")
    ver.add_argument("--source", choices=SOURCES, help="only the tools of this source (a project manifest: download)")
    return parser


def main(argv=None, *, fetcher=None, runner=None, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
        root = Path(args.root) if args.root else DEFAULT_ROOT
        path = Path(args.manifest) if args.manifest else root / "toolchain" / "ci-manifest.toml"
        if not path.is_file():
            raise UsageError("manifest not found")
        if args.command == "check":
            try:
                errors = manifest_module.check_file(path, root)
            except ManifestError as error:
                raise UsageError(str(error)) from None
            for line in errors:
                print(line, file=out)
            print("toolchain: manifest ok" if not errors else f"toolchain: {len(errors)} manifest error(s)", file=out)
            return 1 if errors else 0
        errors = manifest_module.check_file(path, root)
        if errors:
            for line in errors:
                print(line, file=err)
            raise UsageError("the manifest is invalid (run: toolchain/install check)")
        manifest = manifest_module.parse(manifest_module.load(path))
        if args.command == "list":
            arch = args.arch
            tools = [t for t in manifest.for_profile(args.profile) if not args.source or t.source == args.source]
            if args.format == "json":
                print(json.dumps([{"name": t.name, "source": t.source, "version": t.version, "version_min": t.version_min,
                                   "package": t.package, "binaries": list(t.binaries)} for t in tools], indent=2), file=out)
                return 0
            for tool in tools:
                version = tool.version or (f">={tool.version_min}" if tool.version_min else "lock")
                print(f"{tool.name} {version} {tool.source}" + (f" {arch}" if arch and tool.source == "download" else ""), file=out)
            return 0
        arch = args.arch or detect_arch(manifest.architectures)
        if arch not in manifest.architectures:
            raise UsageError("architecture is not declared by the manifest")
        runner = runner or run_command
        for tool in manifest.for_profile(args.profile):
            if tool.source == "download" and arch not in tool.artifacts and (args.command == "verify" or not args.only or args.only == tool.name):
                raise UsageError(f"{tool.name}: the manifest has no artifact for architecture {arch}")
        if args.command == "install":
            if args.only and args.only not in manifest.tools:
                raise UsageError("unknown tool for --only")
            result = install(manifest, args.profile, args.prefix, arch, fetcher or HttpsFetcher(), runner, root, args.only,
                             args.dry_run, out, source=args.source)
            print(f"toolchain: {result.changed} {'would change' if args.dry_run else 'installed'}, {result.skipped} skipped, {result.failed} failed", file=out)
            print(f"changed={result.changed}", file=out)
            return 1 if result.failed else 0
        failed = verify(manifest, args.profile, args.prefix, arch, args.download, args.skip_smoke, fetcher or HttpsFetcher(), runner, out,
                        skip_apt=args.skip_apt, source=args.source)
        return 1 if failed else 0
    except UsageError as error:
        print(f"toolchain: error: {error}", file=err)
        return 2
