"""The identity reader of config/project.yaml: strict, and it reads what initialize-project writes."""
import pathlib
import sys
import unittest

import helpers

import template_config
from cr_tools.identity import read_identities
from pr_contract.errors import ConfigError

CONFIGS = helpers.ROOT / "template-dev" / "fixtures" / "config"


class Reader(unittest.TestCase):
    def test_reads_the_test_configuration(self):
        found = read_identities(helpers.CONFIG)
        self.assertEqual((found.approver_login, found.machine_login, found.owner, found.approver_id), ("example-approver", "example-bot", "example-owner", 1001))

    def test_reads_this_repositorys_own_file(self):
        found = read_identities((helpers.ROOT / "config" / "project.yaml").read_text())
        self.assertTrue(found.approver_login and found.machine_login and found.approver_id)

    def test_trusted_authors(self):
        found = read_identities(helpers.CONFIG)
        self.assertTrue(found.is_trusted_author("EXAMPLE-APPROVER"))
        self.assertTrue(found.is_trusted_author("example-bot"))
        self.assertFalse(found.is_trusted_author("example-bot2"))
        self.assertFalse(found.is_trusted_author(None))
        self.assertFalse(found.is_machine("example-approver"))

    def test_the_approver_id_is_optional(self):
        text = helpers.CONFIG.replace("  approver_id: 1001\n", "")
        self.assertIsNone(read_identities(text).approver_id)

    def test_quoted_plain_single_quoted_and_comments(self):
        text = "github:  # who\n  approver_login: plain-one # c\n  machine_login: 'single'\n  approver_id: 7\n"
        found = read_identities(text)
        self.assertEqual((found.approver_login, found.machine_login, found.approver_id), ("plain-one", "single", 7))

    def test_everything_unclear_fails_closed(self):
        good = helpers.CONFIG
        bad = {
            "no block": "schema_version: 1\n",
            "repeated block": good + "github:\n  approver_login: a\n  machine_login: b\n",
            "missing machine": good.replace('  machine_login: "example-bot"\n', ""),
            "missing approver": good.replace('  approver_login: "example-approver"\n', ""),
            "repeated key": good.replace("  approver_id: 1001\n", "  approver_id: 1001\n  approver_id: 1002\n"),
            "same login": good.replace("example-bot", "example-approver"),
            "same login, other case": good.replace("example-bot", "EXAMPLE-APPROVER"),
            "id not an integer": good.replace("1001", '"12ab"'),
            "id with a leading zero": good.replace("1001", "01"),
            "id negative": good.replace("1001", "-5"),
            "login with a space": good.replace("example-bot", "bot x"),
            "login too long": good.replace("example-bot", "a" * 40),
            "login starting with a hyphen": good.replace("example-bot", "-bot"),
            "flow style": good.replace('  machine_login: "example-bot"', "  machine_login: {a: b}"),
            "tab": good.replace('  machine_login: "example-bot"', '\tmachine_login: "example-bot"'),
            "nul": good + "\x00",
            "too large": good + "# " + "x" * 70000 + "\n",
            "not text": None,
        }
        for name, text in bad.items():
            with self.assertRaises(ConfigError, msg=name):
                read_identities(text)

    def test_a_key_after_the_block_is_not_part_of_it(self):
        text = helpers.CONFIG + 'other:\n  machine_login: "x"\n'
        self.assertEqual(read_identities(text).machine_login, "example-bot")


class RoundTrip(unittest.TestCase):
    """A configuration that the schema accepts is read back by the reader (the lesson of the earlier rows)."""

    def test_every_fixture_configuration_round_trips(self):
        fields = template_config.load_schema(helpers.ROOT / ".template" / "config.schema.toml")
        found = 0
        for path in sorted(CONFIGS.rglob("config.toml")) + sorted(CONFIGS.glob("*.toml")):
            config, _ = template_config.load_config(path, fields)
            identities = read_identities(template_config.project_yaml(config))
            for key in ("approver_login", "machine_login", "approver_id", "owner"):
                self.assertEqual(getattr(identities, key), config["github"][key], (path.name, key))
            found += 1
        self.assertGreaterEqual(found, 2)

    def test_the_longest_and_the_oddest_logins_the_schema_accepts_round_trip(self):
        fields = template_config.load_schema(helpers.ROOT / ".template" / "config.schema.toml")
        raw = (CONFIGS / "neutral.toml").read_text()
        import tomllib
        data = tomllib.loads(raw)
        for approver, machine, ident in (("a" * 39, "b-" + "c" * 37, 999999999999999), ("1234", "9-x", 1), ("A-b-C", "z", 25507517)):
            data["github"].update(approver_login=approver, machine_login=machine, approver_id=ident)
            config, errors = template_config.validate(data, fields)
            self.assertEqual(errors, [])
            found = read_identities(template_config.project_yaml(config))
            self.assertEqual((found.approver_login, found.machine_login, found.approver_id), (approver, machine, ident))


if __name__ == "__main__":
    unittest.main()
