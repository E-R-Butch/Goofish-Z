import importlib
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

api_app = importlib.import_module("goofish_z.api.app")
from goofish_z.core.errors import RateLimitedError


class ItemApiTest(unittest.TestCase):
    def test_item_get_uses_item_id_query_contract(self) -> None:
        synthetic_item_id = "0000000000000"
        expected = {
            "item_id": synthetic_item_id,
            "title": "合成测试商品",
            "price": "¥10",
            "seller_nick": "合成测试卖家",
            "status": "在线",
            "detail": {"title": "合成测试商品", "itemStatusStr": "在线"},
            "raw": {"data": {"itemDO": {"itemId": synthetic_item_id}}},
        }
        with patch.object(api_app, "_call_command", return_value=expected) as call_command:
            response = api_app.api_item_get(item_id=synthetic_item_id)

        self.assertEqual(
            json.loads(response.body),
            {
                "item_id": synthetic_item_id,
                "title": "合成测试商品",
                "price": "¥10",
                "status": "在线",
                "detail": {"title": "合成测试商品", "itemStatusStr": "在线"},
            },
        )
        self.assertNotIn("raw", json.loads(response.body))
        self.assertNotIn("seller_nick", json.loads(response.body))
        call_command.assert_called_once_with("item.get", {"item_id": synthetic_item_id})

    def test_rate_limit_is_exposed_as_retryable_http_response(self) -> None:
        command = type(
            "SyntheticCommand",
            (),
            {
                "func": staticmethod(
                    lambda: (_ for _ in ()).throw(
                        RateLimitedError("synthetic", retry_after=4.1)
                    )
                )
            },
        )()
        with patch.object(api_app, "_all_commands", return_value={"item.synthetic": command}):
            with self.assertRaises(HTTPException) as raised:
                api_app._call_command("item.synthetic", {})

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers, {"Retry-After": "5"})
        self.assertNotIn("synthetic", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
