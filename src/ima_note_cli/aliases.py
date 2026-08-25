from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .errors import ConfigError, InputError, ReferenceError


RESOURCE_TYPES = ("kb", "note", "note-folder", "kb-folder", "media")
SCOPED_RESOURCE_TYPES = frozenset({"kb-folder", "media"})
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ACCOUNT_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_FILE_BYTES = 1024 * 1024
_SCHEMA_VERSION = 1


def account_fingerprint(client_id: str) -> str:
    value = client_id.strip() if isinstance(client_id, str) else ""
    if not value:
        raise InputError("Cannot derive an alias account from an empty client ID.")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise InputError("The configured client ID is not valid UTF-8 text.") from exc
    return hashlib.sha256(encoded).hexdigest()


def parse_alias_key(value: str) -> tuple[str, str]:
    key = value.strip() if isinstance(value, str) else ""
    if "." not in key:
        raise InputError(
            "Alias key must use RESOURCE.ALIAS syntax, for example kb.research.",
            code="invalid_alias_key",
        )
    resource_type, alias = key.split(".", 1)
    validate_resource_type(resource_type)
    validate_alias_name(alias)
    return resource_type, alias


def validate_resource_type(value: str) -> str:
    if value not in RESOURCE_TYPES:
        raise InputError(
            f"Resource type must be one of: {', '.join(RESOURCE_TYPES)}.",
            code="invalid_resource_type",
        )
    return value


def validate_alias_name(value: str) -> str:
    if not isinstance(value, str) or not _ALIAS_RE.fullmatch(value):
        raise InputError(
            "Alias names must be 1-64 ASCII letters, digits, dots, underscores, or hyphens and start with a letter or digit.",
            code="invalid_alias_name",
        )
    return value


@dataclass(frozen=True)
class AliasRecord:
    resource_type: str
    alias: str
    target_id: str
    display_name: str = ""
    scope: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_type": self.resource_type,
            "alias": self.alias,
            "id": self.target_id,
            "name": self.display_name,
            "scope": dict(self.scope),
        }


class AliasStore:
    def __init__(self, path: Path | None, account: str) -> None:
        if not isinstance(account, str) or not _ACCOUNT_RE.fullmatch(account):
            raise InputError("Alias account fingerprint is invalid.")
        self.path = path
        self.account = account

    @classmethod
    def for_client_id(cls, client_id: str, *, config_dir: Path | None = None) -> "AliasStore":
        if config_dir is None:
            try:
                config_dir = Path.home() / ".config" / "ima"
            except RuntimeError:
                config_dir = None
        return cls(config_dir / "aliases.json" if config_dir else None, account_fingerprint(client_id))

    def list(self, resource_type: str | None = None) -> list[AliasRecord]:
        if resource_type is not None:
            validate_resource_type(resource_type)
        document = self._load()
        account_values = document["accounts"].get(self.account, {})
        result: list[AliasRecord] = []
        for current_type in RESOURCE_TYPES:
            if resource_type is not None and current_type != resource_type:
                continue
            values = account_values.get(current_type, {})
            for alias in sorted(values):
                result.append(self._record(current_type, alias, values[alias]))
        return result

    def get(
        self,
        resource_type: str,
        alias: str,
        *,
        expected_scope: dict[str, str] | None = None,
    ) -> AliasRecord:
        validate_resource_type(resource_type)
        validate_alias_name(alias)
        document = self._load()
        raw = document["accounts"].get(self.account, {}).get(resource_type, {}).get(alias)
        if raw is None:
            for account, values in document["accounts"].items():
                if account != self.account and alias in values.get(resource_type, {}):
                    raise ReferenceError(
                        f"Alias {resource_type}.{alias} belongs to a different configured account.",
                        code="alias_account_mismatch",
                        resource_type=resource_type,
                        reference=f"alias:{alias}",
                    )
            raise ReferenceError(
                f"Alias {resource_type}.{alias} was not found for the configured account.",
                code="alias_not_found",
                resource_type=resource_type,
                reference=f"alias:{alias}",
            )
        record = self._record(resource_type, alias, raw)
        if expected_scope:
            for key, expected in expected_scope.items():
                if record.scope.get(key) != expected:
                    raise ReferenceError(
                        f"Alias {resource_type}.{alias} does not belong to the requested scope.",
                        code="alias_scope_mismatch",
                        resource_type=resource_type,
                        reference=f"alias:{alias}",
                        scope=expected_scope,
                    )
        return record

    def set(self, record: AliasRecord, *, force: bool = False) -> None:
        validate_resource_type(record.resource_type)
        validate_alias_name(record.alias)
        if not isinstance(record.target_id, str) or not record.target_id.strip():
            raise InputError("Alias target ID cannot be empty.")
        if not isinstance(record.display_name, str) or not isinstance(record.scope, dict):
            raise InputError("Alias values must be strings and scope must be an object.")
        if record.resource_type in SCOPED_RESOURCE_TYPES and not record.scope.get("kb_id"):
            raise InputError(f"{record.resource_type} aliases require a knowledge-base scope.")
        if record.resource_type not in SCOPED_RESOURCE_TYPES and record.scope:
            raise InputError(f"{record.resource_type} aliases do not accept scope data.")
        if set(record.scope) - {"kb_id"} or any(not isinstance(value, str) or not value.strip() for value in record.scope.values()):
            raise InputError("Alias scope is invalid.")
        try:
            record.target_id.encode("utf-8", errors="strict")
            record.display_name.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise InputError("Alias values must be valid UTF-8 text.") from exc
        document = self._load()
        account_values = document["accounts"].setdefault(self.account, {})
        type_values = account_values.setdefault(record.resource_type, {})
        if record.alias in type_values and not force:
            raise InputError(
                f"Alias {record.resource_type}.{record.alias} already exists; use --force to replace it.",
                code="alias_exists",
            )
        type_values[record.alias] = {
            "id": record.target_id.strip(),
            "name": record.display_name,
            "scope": {key: value.strip() for key, value in record.scope.items()},
        }
        self._write(document)

    def unset(self, resource_type: str, alias: str) -> AliasRecord:
        record = self.get(resource_type, alias)
        document = self._load()
        account_values = document["accounts"][self.account]
        del account_values[resource_type][alias]
        if not account_values[resource_type]:
            del account_values[resource_type]
        if not account_values:
            del document["accounts"][self.account]
        self._write(document)
        return record

    def _load(self) -> dict[str, Any]:
        if self.path is None:
            raise ConfigError("The user alias configuration directory is unavailable.", code="alias_config_unavailable")
        if not self.path.exists():
            return {"schema_version": _SCHEMA_VERSION, "accounts": {}}
        try:
            if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > _MAX_FILE_BYTES:
                raise ConfigError("Alias configuration is not a bounded regular file.", code="alias_config_invalid")
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except ConfigError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError("Alias configuration is not valid UTF-8 JSON.", code="alias_config_invalid") from exc
        schema_version = value.get("schema_version") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or isinstance(schema_version, bool)
            or schema_version != _SCHEMA_VERSION
            or not isinstance(value.get("accounts"), dict)
        ):
            raise ConfigError("Alias configuration has an unsupported schema.", code="alias_config_invalid")
        for account, resources in value["accounts"].items():
            if not isinstance(account, str) or not _ACCOUNT_RE.fullmatch(account) or not isinstance(resources, dict):
                raise ConfigError("Alias configuration contains an invalid account.", code="alias_config_invalid")
            for resource_type, aliases in resources.items():
                if resource_type not in RESOURCE_TYPES or not isinstance(aliases, dict):
                    raise ConfigError("Alias configuration contains an invalid resource group.", code="alias_config_invalid")
                for alias, raw in aliases.items():
                    try:
                        validate_alias_name(alias)
                    except InputError as exc:
                        raise ConfigError("Alias configuration contains an invalid alias name.", code="alias_config_invalid") from exc
                    self._record(resource_type, alias, raw)
        return value

    def _record(self, resource_type: str, alias: str, raw: Any) -> AliasRecord:
        if not isinstance(raw, dict):
            raise ConfigError("Alias configuration contains an invalid record.", code="alias_config_invalid")
        target_id, name, scope = raw.get("id"), raw.get("name", ""), raw.get("scope", {})
        if not isinstance(target_id, str) or not target_id.strip() or not isinstance(name, str) or not isinstance(scope, dict):
            raise ConfigError("Alias configuration contains an invalid record.", code="alias_config_invalid")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in scope.items()):
            raise ConfigError("Alias configuration contains an invalid scope.", code="alias_config_invalid")
        if set(scope) - {"kb_id"} or any(not value.strip() for value in scope.values()):
            raise ConfigError("Alias configuration contains an invalid scope.", code="alias_config_invalid")
        if resource_type in SCOPED_RESOURCE_TYPES and not scope.get("kb_id"):
            raise ConfigError("A scoped alias is missing its knowledge-base ID.", code="alias_config_invalid")
        if resource_type not in SCOPED_RESOURCE_TYPES and scope:
            raise ConfigError("An unscoped alias contains unexpected scope data.", code="alias_config_invalid")
        try:
            target_id.encode("utf-8", errors="strict")
            name.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ConfigError("Alias configuration contains invalid Unicode.", code="alias_config_invalid") from exc
        return AliasRecord(resource_type, alias, target_id.strip(), name, {key: value.strip() for key, value in scope.items()})

    def _write(self, document: dict[str, Any]) -> None:
        if self.path is None:
            raise ConfigError("The user alias configuration directory is unavailable.", code="alias_config_unavailable")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.parent.is_dir():
                raise OSError("configuration parent is not a directory")
            fd, temporary_name = tempfile.mkstemp(prefix="aliases-", suffix=".tmp", dir=self.path.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(document, stream, ensure_ascii=True, indent=2, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            raise ConfigError("Alias configuration could not be written atomically.", code="alias_config_write_failed") from exc
