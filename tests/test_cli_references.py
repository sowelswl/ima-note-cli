from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests._bootstrap import ROOT  # noqa: F401
from ima_note_cli.aliases import AliasRecord, AliasStore, account_fingerprint
from ima_note_cli.cli import build_parser, run
from ima_note_cli.config import CredentialStatus
from ima_note_cli.knowledge_api import KnowledgeBaseSummary, KnowledgeEntry, MediaInfo
from ima_note_cli.notes_api import FolderResult, SearchResult


class ReferenceNotesClient:
    def __init__(self) -> None:
        self.search_docs = [SearchResult(note_id="note-1", title="Plan")]
        self.folders = [FolderResult("nf-1", "Work", None, None, None, None, "")]
        self.last_get_id = ""
        self.last_list_folder = ""

    def search_notes(self, query, limit, *, start=0, search_type=0, sort_type=0):
        return {"docs": self.search_docs, "total_hit_num": len(self.search_docs), "is_end": True, "start": start}

    def list_folders(self, limit, *, cursor="0"):
        return {"folders": self.folders, "next_cursor": "", "is_end": True}

    def get_doc_content(self, note_id):
        self.last_get_id = note_id
        return {"note_id": note_id, "doc_id": note_id, "content": "body"}

    def list_notes(self, limit, *, folder_id="", cursor="", sort_type=0):
        self.last_list_folder = folder_id
        return {"notes": self.search_docs, "next_cursor": "", "is_end": True, "folder_id": folder_id}


class ReferenceKnowledgeClient:
    def __init__(self) -> None:
        self.bases = [KnowledgeBaseSummary("kb-1", "Research", "")]
        self.addable = list(self.bases)
        self.add_note_calls = 0
        self.browse_calls: list[tuple[str, str]] = []

    def search_knowledge_bases(self, query, limit, *, cursor=""):
        return {"knowledge_bases": self.bases, "next_cursor": "", "is_end": True}

    def list_addable_knowledge_bases(self, limit, *, cursor=""):
        return {"knowledge_bases": self.addable, "next_cursor": "", "is_end": True}

    def list_knowledge(self, knowledge_base_id, limit, *, cursor="", folder_id=None):
        current = folder_id or ""
        self.browse_calls.append((knowledge_base_id, current))
        if not current:
            items = [KnowledgeEntry("folder", "kf-1", "Sources", "", "", 1, 0, False)]
        else:
            items = [KnowledgeEntry("file", "media-1", "paper.pdf", "kf-1", "", None, None, None)]
        return {"items": items, "next_cursor": "", "is_end": True, "current_path": []}

    def search_knowledge(self, query, knowledge_base_id, *, cursor=""):
        return {
            "items": [KnowledgeEntry("file", "media-1", "paper.pdf", "kf-1", "", None, None, None)],
            "next_cursor": "", "is_end": True,
        }

    def add_note(self, knowledge_base_id, note_id, *, title, folder_id=None):
        self.add_note_calls += 1
        return {
            "media_id": "media-added", "knowledge_base_id": knowledge_base_id,
            "note_id": note_id, "doc_id": note_id, "title": title, "folder_id": folder_id or "",
        }


class ReferenceMediaService:
    def __init__(self) -> None:
        self.last_media_id = ""

    def inspect_media(self, media_id):
        self.last_media_id = media_id
        return MediaInfo(media_id, 13, "unavailable")


class CliReferenceTests(unittest.TestCase):
    @staticmethod
    def configured() -> CredentialStatus:
        return CredentialStatus("client", "key", "environment", "environment")

    def invoke(self, argv, notes, knowledge, store):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("ima_note_cli.cli.inspect_credentials", return_value=self.configured()), patch(
            "ima_note_cli.cli.NotesApiClient", return_value=notes,
        ), patch("ima_note_cli.cli.KnowledgeBaseApiClient", return_value=knowledge), patch(
            "ima_note_cli.cli.AliasStore.for_client_id", return_value=store,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = run(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_all_id_consuming_commands_expose_compatible_reference_options(self) -> None:
        parser = build_parser()
        cases = [
            (["note", "get", "--note", "id:note-1"], "note_ref"),
            (["note", "create", "--folder", "id:nf-1", "--content", "x"], "folder_ref"),
            (["note", "append", "--note", "id:note-1", "--content", "x"], "note_ref"),
            (["note", "list", "--folder", "id:nf-1"], "folder_ref"),
            (["kb", "show-base", "--kb", "id:kb-1"], "kb_ref"),
            (["kb", "browse", "--kb", "id:kb-1", "--folder", "id:kf-1"], "folder_ref"),
            (["kb", "search", "x", "--kb", "id:kb-1"], "kb_refs"),
            (["kb", "add-note", "--kb", "id:kb-1", "--note", "id:note-1"], "note_ref"),
            (["kb", "add-url", "--kb", "id:kb-1", "--url", "https://example.com"], "kb_ref"),
            (["kb", "add-file", "--kb", "id:kb-1", "--file", "x.pdf"], "kb_ref"),
            (["kb", "media-info", "--media", "id:media-1"], "media_ref"),
            (["kb", "read", "--media", "id:media-1"], "media_ref"),
            (["kb", "export", "--media", "id:media-1", "--output", "x.bin"], "media_ref"),
        ]
        for argv, attribute in cases:
            with self.subTest(argv=argv):
                self.assertTrue(getattr(parser.parse_args(argv), attribute))

    def test_resolve_and_generic_note_and_folder_options(self) -> None:
        notes, knowledge = ReferenceNotesClient(), ReferenceKnowledgeClient()
        with TemporaryDirectory() as tmp_dir:
            store = AliasStore(Path(tmp_dir) / "aliases.json", account_fingerprint("client"))
            code, output, error = self.invoke(["resolve", "kb", "Research", "--json"], notes, knowledge, store)
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["resource"]["id"], "kb-1")

            code, _, _ = self.invoke(["note", "get", "--note", "name:Plan"], notes, knowledge, store)
            self.assertEqual(code, 0)
            self.assertEqual(notes.last_get_id, "note-1")

            code, _, _ = self.invoke(["note", "list", "--folder", "name:Work"], notes, knowledge, store)
            self.assertEqual(code, 0)
            self.assertEqual(notes.last_list_folder, "nf-1")

    def test_legacy_id_path_does_not_read_alias_configuration(self) -> None:
        notes, knowledge = ReferenceNotesClient(), ReferenceKnowledgeClient()
        unavailable_store = AliasStore(None, account_fingerprint("client"))
        code, output, error = self.invoke(
            ["note", "get", "note-legacy", "--json"], notes, knowledge, unavailable_store,
        )
        self.assertEqual((code, error, notes.last_get_id), (0, "", "note-legacy"))
        self.assertEqual(json.loads(output)["note_id"], "note-legacy")

    def test_kb_folder_media_and_alias_workflows_use_scoped_ids(self) -> None:
        notes, knowledge = ReferenceNotesClient(), ReferenceKnowledgeClient()
        with TemporaryDirectory() as tmp_dir:
            store = AliasStore(Path(tmp_dir) / "aliases.json", account_fingerprint("client"))
            code, _, _ = self.invoke(
                ["kb", "browse", "--kb", "name:Research", "--folder", "name:Sources"],
                notes, knowledge, store,
            )
            self.assertEqual(code, 0)
            self.assertEqual(knowledge.browse_calls[-1], ("kb-1", "kf-1"))

            code, output, _ = self.invoke(
                ["resolve", "media", "paper.pdf", "--kb", "name:Research", "--json"],
                notes, knowledge, store,
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["resource"]["id"], "media-1")

            media_service = ReferenceMediaService()
            with patch("ima_note_cli.cli.MediaContentService", return_value=media_service):
                code, _, _ = self.invoke(
                    ["kb", "media-info", "--media", "name:paper.pdf", "--kb", "id:kb-1"],
                    notes, knowledge, store,
                )
            self.assertEqual((code, media_service.last_media_id), (0, "media-1"))

            store.set(AliasRecord("media", "paper", "media-1", "paper.pdf", {"kb_id": "kb-1"}))
            with patch("ima_note_cli.cli.MediaContentService", return_value=media_service):
                code, _, _ = self.invoke(
                    ["kb", "media-info", "--media", "alias:paper", "--kb-id", " kb-1 "],
                    notes, knowledge, store,
                )
            self.assertEqual((code, media_service.last_media_id), (0, "media-1"))

            code, _, _ = self.invoke(
                ["alias", "set", "kb.research", "id:kb-1", "--json"], notes, knowledge, store,
            )
            self.assertEqual(code, 0)
            code, _, _ = self.invoke(["kb", "browse", "--kb", "alias:research"], notes, knowledge, store)
            self.assertEqual(code, 0)
            code, output, _ = self.invoke(["alias", "list", "--type", "kb", "--json"], notes, knowledge, store)
            self.assertEqual(json.loads(output)["aliases"][0]["id"], "kb-1")
            code, _, _ = self.invoke(["alias", "unset", "kb.research"], notes, knowledge, store)
            self.assertEqual(code, 0)

    def test_write_command_resolves_every_generic_target_before_dispatch(self) -> None:
        notes, knowledge = ReferenceNotesClient(), ReferenceKnowledgeClient()
        with TemporaryDirectory() as tmp_dir:
            store = AliasStore(Path(tmp_dir) / "aliases.json", account_fingerprint("client"))
            code, output, error = self.invoke(
                [
                    "kb", "add-note", "--kb", "name:Research", "--folder", "name:Sources",
                    "--note", "name:Plan", "--json",
                ],
                notes, knowledge, store,
            )
        self.assertEqual((code, error, knowledge.add_note_calls), (0, "", 1))
        payload = json.loads(output)
        self.assertEqual((payload["knowledge_base_id"], payload["note_id"], payload["folder_id"]), ("kb-1", "note-1", "kf-1"))

    def test_ambiguous_write_target_fails_before_write_api(self) -> None:
        notes, knowledge = ReferenceNotesClient(), ReferenceKnowledgeClient()
        knowledge.addable = [
            KnowledgeBaseSummary("kb-1", "Research", ""),
            KnowledgeBaseSummary("kb-2", "Research", ""),
        ]
        with TemporaryDirectory() as tmp_dir:
            store = AliasStore(Path(tmp_dir) / "aliases.json", account_fingerprint("client"))
            code, output, error = self.invoke(
                ["kb", "add-note", "--kb", "name:Research", "--note-id", "note-1", "--json"],
                notes, knowledge, store,
            )
        parsed = json.loads(output)
        self.assertEqual((code, error, knowledge.add_note_calls), (2, "", 0))
        self.assertEqual(parsed["error"]["code"], "ambiguous_reference")
        self.assertEqual(parsed["error"]["candidate_count"], 2)


if __name__ == "__main__":
    unittest.main()
