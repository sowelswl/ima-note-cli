from __future__ import annotations

import unittest

from tests._bootstrap import ROOT  # noqa: F401
from ima_note_cli.errors import ApiProtocolError
from ima_note_cli.protocol import optional_int64, require_array, require_bool, require_int, require_int64, require_non_empty_string, require_string_map


class ProtocolTests(unittest.TestCase):
    def test_strict_scalar_and_collection_types(self) -> None:
        self.assertEqual(require_non_empty_string({"id": " x "}, "id", "ep"), "x")
        self.assertEqual(require_int({"n": 2}, "n", "ep"), 2)
        self.assertTrue(require_bool({"flag": True}, "flag", "ep"))
        self.assertEqual(require_array({"items": []}, "items", "ep"), [])
        for payload, fn, key in [({"n": True}, require_int, "n"), ({"flag": "false"}, require_bool, "flag"), ({"items": None}, require_array, "items")]:
            with self.subTest(payload=payload), self.assertRaises(ApiProtocolError):
                fn(payload, key, "ep")

    def test_headers_reject_non_strings_and_crlf_without_echo(self) -> None:
        for value in [{"Authorization": False}, {"X-Test": "secret\r\nInjected: yes"}]:
            with self.assertRaises(ApiProtocolError) as caught:
                require_string_map({"headers": value}, "headers", "ep")
            self.assertNotIn("secret", str(caught.exception))

    def test_int64_wire_forms_are_bounded_and_canonical(self) -> None:
        minimum, maximum = -(1 << 63), (1 << 63) - 1
        for value, expected in (
            (minimum, minimum), (str(minimum), minimum), (0, 0), ("0", 0),
            (maximum, maximum), (str(maximum), maximum),
        ):
            with self.subTest(value=value):
                self.assertEqual(require_int64({"value": value}, "value", "ep"), expected)
        self.assertIsNone(optional_int64({"value": None}, "value", "ep"))

        invalid = (
            True, "-0", "01", minimum - 1, str(minimum - 1),
            maximum + 1, str(maximum + 1), "9" * 5000,
        )
        for value in invalid:
            with self.subTest(value_type=type(value).__name__, value_length=len(value) if isinstance(value, str) else None):
                with self.assertRaises(ApiProtocolError):
                    require_int64({"value": value}, "value", "ep")
