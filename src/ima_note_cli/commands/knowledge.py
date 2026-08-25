from __future__ import annotations

from typing import Any

from ..command_result import CommandResult, CommandStatus
from ..errors import ApiProtocolError, ExitCode, ImaCliError, InputError
from ..knowledge_cli import kb_detail_to_dict, kb_entry_to_dict, kb_summary_to_dict, path_node_to_dict
from ..pagination import collect_cursor_pages


def execute(args: Any, client: Any, *, media_service: Any = None, upload_service: Any = None, url_service: Any = None) -> CommandResult:
    action = args.kb_action
    if action == "search-base":
        return _cursor(args, lambda cursor: client.search_knowledge_bases(args.query, args.limit, cursor=cursor), "knowledge_bases", kb_summary_to_dict, {"query": args.query})
    if action == "addable":
        return _cursor(args, lambda cursor: client.list_addable_knowledge_bases(args.limit, cursor=cursor), "knowledge_bases", kb_summary_to_dict, {})
    if action == "browse":
        return _cursor(args, lambda cursor: client.list_knowledge(args.kb_id, args.limit, cursor=cursor, folder_id=args.folder_id), "items", kb_entry_to_dict, {"knowledge_base_id": args.kb_id, "folder_id": args.folder_id or ""})
    if action == "search":
        kb_ids = args.kb_ids or []
        if kb_ids and args.max_bases is not None:
            raise InputError("--max-bases may only be used with --all-bases.", details={"field": "--max-bases"})
        if (len(kb_ids) != 1) and args.cursor:
            raise InputError("--cursor may only be used with a single --kb-id or --kb.", details={"field": "--cursor"})
        if len(kb_ids) == 1:
            kb_id = kb_ids[0]
            return _cursor(args, lambda cursor: client.search_knowledge(args.query, kb_id, cursor=cursor), "items", kb_entry_to_dict, {"query": args.query, "knowledge_base_id": kb_id})
        return _search_across_bases(args, client, kb_ids)
    if action == "show-base":
        value = client.get_knowledge_base(args.kb_id)
        payload = {"knowledge_base": kb_detail_to_dict(value) if value else None}
        lines = (f"Knowledge base: {value.name}", value.description, *value.recommended_questions) if value else ("Knowledge base not found.",)
        return CommandResult(payload, tuple(lines), status=CommandStatus.SUCCESS if value else CommandStatus.EMPTY)
    if action == "add-note":
        deprecated = getattr(args, "deprecated_doc_id", None)
        note_id = args.note_id or deprecated
        result = client.add_note(args.kb_id, note_id, title=args.title or note_id, folder_id=args.folder_id)
        warnings = ("--doc-id is deprecated; use --note-id.",) if deprecated else ()
        return CommandResult(result, (f"Added note: {note_id}",), warnings)
    if action == "add-file":
        results = upload_service.upload_many(args.kb_id, args.files, folder_id=args.folder_id, content_type=args.content_type, on_conflict=args.on_conflict, timeout=args.upload_timeout)
        payload = {"knowledge_base_id": args.kb_id}
        if len(results) == 1:
            payload.update({key: value for key, value in results[0].items() if key in {"media_id", "file_name", "stage"}})
            payload["title"] = results[0]["file_name"]
            payload["folder_id"] = args.folder_id or ""
        lines = tuple(f"{index}. {item['file_name']} {item['status']} ({item['stage']})" for index, item in enumerate(results, 1))
        batch = CommandResult.batch(results, payload=payload, human_lines=lines)
        if any(item.get("stage") == "interrupted" for item in results):
            error = ImaCliError("Interrupted.", code="interrupted", exit_code=ExitCode.INTERRUPTED)
            return CommandResult(batch.payload, batch.human_lines, batch.warnings, batch.status, int(ExitCode.INTERRUPTED), error)
        return batch
    if action == "add-url":
        return url_service.ingest(args.kb_id, args.urls, folder_id=args.folder_id, on_conflict=args.on_conflict, download_timeout=args.download_timeout, upload_timeout=args.upload_timeout)
    if action == "media-info":
        payload = media_service.inspect_media(args.media_id).to_safe_dict()
        return CommandResult(payload, tuple(f"{key}: {value}" for key, value in payload.items()))
    if action == "read":
        value = media_service.read_media(args.media_id)
        payload = {"media_id": value.media_id, "media_type": value.media_type, "source_kind": value.source_kind, "content": value.content, "content_type": value.content_type}
        return CommandResult(payload, (payload["content"],))
    if action == "export":
        value = media_service.export_media(args.media_id, args.output, force=args.force)
        payload = {"media_id": value.media_id, "media_type": value.media_type, "source_kind": value.source_kind, "output": value.output, "bytes": value.bytes_count, "sha256": value.sha256, "content_type": value.content_type}
        return CommandResult(payload, (f"Exported: {payload['output']}",))
    raise ValueError("unknown knowledge command")


def _cursor(args: Any, fetch: Any, key: str, serialize: Any, base: dict[str, Any]) -> CommandResult:
    if args.all:
        collection = collect_cursor_pages(fetch, key, initial_cursor=args.cursor, max_pages=args.max_pages)
        raw_values = list(collection.items)
        values = _dedupe(raw_values, lambda item: item.knowledge_base_id if key == "knowledge_bases" else item.item_id)
        next_cursor = collection.next_cursor; is_end = collection.complete
    else:
        page = fetch(args.cursor); values = page[key]; next_cursor = page["next_cursor"]; is_end = page["is_end"]
    payload = {**base, "cursor": args.cursor, "next_cursor": next_cursor, "is_end": is_end, key: [serialize(item) for item in values]}
    if args.all:
        payload["pagination"] = {
            "all_requested": True, "max_pages": args.max_pages,
            "pages_fetched": collection.pages_fetched, "truncated": not is_end,
            "start": args.cursor, "next": next_cursor,
        }
        if not is_end:
            lines = tuple([f"Returned: {len(values)}", "Pagination stopped at --max-pages."])
            return CommandResult.batch([{"status": "success"} for _ in values] + [{"status": "not_attempted"}], payload=payload, human_lines=lines)
    return CommandResult(payload, (f"Returned: {len(values)}",), status=CommandStatus.SUCCESS if values else CommandStatus.EMPTY)


def _dedupe(values: list[Any], key: Any) -> list[Any]:
    seen: set[str] = set(); result: list[Any] = []
    for value in values:
        identity = key(value)
        if identity not in seen:
            seen.add(identity); result.append(value)
    return result


def _search_across_bases(args: Any, client: Any, kb_ids: list[str]) -> CommandResult:
    if kb_ids:
        if len(kb_ids) > 20:
            raise InputError("--kb-id may be provided at most 20 times.", details={"field": "--kb-id", "limit": 20})
        if len(set(kb_ids)) != len(kb_ids):
            raise InputError("--kb-id values must be unique.", details={"field": "--kb-id"})
        details = client.get_knowledge_bases(kb_ids)
        targets = [(kb_id, details[kb_id].name if kb_id in details else kb_id) for kb_id in kb_ids]
        discovery = None
        scope = "selected_bases"
    else:
        targets, discovery = _discover_bases(client, args.max_bases or 20)
        scope = "all_bases"

    groups: list[dict[str, Any]] = []
    lines: list[str] = []
    succeeded = empty = partial = failed = total_items = 0
    for kb_id, name in targets:
        try:
            result = _cursor(
                args,
                lambda cursor, current=kb_id: client.search_knowledge(args.query, current, cursor=cursor),
                "items",
                kb_entry_to_dict,
                {"query": args.query, "knowledge_base_id": kb_id},
            )
            items = result.payload["items"]
            total_items += len(items)
            group_status = result.status.value
            group = {
                "knowledge_base": {"knowledge_base_id": kb_id, "name": name},
                "status": group_status,
                "items": items,
                "cursor": result.payload["cursor"],
                "next_cursor": result.payload["next_cursor"],
                "is_end": result.payload["is_end"],
            }
            if "pagination" in result.payload:
                group["pagination"] = result.payload["pagination"]
            if result.error is not None:
                group["error"] = result.error.to_error_dict()
            if result.status is CommandStatus.SUCCESS:
                succeeded += 1
            elif result.status is CommandStatus.EMPTY:
                empty += 1
            elif result.status is CommandStatus.PARTIAL:
                partial += 1
            else:
                failed += 1
            lines.extend((f"Knowledge base: {name}", f"Returned: {len(items)}"))
        except ImaCliError as exc:
            failed += 1
            group = {
                "knowledge_base": {"knowledge_base_id": kb_id, "name": name},
                "status": "failed",
                "items": [],
                "error": exc.to_error_dict(),
            }
            lines.append(f"Knowledge base failed: {name}")
        groups.append(group)

    summary = {
        "total_bases": len(targets), "succeeded": succeeded, "empty": empty,
        "partial": partial, "failed": failed, "total_items": total_items,
    }
    payload: dict[str, Any] = {
        "query": args.query, "scope": scope, "summary": summary,
        "knowledge_bases": groups,
    }
    if discovery is not None:
        payload["discovery"] = discovery
    if failed or partial:
        error = ImaCliError(
            f"{failed + partial} of {len(targets)} knowledge bases failed or were incomplete.",
            code="partial_failure", exit_code=ExitCode.PARTIAL,
        )
        status = CommandStatus.PARTIAL if succeeded or empty or total_items else CommandStatus.FAILED
        return CommandResult(payload, tuple(lines), status=status, exit_code=int(ExitCode.PARTIAL), error=error)
    status = CommandStatus.SUCCESS if total_items else CommandStatus.EMPTY
    return CommandResult(payload, tuple(lines), status=status)


def _discover_bases(client: Any, max_bases: int) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    cursor = ""
    seen_cursors: set[str] = set()
    seen_ids: set[str] = set()
    targets: list[tuple[str, str]] = []
    pages = 0
    is_end = False
    while len(targets) < max_bases and not is_end and pages < max_bases:
        if cursor in seen_cursors:
            raise ApiProtocolError("Knowledge base discovery cursor repeated.", code="pagination_cursor_loop")
        seen_cursors.add(cursor)
        page = client.search_knowledge_bases("", min(20, max_bases - len(targets)), cursor=cursor)
        pages += 1
        for item in page["knowledge_bases"]:
            if item.knowledge_base_id not in seen_ids:
                seen_ids.add(item.knowledge_base_id)
                targets.append((item.knowledge_base_id, item.name))
                if len(targets) == max_bases:
                    break
        is_end = page["is_end"]
        next_cursor = page["next_cursor"]
        if not is_end and not next_cursor:
            raise ApiProtocolError("Knowledge base discovery did not provide a next cursor.", code="pagination_no_progress")
        cursor = next_cursor
    return targets, {
        "max_bases": max_bases, "bases_discovered": len(targets),
        "pages_fetched": pages, "truncated": not is_end, "next_cursor": cursor,
    }
