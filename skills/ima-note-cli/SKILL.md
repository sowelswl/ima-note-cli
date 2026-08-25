---
name: ima-note-cli
description: Safely install, configure, troubleshoot, and use the `ima` Python CLI for IMA Notes and Knowledge workflows, including typed IDs, exact names, aliases, reads, persistent writes, media, imports/uploads, JSON automation, pagination, credential diagnostics, and legacy compatibility.
---

# Use the IMA CLI

## Start quickly

1. Install the CLI with `uv tool install git+https://github.com/Aimer779/ima-note-cli`.
2. Verify it with `ima --help`.
3. Configure credentials without printing their values.
4. Check configuration with `ima auth` or `ima auth --json`.
5. Inspect exact current arguments with `ima <group> <command> --help` before a consequential write.

Use `uv run python -m ima_note_cli ...` instead of `ima ...` when working from an uninstalled source checkout.

## Keep CLI and skill installation separate

Treat `uv tool install` as installation of the Python CLI only. It does not install this agent skill. Use or link `skills/ima-note-cli` separately from a repository checkout. Do not claim that this skill is bundled in the wheel.

## Configure credentials safely

Require `IMA_OPENAPI_CLIENTID` and `IMA_OPENAPI_APIKEY`. Resolve each field independently in this order:

1. Process environment.
2. `.env` in the current working directory.
3. `~/.config/ima/client_id` or `~/.config/ima/api_key`.

Prefer environment variables for a globally installed CLI. Never request, echo, log, or embed real credential values in commands. Use `setx` only when persistent Windows configuration is requested, and remind the user to open a new terminal. Use `ima auth` as the first credential diagnostic.

## Resolve resources without copying IDs

Use explicit typed references with the generic `--kb`, `--note`, `--folder`, and `--media` options:

- `id:VALUE` is an explicit canonical ID.
- `alias:VALUE` is an account-bound local alias.
- `name:VALUE` performs a case-sensitive exact-name match after trimming the input.

Bare values passed to generic options remain IDs. Never assume that a failed ID is a name, request fuzzy matching, or select the first match. Resolution scans at most 100 candidate pages; zero matches, multiple exact matches, and incomplete scans must fail before a read or write continues. Inspect structured `candidates` in JSON ambiguity errors.

Use all four reference-management commands:

- `ima resolve kb "AI Research" --json` resolves a bare exact name to its canonical ID.
- `ima alias set kb.research id:KB_ID` creates a typed alias; add `--force` only after confirming replacement.
- `ima alias list --type kb --json` lists aliases for the configured account.
- `ima alias unset kb.research` removes one alias.

Let `ima alias` manage aliases; it stores them in `~/.config/ima/aliases.json` with atomic writes. Use 1–64 ASCII letters, digits, dots, underscores, or hyphens, starting with a letter or digit. The file contains resource IDs and a non-secret account fingerprint, never credentials. Require `--kb` or `--kb-id` for `name:` resolution of `kb-folder` and `media`. Let scoped aliases carry their stored KB; when a caller supplies a scope, require it to match instead of transplanting the alias.

Prefer generic references for repeat targets, for example `ima kb browse --kb alias:research`, `ima note get --note "name:Weekly Plan"`, and `ima kb add-note --kb alias:research --note "name:Weekly Plan"`. Existing positional IDs and `--kb-id`, `--note-id`, `--folder-id`, and `--media-id` remain pure-ID compatibility paths. All remote writes resolve every target before producing a side effect.

## Gate persistent writes

Resolve every generic target before writing. Stop on zero matches, ambiguity, scope mismatch, or incomplete discovery; never turn a resolution failure into “use the first result.” Confirm the destination and content before create, append, import, or upload.

Treat remote writes as persistent. The CLI has no delete workflow, so run a write smoke test only with an explicitly disposable target and agreement to leave or manually remove test data. Do not blindly retry an uncertain write; inspect JSON IDs, item stages, and errors first because a request may have succeeded or an upload may have left orphaned media.

## Work with Notes

Use all six Note commands:

- `ima note search QUERY` to search titles or content.
- `ima note folders` to list notebooks.
- `ima note list` to list notes, optionally under a folder.
- `ima note get NOTE_ID` to read one note.
- `ima note create --title TITLE --content TEXT` to create one note.
- `ima note append NOTE_ID --content TEXT` to append to one note.

Use `note_id` as the canonical identifier. Select notes with search/list metadata and read full note content only when the user requests it; do not expose private content during diagnostics. Prefer `--file` for substantial Markdown input. Let the CLI validate UTF-8 and remove unsafe local, data, and non-HTTP(S) image references before writes.

## Work with Knowledge bases

Use the eleven Knowledge commands by intent:

- Discover and read metadata with `ima kb search-base`, `ima kb show-base`, `ima kb addable`, `ima kb browse`, and `ima kb search`.
- Write with `ima kb add-note`, `ima kb add-url`, and `ima kb add-file`.
- Inspect or retrieve originals with `ima kb media-info`, `ima kb read`, and `ima kb export`.

Repeat `--file` for a multi-file upload. Local HTML files are limited to 10 MiB and EPUB files to 50 MiB. Use `--on-conflict error` by default and use `--on-conflict rename` only when automatic renaming is acceptable. Set `--download-timeout` and `--upload-timeout` when network conditions require explicit bounds.

## Preserve URL and upload safety

Let `ima kb add-url` classify supported public web pages and remote files. Do not bypass its SSRF, redirect, DNS, scheme, port, or size checks. Unsupported video hosts fail before network access. Remote supported files are downloaded with bounded streaming and uploaded through the same guarded workflow as local files.

Let the CLI complete local type/size/name checks and the whole-batch initial conflict check before `create_media`. Preserve its gate order through COS upload, file identity recheck, and `add_knowledge`; do not call lower-level stages directly. Do not automatically retry write requests or COS PUTs.

Do not recommend direct raw API calls, archived Node/CJS scripts, arbitrary service base URLs, or self-updating skill code. Send long-lived IMA credentials only through the CLI's official-host client. Treat signed COS URLs, signed knowledge-base cover queries, and temporary headers as secrets.

Use `media-info` for redacted metadata. Use `read` only for explicit textual MIME types up to 4 MiB. Use `export` for originals up to 200 MiB; it refuses overwrite unless `--force` is explicit and writes atomically.

## Paginate and automate

Add `--json` for machine-readable output. Expect one JSON document containing `schema_version`, `ok`, `status`, `command`, `warnings`, and command data or a stable error. Keep stderr empty for JSON failures.

Use `--all --max-pages N` for bounded multi-page list/search operations. For `ima note list`, prefer `--all` and never invent a cursor from a note ID. If the service omits `next_cursor`, let the CLI advance only from an empty or canonical decimal request cursor and a nonempty page; explicit empty/null response cursors and opaque or oversized values remain no-progress failures.

Repeat `--kb-id` or `--kb` for 1–20 selected bases, or use `--all-bases --max-bases N` for bounded discovery. Use `--cursor` only when exactly one `--kb` or `--kb-id` is selected; cursors are base-specific. Cross-base results are grouped by knowledge base and are not globally reranked. A page cap, per-base failure, or mixed batch can produce partial output. Interpret exit code 9 as partial or itemized batch failure and inspect `knowledge_bases`, `results`, `summary`, and each error or stage. Exit code 75 means a temporary failure and may be retried with bounded backoff; in a batch, retry only failed items whose error is marked `retryable=true`.

Recognize the remaining exit codes: 0 success, 2 input, 3 configuration, 4 non-temporary network, 5 IMA business/authentication, 6 protocol, 7 local/original-content I/O, 8 upload, 70 internal, 75 temporary failure, and 130 interruption.

## Maintain legacy compatibility

Recommend `ima` as the formal entry point. Treat `ima-note` as a legacy note-only executable that remains available. Treat `--doc-id` and the equal JSON `doc_id` field only as deprecated compatibility for canonical `--note-id` and `note_id`; do not present them as current API fields.

## Troubleshoot in order

1. Run `ima --help` to distinguish installation or PATH failures.
2. Run `ima auth` without exposing values.
3. Run `ima resolve TYPE REFERENCE --json` to separate resource-selection failures from downstream API failures.
4. Run a minimal read such as `ima note search "test"`; avoid reading note content unless requested.
5. Use the command's `--help` output as the argument truth source.
6. In a checkout, run `uv run python -m unittest discover -s tests -v` only when code diagnostics are needed.

On Windows encoding failures, set `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` for the relevant shell, then retry in a new terminal if persistent variables were set.
