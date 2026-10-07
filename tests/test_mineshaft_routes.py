"""Conversion and CLI regression tests; all writes use temporary catalogs."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import mineshaft_routes as tool


def waypoint(number, x):
    return {"x": x, "y": 100, "z": 20, "r": 0, "g": 1, "b": 0, "options": {"name": str(number)}}


class MineshaftRoutesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "mining" / "routes.json"

    def run_cli(self, lines):
        output = io.StringIO()
        with patch.object(tool, "ROUTES_FILE", self.path), patch("builtins.input", side_effect=lines), patch("sys.stdout", output):
            tool.main()
        return output.getvalue()

    def test_conversion_sorts_numeric_names_and_omits_colors(self):
        source = [waypoint(20, 20), waypoint(3, 3), waypoint(10, 10)]
        route = tool.convert_route("eurybium:jasp1", source)
        self.assertEqual(route, {
            "id": "eurybium:JASP1", "island": "MINESHAFT", "mineshaft": "JASP1",
            "space": "WORLD", "points": [[3, 100, 20], [10, 100, 20], [20, 100, 20]],
        })
        self.assertEqual(source[0]["options"]["name"], "20")

    def test_spawning_keys_receive_the_base_camp_scope(self):
        for key in tool.SPAWNING_KEYS:
            route = tool.convert_route(key, [waypoint(1, 0)])
            self.assertEqual(route["island"], "DWARVEN_MINES")
            self.assertEqual(route["region"], "DWARVEN_BASE_CAMP")
            self.assertNotIn("mineshaft", route)

    def test_invalid_keys_and_waypoints_are_rejected(self):
        for key in ["JASP2", "other:JASP1", "../JASP1"]:
            with self.assertRaises(ValueError):
                tool.normalize_key(key)
        bad_points = [[], {}, [None], [waypoint(1, True)], [waypoint(1, 1.5)], [waypoint(1, 2**31)]]
        for value in bad_points:
            with self.subTest(value=value), self.assertRaises(ValueError):
                tool.convert_route("JASP1", value)
        for name in ["first", "1.5", None, str(2**31)]:
            point = waypoint(1, 0)
            point["options"]["name"] = name
            with self.assertRaises(ValueError):
                tool.convert_route("JASP1", [point])

    def test_multiline_paste_handles_brackets_and_escapes_inside_options(self):
        point = waypoint(1, 0)
        point["options"]["label"] = 'quoted "label" with ] } brackets and \\ slash'
        text = json.dumps([point], indent=2)
        with patch("builtins.input", side_effect=text.splitlines()), patch("sys.stdout", io.StringIO()):
            self.assertEqual(tool.read_route(), [point])

    def test_long_clipboard_route_bypasses_terminal_input(self):
        points = [waypoint(number, number) for number in range(1, 201)]
        payload = json.dumps(points).encode("utf-8")
        self.assertGreater(len(payload), 4096)
        result = tool.subprocess.CompletedProcess(["pbpaste"], 0, stdout=payload, stderr=b"")
        with patch.object(tool.sys, "platform", "darwin"), patch.object(tool.subprocess, "run", return_value=result) as read:
            self.run_cli(["JASP1", "clipboard", "q"])
        read.assert_called_once_with(["pbpaste"], capture_output=True, check=True, timeout=10)
        self.assertEqual(len(tool.load_catalog(self.path)["routes"][0]["points"]), 200)

    def test_file_input_accepts_paths_with_spaces_and_utf8_bom(self):
        source = Path(self.directory.name) / "my route.json"
        points = [waypoint(number, number) for number in range(1, 201)]
        source.write_text(json.dumps(points), encoding="utf-8-sig")
        self.run_cli(["JASP1", f'@"{source}"', "q"])
        self.assertEqual(len(tool.load_catalog(self.path)["routes"][0]["points"]), 200)

    def test_clipboard_failure_and_oversized_input_do_not_write(self):
        with patch.object(tool.sys, "platform", "darwin"), patch.object(tool.subprocess, "run", side_effect=tool.subprocess.TimeoutExpired("pbpaste", 10)):
            output = self.run_cli(["JASP1", "clipboard", "q"])
        self.assertIn("Could not read the clipboard", output)
        self.assertFalse(self.path.exists())
        with patch.object(tool, "MAX_FILE_BYTES", 8):
            with self.assertRaises(ValueError):
                tool.parse_route_bytes(b" " * 9)

    def test_save_preserves_other_routes_and_metadata_and_confirms_replacement(self):
        first = tool.convert_route("JASP1", [waypoint(1, 10)])
        second = tool.convert_route("RUBY1", [waypoint(1, 20)])
        tool.save_route(self.path, first)
        catalog = tool.load_catalog(self.path)
        catalog["note"] = "retain me"
        self.path.write_text(json.dumps(catalog), encoding="utf-8")
        tool.save_route(self.path, second)
        before = self.path.read_bytes()
        replacement = tool.convert_route("JASP1", [waypoint(1, 30)])
        with self.assertRaises(ValueError):
            tool.save_route(self.path, replacement)
        self.assertEqual(self.path.read_bytes(), before)
        tool.save_route(self.path, replacement, replace=True)
        catalog = tool.load_catalog(self.path)
        self.assertEqual(catalog["routes"], [replacement, second])
        self.assertEqual(catalog["note"], "retain me")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_invalid_existing_catalog_is_not_overwritten(self):
        self.path.parent.mkdir()
        for text in ['{bad}', '{"schemaVersion":2,"routes":[]}', '{"schemaVersion":1,"routes":[{"id":"x"},{"id":"x"}]}']:
            self.path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                tool.save_route(self.path, tool.convert_route("JASP1", [waypoint(1, 0)]))
            self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_cli_repeats_and_keeps_rejected_replacements(self):
        first = json.dumps([waypoint(1, 10)])
        second = json.dumps([waypoint(1, 20)])
        replacement = json.dumps([waypoint(1, 30)])
        output = self.run_cli(["JASP1", first, "RUBY1", second, "JASP1", replacement, "n", "q"])
        self.assertIn("Saved eurybium:JASP1", output)
        self.assertIn("Existing route kept", output)
        self.assertEqual(len(tool.load_catalog(self.path)["routes"]), 2)
        self.assertEqual(tool.load_catalog(self.path)["routes"][0]["points"][0][0], 10)
        self.run_cli(["JASP1", replacement, "yes", "q"])
        self.assertEqual(tool.load_catalog(self.path)["routes"][0]["points"][0][0], 30)

    def test_invalid_paste_cancel_and_eof_do_not_write(self):
        output = self.run_cli(["JASP1", "[{broken}]", "JASP1", "[", "", "q"])
        self.assertIn("Could not import route", output)
        self.assertIn("Import cancelled", output)
        self.assertFalse(self.path.exists())
        self.run_cli(["JASP1", "[", EOFError()])
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
