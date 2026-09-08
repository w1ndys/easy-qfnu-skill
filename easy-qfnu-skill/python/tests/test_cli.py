import io
import json
import unittest

from qfnu.cli import run
from qfnu.version import VERSION


class CliTest(unittest.TestCase):
    def test_version_returns_ok_json(self):
        out = io.StringIO()
        code = run(["version"], out, io.StringIO())
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["source"], "easy-qfnu")
        self.assertEqual(body["version"], VERSION)

    def test_unknown_command_returns_error_json(self):
        out = io.StringIO()
        code = run(["not-a-command"], out, io.StringIO())
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("unknown command", body["error"])

    def test_help_prints_usage(self):
        out = io.StringIO()
        code = run(["--help"], out, io.StringIO())
        self.assertEqual(code, 2)
        self.assertIn("Usage:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
