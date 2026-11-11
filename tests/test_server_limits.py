"""Kiểm tra giới hạn request của server.py: file đính kèm, hàng đợi Semaphore, timeout, khóa bí mật."""

import asyncio
import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Ưu tiên gemini_webapi trong repo (src/) hơn bản cài sẵn trên máy
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from server import (  # noqa: E402
    GeminiAPIServer,
    RequestError,
    _materialize_files,
    _sniff_image,
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class SniffImageTest(unittest.TestCase):
    def test_known_formats(self):
        self.assertEqual(_sniff_image(JPEG), "jpeg")
        self.assertEqual(_sniff_image(PNG), "png")
        self.assertEqual(_sniff_image(WEBP), "webp")
        self.assertEqual(_sniff_image(b"GIF89a" + b"\x00" * 10), "gif")

    def test_not_image(self):
        self.assertIsNone(_sniff_image(b"%PDF-1.7 hello"))
        self.assertIsNone(_sniff_image(b"hello world"))


class MaterializeFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def run_files(self, files, max_files=8, max_bytes=1024 * 1024):
        return _materialize_files(files, self.tmp, max_files=max_files, max_file_bytes=max_bytes)

    def test_same_name_does_not_overwrite(self):
        paths = self.run_files([{"name": "a.jpg", "data": b64(JPEG)}, {"name": "a.jpg", "data": b64(PNG)}])
        self.assertEqual(len(paths), 2)
        self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(paths[0].read_bytes(), JPEG)
        self.assertEqual(paths[1].read_bytes(), PNG)

    def test_data_url_prefix(self):
        paths = self.run_files([{"name": "x.png", "data": "data:image/png;base64," + b64(PNG)}])
        self.assertEqual(paths[0].read_bytes(), PNG)

    def test_too_many_files(self):
        with self.assertRaises(RequestError) as ctx:
            self.run_files([{"name": f"{i}.jpg", "data": b64(JPEG)} for i in range(3)], max_files=2)
        self.assertEqual(ctx.exception.status, 400)

    def test_file_too_large(self):
        with self.assertRaises(RequestError) as ctx:
            self.run_files([{"name": "big.jpg", "data": b64(JPEG + b"\x00" * 2000)}], max_bytes=1000)
        self.assertEqual(ctx.exception.status, 413)

    def test_bad_base64(self):
        with self.assertRaises(RequestError) as ctx:
            self.run_files([{"name": "a.jpg", "data": "không-phải-base64!!"}])
        self.assertEqual(ctx.exception.status, 400)

    def test_fake_image_extension(self):
        with self.assertRaises(RequestError) as ctx:
            self.run_files([{"name": "fake.jpg", "data": b64(b"hello world, not an image")}])
        self.assertEqual(ctx.exception.status, 400)

    def test_non_image_file_allowed(self):
        paths = self.run_files([{"name": "doc.pdf", "data": b64(b"%PDF-1.7 test")}])
        self.assertEqual(len(paths), 1)

    def test_files_must_be_list(self):
        with self.assertRaises(RequestError):
            self.run_files({"name": "a.jpg"})


class QueueTest(unittest.IsolatedAsyncioTestCase):
    async def test_queue_timeout_returns_busy(self):
        app = GeminiAPIServer(max_concurrency=1, api_key="x" * 20)
        async with app._acquire_slot():
            with self.assertRaises(RequestError) as ctx:
                async with app._acquire_slot(queue_timeout_ms=50):
                    pass
            self.assertEqual(ctx.exception.status, 429)
            self.assertEqual(ctx.exception.extra.get("error_code"), "busy")
        # Sau khi nhả, lấy lại được luồng và bộ đếm hàng đợi về 0
        async with app._acquire_slot(queue_timeout_ms=50):
            pass
        self.assertEqual(app.queued_requests, 0)

    async def test_upstream_timeout(self):
        app = GeminiAPIServer(max_concurrency=1, api_key="x" * 20)
        app.request_timeout_ms = 50
        with self.assertRaises(TimeoutError):
            async with app._upstream_timeout():
                await asyncio.sleep(1)


class ApiKeyTest(unittest.TestCase):
    def test_empty_key_blocks_everything(self):
        app = GeminiAPIServer(api_key="")
        self.assertFalse(app._verify_api_key({"x-api-key": ""}))

    def test_correct_key(self):
        key = "k" * 24
        app = GeminiAPIServer(api_key=key)
        self.assertTrue(app._verify_api_key({"x-api-key": key}))
        self.assertTrue(app._verify_api_key({"authorization": f"Bearer {key}"}))
        self.assertFalse(app._verify_api_key({"x-api-key": "sai"}))

    def test_env_limits(self):
        os.environ["GEMINI_MAX_FILES"] = "3"
        try:
            app = GeminiAPIServer(api_key="k" * 24)
            self.assertEqual(app.max_files, 3)
        finally:
            del os.environ["GEMINI_MAX_FILES"]


if __name__ == "__main__":
    unittest.main()
