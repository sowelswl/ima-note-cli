from __future__ import annotations

import argparse
from pathlib import Path
from textwrap import fill
from typing import Any

from .errors import InputError
from .notes_api import FolderResult, SearchResult


_JSON_HELP = "Print one structured JSON document to stdout; JSON failures keep stderr empty."
_AUTOMATION = "Exit 75 means a temporary failure eligible for bounded backoff."
_PAGING = "With --all, reaching --max-pages preserves results and exits 9 as partial."
_WRITE = "Remote write: confirm the target and content before running this command."


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


def _page_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--all", action="store_true", help="Collect pages until completion or --max-pages.")
    parser.add_argument(
        "--max-pages", type=int, default=100,
        help="Maximum pages with --all (default: 100; range: 1-1000).",
    )


def add_note_subcommands(subparsers: Any) -> None:
    search = _command(
        subparsers, "search", "Search notes by title or content.",
        "Search Notes and return note_id values for get, append, or kb add-note.",
        'ima note search "release plan" --search-type content --all --max-pages 5 --json',
        _PAGING,
    )
    search.add_argument("query", help="Text to match against note titles or content.")
    search.add_argument(
        "--search-type", choices=("title", "content"), default="title",
        help="Field to search (default: title).",
    )
    search.add_argument(
        "--sort", choices=("updated", "created", "title", "size"), default="updated",
        help="Result order (default: updated); size is reserved but currently unsupported.",
    )
    search.add_argument("--start", type=int, default=0, help="Zero-based result offset (default: 0; minimum: 0).")
    search.add_argument("--limit", type=int, default=20, help="Results per page (default: 20; range: 1-20).")
    _page_options(search)
    _json(search)

    folders = _command(
        subparsers, "folders", "List note folders.",
        "List Note folders and return folder_id values for note list or create.",
        "ima note folders --all --max-pages 5 --json", _PAGING,
    )
    folders.add_argument("--cursor", default="0", help="Opaque starting cursor from a previous response (default: 0).")
    folders.add_argument("--limit", type=int, default=20, help="Folders per page (default: 20; range: 1-20).")
    _page_options(folders)
    _json(folders)

    listing = _command(
        subparsers, "list", "List notes within a folder or the root notes view.",
        "List Notes and return note_id values, optionally scoped to one folder.",
        'ima note list --folder-id "folder_test" --all --max-pages 5 --json', _PAGING,
    )
    listing.add_argument("--folder-id", default="", help="Folder to list; omit for the root Notes view.")
    listing.add_argument("--cursor", default="", help="Opaque starting cursor from a previous response.")
    listing.add_argument(
        "--sort", choices=("updated", "created", "title", "size"), default="updated",
        help="Result order (default: updated); size is reserved but currently unsupported.",
    )
    listing.add_argument("--limit", type=int, default=20, help="Notes per page (default: 20; range: 1-20).")
    _page_options(listing)
    _json(listing)

    get = _command(
        subparsers, "get", "Read a note's plain-text content.",
        "Read one Note by the note_id returned by search or list.",
        'ima note get "note_test" --json',
    )
    get.add_argument("note_id", help="Canonical Note identifier returned by search or list.")
    _json(get)

    create = _command(
        subparsers, "create", "Create a new note from Markdown content.",
        "Create one remote Note from inline Markdown or a local UTF-8 Markdown file.",
        'ima note create --title "Release plan" --file ".\\plan.md" --json',
        _WRITE,
        "Local paths, data URIs, and unsupported local images are removed before writing.",
    )
    create.add_argument("--title", help="Optional title; prepended as a Markdown H1.")
    create.add_argument("--folder-id", default="", help="Destination folder_id; omit for the default location.")
    group = create.add_mutually_exclusive_group(required=True)
    group.add_argument("--content", help="Inline Markdown body; mutually exclusive with --file.")
    group.add_argument(
        "--file",
        help="Path to a UTF-8 Markdown content file; this is not a knowledge-base upload.",
    )
    _json(create)

    append = _command(
        subparsers, "append", "Append Markdown content to an existing note.",
        "Append inline Markdown or a local UTF-8 Markdown file to one remote Note.",
        'ima note append "note_test" --content "## Update" --json',
        _WRITE,
        "Local paths, data URIs, and unsupported local images are removed before writing.",
    )
    append.add_argument("note_id", help="Canonical Note identifier returned by search or list.")
    group = append.add_mutually_exclusive_group(required=True)
    group.add_argument("--content", help="Inline Markdown to append; mutually exclusive with --file.")
    group.add_argument("--file", help="Path to a UTF-8 Markdown content file to append.")
    _json(append)


def handle_note_command(args: argparse.Namespace, client: Any):
    """Compatibility façade; rendering is owned by output.py."""
    from .commands.notes import execute
    return execute(args, client)


def search_result_to_dict(result: SearchResult) -> dict[str, object]:
    return {"note_id": result.note_id, "doc_id": result.doc_id, "title": result.title, "summary": result.summary,
            "folder_id": result.folder_id, "folder_name": result.folder_name, "create_time": result.create_time,
            "modify_time": result.modify_time, "cover_image": result.cover_image, "status": result.status,
            "highlight_title": result.highlight_title}


def folder_result_to_dict(result: FolderResult) -> dict[str, object]:
    return {"folder_id": result.folder_id, "name": result.name, "note_number": result.note_number,
            "create_time": result.create_time, "modify_time": result.modify_time, "folder_type": result.folder_type,
            "status": result.status, "parent_folder_id": result.parent_folder_id}


def load_markdown_input(content: str | None, file_path: str | None) -> str:
    if content is not None:
        if not content.strip(): raise InputError("Content cannot be empty.")
        return content
    if file_path is None: raise InputError("Either --content or --file is required.")
    path = Path(file_path)
    if not path.is_file(): raise InputError(f"File not found: {file_path}")
    try: loaded = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc: raise InputError(f"Content file must be valid UTF-8: {file_path}") from exc
    if not loaded.strip(): raise InputError("Content file is empty.")
    return loaded


def compose_markdown(title: str | None, body: str) -> str:
    if title is None: return body
    title = title.strip()
    if not title: raise InputError("--title cannot be empty.")
    return f"# {title}\n\n{body.strip()}" if body.strip() else f"# {title}"
