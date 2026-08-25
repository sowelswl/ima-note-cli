from __future__ import annotations

from typing import Any

from .errors import InputError
from .references import ResourceResolver


KB_WRITE_ACTIONS = frozenset({"add-note", "add-url", "add-file"})


def prepare_note_references(args: Any, resolver: ResourceResolver) -> None:
    action = args.note_action
    note_ref = getattr(args, "note_ref", None)
    if note_ref:
        args.note_id = resolver.resolve("note", note_ref).target_id
    folder_ref = getattr(args, "folder_ref", None)
    if folder_ref:
        args.folder_id = resolver.resolve("note-folder", folder_ref).target_id
    if action in {"get", "append"} and not getattr(args, "note_id", None):
        raise InputError("Provide a note ID or --note reference.", code="usage_error")


def prepare_knowledge_references(args: Any, resolver: ResourceResolver) -> None:
    action = args.kb_action
    if action == "search":
        _prepare_search_bases(args, resolver)
        return

    kb_ref = getattr(args, "kb_ref", None)
    if kb_ref:
        args.kb_id = resolver.resolve("kb", kb_ref, for_write=action in KB_WRITE_ACTIONS).target_id
    kb_scope = (getattr(args, "kb_id", None) or "").strip()

    folder_ref = getattr(args, "folder_ref", None)
    if folder_ref:
        args.folder_id = resolver.resolve("kb-folder", folder_ref, kb_id=kb_scope).target_id

    note_ref = getattr(args, "note_ref", None)
    if note_ref:
        args.note_id = resolver.resolve("note", note_ref).target_id

    media_ref = getattr(args, "media_ref", None)
    if media_ref:
        args.media_id = resolver.resolve("media", media_ref, kb_id=kb_scope).target_id


def _prepare_search_bases(args: Any, resolver: ResourceResolver) -> None:
    references = getattr(args, "kb_refs", None) or []
    if not references:
        return
    if len(references) > 20:
        raise InputError("--kb may be provided at most 20 times.", details={"field": "--kb", "limit": 20})
    resolved = [resolver.resolve("kb", value).target_id for value in references]
    if len(set(resolved)) != len(resolved):
        raise InputError("--kb references must resolve to unique knowledge bases.", details={"field": "--kb"})
    args.kb_ids = resolved


__all__ = ["prepare_knowledge_references", "prepare_note_references"]
