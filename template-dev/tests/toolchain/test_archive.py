"""T-3: extraction rejects absolute paths, '..', escaping links, device files, and setuid bits."""
import pathlib
import tempfile
import unittest

import helpers
from helpers import tar_bytes, tree, zip_bytes
from toolchain_engine import archive
from toolchain_engine.errors import ArchiveError


def extract(data, fmt="tar.gz", strip=1, limits=None):
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "a"
        src.write_bytes(data)
        dest = pathlib.Path(tmp) / "out"
        dest.mkdir()
        try:
            archive.extract(src, fmt, dest, strip, limits or archive.Limits())
        except ArchiveError as error:
            return error, tree(dest)
        return None, tree(dest)


class Conflicts(unittest.TestCase):
    """Members that collide or conflict end as an ArchiveError, never as a traceback of the engine, and leave nothing behind."""

    def zip_of(self, entries):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return zip_bytes(entries)

    def assert_rejected(self, data, fmt):
        error, files = extract(data, fmt)
        self.assertIsInstance(error, ArchiveError)
        return files

    def test_a_zip_member_listed_twice(self):
        self.assert_rejected(self.zip_of([("t/a", b"1", 0o100644), ("t/a", b"2", 0o100644)]), "zip")

    def test_a_zip_file_and_a_directory_of_the_same_name(self):
        self.assert_rejected(self.zip_of([("t/a", b"1", 0o100644), ("t/a/b", b"2", 0o100644)]), "zip")

    def test_a_zip_directory_entry_over_a_file(self):
        self.assert_rejected(self.zip_of([("t/a", b"1", 0o100644), ("t/a/", b"", 0o040755)]), "zip")

    def test_a_tar_file_and_a_directory_of_the_same_name(self):
        data = tar_bytes([("t/a", "file", b"1", 0o644), ("t/a/b", "file", b"2", 0o644)])
        self.assert_rejected(data, "tar.gz")

    def test_a_tar_directory_over_a_file(self):
        data = tar_bytes([("t/a", "file", b"1", 0o644), ("t/a/", "dir", None, 0o755)])
        self.assert_rejected(data, "tar.gz")

    def test_install_reports_a_conflicting_archive_as_a_failed_install(self):
        import io
        from toolchain_engine import cli
        from helpers import FakeFetcher, ZERO
        data = self.zip_of([("t/alpha", b"#!/bin/sh\n", 0o100755), ("t/alpha", b"#!/bin/sh\n", 0o100755)])
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "toolchain").mkdir()
            text = helpers.manifest_text(amd64_sha=helpers.sha256(data), arm64_sha=ZERO).replace('format = "tar.gz"', 'format = "zip"')
            (root / "toolchain" / "ci-manifest.toml").write_text(text)
            out, err = io.StringIO(), io.StringIO()
            code = cli.main(["--manifest", str(root / "toolchain" / "ci-manifest.toml"), "--root", str(root), "install", "--profile", "gate",
                             "--prefix", str(root / "prefix"), "--arch", "amd64", "--only", "alpha"],
                            fetcher=FakeFetcher({helpers.AMD64_URL: data}), out=out, err=err)
            self.assertEqual(code, 1, out.getvalue() + err.getvalue())
            self.assertIn("FAILED at install: extraction rejected", out.getvalue())
            self.assertFalse((root / "prefix" / "tools" / "alpha").exists())


class Valid(unittest.TestCase):
    def test_strip_one_and_modes(self):
        error, files = extract(helpers.good_archive())
        self.assertIsNone(error)
        names = {entry[0] for entry in files}
        self.assertIn("bin/alpha", names)
        self.assertIn("README", names)
        modes = {entry[0]: entry[3] for entry in files if entry[1] == "file"}
        self.assertEqual(modes["bin/alpha"], 0o755)

    def test_strip_zero_and_xz(self):
        data = tar_bytes([("alpha", "file", b"x", 0o755)], mode="w:xz")
        error, files = extract(data, "tar.xz", strip=0)
        self.assertIsNone(error)
        self.assertEqual([entry[0] for entry in files], ["alpha"])

    def test_entries_shorter_than_strip_are_skipped(self):
        data = tar_bytes([("top/", "dir", None, 0o755), ("loose-file", "file", b"x", 0o644), ("top/a", "file", b"y", 0o644)])
        error, files = extract(data, strip=1)
        self.assertIsNone(error)
        self.assertEqual([entry[0] for entry in files], ["a"])

    def test_safe_relative_symlink_inside_destination(self):
        data = tar_bytes([("t/bin/", "dir", None, 0o755), ("t/bin/real", "file", b"x", 0o755), ("t/bin/alias", "symlink", "real", 0o777),
                          ("t/lib/", "dir", None, 0o755), ("t/lib/up", "symlink", "../bin/real", 0o777)])
        error, files = extract(data)
        self.assertIsNone(error)
        self.assertIn(("bin/alias", "link", "real"), files)

    def test_a_zip_member_that_others_can_write_is_extracted_without_that_bit(self):
        """the AI user must not be able to modify an installed project tool, so no mode of an archive grants
        group or other write (the tar `data` filter already clears it; the zip path does it here)."""
        data = zip_bytes([("top/bin/alpha", b"#!/bin/sh\n", 0o100777), ("top/other", b"x", 0o100666)])
        error, files = extract(data, "zip")
        self.assertIsNone(error)
        modes = {entry[0]: entry[3] for entry in files if entry[1] == "file"}
        self.assertEqual(modes["bin/alpha"], 0o755)
        self.assertEqual(modes["other"], 0o644)

    def test_zip(self):
        data = zip_bytes([("top/bin/alpha", b"#!/bin/sh\n", 0o100755), ("top/README", b"r", 0o100644)])
        error, files = extract(data, "zip")
        self.assertIsNone(error)
        modes = {entry[0]: entry[3] for entry in files if entry[1] == "file"}
        self.assertEqual(modes["bin/alpha"], 0o755)


class Rejected(unittest.TestCase):
    def assert_rejected(self, data, fmt="tar.gz", strip=1, fragment=None):
        error, files = extract(data, fmt, strip)
        self.assertIsNotNone(error, "archive was accepted")
        self.assertEqual(files, [], f"files were extracted: {files}")
        if fragment:
            self.assertIn(fragment, str(error))

    def test_absolute_paths(self):
        self.assert_rejected(tar_bytes([("/etc/evil", "file", b"x", 0o644)]), strip=0, fragment="absolute")
        self.assert_rejected(tar_bytes([("t/ok", "file", b"x", 0o644), ("/abs/path", "file", b"x", 0o644)]), strip=1)

    def test_parent_components(self):
        for name in ("../evil", "t/../../evil", "t/a/../../../evil", "..", "t/.."):
            with self.subTest(name=name):
                self.assert_rejected(tar_bytes([(name, "file", b"x", 0o644)]), strip=0, fragment="..")

    def test_parent_component_hidden_by_strip(self):
        self.assert_rejected(tar_bytes([("t/../evil", "file", b"x", 0o644)]), strip=1)

    def test_symlinks_that_escape(self):
        for target in ("/etc/passwd", "../../outside", "../outside", "a/../../outside"):
            with self.subTest(target=target):
                self.assert_rejected(tar_bytes([("t/ok", "file", b"x", 0o644), ("t/link", "symlink", target, 0o777)]), fragment="link")

    def test_hardlinks(self):
        self.assert_rejected(tar_bytes([("t/a", "file", b"x", 0o644), ("t/b", "hardlink", "t/a", 0o644)]), fragment="link")
        self.assert_rejected(tar_bytes([("t/b", "hardlink", "/etc/passwd", 0o644)]), fragment="link")

    def test_device_and_fifo_entries(self):
        self.assert_rejected(tar_bytes([("t/dev", "chr", None, 0o644)]), fragment="special")
        self.assert_rejected(tar_bytes([("t/pipe", "fifo", None, 0o644)]), fragment="special")

    def test_setuid_setgid_sticky(self):
        for perm in (0o4755, 0o2755, 0o1755, 0o6755):
            with self.subTest(perm=oct(perm)):
                self.assert_rejected(tar_bytes([("t/ok", "file", b"x", 0o644), ("t/bad", "file", b"x", perm)]), fragment="mode")
        self.assert_rejected(tar_bytes([("t/dir", "dir", None, 0o4755)]), fragment="mode")

    def test_zip_traversal_absolute_symlink_and_setuid(self):
        self.assert_rejected(zip_bytes([("t/../evil", b"x", 0o100644)]), "zip", fragment="..")
        self.assert_rejected(zip_bytes([("/abs", b"x", 0o100644)]), "zip", strip=0, fragment="absolute")
        self.assert_rejected(zip_bytes([("t/link", b"/etc/passwd", 0o120777)]), "zip", fragment="link")
        self.assert_rejected(zip_bytes([("t/bad", b"x", 0o104755)]), "zip", fragment="mode")

    def test_size_and_count_limits(self):
        limits = archive.Limits(max_files=2, max_bytes=100)
        many = tar_bytes([(f"t/f{i}", "file", b"x", 0o644) for i in range(5)])
        error, files = extract(many, limits=limits)
        self.assertIsNotNone(error)
        self.assertEqual(files, [])
        big = tar_bytes([("t/big", "file", b"x" * 1000, 0o644)])
        error, files = extract(big, limits=limits)
        self.assertIsNotNone(error)
        self.assertEqual(files, [])

    def test_not_an_archive(self):
        self.assert_rejected(b"this is not an archive")
        self.assert_rejected(b"PK\x03\x04garbage", "zip")

    def test_unknown_format(self):
        self.assert_rejected(helpers.good_archive(), "rar")


if __name__ == "__main__":
    unittest.main()
