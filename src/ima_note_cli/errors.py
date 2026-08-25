from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
import re
from typing import Any


class ExitCode(IntEnum):
    INPUT = 2
    CONFIG = 3
    TRANSPORT = 4
    BUSINESS = 5
    PROTOCOL = 6
    LOCAL_IO = 7
    UPLOAD = 8
    PARTIAL = 9
    INTERNAL = 70
    TEMPORARY = 75
    INTERRUPTED = 130


TEMPORARY_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_SPACE_RE = re.compile(r"\s+")
_DETAIL_KEYS = frozenset({"attempts", "http_status", "limit", "field", "max_bytes"})
_CANDIDATE_KEYS = ("id", "name", "kb_id", "parent_folder_id")
_MAX_CANDIDATES = 50


def safe_message(value: object, *, fallback: str = "The operation failed.") -> str:
    text = _SPACE_RE.sub(" ", _CONTROL_RE.sub(" ", str(value))).strip()
    return (text or fallback)[:512]


class ImaCliError(Exception):
    default_code = "ima_cli_error"
    default_exit_code = ExitCode.INTERNAL
    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        exit_code: int | None = None,
        retryable: bool | None = None,
        endpoint: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.message = safe_message(message)
        self.retryable = self.default_retryable if retryable is None else bool(retryable)
        resolved_exit_code = exit_code if exit_code is not None else (
            ExitCode.TEMPORARY if self.retryable else self.default_exit_code
        )
        self.exit_code = int(resolved_exit_code)
        self.endpoint = safe_message(endpoint, fallback="") if endpoint else None
        self.details = {key: value for key, value in (details or {}).items() if key in _DETAIL_KEYS}
        super().__init__(self.message)

    def to_error_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "exit_code": self.exit_code,
            "retryable": self.retryable,
        }
        if self.endpoint:
            payload["endpoint"] = self.endpoint
        if self.details:
            payload["details"] = dict(self.details)
        return payload

    def human_detail_lines(self) -> tuple[str, ...]:
        return ()


class InputError(ImaCliError, ValueError):
    default_code = "invalid_input"
    default_exit_code = ExitCode.INPUT


class ReferenceError(InputError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        resource_type: str,
        reference: str,
        candidates: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
        scope: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.resource_type = safe_message(resource_type, fallback="resource")[:64]
        self.reference = safe_message(reference, fallback="")[:256]
        self.scope = {
            safe_message(key, fallback="")[:64]: safe_message(value, fallback="")[:256]
            for key, value in (scope or {}).items()
            if safe_message(key, fallback="")
        }
        self.candidate_count = len(candidates)
        self.candidates = tuple(self._safe_candidate(value) for value in candidates[:_MAX_CANDIDATES])

    @staticmethod
    def _safe_candidate(value: Mapping[str, Any]) -> dict[str, str]:
        return {
            key: safe_message(value[key], fallback="")[:256]
            for key in _CANDIDATE_KEYS
            if key in value and safe_message(value[key], fallback="")
        }

    def to_error_dict(self) -> dict[str, Any]:
        payload = super().to_error_dict()
        payload.update({
            "resource_type": self.resource_type,
            "reference": self.reference,
            "candidate_count": self.candidate_count,
            "candidates": [dict(value) for value in self.candidates],
        })
        if self.scope:
            payload["scope"] = dict(self.scope)
        if self.candidate_count > len(self.candidates):
            payload["candidates_truncated"] = True
        return payload

    def human_detail_lines(self) -> tuple[str, ...]:
        if not self.candidates:
            return ()
        lines = ["Candidates:"]
        for candidate in self.candidates:
            identity = candidate.get("id", "(unknown ID)")
            name = candidate.get("name", "")
            scope = candidate.get("parent_folder_id") or candidate.get("kb_id") or ""
            suffix = f" — {name}" if name else ""
            if scope:
                suffix += f" (scope: {scope})"
            lines.append(f"  {identity}{suffix}")
        if self.candidate_count > len(self.candidates):
            lines.append(f"  … {self.candidate_count - len(self.candidates)} more candidates")
        return tuple(lines)


class ConfigError(ImaCliError):
    default_code = "configuration_error"
    default_exit_code = ExitCode.CONFIG


class ApiError(ImaCliError):
    """Compatibility error: direct construction means an IMA business failure."""

    default_code = "api_error"
    default_exit_code = ExitCode.BUSINESS


class ApiTransportError(ApiError):
    default_code = "api_transport_error"
    default_exit_code = ExitCode.TRANSPORT
    default_retryable = True


class ApiBusinessError(ApiError):
    default_code = "api_business_error"
    default_exit_code = ExitCode.BUSINESS


class AuthenticationError(ApiBusinessError):
    default_code = "authentication_rejected"


class ApiProtocolError(ApiError):
    default_code = "api_protocol_error"
    default_exit_code = ExitCode.PROTOCOL


class MediaUnavailableError(ImaCliError):
    default_code = "media_unavailable"
    default_exit_code = ExitCode.LOCAL_IO


class LocalIOError(ImaCliError):
    default_code = "local_io_error"
    default_exit_code = ExitCode.LOCAL_IO


class KnowledgeUploadError(ImaCliError):
    default_code = "knowledge_upload_error"
    default_exit_code = ExitCode.UPLOAD


class RemoteFetchError(ImaCliError):
    default_code = "remote_fetch_error"
    default_exit_code = ExitCode.TRANSPORT
