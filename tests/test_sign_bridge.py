import hashlib
import json
import subprocess
import sys
import unittest

from goofish_z.core.sign import _ctx, generate_sign


class SignBridgeTest(unittest.TestCase):
    def test_signing_import_keeps_binary_subprocess_io_working(self):
        payload = b"\x00\xff\n"
        result = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            input=payload, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout, payload)

    def test_javascript_signing_and_chinese_output_remain_utf8(self):
        data = json.dumps({"query": "示例商品"}, ensure_ascii=False, separators=(",", ":"))
        expected = hashlib.md5(f"synthetic-token&0&34839810&{data}".encode("utf-8")).hexdigest()
        self.assertEqual(generate_sign("0", "synthetic-token", data), expected)
        self.assertEqual(_ctx().eval("'示例商品'"), "示例商品")
