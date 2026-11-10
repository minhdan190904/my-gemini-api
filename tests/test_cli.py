import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the repo root (for `cli`) and the source tree importable without installing
if (_root := str(Path(__file__).resolve().parent.parent)) not in sys.path:
    sys.path.insert(0, _root)
if (_src := str(Path(__file__).resolve().parent.parent / "src")) not in sys.path:
    sys.path.insert(0, _src)

from cli import build_parser
from gemini_webapi.utils import parse_raw_cookies


class TestCookieParserAndCLIArgs(unittest.TestCase):
    def test_parse_raw_cookie_header(self):
        raw = "Cookie: __Secure-1PSID=psid_val_123; __Secure-1PSIDTS=ts_val_456; OTHER=abc"
        parsed = parse_raw_cookies(raw)
        self.assertEqual(parsed["__Secure-1PSID"], "psid_val_123")
        self.assertEqual(parsed["__Secure-1PSIDTS"], "ts_val_456")

    def test_parse_json_formats(self):
        flat = '{"__Secure-1PSID": "p1", "__Secure-1PSIDTS": "t1"}'
        self.assertEqual(parse_raw_cookies(flat)["__Secure-1PSID"], "p1")

        ext_list = '[{"name": "__Secure-1PSID", "value": "p2"}, {"name": "__Secure-1PSIDTS", "value": "t2"}]'
        self.assertEqual(parse_raw_cookies(ext_list)["__Secure-1PSID"], "p2")

    def test_new_cli_subcommands_parse(self):
        parser = build_parser()
        args_fetch = parser.parse_args(["cookies", "fetch", "--browser", "firefox"])
        self.assertEqual(args_fetch.command, "cookies")
        self.assertEqual(args_fetch.cookies_command, "fetch")
        self.assertEqual(args_fetch.browser, "firefox")

        args_test = parser.parse_args(["test", "--mode", "concurrent", "--concurrency", "4", "--requests", "8"])
        self.assertEqual(args_test.command, "test")
        self.assertEqual(args_test.mode, "concurrent")
        self.assertEqual(args_test.concurrency, 4)
        self.assertEqual(args_test.requests, 8)

        args_serve = parser.parse_args(["serve", "--port", "8080", "--concurrency", "5"])
        self.assertEqual(args_serve.command, "serve")
        self.assertEqual(args_serve.port, 8080)
        self.assertEqual(args_serve.concurrency, 5)



@unittest.skipUnless(
    os.getenv("SECURE_1PSID"),
    "Skipped: SECURE_1PSID not set (live credential required)",
)
class TestCLITool(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        fd, cls._cookie_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        cookies = {
            "__Secure-1PSID": os.getenv("SECURE_1PSID", ""),
            "__Secure-1PSIDTS": os.getenv("SECURE_1PSIDTS", ""),
        }
        Path(cls._cookie_path).write_text(json.dumps(cookies), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        with contextlib.suppress(OSError):
            os.unlink(cls._cookie_path)

    def _parse(self, *argv):
        parser = build_parser()
        return parser.parse_args(
            [
                "--cookies-json",
                self._cookie_path,
                "--no-persist",
                "--skip-verify",
                *argv,
            ]
        )

    async def test_cli_ask_stream(self):
        from cli import cmd_ask

        args = self._parse("ask", "Give me a inspiring idea for web development")
        result = await cmd_ask(args)
        assert result == 0

    async def test_cli_models(self):
        from cli import cmd_models

        args = self._parse("models")
        result = await cmd_models(args)
        assert result == 0

    async def test_cli_list_chats(self):
        from cli import cmd_list

        args = self._parse("list")
        result = await cmd_list(args)
        assert result == 0

    async def test_cli_inspect(self):
        from cli import cmd_inspect

        args = self._parse("inspect")
        result = await cmd_inspect(args)
        assert result == 0


if __name__ == "__main__":
    unittest.main()
