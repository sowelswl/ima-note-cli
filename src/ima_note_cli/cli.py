from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from textwrap import fill
from typing import Sequence

from .aliases import AliasStore, RESOURCE_TYPES
from .config import CredentialStatus, Credentials, inspect_credentials, load_credentials
from .errors import ConfigError, ImaCliError, InputError
from .knowledge_api import KnowledgeBaseApiClient
from .knowledge_cli import add_kb_subcommands
from .media_service import MediaContentService
from .notes_api import NotesApiClient
from .notes_cli import add_note_subcommands
from .output import emit_command_result, emit_human_error, emit_json_error
from .source_http import SourceHttpClient
from .command_result import CommandResult
from .commands.notes import execute as execute_note
from .commands.knowledge import execute as execute_knowledge
from .commands.references import execute_alias, execute_resolve
from .reference_cli import prepare_knowledge_references, prepare_note_references
from .references import ResourceResolver
from .upload_service import UploadService
from .url_ingest import UrlIngestService
from .validation import validate_max_bases, validate_max_pages, validate_timeout


def _wrap(text: str) -> str:
    return fill(text, width=78, break_on_hyphens=False)


class CliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(message, code="usage_error")


def build_parser(*, prog: str = "ima") -> argparse.ArgumentParser:
    parser = CliArgumentParser(
        prog=prog,
        description="Manage IMA notes and knowledge bases from the command line.",
        epilog=(
            "Start here:\n"
            "  ima auth\n"
            "  ima note --help\n"
            "  ima kb --help\n"
            "  ima resolve --help\n"
            "  ima alias --help\n\n" + _wrap(
                "Credentials are read from IMA_OPENAPI_CLIENTID and IMA_OPENAPI_APIKEY, "
                "then project .env or user config. Use --json on leaf commands for one "
                "machine-readable stdout document. Exit 9 means partial/itemized failure; "
                "exit 75 means a temporary failure eligible for bounded backoff.",
            )
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser(
        "auth",
        help="Check whether IMA credentials are configured.",
        description=_wrap(
            "Report whether both IMA credentials are configured and where each value "
            "was found, without printing credential values.",
        ),
        epilog=(
            "Example:\n"
            "  ima auth --json\n\n" + _wrap(
                "Credential priority: environment, project .env, then user config. "
                "Missing credentials exit 3. JSON writes one stdout document and keeps "
                "stderr empty on failure.",
            )
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    auth_parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Print one structured JSON document to stdout.",
    )

    note_parser = subparsers.add_parser(
        "note",
        help="Manage IMA notes.",
        description="Search, list, read, create, and append IMA Notes.",
        epilog=_wrap(
            "Use canonical IDs directly or pass id:, alias:, or exact name: references "
            "through --note and --folder. Run ima resolve or ima alias --help to avoid "
            "copying repeated IDs, and inspect leaf-command help before writes.",
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_note_subcommands(note_parser.add_subparsers(dest="note_action", required=True))

    kb_parser = subparsers.add_parser(
        "kb",
        help="Manage IMA knowledge bases.",
        description=_wrap("Discover knowledge bases, search content, import material, and read original media."),
        epilog=_wrap(
            "Use canonical IDs directly or pass id:, alias:, or exact name: references "
            "through --kb, --folder, --note, and --media. Scoped names fail on ambiguity. "
            "Run ima kb <command> --help before remote writes.",
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_kb_subcommands(kb_parser.add_subparsers(dest="kb_action", required=True))

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Resolve an exact resource name or explicit reference.",
        description=_wrap(
            "Resolve one kb, note, note-folder, kb-folder, or media reference to its canonical ID. "
            "A bare value is treated as an exact name only for this command."
        ),
        epilog=(
            "Example:\n"
            '  ima resolve kb "AI Research" --json\n\n'
            + _wrap("Exact-name ambiguity and incomplete candidate scans fail without choosing a result. Exit 75 means a temporary failure eligible for bounded backoff.")
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    resolve_parser.add_argument("resource_type", choices=RESOURCE_TYPES, metavar="TYPE", help="Resource type to resolve.")
    resolve_parser.add_argument("reference", help="Exact name, or an explicit id:, alias:, or name: reference.")
    resolve_kb = resolve_parser.add_mutually_exclusive_group()
    resolve_kb.add_argument("--kb-id", help="Knowledge-base ID that scopes kb-folder or media resolution.")
    resolve_kb.add_argument("--kb", dest="kb_ref", metavar="KB_REF", help="Knowledge-base reference for scoped resolution.")
    resolve_parser.add_argument("--json", action="store_true", dest="as_json", help="Print one structured JSON document to stdout.")

    alias_parser = subparsers.add_parser(
        "alias",
        help="Manage account-bound local resource aliases.",
        description=_wrap("Store typed aliases in the current user's IMA configuration."),
        epilog=_wrap("Alias records contain resource IDs and a non-secret account fingerprint, never API credentials."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    alias_subparsers = alias_parser.add_subparsers(dest="alias_action", required=True)
    alias_set = alias_subparsers.add_parser(
        "set", help="Create or replace one alias.",
        description=_wrap("Bind one typed local alias to a canonical resource target for the configured account."),
        epilog="Example:\n  ima alias set kb.research id:kb_test --json\n\n" + _wrap("Exit 75 means a temporary failure eligible for bounded backoff."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    alias_set.add_argument("alias_key", help="Typed key such as kb.research or media.paper.")
    alias_set.add_argument("target", help="Target ID or explicit id:, alias:, or name: reference.")
    alias_set_kb = alias_set.add_mutually_exclusive_group()
    alias_set_kb.add_argument("--kb-id", help="Knowledge-base ID required for kb-folder and media aliases.")
    alias_set_kb.add_argument("--kb", dest="kb_ref", metavar="KB_REF", help="Knowledge-base reference for a scoped alias.")
    alias_set.add_argument("--force", action="store_true", help="Replace an existing alias for this account.")
    alias_set.add_argument("--json", action="store_true", dest="as_json", help="Print one structured JSON document to stdout.")

    alias_list = alias_subparsers.add_parser(
        "list", help="List aliases for the configured account.",
        description=_wrap("List typed aliases stored for the currently configured IMA account."),
        epilog="Example:\n  ima alias list --type kb --json\n\n" + _wrap("Exit 75 means a temporary failure eligible for bounded backoff."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    alias_list.add_argument("--type", dest="resource_type", choices=RESOURCE_TYPES, help="Filter by resource type.")
    alias_list.add_argument("--json", action="store_true", dest="as_json", help="Print one structured JSON document to stdout.")

    alias_unset = alias_subparsers.add_parser(
        "unset", help="Remove one alias.",
        description=_wrap("Remove one typed local alias for the currently configured IMA account."),
        epilog="Example:\n  ima alias unset kb.research --json\n\n" + _wrap("Exit 75 means a temporary failure eligible for bounded backoff."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    alias_unset.add_argument("alias_key", help="Typed key such as kb.research or media.paper.")
    alias_unset.add_argument("--json", action="store_true", dest="as_json", help="Print one structured JSON document to stdout.")

    return parser


def run(argv: Sequence[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else list(sys.argv[1:])
    as_json = "--json" in argv_list
    command_name = _command_name(argv_list)
    parser = build_parser()
    try:
        args = parser.parse_args(argv_list)
        if hasattr(args, "max_pages"):
            validate_max_pages(args.max_pages)
        if getattr(args, "max_bases", None) is not None:
            validate_max_bases(args.max_bases)
        if hasattr(args, "download_timeout"):
            validate_timeout(args.download_timeout, "--download-timeout")
        if hasattr(args, "upload_timeout"):
            validate_timeout(args.upload_timeout, "--upload-timeout")
        command_name = _command_name_from_args(args)
        status = inspect_credentials(Path.cwd())
        if args.command == "auth":
            if not status.is_configured:
                if not as_json:
                    result = auth_result(status)
                    emit_command_result(command_name, result, as_json=False)
                    return 3
                raise ConfigError("IMA credentials are not fully configured.", code="credentials_missing")
            return emit_command_result(command_name, auth_result(status), as_json=as_json)

        if not status.is_configured:
            raise ConfigError("IMA credentials are not fully configured.", code="credentials_missing")
        credentials = Credentials(
            status.client_id, status.api_key,
            status.client_id_source or "unknown", status.api_key_source or "unknown",
        )
        notes = NotesApiClient(credentials)
        knowledge = KnowledgeBaseApiClient(credentials)
        aliases = AliasStore.for_client_id(credentials.client_id)
        resolver = ResourceResolver(aliases=aliases, notes=notes, knowledge=knowledge)
        if args.command == "note":
            prepare_note_references(args, resolver)
            return emit_command_result(command_name, execute_note(args, notes), as_json=as_json)
        if args.command == "kb":
            prepare_knowledge_references(args, resolver)
            media_service = None
            if args.kb_action in {"media-info", "read", "export"}:
                media_service = MediaContentService(knowledge, notes, SourceHttpClient())
            upload = UploadService(knowledge)
            url_service = UrlIngestService(knowledge, upload)
            result = execute_knowledge(args, knowledge, media_service=media_service, upload_service=upload, url_service=url_service)
            return emit_command_result(command_name, result, as_json=as_json)
        if args.command == "resolve":
            return emit_command_result(command_name, execute_resolve(args, resolver), as_json=as_json)
        if args.command == "alias":
            return emit_command_result(command_name, execute_alias(args, resolver, aliases), as_json=as_json)
        raise InputError("Unknown command.")
    except KeyboardInterrupt:
        error = ImaCliError("Interrupted.", code="interrupted", exit_code=130)
    except ImaCliError as exc:
        error = exc
    except Exception:
        error = ImaCliError("An unexpected internal error occurred.", code="internal_error", exit_code=70)
    if as_json:
        emit_json_error(command_name, error)
    else:
        emit_human_error(error)
    return error.exit_code


def _command_name(argv: Sequence[str]) -> str:
    values = [value for value in argv if not value.startswith("-")]
    if not values:
        return "cli"
    if values[0] in {"note", "kb", "alias"} and len(values) > 1:
        return f"{values[0]}.{values[1]}"
    return values[0] if values[0] in {"auth", "resolve"} else "cli"


def _command_name_from_args(args: argparse.Namespace) -> str:
    if args.command == "note":
        return f"note.{args.note_action}"
    if args.command == "kb":
        return f"kb.{args.kb_action}"
    if args.command == "alias":
        return f"alias.{args.alias_action}"
    return args.command


def run_note_legacy(argv: Sequence[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else list(sys.argv[1:])
    if argv_list and argv_list[0] == "auth":
        return run(argv_list)
    return run(["note", *argv_list])


def auth_result(status: CredentialStatus) -> CommandResult:
    environment_check = inspect_runtime_environment()
    payload = {
        "configured": status.is_configured,
        "credentials": {
            "IMA_OPENAPI_CLIENTID": {
                "set": bool(status.client_id),
                "source": status.client_id_source,
            },
            "IMA_OPENAPI_APIKEY": {
                "set": bool(status.api_key),
                "source": status.api_key_source,
            },
        },
        "environment_check": environment_check,
    }

    lines = [f"Status: {'configured' if status.is_configured else 'missing credentials'}"]
    lines.append(
        "IMA_OPENAPI_CLIENTID: "
        f"{'set' if status.client_id else 'missing'}"
        f"{format_source_suffix(status.client_id_source)}"
    )
    lines.append(
        "IMA_OPENAPI_APIKEY: "
        f"{'set' if status.api_key else 'missing'}"
        f"{format_source_suffix(status.api_key_source)}"
    )
    if environment_check["platform"] == "windows" and not environment_check["ok"]:
        lines.extend(environment_check_lines(environment_check))
    warnings = () if status.is_configured else ("Configure the missing values in the environment, project .env, or ~/.config/ima files.",)
    return CommandResult(payload, tuple(lines), warnings)


def format_source_suffix(source: str | None) -> str:
    if not source:
        return ""
    return f" ({source})"


def inspect_runtime_environment() -> dict[str, object]:
    platform_name = "windows" if sys.platform.startswith("win") else sys.platform
    if platform_name != "windows":
        return {
            "platform": platform_name,
            "shell": "unknown",
            "ok": True,
            "missing": [],
        }

    missing: list[str] = []
    if os.environ.get("PYTHONUTF8") != "1":
        missing.append("PYTHONUTF8")

    pythonioencoding = os.environ.get("PYTHONIOENCODING", "")
    if pythonioencoding.lower() != "utf-8":
        missing.append("PYTHONIOENCODING")

    return {
        "platform": "windows",
        "shell": detect_windows_shell(),
        "ok": not missing,
        "missing": missing,
    }


def detect_windows_shell() -> str:
    if os.environ.get("PSModulePath"):
        return "powershell"

    comspec_name = Path(os.environ.get("ComSpec", "")).name.lower()
    if comspec_name == "cmd.exe":
        return "cmd"
    return "unknown"


def environment_check_lines(environment_check: dict[str, object]) -> list[str]:
    lines = ["", "Environment: warning", "Windows terminal encoding may cause garbled output."]
    if environment_check["shell"] == "powershell":
        lines.append('Set PowerShell session variables: `$env:PYTHONUTF8="1"` and `$env:PYTHONIOENCODING="utf-8"`')
    elif environment_check["shell"] == "cmd":
        lines.append("Or in CMD: `set PYTHONUTF8=1` and `set PYTHONIOENCODING=utf-8`")
    else:
        lines.extend(['Set PowerShell session variables: `$env:PYTHONUTF8="1"` and `$env:PYTHONIOENCODING="utf-8"`', "Or in CMD: `set PYTHONUTF8=1` and `set PYTHONIOENCODING=utf-8`"])
    return lines


def handle_auth(status: CredentialStatus, as_json: bool) -> int:
    """Compatibility façade for callers that used the pre-batch-C handler."""
    return emit_command_result("auth", auth_result(status), as_json=as_json)
