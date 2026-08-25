from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from .aliases import AliasStore, RESOURCE_TYPES, validate_alias_name, validate_resource_type
from .errors import InputError, ReferenceError


REFERENCE_PREFIXES = frozenset({"id", "alias", "name"})
DEFAULT_MAX_PAGES = 100


@dataclass(frozen=True)
class ResourceCandidate:
    target_id: str
    name: str
    kb_id: str = ""
    parent_folder_id: str = ""

    def to_dict(self) -> dict[str, str]:
        value = {"id": self.target_id, "name": self.name}
        if self.kb_id:
            value["kb_id"] = self.kb_id
        if self.parent_folder_id:
            value["parent_folder_id"] = self.parent_folder_id
        return value


@dataclass(frozen=True)
class ResolvedReference:
    resource_type: str
    target_id: str
    name: str
    source: str
    reference: str
    scope: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_type": self.resource_type,
            "id": self.target_id,
            "name": self.name,
            "source": self.source,
            "reference": self.reference,
            "scope": dict(self.scope),
        }


def parse_reference(value: str, *, bare_is_name: bool = False) -> tuple[str, str]:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise InputError("Resource reference cannot be empty.", code="invalid_reference")
    if ":" not in text:
        return ("name" if bare_is_name else "id"), text
    prefix, resolved = text.split(":", 1)
    if prefix not in REFERENCE_PREFIXES:
        raise InputError(
            f"Unknown reference prefix {prefix!r}; use id:, alias:, or name:.",
            code="invalid_reference",
        )
    resolved = resolved.strip()
    if not resolved:
        raise InputError(f"{prefix}: requires a non-empty value.", code="invalid_reference")
    if prefix == "alias":
        validate_alias_name(resolved)
    return prefix, resolved


class ResourceResolver:
    def __init__(
        self,
        *,
        aliases: AliasStore,
        notes: Any = None,
        knowledge: Any = None,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
            raise InputError("Resolver max_pages must be a positive integer.")
        self.aliases = aliases
        self.notes = notes
        self.knowledge = knowledge
        self.max_pages = max_pages

    def resolve(
        self,
        resource_type: str,
        reference: str,
        *,
        bare_is_name: bool = False,
        kb_id: str = "",
        for_write: bool = False,
    ) -> ResolvedReference:
        validate_resource_type(resource_type)
        source, value = parse_reference(reference, bare_is_name=bare_is_name)
        scope = {"kb_id": kb_id} if kb_id else {}
        if source == "id":
            return ResolvedReference(resource_type, value, "", source, reference, scope)
        if source == "alias":
            expected_scope = scope or None
            record = self.aliases.get(resource_type, value, expected_scope=expected_scope)
            return ResolvedReference(
                resource_type,
                record.target_id,
                record.display_name,
                source,
                reference,
                dict(record.scope),
            )
        if resource_type in {"kb-folder", "media"} and not kb_id:
            raise ReferenceError(
                f"name: references for {resource_type} require a knowledge-base scope.",
                code="reference_scope_required",
                resource_type=resource_type,
                reference=reference,
            )
        candidates, complete = self._name_candidates(resource_type, value, kb_id=kb_id, for_write=for_write)
        exact = self._dedupe([item for item in candidates if item.name == value])
        if not complete:
            raise ReferenceError(
                f"Could not completely scan {resource_type} candidates for {value!r}.",
                code="resolution_incomplete",
                resource_type=resource_type,
                reference=reference,
                candidates=[item.to_dict() for item in exact],
                scope=scope,
            )
        if not exact:
            raise ReferenceError(
                f"No {resource_type} exactly matches {value!r}.",
                code="reference_not_found",
                resource_type=resource_type,
                reference=reference,
                scope=scope,
            )
        if len(exact) > 1:
            raise ReferenceError(
                f"Multiple {resource_type} resources exactly match {value!r}.",
                code="ambiguous_reference",
                resource_type=resource_type,
                reference=reference,
                candidates=[item.to_dict() for item in exact],
                scope=scope,
            )
        selected = exact[0]
        resolved_scope = {"kb_id": selected.kb_id or kb_id} if selected.kb_id or kb_id else {}
        return ResolvedReference(resource_type, selected.target_id, selected.name, source, reference, resolved_scope)

    def _name_candidates(
        self, resource_type: str, name: str, *, kb_id: str, for_write: bool,
    ) -> tuple[list[ResourceCandidate], bool]:
        if resource_type == "kb":
            return self._knowledge_bases(name, for_write=for_write)
        if resource_type == "note":
            return self._notes(name)
        if resource_type == "note-folder":
            return self._note_folders()
        if resource_type == "kb-folder":
            return self._knowledge_folders(kb_id)
        if resource_type == "media":
            return self._knowledge_media(name, kb_id)
        raise AssertionError(resource_type)

    def _knowledge_bases(self, _name: str, *, for_write: bool) -> tuple[list[ResourceCandidate], bool]:
        client = self._require_client("kb", self.knowledge)
        if for_write:
            values, complete = self._cursor(
                lambda cursor: client.list_addable_knowledge_bases(50, cursor=cursor),
                "knowledge_bases",
                initial="",
            )
        else:
            values, complete = self._cursor(
                lambda cursor: client.search_knowledge_bases("", 20, cursor=cursor),
                "knowledge_bases",
                initial="",
            )
        return [ResourceCandidate(item.knowledge_base_id, item.name) for item in values], complete

    def _notes(self, name: str) -> tuple[list[ResourceCandidate], bool]:
        client = self._require_client("note", self.notes)
        values: list[Any] = []
        start = 0
        for _ in range(self.max_pages):
            page = client.search_notes(name, 20, start=start, search_type=0, sort_type=0)
            current = page["docs"]
            values.extend(current)
            if page["is_end"]:
                return [ResourceCandidate(item.note_id, item.title, parent_folder_id=item.folder_id) for item in values], True
            if not current:
                return [ResourceCandidate(item.note_id, item.title, parent_folder_id=item.folder_id) for item in values], False
            start += len(current)
        return [ResourceCandidate(item.note_id, item.title, parent_folder_id=item.folder_id) for item in values], False

    def _note_folders(self) -> tuple[list[ResourceCandidate], bool]:
        client = self._require_client("note-folder", self.notes)
        values, complete = self._cursor(
            lambda cursor: client.list_folders(20, cursor=cursor),
            "folders",
            initial="0",
        )
        return [ResourceCandidate(item.folder_id, item.name, parent_folder_id=item.parent_folder_id) for item in values], complete

    def _knowledge_media(self, _name: str, kb_id: str) -> tuple[list[ResourceCandidate], bool]:
        values, complete = self._knowledge_tree(kb_id)
        return [
            ResourceCandidate(item.item_id, item.title, kb_id, item.parent_folder_id)
            for item in values if item.kind == "file"
        ], complete

    def _knowledge_folders(self, kb_id: str) -> tuple[list[ResourceCandidate], bool]:
        values, complete = self._knowledge_tree(kb_id)
        return [
            ResourceCandidate(item.item_id, item.title, kb_id, item.parent_folder_id)
            for item in values if item.kind == "folder"
        ], complete

    def _knowledge_tree(self, kb_id: str) -> tuple[list[Any], bool]:
        client = self._require_client("knowledge", self.knowledge)
        queue: deque[str] = deque([""])
        traversed: set[str] = set()
        seen_items: set[tuple[str, str]] = set()
        values: list[Any] = []
        pages = 0
        while queue:
            parent_id = queue.popleft()
            if parent_id in traversed:
                continue
            traversed.add(parent_id)
            cursor = ""
            seen_cursors: set[str] = set()
            while True:
                if pages >= self.max_pages or cursor in seen_cursors:
                    return values, False
                seen_cursors.add(cursor)
                page = client.list_knowledge(kb_id, 50, cursor=cursor, folder_id=parent_id or None)
                pages += 1
                for item in page["items"]:
                    identity = (item.kind, item.item_id)
                    if identity not in seen_items:
                        seen_items.add(identity)
                        values.append(item)
                    if item.kind == "folder":
                        if item.item_id not in traversed:
                            queue.append(item.item_id)
                if page["is_end"]:
                    break
                cursor = page["next_cursor"]
                if not cursor:
                    return values, False
        return values, True

    def _cursor(
        self, fetch: Callable[[str], dict[str, Any]], key: str, *, initial: str,
    ) -> tuple[list[Any], bool]:
        cursor = initial
        seen: set[str] = set()
        values: list[Any] = []
        for _ in range(self.max_pages):
            if cursor in seen:
                return values, False
            seen.add(cursor)
            page = fetch(cursor)
            values.extend(page[key])
            if page["is_end"]:
                return values, True
            cursor = page["next_cursor"]
            if not cursor:
                return values, False
        return values, False

    @staticmethod
    def _dedupe(values: list[ResourceCandidate]) -> list[ResourceCandidate]:
        result: list[ResourceCandidate] = []
        seen: set[str] = set()
        for value in values:
            if value.target_id not in seen:
                seen.add(value.target_id)
                result.append(value)
        return result

    @staticmethod
    def _require_client(resource_type: str, client: Any) -> Any:
        if client is None:
            raise InputError(f"A {resource_type} API client is required for name resolution.")
        return client


__all__ = [
    "DEFAULT_MAX_PAGES", "REFERENCE_PREFIXES", "RESOURCE_TYPES", "ResolvedReference",
    "ResourceCandidate", "ResourceResolver", "parse_reference",
]
