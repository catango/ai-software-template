"""Checks of the scripted dry run (template-dev/dry-run.sh, design section 12 (a)). Standard library only; no network.

    dry_run_checks.py export TEMPLATE DEST   copy the files of the template (tracked, and untracked but not ignored) into the
                                             empty directory DEST, without history: what "Use this template" copies (step 1)
    dry_run_checks.py tree TREE              a generated tree has no template-only path, every file has one class, and no change
                                             request or ADR instance of the template (step 2)
    dry_run_checks.py leak TEMPLATE TREE     the payload of TEMPLATE (every file not classed template-only) and the generated TREE
                                             hold no identifier pattern, platform reference, fixed architecture, or unfilled
                                             placeholder (step 8, T-21)
    dry_run_checks.py host-arch              the machine name of this host (the value of uname -m)

Findings are "path:line: pattern" or "path: message" lines on standard output and never the matched text; the exit status is 1 when
there is a finding, 2 for a usage error. The patterns, the manifest reader and the exemptions are those of the `leak` and `manifest`
tests, so one definition serves the tests and the dry run.
"""
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(HERE))

import leak  # noqa: E402
import template_manifest as tm  # noqa: E402

PATTERNS = ROOT / "template-dev" / "leak" / "patterns.toml"
INSTANCE = re.compile(r"(?:^|/)(?:cr/CR-[0-9]+|adr/ADR-[0-9]+)(?:/|$|\.)")


def listed_files(template):
    """Tracked files plus untracked files that are not ignored, as relative paths that exist on disk (a tracked file that was
    deleted from the working tree is not a file of the template)."""
    done = subprocess.run(
        ["git", "-C", str(template), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=False, capture_output=True,
    )
    if done.returncode != 0:
        raise SystemExit(f"dry-run: {template} is not a git repository")
    paths = sorted(set(p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p))
    return [p for p in paths if (Path(template) / p).is_file() or (Path(template) / p).is_symlink()]


def export(template, dest):
    dest = Path(dest)
    if any(dest.iterdir()):
        print(f"{dest}: the destination is not empty")
        return 1
    count = 0
    for rel in listed_files(template):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(template) / rel, target, follow_symlinks=False)
        count += 1
    print(f"files={count}")
    return 0


def tree(root):
    root = Path(root)
    try:
        manifest = tm.load_manifest(root / ".template" / "manifest.toml")
    except (OSError, tm.ManifestError) as exc:
        print(f".template/manifest.toml: {exc}")
        return 1
    paths = tm.walk_files(root)
    findings = []
    if (root / "template-dev").exists():
        findings.append("template-dev: a template-only directory is still there")
    for rel in paths:
        if "template-only" in manifest.classify(rel) or rel.startswith("template-dev/"):
            findings.append(f"{rel}: a template-only path is still there")
        if INSTANCE.search(rel):
            findings.append(f"{rel}: a change request or ADR instance from the template")
    report = tm.check_paths(manifest, paths)
    findings += [f"{rel}: no class in the manifest" for rel in report["unclassed"]]
    findings += [f"{rel}: more than one class ({', '.join(classes)})" for rel, classes in report["multiple"]]
    for line in dict.fromkeys(findings):
        print(line)
    return 1 if findings else 0


def leak_scan(template, generated):
    patterns = leak.load_patterns(PATTERNS)
    manifest = tm.load_manifest(Path(template) / ".template" / "manifest.toml")
    payload = [p for p in listed_files(template) if "template-only" not in manifest.classify(p)]
    findings = [f"payload: {f}" for f in leak.scan(template, payload, patterns, scope="both", arch_exempt=leak.ARCH_DATA_FILES,
                                                   name_exempt=leak.PAYLOAD_NAME_EXEMPT)]
    findings += [f"tree: {f}" for f in leak.scan(generated, tm.walk_files(generated), patterns, scope="generated",
                                                 arch_exempt=leak.ARCH_DATA_FILES)]
    if findings:
        print("\n".join(findings))
    return 1 if findings else 0


def main(argv):
    command, args = (argv[0], argv[1:]) if argv else (None, [])
    try:
        if command == "export" and len(args) == 2:
            return export(*args)
        if command == "tree" and len(args) == 1:
            return tree(*args)
        if command == "leak" and len(args) == 2:
            return leak_scan(*args)
        if command == "host-arch" and not args:
            print(platform.machine())
            return 0
    except (OSError, leak.PatternError, tm.ManifestError) as exc:
        print(f"dry-run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
