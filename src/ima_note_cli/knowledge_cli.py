from __future__ import annotations

import argparse
from textwrap import fill
from typing import Any
from urllib.parse import urlsplit

from .errors import InputError
from .knowledge_api import KnowledgeBaseResult, KnowledgeBaseSummary, KnowledgeEntry, KnowledgePathNode
from .security import safe_url


_JSON_HELP = "Print one structured JSON document to stdout; JSON failures keep stderr empty."
_AUTOMATION = "Exit 75 means a temporary failure eligible for bounded backoff."
_PAGING = "With --all, reaching --max-pages preserves results and exits 9 as partial."
_WRITE = "Remote write: confirm the knowledge base and content before running this command."


def _wrap(text: str) -> str:
    return fill(text, width=78, break_on_hyphens=False)


def _command(subparsers: Any, name: str, summary: str, description: str, example: str, *notes: str) -> argparse.ArgumentParser:
    epilog = "\n\n".join((f"Example:\n  {example}", *(_wrap(note) for note in notes), _wrap(_AUTOMATION)))
    return subparsers.add_parser(
        name, help=summary, description=_wrap(description), epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json", help=_JSON_HELP)


def _pages(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--all", action="store_true", help="Collect pages until completion or --max-pages.")
    parser.add_argument(
        "--max-pages", type=int, default=100,
        help="Maximum pages with --all (default: 100; range: 1-1000).",
    )


def add_kb_subcommands(subparsers: Any) -> None:
    search_base = _command(
        subparsers, "search-base", "Search knowledge bases.",
        "Search knowledge-base metadata and return knowledge_base_id values for other KB commands.",
        'ima kb search-base "product docs" --all --max-pages 5 --json', _PAGING,
    )
    search_base.add_argument("query", help="Knowledge-base query; pass an empty string to enumerate accessible bases.")
    search_base.add_argument("--cursor", default="", help="Opaque starting cursor from a previous response.")
    search_base.add_argument("--limit", type=int, default=20, help="Bases per page (default: 20; range: 1-20).")
    _pages(search_base)
    _json(search_base)

    show = _command(
        subparsers, "show-base", "Show a knowledge base.",
        "Read metadata for one knowledge base selected by search-base or addable.",
        'ima kb show-base --kb-id "kb_test" --json',
        "Cover URL query strings and fragments are removed from CLI output.",
    )
    show.add_argument("--kb-id", required=True, help="Knowledge-base identifier from search-base or addable.")
    _json(show)

    browse = _command(
        subparsers, "browse", "Browse knowledge.",
        "Browse folders and knowledge items in one base; item results provide media_id values.",
        'ima kb browse --kb-id "kb_test" --all --max-pages 5 --json', _PAGING,
    )
    browse.add_argument("--kb-id", required=True, help="Knowledge-base identifier from search-base or addable.")
    browse.add_argument("--folder-id", help="Optional folder to browse; omit for the base root.")
    browse.add_argument("--cursor", default="", help="Opaque starting cursor from a previous response.")
    browse.add_argument("--limit", type=int, default=20, help="Items per page (default: 20; range: 1-50).")
    _pages(browse)
    _json(browse)

    search = _command(
        subparsers, "search", "Search knowledge.",
        "Search one or more knowledge bases and return grouped items with media_id values.",
        'ima kb search "deployment" --kb-id "kb_test" --all --max-pages 5 --json',
        (
            "Repeat --kb-id for 1-20 selected bases, or use --all-bases. Cross-base "
            "results stay grouped by base and are not globally reranked. A failed or "
            "incomplete base preserves other results and exits 9."
        ),
        _PAGING,
    )
    search.add_argument("query", help="Content query sent independently to each selected knowledge base.")
    search_targets = search.add_mutually_exclusive_group(required=True)
    search_targets.add_argument("--kb-id", dest="kb_ids", action="append", metavar="KB_ID", help="Knowledge base ID; repeat for up to 20 bases.")
    search_targets.add_argument("--all-bases", action="store_true", help="Search across discovered knowledge bases.")
    search.add_argument("--max-bases", type=int, help="Maximum bases for --all-bases (default: 20; range: 1-100).")
    search.add_argument("--cursor", default="", help="Base-specific cursor; valid only with exactly one --kb-id.")
    _pages(search)
    _json(search)

    addable = _command(
        subparsers, "addable", "List addable knowledge bases.",
        "List knowledge bases that the current credentials may target for imports or uploads.",
        "ima kb addable --all --max-pages 5 --json", _PAGING,
    )
    addable.add_argument("--cursor", default="", help="Opaque starting cursor from a previous response.")
    addable.add_argument("--limit", type=int, default=20, help="Bases per page (default: 20; range: 1-50).")
    _pages(addable)
    _json(addable)

    add_note = _command(
        subparsers, "add-note", "Add an IMA note.",
        "Add an existing IMA Note to one knowledge base.",
        'ima kb add-note --kb-id "kb_test" --note-id "note_test" --json', _WRITE,
    )
    add_note.add_argument("--kb-id", required=True, help="Destination knowledge-base identifier from addable.")
    ids = add_note.add_mutually_exclusive_group(required=True)
    ids.add_argument("--note-id", help="Canonical Note identifier from note search or list.")
    ids.add_argument("--doc-id", dest="deprecated_doc_id", help="Deprecated alias for --note-id; retained for compatibility.")
    add_note.add_argument("--title", help="Optional title stored for this knowledge-base item.")
    add_note.add_argument("--folder-id", help="Optional destination folder inside the knowledge base.")
    _json(add_note)

    add_url = _command(
        subparsers, "add-url", "Import web pages or supported remote files.",
        "Import 1-10 public HTTP(S) URLs; web pages use URL import and supported files use bounded download plus upload.",
        'ima kb add-url --kb-id "kb_test" --url "https://example.com/article" --json',
        _WRITE,
        (
            "Repeat --url for a batch. Batch failures exit 9; inspect results and retry "
            "only failed items marked retryable. Videos and private/local network URLs are rejected."
        ),
    )
    add_url.add_argument("--kb-id", required=True, help="Destination knowledge-base identifier from addable.")
    add_url.add_argument("--url", dest="urls", action="append", required=True, help="Public HTTP(S) URL; repeat 1-10 times.")
    add_url.add_argument("--folder-id", help="Optional destination folder inside the knowledge base.")
    add_url.add_argument(
        "--on-conflict", choices=("error", "rename"), default="error",
        help="Downloaded-file name policy (default: error; rename chooses a unique name).",
    )
    add_url.add_argument(
        "--download-timeout", type=int, default=300,
        help="Per-request remote download timeout in seconds (default: 300; range: 1-3600).",
    )
    add_url.add_argument(
        "--upload-timeout", type=int, default=300,
        help="COS upload timeout in seconds (default: 300; range: 1-3600).",
    )
    _json(add_url)

    add_file = _command(
        subparsers, "add-file", "Upload supported local files.",
        "Upload 1-2000 supported local files to one knowledge base.",
        'ima kb add-file --kb-id "kb_test" --file ".\\report.pdf" --json',
        _WRITE,
        (
            "Repeat --file for a batch. Supported: PDF, Office, CSV, Markdown, images, "
            "text, XMind, MP3/M4A/WAV/AAC, HTML, and EPUB; video is unsupported. "
            "Batch failures exit 9; retry only failed items marked retryable."
        ),
    )
    add_file.add_argument("--kb-id", required=True, help="Destination knowledge-base identifier from addable.")
    add_file.add_argument("--file", dest="files", action="append", required=True, help="Local file path; repeat 1-2000 times.")
    add_file.add_argument("--folder-id", help="Optional destination folder inside the knowledge base.")
    add_file.add_argument(
        "--content-type",
        help="MIME type override for one file only; cannot be used with repeated --file.",
    )
    add_file.add_argument(
        "--on-conflict", choices=("error", "rename"), default="error",
        help="File-name policy (default: error; rename chooses a unique name).",
    )
    add_file.add_argument(
        "--upload-timeout", type=int, default=300,
        help="COS upload timeout in seconds (default: 300; range: 1-3600).",
    )
    _json(add_file)

    info = _command(
        subparsers, "media-info", "Inspect original media.",
        "Inspect redacted metadata and source availability for a media_id returned by browse or search.",
        'ima kb media-info --media-id "media_test" --json',
        "Signed URLs and temporary headers are never printed.",
    )
    info.add_argument("--media-id", required=True, help="Media identifier returned by kb browse or search.")
    _json(info)

    read = _command(
        subparsers, "read", "Read original text media.",
        "Read up to 4 MiB of textual original content; use ima kb export for binary media.",
        'ima kb read --media-id "media_test" --json',
        "This command is read-only and does not print signed source credentials.",
    )
    read.add_argument("--media-id", required=True, help="Media identifier returned by kb browse or search.")
    _json(read)

    export = _command(
        subparsers, "export", "Export original media.",
        "Export up to 200 MiB of original media to a local file using atomic replacement.",
        'ima kb export --media-id "media_test" --output ".\\original.bin" --json',
        "The destination is not overwritten unless --force is supplied.",
    )
    export.add_argument("--media-id", required=True, help="Media identifier returned by kb browse or search.")
    export.add_argument("--output", required=True, help="Local output file; its parent directory must already exist.")
    export.add_argument("--force", action="store_true", help="Atomically replace an existing regular output file.")
    _json(export)


def handle_kb_command(args: argparse.Namespace, client: Any, media_service: Any = None):
    """Compatibility façade; rendering is owned by output.py."""
    from .commands.knowledge import execute
    return execute(args, client, media_service=media_service)


def kb_summary_to_dict(item: KnowledgeBaseSummary) -> dict[str, object]:
    return {"knowledge_base_id": item.knowledge_base_id, "name": item.name, "cover_url": safe_url(item.cover_url) if item.cover_url else ""}


def kb_detail_to_dict(item: KnowledgeBaseResult) -> dict[str, object]:
    return {"knowledge_base_id": item.knowledge_base_id, "name": item.name, "cover_url": safe_url(item.cover_url) if item.cover_url else "", "description": item.description, "recommended_questions": list(item.recommended_questions)}


def kb_entry_to_dict(item: KnowledgeEntry) -> dict[str, object]:
    return {"kind": item.kind, "item_id": item.item_id, "media_id": item.media_id, "folder_id": item.folder_id, "title": item.title, "parent_folder_id": item.parent_folder_id, "highlight_content": item.highlight_content, "file_number": item.file_number, "folder_number": item.folder_number, "is_top": item.is_top}


def path_node_to_dict(item: KnowledgePathNode) -> dict[str, object]:
    return {"folder_id": item.folder_id, "name": item.name}


def validate_urls(urls: list[str]) -> None:
    if not 1 <= len(urls) <= 10: raise InputError("--url must be provided between 1 and 10 times.")
    for value in urls:
        try: parsed = urlsplit(value)
        except ValueError as exc: raise InputError("Each URL must be an absolute HTTP or HTTPS URL.") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InputError("Each URL must be an absolute HTTP or HTTPS URL.")
