from __future__ import annotations

import copy
import unittest

from tests._bootstrap import ROOT  # noqa: F401
from ima_note_cli.http import ApiError
from ima_note_cli.notes_api import NotesApiClient, SearchResult
from ima_note_cli.api import ImaNoteApiClient

from tests._fixtures import load_data


class RecordingNotesClient(NotesApiClient):
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def _record(self, endpoint: str, payload: dict):
        self.calls.append((endpoint, payload))
        return copy.deepcopy(self.responses[endpoint])

    post_read_json = _record
    post_write_json = _record


class NotesApiContractTests(unittest.TestCase):
    def test_search_endpoint_payload_and_new_response_shape(self):
        client = RecordingNotesClient({"search_note": load_data("notes/search_note_success.json")})
        result = client.search_notes("  项目  ", 20, start=2, search_type=0, sort_type=1)
        self.assertEqual(client.calls, [("search_note", {"search_type": 0, "sort_type": 1, "query_info": {"title": "项目"}, "start": 2, "end": 22})])
        note = result["docs"][0]
        self.assertEqual(note.note_id, "note_test_001")
        self.assertEqual(note.doc_id, note.note_id)
        self.assertEqual(note.folder_id, "folder_test_001")
        self.assertEqual(note.highlight_title, "<em>项目</em>计划")
        self.assertEqual(note.cover_image, "https://example.com/cover.png")

    def test_content_search_and_input_validation(self):
        client = RecordingNotesClient({"search_note": load_data("notes/search_note_empty.json")})
        client.search_notes("正文", 1, search_type=1, sort_type=3)
        self.assertEqual(client.calls[0][1]["query_info"], {"content": "正文"})
        invalid = [
            lambda: client.search_notes("", 1),
            lambda: client.search_notes("x", 0),
            lambda: client.search_notes("x", 21),
            lambda: client.search_notes("x", 1, start=-1),
            lambda: client.search_notes("x", 1, search_type=2),
            lambda: client.search_notes("x", 1, sort_type=4),
        ]
        for call in invalid:
            with self.assertRaises(ValueError):
                call()

    def test_search_rejects_missing_note_id_and_wrong_collection_shape(self):
        data = load_data("notes/search_note_success.json")
        del data["search_note_infos"][0]["note_book_info"]["note_id"]
        with self.assertRaisesRegex(ApiError, "note_id"):
            RecordingNotesClient({"search_note": data}).search_notes("x", 1)
        with self.assertRaisesRegex(ApiError, "expected an array"):
            RecordingNotesClient({"search_note": {"search_note_infos": {}}}).search_notes("x", 1)

    def test_list_notebooks_supports_version_and_parses_metadata(self):
        client = RecordingNotesClient({"list_notebook": load_data("notes/list_notebook_success.json")})
        result = client.list_folders(20, version="version_test_001")
        self.assertEqual(client.calls[0], ("list_notebook", {"cursor": "0", "limit": 20, "version": "version_test_001"}))
        self.assertEqual(result["folders"][0].folder_id, "folder_test_001")
        self.assertEqual(result["next_version"], "version_test_002")
        self.assertTrue(result["need_update"])

    def test_list_notes_uses_flat_entries_and_preserves_optional_cursor(self):
        data = load_data("notes/list_note_success.json")
        client = RecordingNotesClient({"list_note": data})
        result = client.list_notes(5, folder_id="folder_test_001", cursor="cursor", sort_type=2)
        self.assertEqual(client.calls[0], ("list_note", {"folder_id": "folder_test_001", "sort_type": 2, "cursor": "cursor", "limit": 5}))
        self.assertEqual(result["notes"][0].note_id, "note_test_001")
        self.assertEqual(result["next_cursor"], "")
        data["next_cursor"] = "cursor_next"
        self.assertEqual(RecordingNotesClient({"list_note": data}).list_notes(1)["next_cursor"], "cursor_next")
        with self.assertRaises(ValueError):
            client.list_notes(1, folder_id="0")

    def test_list_notes_synthesizes_missing_offset_cursor_without_overriding_server_cursor(self):
        data = load_data("notes/list_note_success.json")
        data["is_end"] = False
        first = RecordingNotesClient({"list_note": data}).list_notes(1)
        self.assertEqual(first["next_cursor"], "1")

        later = RecordingNotesClient({"list_note": data}).list_notes(1, cursor="3")
        self.assertEqual(later["next_cursor"], "4")

        data["next_cursor"] = "server_cursor"
        explicit = RecordingNotesClient({"list_note": data}).list_notes(1, cursor="3")
        self.assertEqual(explicit["next_cursor"], "server_cursor")

        data["next_cursor"] = ""
        explicit_empty = RecordingNotesClient({"list_note": data}).list_notes(1, cursor="3")
        self.assertEqual(explicit_empty["next_cursor"], "")

        data["next_cursor"] = None
        explicit_null = RecordingNotesClient({"list_note": data}).list_notes(1, cursor="3")
        self.assertEqual(explicit_null["next_cursor"], "")

        del data["next_cursor"]
        for cursor in ("opaque", "03", "٣", "9" * 5000):
            with self.subTest(cursor_length=len(cursor)):
                refused = RecordingNotesClient({"list_note": data}).list_notes(1, cursor=cursor)
                self.assertEqual(refused["next_cursor"], "")

        data["note_book_list"] = []
        empty_page = RecordingNotesClient({"list_note": data}).list_notes(1, cursor="3")
        self.assertEqual(empty_page["next_cursor"], "")

    def test_note_int64_fields_accept_decimal_string_wire_values(self):
        search_data = load_data("notes/search_note_success.json")
        search_info = search_data["search_note_infos"][0]["note_book_info"]
        search_data["total_hit_num"] = "1"
        search_info["create_time"] = "1700000000000"
        search_info["modify_time"] = "1700000100000"
        search_result = RecordingNotesClient({"search_note": search_data}).search_notes("项目", 1)
        self.assertEqual(search_result["total_hit_num"], 1)
        self.assertEqual(search_result["docs"][0].create_time, 1700000000000)
        self.assertEqual(search_result["docs"][0].modify_time, 1700000100000)

        folder_data = load_data("notes/list_notebook_success.json")
        folder_info = folder_data["note_folder_infos"][0]
        folder_info["note_number"] = "2"
        folder_info["create_time"] = "1700000000000"
        folder_info["modify_time"] = "1700000100000"
        folder = RecordingNotesClient({"list_notebook": folder_data}).list_folders(1)["folders"][0]
        self.assertEqual(folder.note_number, 2)
        self.assertEqual(folder.create_time, 1700000000000)
        self.assertEqual(folder.modify_time, 1700000100000)

        note_data = load_data("notes/list_note_success.json")
        note_info = note_data["note_book_list"][0]
        note_info["create_time"] = "1700000000000"
        note_info["modify_time"] = "1700000100000"
        note = RecordingNotesClient({"list_note": note_data}).list_notes(1)["notes"][0]
        self.assertEqual(note.create_time, 1700000000000)
        self.assertEqual(note.modify_time, 1700000100000)

    def test_note_int64_fields_reject_noncanonical_or_out_of_range_values(self):
        search_data = load_data("notes/search_note_success.json")
        search_data["total_hit_num"] = "01"
        with self.assertRaisesRegex(ApiError, "total_hit_num"):
            RecordingNotesClient({"search_note": search_data}).search_notes("项目", 1)

        note_data = load_data("notes/list_note_success.json")
        note_data["note_book_list"][0]["create_time"] = True
        with self.assertRaisesRegex(ApiError, "create_time"):
            RecordingNotesClient({"list_note": note_data}).list_notes(1)

        folder_data = load_data("notes/list_notebook_success.json")
        folder_data["note_folder_infos"][0]["note_number"] = str(1 << 63)
        with self.assertRaisesRegex(ApiError, "note_number"):
            RecordingNotesClient({"list_notebook": folder_data}).list_folders(1)

    def test_get_create_and_append_use_note_id_and_dual_output(self):
        client = RecordingNotesClient({
            "get_doc_content": load_data("notes/get_doc_content_success.json"),
            "import_doc": load_data("notes/import_doc_success.json"),
            "append_doc": load_data("notes/append_doc_success.json"),
        })
        read = client.get_doc_content("note_test_001")
        created = client.create_note("# 标题", folder_id="folder_test_001")
        appended = client.append_note("note_test_001", "补充")
        self.assertEqual(client.calls[0], ("get_doc_content", {"note_id": "note_test_001", "target_content_format": 0}))
        self.assertEqual(client.calls[1][1], {"content_format": 1, "content": "# 标题", "folder_id": "folder_test_001"})
        self.assertEqual(client.calls[2][1], {"note_id": "note_test_001", "content_format": 1, "content": "补充"})
        for result in (read, created, appended):
            self.assertEqual(result["note_id"], result["doc_id"])

    def test_write_validation_and_required_response_id(self):
        client = RecordingNotesClient({"import_doc": {}, "append_doc": {}})
        for call in (
            lambda: client.get_doc_content(""),
            lambda: client.create_note(" "),
            lambda: client.append_note("", "x"),
            lambda: client.append_note("note", " "),
        ):
            with self.assertRaises(ValueError):
                call()
        with self.assertRaisesRegex(ApiError, "note_id"):
            client.create_note("valid")
        with self.assertRaisesRegex(ApiError, "note_id"):
            client.append_note("note", "valid")

    def test_search_result_accepts_deprecated_constructor_alias(self):
        result = SearchResult(doc_id="legacy")
        self.assertEqual(result.note_id, "legacy")
        with self.assertRaises(ValueError):
            SearchResult(note_id="new", doc_id="old")
        self.assertIs(ImaNoteApiClient, NotesApiClient)

    def test_direct_writes_reject_invalid_utf8(self):
        client = RecordingNotesClient({})
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            client.create_note("bad \ud800")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            client.append_note("note", "bad \ud800")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
