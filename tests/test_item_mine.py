import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

item_mine = importlib.import_module("goofish_z.commands.item.mine")
from goofish_z.commands.item.mine import (
    _normalize_limit,
    _normalize_status,
    _parse_card,
    _status_name,
    _wait_for_seller_page_slot,
)
from goofish_z.core.errors import GoofishError, RateLimitedError


class ItemMineHelpersTest(unittest.TestCase):
    def test_status_mapping(self) -> None:
        self.assertEqual(_status_name(0), "online")
        self.assertEqual(_status_name("1"), "sold")
        self.assertEqual(_status_name(7), "offline")

    def test_status_aliases_and_validation(self) -> None:
        self.assertEqual(_normalize_status("在售"), "online")
        self.assertEqual(_normalize_status("已卖出"), "sold")
        self.assertEqual(_normalize_status("全部"), "all")
        with self.assertRaises(GoofishError):
            _normalize_status("maybe")

    def test_limit_is_bounded(self) -> None:
        self.assertEqual(_normalize_limit(0), 1)
        self.assertEqual(_normalize_limit("50"), 50)
        self.assertEqual(_normalize_limit(999), 200)

    def test_parse_card_preserves_status_and_images(self) -> None:
        image_infos = json.dumps(
            [
                {"url": "http://img.example/one.jpg", "major": True},
                {"url": "http://img.example/two.jpg", "major": False},
            ]
        )
        item = _parse_card(
            {
                "cardData": {
                    "id": "123",
                    "title": "测试商品",
                    "itemStatus": 1,
                    "categoryId": 42,
                    "detailParams": {
                        "soldPrice": "30",
                        "postInfo": "包邮",
                        "imageInfos": image_infos,
                    },
                    "priceInfo": {"price": "30"},
                    "picInfo": {"picUrl": "http://img.example/one.jpg"},
                }
            }
        )
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item["item_id"], "123")
        self.assertEqual(item["price"], "¥30")
        self.assertEqual(item["status"], "sold")
        self.assertEqual(item["status_code"], 1)
        self.assertEqual(len(item["image_urls"]), 2)


class ItemMinePaginationTest(unittest.TestCase):
    @staticmethod
    def _card(item_id: str, title: str) -> dict:
        return {
            "cardData": {
                "id": item_id,
                "title": title,
                "itemStatus": 0,
                "priceInfo": {"price": "10"},
            }
        }

    def test_every_page_waits_for_its_own_limiter_slot(self) -> None:
        responses = [
            {
                "data": {
                    "cardList": [self._card("101", "合成商品一")],
                    "nextPage": True,
                    "totalCount": 2,
                }
            },
            {
                "data": {
                    "cardList": [self._card("202", "合成商品二")],
                    "nextPage": False,
                    "totalCount": 2,
                }
            },
        ]
        requested_pages: list[int] = []

        def fake_call(_session, **kwargs):
            requested_pages.append(kwargs["data"]["pageNumber"])
            return responses[len(requested_pages) - 1]

        with (
            patch.object(item_mine.Session, "load", return_value=SimpleNamespace(unb="synthetic-user")),
            patch.object(item_mine, "call", side_effect=fake_call),
            patch.object(item_mine, "_wait_for_seller_page_slot") as wait_for_slot,
        ):
            result = item_mine.mine(status="online", limit=2)

        self.assertEqual(requested_pages, [1, 2])
        self.assertEqual(wait_for_slot.call_count, 2)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["pages_fetched"], 2)
        self.assertFalse(result["has_more"])

    def test_limiter_wait_retries_after_reported_delay(self) -> None:
        with (
            patch(
                "goofish_z.core.limiter.check",
                side_effect=[RateLimitedError("synthetic rate limit"), None],
            ) as rate_check,
            patch(
                "goofish_z.core.limiter.status",
                return_value={"seller_page": {"next_available_in": 0.5}},
            ),
            patch.object(item_mine.time, "sleep") as sleep,
        ):
            _wait_for_seller_page_slot()

        self.assertEqual(rate_check.call_count, 2)
        sleep.assert_called_once_with(0.6)


if __name__ == "__main__":
    unittest.main()
