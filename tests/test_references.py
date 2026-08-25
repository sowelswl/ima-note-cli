from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests._bootstrap import ROOT  # noqa: F401
from ima_note_cli.aliases import AliasRecord, AliasStore, account_fingerprint
from ima_note_cli.errors import InputError, ReferenceError
from ima_note_cli.knowledge_api import KnowledgeBaseSummary, KnowledgeEntry
from ima_note_cli.notes_api import FolderResult, SearchResult
from ima_note_cli.references import ResourceResolver, parse_reference


def note(note_id: str, title: str) -> SearchResult:
    return SearchResult(note_id=note_id, title=title)


def folder(folder_id: str, name: str, parent: str = "") -> FolderResult:
    return FolderResult(folder_id, name, None, None, None, None, parent)


class FakeNotes:
    def __init__(self) -> None:
        self.search_pages: dict[int, dict] = {}
        self.folder_pages: dict[str, dict] = {}
        self.search_calls: list[tuple] = []

    def search_notes(self, query, limit, *, start=0, search_type=0, sort_type=0):
        self.search_calls.append((query, limit, start, search_type, sort_type))
        return self.search_pages[start]

    def list_folders(self, limit, *, cursor="0", version=None):
        return self.folder_pages[cursor]


class FakeKnowledge:
    def __init__(self) -> None:
        self.base_pages: dict[str, dict] = {}
        self.addable_pages: dict[str, dict] = {}
        self.browse_pages: dict[tuple[str, str, str], dict] = {}
        self.base_calls = 0
        self.addable_calls = 0

    def search_knowledge_bases(self, query, limit, *, cursor=""):
        self.base_calls += 1
        return self.base_pages[cursor]

    def list_addable_knowledge_bases(self, limit, *, cursor=""):
        self.addable_calls += 1
        return self.addable_pages[cursor]

    def list_knowledge(self, knowledge_base_id, limit, *, cursor="", folder_id=None):
        return self.browse_pages[(knowledge_base_id, folder_id or "", cursor)]


class ResourceResolverTests(unittest.TestCase):
    def resolver(self, root: Path, notes=None, knowledge=None, *, max_pages=100) -> ResourceResolver:
        store = AliasStore(root / "aliases.json", account_fingerprint("client-one"))
        return ResourceResolver(aliases=store, notes=notes, knowledge=knowledge, max_pages=max_pages)

    def test_reference_grammar_is_explicit(self) -> None:
        self.assertEqual(parse_reference("plain-id"), ("id", "plain-id"))
        self.assertEqual(parse_reference(" Research ", bare_is_name=True), ("name", "Research"))
        self.assertEqual(parse_reference("name:Research"), ("name", "Research"))
        self.assertEqual(parse_reference("alias:work"), ("alias", "work"))
        for value in ("", "name:", "fuzzy:Research"):
            with self.subTest(value=value), self.assertRaises(InputError):
                parse_reference(value)

    def test_exact_name_resolution_covers_all_resource_types(self) -> None:
        notes = FakeNotes()
        notes.search_pages[0] = {"docs": [note("note-1", "Plan")], "is_end": True}
        notes.folder_pages["0"] = {"folders": [folder("nf-1", "Work")], "next_cursor": "", "is_end": True}

        knowledge = FakeKnowledge()
        knowledge.base_pages[""] = {
            "knowledge_bases": [KnowledgeBaseSummary("kb-1", "Research", "")],
            "next_cursor": "", "is_end": True,
        }
        knowledge.browse_pages[("kb-1", "", "")] = {
            "items": [KnowledgeEntry("folder", "kf-1", "Sources", "", "", 1, 1, False)],
            "next_cursor": "", "is_end": True,
        }
        knowledge.browse_pages[("kb-1", "kf-1", "")] = {
            "items": [
                KnowledgeEntry("folder", "kf-2", "Archive", "kf-1", "", 0, 0, False),
                KnowledgeEntry("file", "media-1", "paper.pdf", "kf-1", "", None, None, None),
            ],
            "next_cursor": "", "is_end": True,
        }
        knowledge.browse_pages[("kb-1", "kf-2", "")] = {"items": [], "next_cursor": "", "is_end": True}

        with TemporaryDirectory() as tmp_dir:
            resolver = self.resolver(Path(tmp_dir), notes, knowledge)
            cases = [
                ("kb", "Research", {}, "kb-1"),
                ("note", "Plan", {}, "note-1"),
                ("note-folder", "Work", {}, "nf-1"),
                ("kb-folder", "Sources", {"kb_id": "kb-1"}, "kf-1"),
                ("media", "paper.pdf", {"kb_id": "kb-1"}, "media-1"),
            ]
            for resource_type, value, kwargs, expected in cases:
                with self.subTest(resource_type=resource_type):
                    result = resolver.resolve(resource_type, value, bare_is_name=True, **kwargs)
                    self.assertEqual((result.target_id, result.source), (expected, "name"))

    def test_ambiguity_not_found_and_incomplete_scans_never_choose(self) -> None:
        notes = FakeNotes()
        notes.search_pages[0] = {
            "docs": [note("note-1", "Plan"), note("note-2", "Plan")],
            "is_end": True,
        }
        with TemporaryDirectory() as tmp_dir:
            resolver = self.resolver(Path(tmp_dir), notes=notes)
            with self.assertRaises(ReferenceError) as ambiguous:
                resolver.resolve("note", "name:Plan")
            self.assertEqual(ambiguous.exception.code, "ambiguous_reference")
            self.assertEqual(ambiguous.exception.candidate_count, 2)

            notes.search_pages[0] = {"docs": [], "is_end": True}
            with self.assertRaises(ReferenceError) as missing:
                resolver.resolve("note", "name:Missing")
            self.assertEqual(missing.exception.code, "reference_not_found")

            notes.search_pages[0] = {"docs": [note("note-1", "Plan")], "is_end": False}
            capped = self.resolver(Path(tmp_dir), notes=notes, max_pages=1)
            with self.assertRaises(ReferenceError) as incomplete:
                capped.resolve("note", "name:Plan")
            self.assertEqual((incomplete.exception.code, incomplete.exception.candidate_count), ("resolution_incomplete", 1))

    def test_exact_kb_resolution_scans_every_page_before_deciding(self) -> None:
        knowledge = FakeKnowledge()
        knowledge.base_pages[""] = {
            "knowledge_bases": [KnowledgeBaseSummary("kb-1", "Research", "")],
            "next_cursor": "page-2", "is_end": False,
        }
        knowledge.base_pages["page-2"] = {
            "knowledge_bases": [KnowledgeBaseSummary("kb-2", "Research", "")],
            "next_cursor": "", "is_end": True,
        }
        with TemporaryDirectory() as tmp_dir:
            resolver = self.resolver(Path(tmp_dir), knowledge=knowledge)
            with self.assertRaises(ReferenceError) as ambiguous:
                resolver.resolve("kb", "name:Research")
            self.assertEqual((ambiguous.exception.code, ambiguous.exception.candidate_count), ("ambiguous_reference", 2))
            self.assertEqual(knowledge.base_calls, 2)

            knowledge.base_pages[""] = {
                "knowledge_bases": [KnowledgeBaseSummary("kb-1", "research", "")],
                "next_cursor": "", "is_end": True,
            }
            with self.assertRaises(ReferenceError) as case_mismatch:
                resolver.resolve("kb", "name:Research")
            self.assertEqual(case_mismatch.exception.code, "reference_not_found")

    def test_media_resolution_detects_duplicates_across_folders(self) -> None:
        knowledge = FakeKnowledge()
        knowledge.browse_pages[("kb-1", "", "")] = {
            "items": [
                KnowledgeEntry("folder", "folder-a", "A", "", "", 1, 0, False),
                KnowledgeEntry("folder", "folder-b", "B", "", "", 1, 0, False),
            ],
            "next_cursor": "", "is_end": True,
        }
        for folder_id, media_id in (("folder-a", "media-a"), ("folder-b", "media-b")):
            knowledge.browse_pages[("kb-1", folder_id, "")] = {
                "items": [KnowledgeEntry("file", media_id, "paper.pdf", folder_id, "", None, None, None)],
                "next_cursor": "", "is_end": True,
            }
        with TemporaryDirectory() as tmp_dir:
            resolver = self.resolver(Path(tmp_dir), knowledge=knowledge)
            with self.assertRaises(ReferenceError) as ambiguous:
                resolver.resolve("media", "name:paper.pdf", kb_id="kb-1")
        self.assertEqual((ambiguous.exception.code, ambiguous.exception.candidate_count), ("ambiguous_reference", 2))
        self.assertEqual({item["parent_folder_id"] for item in ambiguous.exception.candidates}, {"folder-a", "folder-b"})

    def test_alias_scope_direct_ids_and_write_kb_discovery(self) -> None:
        knowledge = FakeKnowledge()
        knowledge.addable_pages[""] = {
            "knowledge_bases": [KnowledgeBaseSummary("kb-write", "Writable", "")],
            "next_cursor": "", "is_end": True,
        }
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            resolver = self.resolver(root, knowledge=knowledge)
            resolver.aliases.set(AliasRecord("media", "paper", "media-1", "paper.pdf", {"kb_id": "kb-1"}))

            self.assertEqual(resolver.resolve("media", "alias:paper", kb_id="kb-1").target_id, "media-1")
            with self.assertRaises(ReferenceError) as mismatch:
                resolver.resolve("media", "alias:paper", kb_id="kb-2")
            self.assertEqual(mismatch.exception.code, "alias_scope_mismatch")
            self.assertEqual(resolver.resolve("kb", "id:kb-direct").target_id, "kb-direct")
            self.assertEqual(knowledge.base_calls, 0)

            result = resolver.resolve("kb", "name:Writable", for_write=True)
            self.assertEqual(result.target_id, "kb-write")
            self.assertEqual((knowledge.addable_calls, knowledge.base_calls), (1, 0))


if __name__ == "__main__":
    unittest.main()
