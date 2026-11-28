"""The template source: its tags, its default branch, and the files of one tag, read through git with credential helpers disabled.

The source is `owner/name` from `.template/state.toml` and nothing else. Without a fixture directory it is read over HTTPS from the one
host written in this file; with `--fixture DIR` it is the local repository `DIR/<owner>/<name>` and nothing leaves the machine.

Every call runs git with the same restrictions, as an argument array (no shell), with a fresh environment that has no token, no askpass
program, no user or system git configuration, and a throw-away HOME:

  * `credential.helper=` (an empty value resets the list of helpers), `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=true`: nothing asks for or
    supplies a credential;
  * `core.hooksPath=/dev/null` and a repository made with an empty template directory: no hook exists, and none could run;
  * `protocol.allow=never` with only `https` (and `file` for a fixture) allowed: no `ext`, `ssh`, `git`, or plain `http`;
  * a bounded depth, `--no-tags`, `--no-recurse-submodules`: no submodule is fetched.

The tag is never checked out. Its files are read as objects (`ls-tree`, `cat-file`), so no attribute, filter, smudge program, or hook of
the template applies, and nothing of the clone is run. A symbolic link or a submodule entry where the template wants a file is refused.
"""
import os
import re
import subprocess
import tempfile
from collections import namedtuple
from pathlib import Path

from .errors import Refused, SourceError

HOST = "https://github.com/"
MAX_DEPTH = 500
MAX_BLOB = 2 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
MAX_ENTRIES = 20000
TIMEOUT = 180
_BRANCH = re.compile(r"refs/heads/([A-Za-z0-9._][A-Za-z0-9._/-]{0,99})")
_SHA = re.compile(r"[0-9a-f]{40}")
_TAG_REF = re.compile(r"refs/tags/(v[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9})(\^\{\})?")

Release = namedtuple("Release", "tag commit entries manifest_text manifest reader")


def locked_options(fixture):
    options = ["-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
               "-c", "core.fsmonitor=false", "-c", "core.askPass=", "-c", "transfer.fsckObjects=true", "-c", "submodule.recurse=false"]
    if fixture:
        options += ["-c", "protocol.file.allow=always"]
    return options


def locked_env(home):
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true",
            "GIT_ATTR_NOSYSTEM": "1"}


class Source:
    def __init__(self, source, fixture=None, runner=None):
        self.source = source
        self.fixture = Path(fixture) if fixture else None
        self._runner = runner or self._spawn
        self._tmp = None

    # ---- process
    @property
    def url(self):
        return str(self.fixture / self.source) if self.fixture else f"{HOST}{self.source}.git"

    def _home(self):
        if self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="template-sync-")
        return Path(self._tmp.name)

    def close(self):
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def _spawn(self, argv, cwd, env, data):
        try:
            done = subprocess.run(argv, cwd=cwd, env=env, input=data, capture_output=True, timeout=TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            raise SourceError("cannot read the template source: timeout") from None
        except OSError:
            raise SourceError("cannot run git") from None
        return done.returncode, done.stdout

    def git(self, *args, cwd=None, data=None, ok=(0,)):
        """Output of `git <locked options> <args>`; SourceError (fixed text) unless the status is in `ok`. Returns (status, output)."""
        argv = ["git", *locked_options(self.fixture), *args]
        status, out = self._runner(argv, str(cwd or self._home()), locked_env(self._home()), data)
        if status not in ok:
            raise SourceError("cannot read the template source")
        if len(out) > MAX_OUTPUT:
            raise SourceError("cannot read the template source: the answer is too large")
        return status, out

    # ---- tags and the default branch
    def check_reachable(self):
        if self.fixture and not (self.fixture / self.source).exists():
            raise SourceError("cannot read the template source")

    def tags(self):
        """{tag name: commit} of the tags `v<MAJOR>.<MINOR>.<PATCH>` of the source (a tag object is followed to its commit)."""
        self.check_reachable()
        _, out = self.git("ls-remote", "--tags", "--", self.url)
        plain, peeled = {}, {}
        for line in out.decode("utf-8", "replace").splitlines():
            sha, _, ref = line.partition("\t")
            match = _TAG_REF.fullmatch(ref)
            if match and _SHA.fullmatch(sha):
                (peeled if match.group(2) else plain)[match.group(1)] = sha
        return {name: peeled.get(name, sha) for name, sha in plain.items()}

    def default_branch(self):
        """The default branch of the source: its name, as the source's HEAD says."""
        _, out = self.git("ls-remote", "--symref", "--", self.url, "HEAD")
        for line in out.decode("utf-8", "replace").splitlines():
            if line.startswith("ref: "):
                target = line[5:].split("\t", 1)[0]
                match = _BRANCH.fullmatch(target)
                if match:
                    return match.group(1)
        raise SourceError("cannot read the template source: no default branch")

    # ---- one tag
    def fetch(self, tag, expected_commit):
        """A Release for `tag`. Refused unless its commit is the one the listing gave and is contained in the default branch (within the
        newest MAX_DEPTH commits of it). The files are read as objects, nothing is checked out."""
        branch = self.default_branch()
        repo = self._home() / "clone"
        self.git("init", "-q", "--template=", str(repo), cwd=self._home())
        self.git("fetch", "-q", f"--depth={MAX_DEPTH}", "--no-tags", "--no-recurse-submodules", "--", self.url,
                 f"+refs/heads/{branch}:refs/remotes/source/default", f"+refs/tags/{tag}:refs/remotes/source/tag", cwd=repo)
        commit = self._text(self.git("rev-parse", "--verify", "-q", "refs/remotes/source/tag^{commit}", cwd=repo)[1])
        if commit != expected_commit:
            raise Refused(f"the tag {tag} changed while it was being read")
        status, _ = self.git("merge-base", "--is-ancestor", commit, "refs/remotes/source/default", cwd=repo, ok=(0, 1))
        if status != 0:
            raise Refused(f"the commit of the tag {tag} is not contained in the default branch of the template "
                          f"(looked at its newest {MAX_DEPTH} commits)")
        _, listing = self.git("ls-tree", "-r", "-z", "--full-tree", commit, cwd=repo)
        entries = {}
        for item in listing.split(b"\0"):
            if not item:
                continue
            meta, _, raw_path = item.partition(b"\t")
            parts = meta.split(b" ")
            try:
                path = raw_path.decode("utf-8")
                mode, kind, sha = parts[0].decode(), parts[1].decode(), parts[2].decode()
            except (UnicodeDecodeError, IndexError):
                raise Refused("a path or entry of the tag cannot be read") from None
            entries[path] = (mode, kind, sha)
            if len(entries) > MAX_ENTRIES:
                raise Refused("the tag has too many files")
        reader = _Reader(self, repo, entries)
        manifest = entries.get(".template/manifest.toml")
        if manifest is None or manifest[0] not in ("100644", "100755"):
            raise Refused("the tag has no .template/manifest.toml")
        return Release(tag, commit, entries, reader.read(".template/manifest.toml").decode("utf-8", "replace"), None, reader)

    @staticmethod
    def _text(output):
        return output.decode("utf-8", "replace").strip()


class _Reader:
    """Blobs of one tag, by path, through one `git cat-file --batch` call per request."""

    def __init__(self, source, repo, entries):
        self.source, self.repo, self.entries = source, repo, entries

    def read_many(self, paths):
        wanted = [self.entries[path][2] for path in paths]
        _, out = self.source.git("cat-file", "--batch", cwd=self.repo, data=("\n".join(wanted) + "\n").encode())
        found, at = {}, 0
        for path, sha in zip(paths, wanted):
            end = out.find(b"\n", at)
            header = out[at:end].split(b" ") if end >= 0 else []
            if len(header) != 3 or header[0].decode("ascii", "replace") != sha or header[1] != b"blob":
                raise SourceError("cannot read the template source: a file of the tag is missing")
            size = int(header[2])
            if size > MAX_BLOB:
                raise Refused(f"a file of the tag is larger than {MAX_BLOB} bytes")
            found[path] = out[end + 1:end + 1 + size]
            at = end + 1 + size + 1
        return found

    def read(self, path):
        return self.read_many([path])[path]

    def paths_at(self, commit):
        """The set of file paths of `commit` (a 40-hex commit of the template history), or None when that commit is not in the clone:
        no commit recorded, or one beyond the bounded depth. Read as objects, like the files of the tag."""
        if not isinstance(commit, str) or not _SHA.fullmatch(commit):
            return None
        status, _ = self.source.git("cat-file", "-e", f"{commit}^{{commit}}", cwd=self.repo, ok=(0, 1, 128))
        if status != 0:
            return None
        _, listing = self.source.git("ls-tree", "-r", "-z", "--name-only", "--full-tree", commit, cwd=self.repo)
        return {raw.decode("utf-8", "replace") for raw in listing.split(b"\0") if raw}
