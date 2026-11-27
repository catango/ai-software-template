"""The identities of config/project.yaml: a strict reader of the `github:` block.

    github:
      owner: "example-owner"            # optional here, not used by these scripts
      approver_login: "example-approver"
      approver_id: 12345                # optional here (reviews are matched by it from the review-threads script on)
      machine_login: "example-bot"

Values are plain, "double" or 'single' quoted logins (letters, digits, hyphens, at most 39, the pattern of the configuration
schema) and a positive integer for the ID. A repeated block or key, a login or ID outside those forms, and a missing
`approver_login` or `machine_login` raise ConfigError, so a script that judges identity fails closed. Nothing is inferred.
Callers pass the text of the file as it is on the BASE commit (or the default branch) of what they judge, never the head.
"""
import re
from dataclasses import dataclass
from typing import Optional

from pr_contract.errors import ConfigError

MAX_TEXT = 65536
_LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_NUMBER = r"[1-9][0-9]" + r"{0,17}"
_VALUE = re.compile(r"(?P<q>[\"']?)(?P<v>" + _LOGIN + "|" + _NUMBER + r")(?P=q)(?: +#.*)?")
_BLOCK = re.compile(r"github:(?: +#.*)?")
_KEY = re.compile(r"(?P<indent> +)(?P<key>[a-z_]+):(?: +(?P<rest>.*))?")
_LOGIN_ONLY = re.compile(_LOGIN)
KEYS = ("owner", "approver_login", "approver_id", "machine_login")


@dataclass(frozen=True)
class Identities:
    approver_login: str
    machine_login: str
    owner: Optional[str] = None
    approver_id: Optional[int] = None

    def is_trusted_author(self, login):
        """True for the approver or the machine account (logins compare without case, as GitHub does)."""
        return isinstance(login, str) and login.lower() in (self.approver_login.lower(), self.machine_login.lower())

    def is_machine(self, login):
        return isinstance(login, str) and login.lower() == self.machine_login.lower()


def read_identities(text):
    if not isinstance(text, str) or len(text.encode("utf-8", "replace")) > MAX_TEXT or "\x00" in text:
        raise ConfigError("configuration unreadable")
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    start = None
    for number, line in enumerate(lines):
        if _BLOCK.fullmatch(line):
            if start is not None:
                raise ConfigError("the github block is repeated")
            start = number
    if start is None:
        raise ConfigError("the github block is missing")
    found = {}
    for line in lines[start + 1:]:
        if line.strip(" ") == "" or line.lstrip(" ").startswith("#"):
            continue
        if line[0] != " ":
            break
        match = _KEY.fullmatch(line)
        if match is None or "\t" in line:
            raise ConfigError("the github block holds a line that is not a plain key")
        key, rest = match.group("key"), match.group("rest")
        if key not in KEYS:
            continue
        if key in found:
            raise ConfigError("a key of the github block is repeated")
        value = _VALUE.fullmatch(rest or "")
        if value is None:
            raise ConfigError("a key of the github block has a value that is not a login or an ID")
        found[key] = value.group("v")
    for key in ("approver_login", "machine_login"):
        if key not in found or not _LOGIN_ONLY.fullmatch(found[key]):
            raise ConfigError("the github block lacks a login")
    if "approver_id" in found and not re.fullmatch(r"[1-9][0-9]{0,17}", found["approver_id"]):
        raise ConfigError("approver_id is not an integer")
    if found["approver_login"].lower() == found["machine_login"].lower():
        raise ConfigError("the approver and the machine account are the same login")
    return Identities(
        approver_login=found["approver_login"],
        machine_login=found["machine_login"],
        owner=found.get("owner"),
        approver_id=int(found["approver_id"]) if "approver_id" in found else None,
    )
