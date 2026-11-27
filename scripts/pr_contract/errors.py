"""Exception types. Messages are fixed text: they never contain PR text or the token."""


class InputError(Exception):
    """Bad command line or local input (exit 2)."""


class ApiError(Exception):
    """The API failed, was rate limited, or returned something malformed (exit 3)."""


class ReadError(Exception):
    """A read of a resource that was asked for by name failed in a way that says something about the resource (not a transient
    error): it is missing, or the read was refused. The two kinds are siblings, so a handler that means "absent" (`except
    NotFound`) can never catch a refused read."""


class NotFound(ReadError):
    """The API answered 404: the resource is missing."""


class NotReadable(ReadError):
    """The API refused the read (401 or 403): the resource may exist. It never means "absent"."""


class LimitExceeded(Exception):
    """A hard cap was reached (changed files); the check fails closed."""


class ConfigError(Exception):
    """The configuration of the base commit cannot be read as one YAML mapping, or a key has the wrong shape."""


class NearMissKey(ConfigError):
    """A key of the configuration looks like a policy key but is not one (a likely misspelling). `key` is the key as written,
    of letters, digits, `.`, `_`, `-` only."""

    def __init__(self, key):
        super().__init__("a key looks like a misspelling of a policy key")
        self.key = key
