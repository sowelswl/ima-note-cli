from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests._bootstrap import ROOT  # noqa: F401
from ima_note_cli.aliases import AliasRecord, AliasStore, account_fingerprint, parse_alias_key
from ima_note_cli.errors import ConfigError, InputError, ReferenceError


class AliasStoreTests(unittest.TestCase):
    def store(self, root: Path, client_id: str = "client-one") -> AliasStore:
        return AliasStore(root / "aliases.json", account_fingerprint(client_id))

    def test_round_trip_force_list_and_unset_are_account_scoped(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            first = self.store(root)
            record = AliasRecord("kb", "research", "kb-1", "Research")
            first.set(record)

            self.assertEqual(first.get("kb", "research"), record)
            self.assertEqual(first.list("kb"), [record])
            stored = (root / "aliases.json").read_text(encoding="utf-8")
            self.assertNotIn("client-one", stored)
            self.assertIn(account_fingerprint("client-one"), stored)
            with self.assertRaises(InputError) as duplicate:
                first.set(AliasRecord("kb", "research", "kb-2"))
            self.assertEqual(duplicate.exception.code, "alias_exists")

            first.set(AliasRecord("kb", "research", "kb-2", "Research 2"), force=True)
            self.assertEqual(first.get("kb", "research").target_id, "kb-2")
            with self.assertRaises(ReferenceError) as mismatch:
                self.store(root, "client-two").get("kb", "research")
            self.assertEqual(mismatch.exception.code, "alias_account_mismatch")

            removed = first.unset("kb", "research")
            self.assertEqual(removed.target_id, "kb-2")
            self.assertEqual(first.list(), [])
            self.assertFalse(list(root.glob("aliases-*.tmp")))

    def test_scoped_aliases_require_and_validate_kb_scope(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            store = self.store(Path(tmp_dir))
            with self.assertRaises(InputError):
                store.set(AliasRecord("media", "paper", "media-1"))

            record = AliasRecord("media", "paper", "media-1", "paper.pdf", {"kb_id": "kb-1"})
            store.set(record)
            self.assertEqual(store.get("media", "paper", expected_scope={"kb_id": "kb-1"}), record)
            with self.assertRaises(ReferenceError) as mismatch:
                store.get("media", "paper", expected_scope={"kb_id": "kb-2"})
            self.assertEqual(mismatch.exception.code, "alias_scope_mismatch")

    def test_failed_atomic_replace_preserves_existing_alias_file(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store = self.store(root)
            original = AliasRecord("kb", "research", "kb-1")
            store.set(original)
            with patch("ima_note_cli.aliases.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(ConfigError) as failed:
                    store.set(AliasRecord("kb", "other", "kb-2"))
            self.assertEqual(failed.exception.code, "alias_config_write_failed")
            self.assertEqual(store.list(), [original])
            self.assertFalse(list(root.glob("aliases-*.tmp")))

    def test_invalid_keys_and_malformed_documents_fail_safely(self) -> None:
        self.assertEqual(parse_alias_key("kb.research"), ("kb", "research"))
        for value in ("research", "unknown.name", "kb.bad name", "kb."):
            with self.subTest(value=value), self.assertRaises(InputError):
                parse_alias_key(value)

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "aliases.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ConfigError) as invalid_json:
                self.store(root).list()
            self.assertEqual(invalid_json.exception.code, "alias_config_invalid")

            path.write_text(json.dumps({"schema_version": 999, "accounts": {}}), encoding="utf-8")
            with self.assertRaises(ConfigError):
                self.store(root).list()

            malformed_scope = {
                "schema_version": 1,
                "accounts": {
                    account_fingerprint("client-one"): {
                        "media": {"paper": {"id": "media-1", "name": "paper.pdf", "scope": {}}}
                    }
                },
            }
            path.write_text(json.dumps(malformed_scope), encoding="utf-8")
            with self.assertRaises(ConfigError):
                self.store(root).get("media", "paper")

    def test_unavailable_home_is_deferred_until_alias_access(self) -> None:
        with patch("ima_note_cli.aliases.Path.home", side_effect=RuntimeError):
            store = AliasStore.for_client_id("client-one")
        self.assertIsNone(store.path)
        with self.assertRaises(ConfigError) as unavailable:
            store.list()
        self.assertEqual(unavailable.exception.code, "alias_config_unavailable")


if __name__ == "__main__":
    unittest.main()
