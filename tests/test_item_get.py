import importlib
import unittest
from unittest.mock import patch


item_get = importlib.import_module("goofish_z.commands.item.get")


class ItemGetTest(unittest.TestCase):
    def test_extracts_item_do_instead_of_empty_track_params(self) -> None:
        synthetic_item_id = "0000000000000"
        raw = {
            "data": {
                "itemDO": {
                    "itemId": synthetic_item_id,
                    "title": "合成测试商品",
                    "soldPrice": "123.45",
                    "itemStatus": 1,
                    "itemStatusStr": "卖掉了",
                },
                "sellerDO": {"nick": "合成测试卖家"},
                "trackParams": {},
            }
        }
        with (
            patch("goofish_z.core.limiter.check"),
            patch.object(item_get.Session, "load", return_value=object()),
            patch.object(item_get, "call", return_value=raw),
        ):
            result = item_get.get(synthetic_item_id)

        self.assertEqual(result["item_id"], synthetic_item_id)
        self.assertEqual(result["title"], "合成测试商品")
        self.assertEqual(result["price"], "¥123.45")
        self.assertEqual(result["seller_nick"], "合成测试卖家")
        self.assertEqual(result["status"], "卖掉了")
        self.assertEqual(
            result["detail"],
            {
                "title": "合成测试商品",
                "itemStatusStr": "卖掉了",
            },
        )


    def test_sku_prices_respect_field_units(self) -> None:
        raw = {
            "data": {
                "itemDO": {
                    "itemId": "0000000000000",
                    "title": "合成多规格商品",
                    "soldPrice": "100",
                    "skuList": [
                        {"skuId": "1", "price": "4099", "quantity": 1,
                         "propertyList": [{"actualValueText": "20G"}]},
                        {"skuId": "2", "priceInCent": "999", "quantity": 2,
                         "propertyList": [{"actualValueText": "10G"}]},
                        {"skuId": "3", "priceInCent": "129900", "quantity": 1,
                         "propertyList": [{"actualValueText": "24G"}]},
                        {"skuId": "4", "price": "", "quantity": 0,
                         "propertyList": [{"actualValueText": "默认"}]},
                    ],
                },
                "sellerDO": {"nick": "合成测试卖家"},
                "trackParams": {},
            }
        }
        with (
            patch("goofish_z.core.limiter.check"),
            patch.object(item_get.Session, "load", return_value=object()),
            patch.object(item_get, "call", return_value=raw),
        ):
            result = item_get.get("0000000000000")
        self.assertEqual([round(sku["price"], 2) for sku in result["skus"]],
                         [4099.0, 9.99, 1299.0, 0.0])
        self.assertEqual([sku["name"] for sku in result["skus"]], ["20G", "10G", "24G", "默认"])


if __name__ == "__main__":
    unittest.main()
