"""Exception types. Messages are fixed text: they never contain PR text or the token."""


class InputError(Exception):
    """Bad command line or local input (exit 2)."""


class ApiError(Exception):
    """The API failed, was rate limited, or returned something malformed (exit 3)."""


class NotFound(Exception):
    """The API answered 404 (or refused access) for a resource that was asked for: it is missing or not readable."""


class LimitExceeded(Exception):
    """A hard cap was reached (changed files); the check fails closed."""


class ConfigError(Exception):
    """The configuration of the base commit cannot be read as one YAML mapping, or a key has the wrong shape."""
