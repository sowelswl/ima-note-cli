from __future__ import annotations

from typing import Any

from ..aliases import AliasRecord, AliasStore, SCOPED_RESOURCE_TYPES, parse_alias_key
from ..command_result import CommandResult, CommandStatus
from ..errors import InputError, safe_message
from ..references import ResourceResolver


def execute_resolve(args: Any, resolver: ResourceResolver) -> CommandResult:
    if _has_kb_scope(args) and args.resource_type not in SCOPED_RESOURCE_TYPES:
        raise InputError("--kb and --kb-id only scope kb-folder or media resolution.", code="invalid_reference_scope")
    kb_id = _resolve_optional_kb(args, resolver)
    result = resolver.resolve(
        args.resource_type,
        args.reference,
        bare_is_name=True,
        kb_id=kb_id,
    )
    payload = {"resource": result.to_dict()}
    label = safe_message(result.name, fallback="(name unavailable)")
    identity = safe_message(result.target_id, fallback="(invalid ID)")
    return CommandResult(payload, (f"{result.resource_type}: {label}", f"id: {identity}"))


def execute_alias(args: Any, resolver: ResourceResolver, store: AliasStore) -> CommandResult:
    action = args.alias_action
    if action == "list":
        records = store.list(args.resource_type)
        payload = {"aliases": [record.to_dict() for record in records]}
        lines = tuple(safe_message(f"{record.resource_type}.{record.alias}: {record.target_id}") for record in records)
        return CommandResult(payload, lines or ("No aliases configured.",), status=CommandStatus.SUCCESS if records else CommandStatus.EMPTY)

    resource_type, alias = parse_alias_key(args.alias_key)
    if action == "unset":
        record = store.unset(resource_type, alias)
        return CommandResult(
            {"alias": record.to_dict()},
            (safe_message(f"Removed alias: {resource_type}.{alias}"),),
        )
    if action != "set":
        raise ValueError("unknown alias command")

    if _has_kb_scope(args) and resource_type not in SCOPED_RESOURCE_TYPES:
        raise InputError("--kb and --kb-id are only valid for kb-folder or media aliases.", code="invalid_reference_scope")
    kb_id = _resolve_optional_kb(args, resolver)
    if resource_type in SCOPED_RESOURCE_TYPES and not kb_id:
        raise InputError(f"{resource_type} aliases require --kb or --kb-id.", code="reference_scope_required")
    target = resolver.resolve(resource_type, args.target, kb_id=kb_id)
    scope = {"kb_id": kb_id} if kb_id else {}
    record = AliasRecord(resource_type, alias, target.target_id, target.name, scope)
    store.set(record, force=args.force)
    return CommandResult(
        {"alias": record.to_dict()},
        (safe_message(f"Set alias: {resource_type}.{alias} -> {target.target_id}"),),
    )


def _resolve_optional_kb(args: Any, resolver: ResourceResolver) -> str:
    kb_id = getattr(args, "kb_id", None) or ""
    kb_ref = getattr(args, "kb_ref", None)
    if kb_ref:
        return resolver.resolve("kb", kb_ref).target_id
    return kb_id.strip()


def _has_kb_scope(args: Any) -> bool:
    return bool(getattr(args, "kb_id", None) or getattr(args, "kb_ref", None))
