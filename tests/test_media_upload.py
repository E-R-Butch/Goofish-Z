"""media upload 的离线契约测试：只有服务端确认过的收据才允许返回。

不触网：session.http.post 用假响应替换，文件用临时目录里的真实小文件。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from requests.exceptions import ConnectionError as RequestsConnectionError

from goofish_z.commands.media import upload as upload_mod
from goofish_z.core.errors import GoofishError, RiskControlError

GOOD_URL = "https://img.alicdn.com/imgextra/i1/O1CN01fake.jpg"


class FakeResponse:
    def __init__(self, body, status_error=None) -> None:
        self._body = body
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


def success_body(url: str = GOOD_URL, pix: str = "800x600", size: int = 12345) -> dict:
    return {"success": True, "object": {"url": url, "pix": pix, "size": size}}


class MediaUploadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.image = self.tmp / "photo.jpg"
        self.image.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 64)
        self.session = MagicMock()

    def tearDown(self) -> None:
        for child in self.tmp.iterdir():
            child.unlink()
        self.tmp.rmdir()

    def _run(self, body, status_error=None, path: str | None = None) -> dict:
        self.session.http.post.return_value = FakeResponse(body, status_error)
        with (
            patch.object(upload_mod, "Session") as session_cls,
            patch.object(upload_mod, "acquire") as acquire,
            patch("goofish_z.core.guard.check"),
        ):
            session_cls.load.return_value = self.session
            result = upload_mod.upload(str(path or self.image))
        self.acquired = acquire
        return result

    def test_returns_confirmed_receipt(self) -> None:
        result = self._run(success_body())
        self.assertEqual(result["url"], GOOD_URL)
        self.assertEqual((result["width"], result["height"]), (800, 600))
        self.assertEqual(result["size"], 12345)

    def test_consumes_media_bucket_not_write_bucket(self) -> None:
        self._run(success_body())
        self.acquired.assert_called_once_with("media.write")

    def test_unconfirmed_upload_is_error(self) -> None:
        with self.assertRaises(GoofishError) as ctx:
            self._run({"success": False, "status": "timeout"})
        self.assertIn("未", str(ctx.exception))

    def test_missing_success_flag_is_error(self) -> None:
        with self.assertRaises(GoofishError):
            self._run({"object": {"url": GOOD_URL, "pix": "800x600"}})

    def test_risk_marker_maps_to_risk_error(self) -> None:
        with self.assertRaises(RiskControlError):
            self._run({"success": False, "status": "FAIL_SYS_USER_VALIDATE::RGV587_ERROR"})

    def test_missing_object_is_error(self) -> None:
        with self.assertRaises(GoofishError):
            self._run({"success": True})

    def test_unparsable_pix_is_error(self) -> None:
        for pix in ("", "0x0", "abc", "800"):
            with self.assertRaises(GoofishError, msg=pix):
                self._run(success_body(pix=pix))

    def test_untrusted_receipt_url_is_error(self) -> None:
        for url in (
            "http://img.alicdn.com/x.jpg",
            "https://evil.example/x.jpg",
            "https://alicdn.com.evil.example/x.jpg",
            "",
        ):
            with self.assertRaises(GoofishError, msg=url):
                self._run(success_body(url=url))

    def test_network_failure_is_not_retried(self) -> None:
        with self.assertRaises(GoofishError) as ctx:
            self._run(None, status_error=RequestsConnectionError("connection reset"))
        self.assertIn("不自动重传", str(ctx.exception))

    def test_missing_or_empty_file_is_error(self) -> None:
        with self.assertRaises(GoofishError):
            self._run(success_body(), path=str(self.tmp / "nope.jpg"))
        empty = self.tmp / "empty.jpg"
        empty.write_bytes(b"")
        with self.assertRaises(GoofishError):
            self._run(success_body(), path=str(empty))

    def test_non_image_extension_is_error(self) -> None:
        doc = self.tmp / "note.txt"
        doc.write_text("not an image")
        with self.assertRaises(GoofishError):
            self._run(success_body(), path=str(doc))


class ReceiptValidationTest(unittest.TestCase):
    def test_accepts_int_and_digit_string_dimensions(self) -> None:
        receipt = upload_mod.validate_image_receipt({"url": GOOD_URL, "width": "640", "height": 480})
        self.assertEqual((receipt["width"], receipt["height"]), (640, 480))

    def test_rejects_boolean_and_float_dimensions(self) -> None:
        for width in (True, 1.5, "0", -3):
            with self.assertRaises(GoofishError, msg=repr(width)):
                upload_mod.validate_image_receipt({"url": GOOD_URL, "width": width, "height": 480})


if __name__ == "__main__":
    unittest.main()
