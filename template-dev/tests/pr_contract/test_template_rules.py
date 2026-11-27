"""TC-1 (template-sync PRs) and TC-2 (init PRs) (T-8, T-9, T-20 rule part). The classes come from the manifest of the base commit;
for added paths of a template-sync PR, from the manifest of the head commit. A new seeded path of a sync must be listed in the
body (TC-1, decision of 2026-11-27, option B). An init PR may append entries to `permissions.deny` of `.claude/settings.json`
that the head's `.claude/deny.platform.txt` lists, and change nothing else in that file (TC-2, option 1)."""
import json
import unittest

import helpers
from helpers import BASE_SHA, HEAD_SHA, MemClient, body, fc, make_pr
from pr_contract import rules
from pr_contract.errors import ApiError

MANIFEST = '''schema_version = 1
version = "1.0.0"
[classes]
owned = ["scripts/", ".template/manifest.toml", ".github/workflows/ci.yml", ".claude/settings.json"]
seeded = ["config/project.yaml", "README.md", ".template/state.toml", ".template/overrides.toml", ".claude/deny.platform.txt"]
template-only = ["template-dev/", ".github/workflows/template-ci.yml"]
'''
HEAD_MANIFEST = MANIFEST.replace('owned = [', 'owned = ["newdir/", ').replace('seeded = [', 'seeded = ["docs/NEW.md", "docs/NEW2.md", ')
CLASS = "Changed files by class"
STATE_OLD = '[template]\nversion = "1.0.0"\n'
STATE_NEW = '[template]\nversion = "1.2.0"\n'
STATE = ".template/state.toml"


def evaluate(kind, files, section=None, **kw):
    pr = make_pr(kind, body=body(kind, sections={CLASS: section} if section is not None else None))
    client = MemClient(files={pr.number: files}, **kw)
    return rules.evaluate(pr, client, helpers.TEMPLATES), client


def ids(found, rule):
    return [v.message for v in found if v.rule == rule]


def sync(files, base=None, head=None, **kw):  # kw: section=<text of "Changed files by class">
    base_files = {".template/manifest.toml": MANIFEST, STATE: STATE_OLD}
    head_files = {".template/manifest.toml": HEAD_MANIFEST, STATE: STATE_NEW}
    base_files.update(base or {})
    head_files.update(head or {})
    for key in [k for k, v in {**base_files}.items() if v is None]:
        del base_files[key]
    for key in [k for k, v in {**head_files}.items() if v is None]:
        del head_files[key]
    return evaluate("template-sync", files, base_files=base_files, head_files=head_files, **kw)


UNLISTED = "added seeded path is not listed under 'Changed files by class': "


class TC1(unittest.TestCase):
    OK = [fc("scripts/a.py"), fc("scripts/gone.py", "removed"), fc("newdir/x.py", "added"), fc(STATE)]

    def test_a_clean_sync_passes(self):
        found, _ = sync(self.OK)
        self.assertEqual(found, [])

    def test_a_changed_or_removed_path_must_be_owned_in_the_base_manifest(self):
        for status in ("modified", "removed", "changed"):
            with self.subTest(status=status):
                found, _ = sync([fc("README.md", status), fc(STATE)])
                self.assertEqual(ids(found, "TC-1"), ["path is not owned in the base manifest: README.md"])

    def test_a_path_that_is_unclassed_or_template_only_is_refused(self):
        for path in ("docs/x.md", "template-dev/a.py", ".github/workflows/template-ci.yml"):
            with self.subTest(path=path):
                found, _ = sync([fc(path), fc(STATE)])
                self.assertEqual(len(ids(found, "TC-1")), 1)

    def test_an_added_path_must_be_owned_or_seeded_in_the_head_manifest(self):
        found, _ = sync([fc("docs/new.md", "added"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["added path is neither owned nor seeded in the head manifest: docs/new.md"])
        # owned only by the head manifest: fine; owned only by the base manifest: still not "new at the head"
        self.assertEqual(sync([fc("newdir/x.py", "added"), fc(STATE)])[0], [])
        found, _ = sync([fc("newdir/x.py", "added"), fc(STATE)], head={".template/manifest.toml": MANIFEST})
        self.assertEqual(len(ids(found, "TC-1")), 1)

    def test_a_template_only_or_unclassed_added_path_is_refused_even_when_listed(self):
        for path in ("template-dev/new.py", "docs/other.md"):
            with self.subTest(path=path):
                found, _ = sync([fc(path, "added"), fc(STATE)], section=f"- {path}")
                self.assertEqual(ids(found, "TC-1"), [f"added path is neither owned nor seeded in the head manifest: {path}"])

    def test_the_head_manifest_does_not_decide_what_may_change(self):
        # the head makes README.md owned; a change to it is still judged by the base manifest
        head = {".template/manifest.toml": MANIFEST.replace('"README.md", ', "").replace('owned = [', 'owned = ["README.md", ')}
        found, _ = sync([fc("README.md"), fc(STATE)], head=head)
        self.assertEqual(ids(found, "TC-1"), ["path is not owned in the base manifest: README.md"])

    def test_overrides_are_never_changed_in_any_way(self):
        for item in (fc(".template/overrides.toml"), fc(".template/overrides.toml", "added"), fc(".template/overrides.toml", "removed"),
                     fc("scripts/o.toml", "renamed", previous=".template/overrides.toml"),
                     fc(".template/overrides.toml", "renamed", previous="scripts/o.toml")):
            with self.subTest(item=item):
                found, _ = sync([item, fc(STATE)])
                self.assertIn(".template/overrides.toml is changed", ids(found, "TC-1"))

    def test_the_state_file_must_be_updated_and_equal_the_branch_version(self):
        found, _ = sync([fc("scripts/a.py")])
        self.assertEqual(ids(found, "TC-1"), [".template/state.toml is not updated"])
        for text in (STATE_OLD, '[template]\nversion = "1.20.0"\n', '[template]\nversion = 120\n', "not toml [", "", None):
            with self.subTest(text=text):
                found, _ = sync([fc("scripts/a.py"), fc(STATE)], head={STATE: text})
                self.assertEqual(ids(found, "TC-1"), ["the version in .template/state.toml does not equal the branch version"])

    def test_the_state_file_cannot_be_added_removed_or_renamed(self):
        for item in (fc(STATE, "added"), fc(STATE, "removed"), fc("scripts/s.toml", "renamed", previous=STATE)):
            with self.subTest(item=item):
                found, _ = sync([item])
                self.assertTrue(ids(found, "TC-1"))

    def test_a_rename_removes_the_old_path_and_adds_the_new_one(self):
        found, _ = sync([fc("newdir/b.py", "renamed", previous="scripts/b.py"), fc(STATE)])
        self.assertEqual(found, [])
        found, _ = sync([fc("docs/b.md", "renamed", previous="scripts/b.py"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["added path is neither owned nor seeded in the head manifest: docs/b.md"])
        found, _ = sync([fc("scripts/b.py", "renamed", previous="README.md"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["path is not owned in the base manifest: README.md"])
        # a rename from an owned path to a new seeded path is a removal plus an addition; the new name must be listed
        found, _ = sync([fc("docs/NEW.md", "renamed", previous="scripts/b.py"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), [UNLISTED + "docs/NEW.md"])
        found, _ = sync([fc("docs/NEW.md", "renamed", previous="scripts/b.py"), fc(STATE)], section="- docs/NEW.md")
        self.assertEqual(found, [])
        found, _ = sync([fc("docs/NEW.md", "renamed", previous="README.md"), fc(STATE)], section="- docs/NEW.md")
        self.assertEqual(ids(found, "TC-1"), ["path is not owned in the base manifest: README.md"])

    def test_a_status_the_rule_does_not_know_fails(self):
        found, _ = sync([fc("scripts/a.py", "unchanged"), fc(STATE)])
        self.assertEqual(ids(found, "TC-1"), ["change of unknown kind: scripts/a.py"])

    def test_a_missing_or_invalid_manifest_fails_closed(self):
        for which, files in (("base", {"base": {".template/manifest.toml": None}}), ("base", {"base": {".template/manifest.toml": "x ["}}),
                             ("head", {"head": {".template/manifest.toml": None}}), ("head", {"head": {".template/manifest.toml": "version = 1"}})):
            with self.subTest(which=which, files=files):
                found, _ = sync(self.OK, **files)
                self.assertIn(f"the manifest of the {which} commit is missing or invalid", ids(found, "TC-1"))

    def test_findings_are_capped_and_hold_no_pr_text(self):
        found, _ = sync([fc(f"docs/f{i}.md") for i in range(30)] + [fc(STATE)])
        messages = ids(found, "TC-1")
        self.assertEqual(len(messages), rules.PATH_CAP + 1)
        self.assertEqual(messages[-1], "and 20 more findings")

    def test_policy_is_read_only_from_the_base_and_the_head_commits(self):
        found, client = sync(self.OK)
        reads = [c[1] for c in client.calls if c[0] == "file_text"]
        self.assertTrue(all(sha in (BASE_SHA, HEAD_SHA) for sha, _ in reads))
        self.assertIn((HEAD_SHA, STATE), reads)
        self.assertNotIn((HEAD_SHA, "config/project.yaml"), reads)

    def test_a_transient_failure_is_an_error(self):
        with self.assertRaises(ApiError):
            sync(self.OK, fail=ApiError("HTTP 500"))

    def test_other_rules_still_apply(self):
        found, _ = sync([fc(".github/workflows/ci.yml"), fc(STATE)])
        self.assertEqual([v.rule for v in found], ["PC-6"])
        found, _ = sync([fc("scripts/operator/run.py", "removed"), fc("README.md"), fc(STATE)])
        self.assertEqual(sorted({v.rule for v in found}), ["PC-8", "TC-1"])

    def test_the_branch_version_is_read_from_the_branch_only(self):
        self.assertEqual(rules.branch_version("template-sync/v1.2.0"), "1.2.0")
        self.assertEqual(rules.branch_version("init/v10.0.123"), "10.0.123")
        for ref in ("impl/CR-2-x", "template-sync/1.2.0", "template-sync/v1.2", ""):
            self.assertIsNone(rules.branch_version(ref))


class TC1SeededAdditions(unittest.TestCase):
    """Option B: a sync may add a path that is new and seeded in the head manifest, if the body lists it."""
    NEW = [fc("docs/NEW.md", "added"), fc(STATE)]

    def test_an_added_seeded_path_that_is_listed_passes(self):
        found, _ = sync(self.NEW, section="- `docs/NEW.md` (seeded, new in this version)")
        self.assertEqual(found, [])

    def test_an_added_seeded_path_that_is_not_listed_names_the_path(self):
        for section in (None, "Filled in for the test.", "- scripts/a.py (owned, changed)"):
            with self.subTest(section=section):
                found, _ = sync(self.NEW, section=section)
                self.assertEqual(ids(found, "TC-1"), [UNLISTED + "docs/NEW.md"])

    def test_the_listing_forms_that_count(self):
        for section in ("docs/NEW.md", "- docs/NEW.md", "* `docs/NEW.md`", "1. docs/NEW.md - seeded", "| seeded | docs/NEW.md |",
                        "Seeded: docs/NEW.md.", "Seeded: docs/NEW.md, docs/NEW2.md", "```\ndocs/NEW.md\n```", "(docs/NEW.md)"):
            with self.subTest(section=section):
                found, _ = sync(self.NEW, section=section)
                self.assertEqual(found, [])

    def test_a_longer_or_different_path_is_not_a_listing(self):
        for section in ("docs/NEW.md.bak", "docs/NEW.mdx", "xdocs/NEW.md", "other/docs/NEW.md", "docs/NEW.md/x", "docs/NEW_md",
                        "docs/NEW.md-old", "docs/NEW2.md", "docs/new.md", "docs/NEW", "docs/ NEW.md", "docs/NEW .md"):
            with self.subTest(section=section):
                found, _ = sync(self.NEW, section=section)
                self.assertEqual(ids(found, "TC-1"), [UNLISTED + "docs/NEW.md"])

    def test_a_path_next_to_a_letter_or_digit_of_any_script_is_not_a_listing(self):
        for section in ("\u00e9docs/NEW.md", "docs/NEW.md\u00e9", "\u00f1docs/NEW.md", "\u6587docs/NEW.md", "docs/NEW.md\u6587",
                        "docs/NEW.md\u0663", "\u0663docs/NEW.md", "docs/NEW.md.\u00e9", "docs/NEW.md\u00e9\u00e9", "\u03b1docs/NEW.md",
                        "docs/NEW.md\u0301", "docs/NEW.m\u0301d", "x\u0301docs/NEW.md", "docs/NEW.md\u20dd"):
            with self.subTest(section=ascii(section)):
                found, _ = sync(self.NEW, section=section)
                self.assertEqual(ids(found, "TC-1"), [UNLISTED + "docs/NEW.md"])

    def test_a_path_next_to_punctuation_or_a_space_of_any_script_is_a_listing(self):
        for section in ("docs/NEW.md\u3002", "docs/NEW.md\u00a0(seeded)", "\u300cdocs/NEW.md\u300d", "docs/NEW.md\u2026",
                        "\u2013 docs/NEW.md \u2013", "seeded\u2014docs/NEW.md\u2014new", "\u00abdocs/NEW.md\u00bb", "docs/NEW.md\uff0c",
                        "\u3000docs/NEW.md", "\u2022 docs/NEW.md", "docs/NEW.md\u00e9 docs/NEW.md"):
            with self.subTest(section=ascii(section)):
                found, _ = sync(self.NEW, section=section)
                self.assertEqual(found, [])

    def test_text_in_a_comment_or_another_section_is_not_a_listing(self):
        found, _ = sync(self.NEW, section="<!-- docs/NEW.md -->\nnothing else")
        self.assertEqual(ids(found, "TC-1"), [UNLISTED + "docs/NEW.md"])
        pr = make_pr("template-sync", body=body("template-sync", sections={"Summary": "docs/NEW.md", CLASS: "none"}))
        client = MemClient(files={pr.number: self.NEW}, base_files={".template/manifest.toml": MANIFEST, STATE: STATE_OLD},
                           head_files={".template/manifest.toml": HEAD_MANIFEST, STATE: STATE_NEW})
        found = rules.evaluate(pr, client, helpers.TEMPLATES)
        self.assertEqual(ids(found, "TC-1"), [UNLISTED + "docs/NEW.md"])

    def test_each_added_seeded_path_must_be_listed(self):
        files = [fc("docs/NEW.md", "added"), fc("docs/NEW2.md", "added"), fc(STATE)]
        found, _ = sync(files, section="- docs/NEW2.md")
        self.assertEqual(ids(found, "TC-1"), [UNLISTED + "docs/NEW.md"])
        found, _ = sync(files, section="- docs/NEW.md\n- docs/NEW2.md")
        self.assertEqual(found, [])

    def test_an_added_seeded_path_must_be_seeded_in_the_head_manifest(self):
        found, _ = sync(self.NEW, section="- docs/NEW.md", head={".template/manifest.toml": MANIFEST})
        self.assertEqual(ids(found, "TC-1"), ["added path is neither owned nor seeded in the head manifest: docs/NEW.md"])

    def test_an_added_path_with_more_than_one_class_in_the_head_manifest_is_named_as_such(self):
        for classes in (('"docs/BOTH.md", ', '"docs/BOTH.md", ', ""), ('"docs/BOTH.md", ', "", '"docs/BOTH.md", '),
                        ("", '"docs/BOTH.md", ', '"docs/BOTH.md", '), ('"docs/BOTH.md", ', '"docs/BOTH.md", ', '"docs/BOTH.md", ')):
            with self.subTest(classes=classes):
                head = HEAD_MANIFEST.replace("owned = [", f"owned = [{classes[0]}", 1).replace("seeded = [", f"seeded = [{classes[1]}", 1)
                head = head.replace("template-only = [", f"template-only = [{classes[2]}", 1)
                found, _ = sync([fc("docs/BOTH.md", "added"), fc(STATE)], section="- docs/BOTH.md", head={".template/manifest.toml": head})
                self.assertEqual(ids(found, "TC-1"), ["added path has more than one class in the head manifest: docs/BOTH.md"])

    def test_an_added_path_with_no_class_keeps_its_message(self):
        found, _ = sync([fc("docs/NOCLASS.md", "added"), fc(STATE)], section="- docs/NOCLASS.md")
        self.assertEqual(ids(found, "TC-1"), ["added path is neither owned nor seeded in the head manifest: docs/NOCLASS.md"])

    def test_an_added_seeded_path_must_be_absent_from_the_base_commit(self):
        found, _ = sync(self.NEW, section="- docs/NEW.md", base={"docs/NEW.md": "already here"})
        self.assertEqual(ids(found, "TC-1"), ["added seeded path exists on the base commit: docs/NEW.md"])

    def test_a_seeded_path_that_exists_is_never_changed_removed_or_renamed(self):
        for item in (fc("README.md", "modified"), fc("README.md", "removed"), fc("README.md", "changed"),
                     fc(".claude/deny.platform.txt", "modified"), fc("docs/NEW.md", "renamed", previous="README.md")):
            with self.subTest(item=item):
                found, _ = sync([item, fc(STATE)], section="- README.md\n- docs/NEW.md\n- .claude/deny.platform.txt")
                self.assertEqual(len(ids(found, "TC-1")), 1, found)
                self.assertIn("is not owned in the base manifest", ids(found, "TC-1")[0])

    def test_the_owned_settings_file_may_be_changed_by_a_sync(self):
        # decision Q16: the sync overwrites the owned settings file; TC-1 needs no exception because the base manifest says owned
        found, _ = sync([fc(".claude/settings.json"), fc(STATE)])
        self.assertEqual(found, [])

    def test_the_settings_file_is_not_special_when_the_base_manifest_does_not_make_it_owned(self):
        manifest = MANIFEST.replace('".claude/settings.json"', '"other.json"')
        found, _ = sync([fc(".claude/settings.json"), fc(STATE)], base={".template/manifest.toml": manifest})
        self.assertEqual(ids(found, "TC-1"), ["path is not owned in the base manifest: .claude/settings.json"])

    def test_overrides_and_template_only_paths_stay_forbidden_with_listing(self):
        found, _ = sync([fc(".template/overrides.toml", "added"), fc(STATE)], section="- .template/overrides.toml")
        self.assertIn(".template/overrides.toml is changed", ids(found, "TC-1"))
        found, _ = sync([fc("template-dev/a.py", "added"), fc(STATE)], section="- template-dev/a.py")
        self.assertTrue(ids(found, "TC-1"))

    def test_findings_are_capped_and_hold_no_pr_text(self):
        files = [fc(f"docs/NEW{i}.md", "added") for i in range(25)] + [fc(STATE)]
        head = HEAD_MANIFEST.replace('"docs/NEW.md", ', "".join(f'"docs/NEW{i}.md", ' for i in range(25)))
        found, _ = sync(files, head={".template/manifest.toml": head})
        messages = ids(found, "TC-1")
        self.assertEqual(len(messages), rules.PATH_CAP + 1)
        self.assertEqual(messages[-1], "and 15 more findings")

    def test_an_unlisted_path_is_sanitized(self):
        path = "docs/NEW\u202e.md"
        head = HEAD_MANIFEST.replace('"docs/NEW.md", ', f'"{path}", ')
        found, _ = sync([fc(path, "added"), fc(STATE)], head={".template/manifest.toml": head})
        self.assertEqual(ids(found, "TC-1"), [UNLISTED + "docs/NEW.md"])

    def test_the_base_commit_is_read_only_for_the_paths_that_are_added_seeded(self):
        _, client = sync(self.NEW, section="- docs/NEW.md")
        self.assertIn((BASE_SHA, "docs/NEW.md"), [c[1] for c in client.calls if c[0] == "file_text"])
        _, client = sync(TC1.OK)
        self.assertNotIn((BASE_SHA, "newdir/x.py"), [c[1] for c in client.calls if c[0] == "file_text"])


INIT_OK = [fc("template-dev/a.py", "removed"), fc(".github/workflows/template-ci.yml", "removed"), fc("README.md"),
           fc("config/project.yaml"), fc(STATE, "added")]
INIT_BASE = {".template/manifest.toml": MANIFEST}


def init(files, base=None, **kw):
    base_files = dict(INIT_BASE)
    base_files.update(base or {})
    base_files = {k: v for k, v in base_files.items() if v is not None}
    return evaluate("init", files, base_files=base_files, **kw)


class TC2(unittest.TestCase):
    def test_a_clean_init_passes_once(self):
        found, _ = init(INIT_OK)
        self.assertEqual(found, [])

    def test_it_fails_when_the_base_has_a_state_file(self):
        found, _ = init(INIT_OK, base={STATE: STATE_OLD})
        self.assertEqual(ids(found, "TC-2"), ["the base branch already has .template/state.toml"])

    def test_a_deleted_path_must_be_template_only(self):
        for path in ("scripts/a.py", "README.md", "docs/x.md"):
            with self.subTest(path=path):
                found, _ = init(INIT_OK + [fc(path, "removed")])
                self.assertEqual(ids(found, "TC-2"), [f"deleted path is not template-only: {path}"])

    def test_a_written_path_must_be_seeded(self):
        for path, status in (("scripts/a.py", "modified"), ("scripts/a.py", "added"), ("template-dev/n.py", "added"),
                             ("template-dev/n.py", "modified"), ("docs/x.md", "added"), (".template/manifest.toml", "modified")):
            with self.subTest(path=path, status=status):
                found, _ = init(INIT_OK + [fc(path, status)])
                self.assertEqual(ids(found, "TC-2"), [f"written path is not seeded: {path}"])

    def test_deleting_a_seeded_file_is_not_allowed(self):
        found, _ = init(INIT_OK + [fc("docs/PLATFORM.md", "removed")])
        self.assertTrue(ids(found, "TC-2"))

    def test_a_rename_is_a_delete_and_a_write(self):
        found, _ = init([fc("README.md", "renamed", previous="template-dev/r.md")])
        self.assertEqual(found, [])
        found, _ = init([fc("scripts/r.md", "renamed", previous="template-dev/r.md")])
        self.assertEqual(ids(found, "TC-2"), ["written path is not seeded: scripts/r.md"])
        found, _ = init([fc("README.md", "renamed", previous="scripts/r.md")])
        self.assertEqual(ids(found, "TC-2"), ["deleted path is not template-only: scripts/r.md"])

    def test_a_status_the_rule_does_not_know_fails(self):
        found, _ = init(INIT_OK + [fc("README.md", "unchanged")])
        self.assertEqual(ids(found, "TC-2"), ["change of unknown kind: README.md"])

    def test_a_missing_or_invalid_base_manifest_fails_closed(self):
        for text in (None, "x [", "schema_version = 2\n"):
            with self.subTest(text=text):
                found, _ = init(INIT_OK, base={".template/manifest.toml": text})
                self.assertEqual(ids(found, "TC-2"), ["the manifest of the base commit is missing or invalid"])

    def test_the_head_manifest_is_not_read_at_all(self):
        _, client = init(INIT_OK)
        self.assertNotIn(HEAD_SHA, [c[1][0] for c in client.calls if c[0] == "file_text"])

    def test_pc6_applies_to_the_removal_of_the_template_workflow(self):
        pr = make_pr("init", body=body("init", omit=("Workflow change justification",)))
        client = MemClient(files={pr.number: INIT_OK}, base_files=dict(INIT_BASE))
        self.assertEqual(sorted({v.rule for v in rules.evaluate(pr, client, helpers.TEMPLATES)}), ["PC-1", "PC-6"])
        pr = make_pr("init", body=body("init", sections={"Workflow change justification": ""}))
        found = rules.evaluate(pr, MemClient(files={pr.number: INIT_OK}, base_files=dict(INIT_BASE)), helpers.TEMPLATES)
        self.assertIn("PC-6", {v.rule for v in found})

    def test_findings_are_capped(self):
        found, _ = init([fc(f"scripts/f{i}.py") for i in range(25)])
        messages = ids(found, "TC-2")
        self.assertEqual(len(messages), rules.PATH_CAP + 1)
        self.assertEqual(messages[-1], "and 15 more paths")

    def test_a_pr_of_another_type_is_not_judged_by_the_tc_rules(self):
        for kind in ("design", "implementation", "change"):
            with self.subTest(kind=kind):
                found, _ = evaluate(kind, [fc("template-dev/a.py", "removed")], base_files=dict(INIT_BASE))
                self.assertEqual([v.rule for v in found if v.rule.startswith("TC")], [])


SETTINGS = ".claude/settings.json"
DENY_LIST = ".claude/deny.platform.txt"
CORE = ["Bash(gh pr merge *)", "Bash(sudo *)", "Read(./.env)"]
NOT_LISTED = "appended deny entry is not listed in .claude/deny.platform.txt: "
CHANGED_EXISTING = "an existing permissions.deny entry is removed, changed, or reordered"
OTHER_CHANGE = ".claude/settings.json changes something other than appended permissions.deny entries"


def settings(deny=None, **top):
    data = {"$schema": "x", "enableAllProjectMcpServers": False,
            "permissions": {"disableBypassPermissionsMode": "disable", "deny": list(CORE if deny is None else deny), "ask": ["Bash(git push *)"],
                            "allow": ["Read"]}}
    data.update(top)
    return json.dumps(data, indent=2) + "\n"


def init_settings(head_settings, listed="Bash(terraform apply *)\nBash(oci *)\n", base_settings=None, files=None, base=None, **kw):
    base_files = {SETTINGS: settings() if base_settings is None else base_settings, DENY_LIST: ""}
    base_files.update(base or {})
    head_files = {SETTINGS: head_settings, DENY_LIST: listed}
    head_files = {k: v for k, v in head_files.items() if v is not None}
    return init(INIT_OK + [fc(SETTINGS)] if files is None else files, base=base_files, head_files=head_files, **kw)


class TC2Settings(unittest.TestCase):
    """Option 1: an init PR may append entries to permissions.deny of the owned settings file, all listed in the head's seeded list,
    and change nothing else in that file."""
    APPENDED = ["Bash(terraform apply *)", "Bash(oci *)"]

    def test_appended_listed_entries_pass(self):
        found, _ = init_settings(settings(deny=CORE + self.APPENDED))
        self.assertEqual(found, [])

    def test_one_appended_entry_and_the_whole_list_pass(self):
        for entries in (self.APPENDED[:1], self.APPENDED[1:], self.APPENDED):
            with self.subTest(entries=entries):
                found, _ = init_settings(settings(deny=CORE + entries))
                self.assertEqual(found, [])

    def test_the_formatting_of_the_file_may_differ(self):
        text = json.dumps(json.loads(settings(deny=CORE + self.APPENDED)), separators=(",", ":"))
        found, _ = init_settings(text)
        self.assertEqual(found, [])

    def test_the_list_file_is_read_line_by_line_with_comments_and_blank_lines_ignored(self):
        listed = "# platform rules\n\n  Bash(terraform apply *)  \r\nBash(oci *)"
        found, _ = init_settings(settings(deny=CORE + self.APPENDED), listed=listed)
        self.assertEqual(found, [])
        found, _ = init_settings(settings(deny=CORE + ["Bash(oci *)"]), listed="# Bash(oci *)\n")
        self.assertEqual(ids(found, "TC-2"), [NOT_LISTED + "Bash(oci *)"])

    def test_the_list_file_is_split_into_lines_as_initialize_project_splits_it(self):
        # `merge_platform_deny` uses str.splitlines(), which also breaks at these characters; each part is an entry it appends
        for separator in ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\r", "\r\n"):
            with self.subTest(separator=ascii(separator)):
                found, _ = init_settings(settings(deny=CORE + self.APPENDED), listed=separator.join(self.APPENDED))
                self.assertEqual(found, [])

    def test_a_comment_ends_at_the_same_line_break_as_in_initialize_project(self):
        found, _ = init_settings(settings(deny=CORE + ["Bash(oci *)"]), listed="# a comment\x0bBash(oci *)\n")
        self.assertEqual(found, [])
        found, _ = init_settings(settings(deny=CORE + ["Bash(oci *)"]), listed="# a comment \u2028 Bash(oci *)\n")
        self.assertEqual(found, [])
        found, _ = init_settings(settings(deny=CORE + ["Bash(oci *)"]), listed="# a comment Bash(oci *)\n")
        self.assertEqual(ids(found, "TC-2"), [NOT_LISTED + "Bash(oci *)"])

    def test_an_appended_entry_that_is_not_listed_fails_and_names_the_entry(self):
        found, _ = init_settings(settings(deny=CORE + self.APPENDED + ["Bash(rm -rf *)"]))
        self.assertEqual(ids(found, "TC-2"), [NOT_LISTED + "Bash(rm -rf *)"])

    def test_an_entry_that_only_resembles_a_listed_one_is_not_listed(self):
        for entry in ("Bash(oci *) ", "bash(oci *)", "Bash(oci  *)", "Bash(oci *", "Bash(oci)"):
            with self.subTest(entry=entry):
                found, _ = init_settings(settings(deny=CORE + [entry]))
                self.assertEqual(len(ids(found, "TC-2")), 1, found)

    def test_no_list_file_at_the_head_means_nothing_is_listed(self):
        found, _ = init_settings(settings(deny=CORE + self.APPENDED), listed=None)
        self.assertEqual(ids(found, "TC-2"), [NOT_LISTED + e for e in self.APPENDED])

    def test_the_list_file_of_the_base_does_not_count(self):
        found, _ = init_settings(settings(deny=CORE + self.APPENDED), listed="", base={DENY_LIST: "\n".join(self.APPENDED)})
        self.assertEqual(len(ids(found, "TC-2")), 2)

    def test_a_removed_changed_or_reordered_existing_entry_fails(self):
        cases = {
            "removed first": CORE[1:], "removed last": CORE[:-1], "removed all": [], "changed": [CORE[0] + "x"] + CORE[1:],
            "reordered": [CORE[1], CORE[0], CORE[2]], "reversed": CORE[::-1], "inserted before": self.APPENDED[:1] + CORE,
            "inserted between": [CORE[0]] + self.APPENDED[:1] + CORE[1:], "removed and appended": CORE[1:] + self.APPENDED[:1],
            "replaced by an appended one": self.APPENDED[:1] + CORE[1:],
        }
        for name, deny in cases.items():
            with self.subTest(name):
                found, _ = init_settings(settings(deny=deny))
                self.assertIn(CHANGED_EXISTING, ids(found, "TC-2"))

    def test_a_duplicate_entry_fails(self):
        for deny in (CORE + [CORE[0]], CORE + [self.APPENDED[0]] * 2, CORE + self.APPENDED + [self.APPENDED[1]]):
            with self.subTest(deny=deny):
                found, _ = init_settings(settings(deny=deny))
                self.assertTrue(any(m.startswith("appended deny entry duplicates ") for m in ids(found, "TC-2")), found)

    def test_a_change_to_anything_else_in_the_file_fails(self):
        def mutated(change):
            data = json.loads(settings())
            change(data)
            data["permissions"]["deny"] = CORE + self.APPENDED  # the appended listed entries are fine; the other change is what fails
            return json.dumps(data, indent=2)

        cases = {
            "top-level value": lambda d: d.update(enableAllProjectMcpServers=True),
            "new top-level key": lambda d: d.update(extra=1),
            "removed top-level key": lambda d: d.pop("$schema"),
            "ask changed": lambda d: d["permissions"].update(ask=[]),
            "ask entry replaced": lambda d: d["permissions"].update(ask=["x"]),
            "allow added": lambda d: d["permissions"].update(allow=["Read", "Bash"]),
            "new key under permissions": lambda d: d["permissions"].update(defaultMode="x"),
            "removed key under permissions": lambda d: d["permissions"].pop("ask"),
            "string to another": lambda d: d["permissions"].update(disableBypassPermissionsMode="x"),
            "boolean to number": lambda d: d.update(enableAllProjectMcpServers=0),
            "nested object added": lambda d: d.update(hooks={"a": []}),
        }
        for name, change in cases.items():
            with self.subTest(name):
                found, _ = init_settings(mutated(change))
                self.assertIn(OTHER_CHANGE, ids(found, "TC-2"))

    def test_a_value_of_another_json_type_is_a_change_even_when_python_calls_it_equal(self):
        base = json.loads(settings())
        for before, after in ((False, 0), (0, False), (1, True), (1, 1.0), (True, 1)):
            with self.subTest(before=before, after=after):
                one, two = json.loads(settings()), json.loads(settings())
                one["n"], two["n"] = before, after
                two["permissions"]["deny"] = CORE + self.APPENDED
                found, _ = init_settings(json.dumps(two), base_settings=json.dumps(one))
                self.assertIn(OTHER_CHANGE, ids(found, "TC-2"))
        self.assertEqual(base["permissions"]["deny"], CORE)

    def test_invalid_json_is_a_violation_never_a_pass(self):
        for name, text in {"empty": "", "truncated": settings()[:-20], "trailing comma": settings().replace('"Read"', '"Read",'),
                           "single quotes": "{'permissions': {}}", "nan": settings().replace('"x"', "NaN"),
                           "not an object": "[]", "null": "null", "string": '"x"',
                           "duplicate key": settings().replace('"ask"', '"deny": [], "ask"', 1)}.items():
            with self.subTest(head=name):
                found, _ = init_settings(text)
                self.assertTrue(ids(found, "TC-2"), found)
        found, _ = init_settings(settings(deny=CORE + self.APPENDED), base_settings="{ not json")
        self.assertTrue(ids(found, "TC-2"))

    def test_permissions_deny_must_be_a_list_of_strings_in_both_versions(self):
        for name, data in {"missing permissions": {"x": 1}, "permissions a list": {"permissions": []}, "no deny": {"permissions": {}},
                           "deny a string": {"permissions": {"deny": "Bash"}}, "deny an object": {"permissions": {"deny": {}}},
                           "an entry that is not a string": {"permissions": {"deny": CORE + [1]}},
                           "null entry": {"permissions": {"deny": CORE + [None]}}}.items():
            with self.subTest(head=name):
                found, _ = init_settings(json.dumps(data))
                self.assertTrue(ids(found, "TC-2"), found)
            with self.subTest(base=name):
                found, _ = init_settings(settings(deny=CORE + self.APPENDED), base_settings=json.dumps(data))
                self.assertTrue(ids(found, "TC-2"), found)

    def test_a_missing_settings_file_on_either_side_fails(self):
        found, _ = init_settings(None)
        self.assertTrue(ids(found, "TC-2"))
        found, _ = init_settings(settings(deny=CORE + self.APPENDED), base={SETTINGS: None})
        self.assertTrue(ids(found, "TC-2"))

    def test_the_exception_is_for_a_modified_settings_file_only(self):
        for item in (fc(SETTINGS, "added"), fc(SETTINGS, "removed"), fc(SETTINGS, "renamed", previous="template-dev/s.json"),
                     fc("scripts/s.json", "renamed", previous=SETTINGS), fc(SETTINGS, "unchanged")):
            with self.subTest(item=item):
                found, _ = init_settings(settings(deny=CORE + self.APPENDED), files=INIT_OK + [item])
                self.assertTrue(ids(found, "TC-2"), found)

    def test_the_exception_is_for_that_one_file_and_needs_the_owned_class_in_the_base_manifest(self):
        for path in ("scripts/settings.json", ".claude/settings.local.json", ".claude/required-deny.txt", "settings.json"):
            with self.subTest(path=path):
                found, _ = init_settings(settings(deny=CORE + self.APPENDED), files=INIT_OK + [fc(path)])
                self.assertEqual(ids(found, "TC-2"), [f"written path is not seeded: {path}"])
        manifest = MANIFEST.replace('".claude/settings.json"', '"other.json"')
        found, _ = init_settings(settings(deny=CORE + self.APPENDED), base={".template/manifest.toml": manifest})
        self.assertEqual(ids(found, "TC-2"), ["written path is not seeded: .claude/settings.json"])

    def test_other_findings_are_still_reported_next_to_a_valid_settings_change(self):
        found, _ = init_settings(settings(deny=CORE + self.APPENDED), files=INIT_OK + [fc(SETTINGS), fc("scripts/a.py")])
        self.assertEqual(ids(found, "TC-2"), ["written path is not seeded: scripts/a.py"])

    def test_every_other_rule_of_tc2_is_unchanged(self):
        found, _ = init_settings(settings(deny=CORE + self.APPENDED), base={STATE: STATE_OLD})
        self.assertEqual(ids(found, "TC-2"), ["the base branch already has .template/state.toml"])

    def test_the_head_is_read_only_for_the_settings_file_and_the_seeded_list(self):
        _, client = init_settings(settings(deny=CORE + self.APPENDED))
        head = sorted(path for sha, path in (c[1] for c in client.calls if c[0] == "file_text") if sha == HEAD_SHA)
        self.assertEqual(head, [DENY_LIST, SETTINGS])
        _, client = init(INIT_OK, base=dict(INIT_BASE))
        self.assertNotIn(HEAD_SHA, [c[1][0] for c in client.calls if c[0] == "file_text"])

    def test_findings_are_capped_sanitized_and_hold_no_more_than_the_entry(self):
        entries = [f"Bash(x{i} *)" for i in range(25)]
        found, _ = init_settings(settings(deny=CORE + entries))
        messages = ids(found, "TC-2")
        self.assertEqual(len(messages), rules.PATH_CAP + 1)
        self.assertEqual(messages[-1], "and 15 more entries")
        found, _ = init_settings(settings(deny=CORE + ["Bash(\u202e::error::" + "x" * 300 + ")"]))
        (message,) = ids(found, "TC-2")
        self.assertNotIn("\u202e", message)
        self.assertNotIn("::", message)
        self.assertLessEqual(len(message), len(NOT_LISTED) + 80)


if __name__ == "__main__":
    unittest.main()
