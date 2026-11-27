"""Rule PC-3 with qualified references (T-8): `<owner>/<repo>#<n>` only for a repository listed under `design_repos` of the base
configuration; the design PR exists, is merged, and is linked to the same issue; a listed repository that cannot be read fails."""
import unittest

import helpers
from helpers import DESIGN_REPO, MemClient, body, fc, make_pr
from pr_contract import rules
from pr_contract.errors import ApiError

TOP = f"Implements: {DESIGN_REPO}#39\nDesign: {DESIGN_REPO}#40\n"


def design_pr(change_request="CR: #39", **over):
    fields = dict(number=40, head_ref="design/CR-39-the-design", merged=True, state="closed", draft=False,
                  body=body("design", sections={"Change request": change_request}))
    fields.update(over)
    return make_pr("design", **fields)


def foreign(**over):
    return {DESIGN_REPO: {"issues": {39: "issue"}, "prs": {40: design_pr(**over)}}}


def judge(top=TOP, client=None, branch="impl/CR-39-the-work", **kw):
    pr = make_pr(head_ref=branch, body=body("implementation", top=top))
    client = client or MemClient(foreign=foreign())
    client.files[pr.number] = [fc("scripts/x")]
    return rules.evaluate(pr, client, helpers.TEMPLATES), client


def pc3(violations):
    return [v.message for v in violations if v.rule == "PC-3"]


class Qualified(unittest.TestCase):
    def test_a_listed_readable_repository_with_a_merged_linked_design_passes(self):
        found, client = judge()
        self.assertEqual(found, [])
        self.assertIn(("repo_readable", DESIGN_REPO), client.calls)

    def test_a_reference_qualified_to_this_repository_is_a_local_one(self):
        found, client = judge(f"Implements: {helpers.OWN_REPO}#2\nDesign: {helpers.OWN_REPO}#3\n", MemClient(), branch="impl/CR-2-x")
        self.assertEqual(found, [])
        self.assertNotIn("repo_readable", [c[0] for c in client.calls])

    def test_a_repository_that_is_not_listed_fails(self):
        found, _ = judge(client=MemClient(config=helpers.base_config(repos=[]), foreign=foreign()))
        self.assertEqual(pc3(found), [f"repository {DESIGN_REPO} is not listed in design_repos"])
        found, _ = judge(client=MemClient(config=helpers.base_config(repos=["other-owner/x"]), foreign=foreign()))
        self.assertEqual(len(pc3(found)), 1)

    def test_the_listing_ignores_case(self):
        found, _ = judge(client=MemClient(config=helpers.base_config(repos=[DESIGN_REPO.upper()]), foreign=foreign()))
        self.assertEqual(found, [])

    def test_an_unreadable_repository_fails_closed_and_nothing_else_is_asked(self):
        client = MemClient(foreign={})
        found, _ = judge(client=client)
        self.assertEqual(pc3(found), [f"repository {DESIGN_REPO} cannot be read"])
        self.assertEqual([c for c in client.calls if c[0] in ("pull", "issue_kind")], [])

    def test_a_transient_failure_is_an_error_not_a_pass(self):
        with self.assertRaises(ApiError):
            judge(client=MemClient(foreign=foreign(), fail=ApiError("HTTP 502")))

    def test_the_configuration_decides_and_a_broken_one_fails_closed(self):
        for config in (None, {}, {"architectures": []}, {"workflow": {"operator_paths": []}},
                       {"workflow": {"design_repos": "x"}}, {"workflow": {"design_repos": ["not a repo"]}}):
            with self.subTest(config=config):
                found, _ = judge(client=MemClient(config=config, foreign=foreign()))
                self.assertEqual(pc3(found), ["design_repos missing or invalid in the base configuration"])

    def test_a_local_reference_needs_no_configuration(self):
        found, _ = judge(helpers.IMPL_TOP, MemClient(config=helpers.base_config(), foreign={}), branch="impl/CR-2-x")
        self.assertEqual(pc3(found), [])

    def test_the_issue_and_the_design_must_be_in_the_same_repository(self):
        for top in (f"Implements: #2\nDesign: {DESIGN_REPO}#40\n", f"Implements: {DESIGN_REPO}#39\nDesign: #3\n"):
            with self.subTest(top=top):
                found, _ = judge(top)
                self.assertEqual(pc3(found), ["Implements and Design are in different repositories"])

    def test_the_design_pr_must_be_merged(self):
        found, _ = judge(client=MemClient(foreign=foreign(merged=False, state="open")))
        self.assertEqual(pc3(found), [f"design PR {DESIGN_REPO}#40 is not merged"])

    def test_the_design_pr_must_be_found(self):
        found, _ = judge(client=MemClient(foreign={DESIGN_REPO: {"issues": {39: "issue"}, "prs": {}}}))
        self.assertEqual(pc3(found), [f"design PR {DESIGN_REPO}#40 not found"])

    def test_the_issue_must_be_an_issue_and_exist(self):
        found, _ = judge(client=MemClient(foreign={DESIGN_REPO: {"issues": {39: "pull_request"}, "prs": {40: design_pr()}}}))
        self.assertEqual(pc3(found), [f"{DESIGN_REPO}#39 is a pull request, not an issue"])
        found, _ = judge(client=MemClient(foreign={DESIGN_REPO: {"issues": {}, "prs": {40: design_pr()}}}))
        self.assertEqual(pc3(found), [f"issue {DESIGN_REPO}#39 not found"])

    def test_the_design_pr_must_be_a_design_branch_of_the_same_issue(self):
        for head in ("impl/CR-39-x", "design/CR-9-x", "change/x"):
            with self.subTest(head=head):
                found, _ = judge(client=MemClient(foreign=foreign(head_ref=head)))
                self.assertEqual(pc3(found), [f"design PR {DESIGN_REPO}#40 is not a design branch for CR 39"])

    def test_the_design_pr_must_link_the_issue_in_its_own_repository(self):
        ok = ("CR: #39", f"CR: {DESIGN_REPO}#39")
        for text in ok:
            with self.subTest(text=text):
                self.assertEqual(judge(client=MemClient(foreign=foreign(change_request=text)))[0], [])
        for text in ("CR: #38", "none", f"CR: other-owner/x#39", "CR: #390"):
            with self.subTest(text=text):
                found, _ = judge(client=MemClient(foreign=foreign(change_request=text)))
                self.assertEqual(pc3(found), [f"design PR {DESIGN_REPO}#40 does not reference issue 39 in its Change request section"])

    def test_a_mention_outside_the_change_request_section_does_not_link(self):
        pr = design_pr(change_request="none", body=body("design", sections={"Change request": "none", "Summary": "mentions #39 here"}))
        found, _ = judge(client=MemClient(foreign={DESIGN_REPO: {"issues": {39: "issue"}, "prs": {40: pr}}}))
        self.assertEqual(len(pc3(found)), 1)

    def test_the_base_branch_is_compared_for_this_repository_only(self):
        found, _ = judge(client=MemClient(foreign=foreign(base_ref="trunk")))
        self.assertEqual(found, [])

    def test_the_branch_number_is_the_issue_number_of_the_other_repository_too(self):
        found, _ = judge(branch="impl/CR-5-x")
        self.assertIn("PC-2", {v.rule for v in found})

    def test_only_the_base_configuration_is_read_for_the_list(self):
        found, client = judge()
        self.assertEqual([c for c in client.calls if c[0] == "base_config"], [("base_config", helpers.BASE_SHA)])
        self.assertNotIn(helpers.HEAD_SHA, [c[1] for c in client.calls if c[0] == "base_config"])
        # a head that lists the repository, in the file list, changes nothing: the base list is empty
        pr = make_pr(head_ref="impl/CR-39-the-work", body=body("implementation", top=TOP))
        client = MemClient(config=helpers.base_config(repos=[]), foreign=foreign(), files={10: [fc("scripts/x"), fc("config/project.yaml")]})
        self.assertEqual(len(pc3(rules.evaluate(pr, client, helpers.TEMPLATES))), 1)

    def test_a_design_reference_to_this_pr_fails_for_a_local_one_only(self):
        found, _ = judge("Implements: #2\nDesign: #10\n", MemClient(prs={10: make_pr(number=10)}), branch="impl/CR-2-x")
        self.assertIn("Design reference points to this PR", pc3(found))

    def test_messages_hold_no_pr_text(self):
        canary = "CANARY-5f3a"
        found, _ = judge(client=MemClient(foreign=foreign(change_request=canary)))
        self.assertNotIn(canary, " ".join(pc3(found)))


if __name__ == "__main__":
    unittest.main()
