"""Exception types. Messages are fixed text and never contain file contents or URLs' query data."""


class UsageError(Exception):
    """Bad command line or an invalid manifest (exit 2)."""


class ManifestError(Exception):
    """The manifest could not be read or parsed."""


class FetchError(Exception):
    """A download failed (network, HTTP status, scheme, or size)."""


class ArchiveError(Exception):
    """An archive was rejected by the extraction rules."""


class InstallError(Exception):
    """An install step failed; nothing from this tool is left installed."""
