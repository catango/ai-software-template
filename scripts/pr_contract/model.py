"""Plain data objects built from API responses, with strict validation (malformed means error)."""
from dataclasses import dataclass
from typing import Optional

from .errors import ApiError


def _need(data, key, kind):
    value = data.get(key)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ApiError("malformed response")
    return value


def _nested(data, key, inner, kind):
    obj = _need(data, key, dict)
    return _need(obj, inner, kind)


def _optional(data, key, inner):
    """A string inside an object, or "" when it is absent (older recorded fixtures); a wrong type is malformed."""
    value = _need(data, key, dict).get(inner, "")
    if not isinstance(value, str):
        raise ApiError("malformed response")
    return value


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    body: str
    state: str
    draft: bool
    merged: bool
    author: str
    head_ref: str
    base_ref: str
    changed_files: int = 0
    head_sha: str = ""
    base_sha: str = ""

    @classmethod
    def from_api(cls, data):
        if not isinstance(data, dict):
            raise ApiError("malformed response")
        body = data.get("body")
        if body is not None and not isinstance(body, str):
            raise ApiError("malformed response")
        changed = data.get("changed_files", 0)
        if not isinstance(changed, int) or isinstance(changed, bool):
            raise ApiError("malformed response")
        return cls(
            number=_need(data, "number", int),
            title=_need(data, "title", str),
            body=body or "",
            state=_need(data, "state", str),
            draft=_need(data, "draft", bool),
            merged=_need(data, "merged", bool),
            author=_nested(data, "user", "login", str),
            head_ref=_nested(data, "head", "ref", str),
            base_ref=_nested(data, "base", "ref", str),
            changed_files=changed,
            head_sha=_optional(data, "head", "sha"),
            base_sha=_optional(data, "base", "sha"),
        )


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    previous: Optional[str] = None
    patch: Optional[str] = None

    @classmethod
    def from_api(cls, data):
        if not isinstance(data, dict):
            raise ApiError("malformed response")
        previous = data.get("previous_filename")
        patch = data.get("patch")
        if (previous is not None and not isinstance(previous, str)) or (patch is not None and not isinstance(patch, str)):
            raise ApiError("malformed response")
        return cls(path=_need(data, "filename", str), status=_need(data, "status", str), previous=previous, patch=patch)
