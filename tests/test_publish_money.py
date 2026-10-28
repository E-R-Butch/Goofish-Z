"""发布金额换算契约：元 → 分必须精确，且只接受非负、最多两位小数。"""
from __future__ import annotations

import unittest

from goofish_z.commands.item import publish as publish_mod
from goofish_z.core.errors import GoofishError


class CentsTest(unittest.TestCase):
    def test_typical_prices_are_exact(self) -> None:
        cases = {
            19.99: "1999",
            0.1: "10",
            100: "10000",
            0.01: "1",
            "88.80": "8880",
            1999.0: "199900",
        }
        for value, expected in cases.items():
            self.assertEqual(publish_mod._cents(value, "price"), expected, msg=repr(value))

    def test_rejects_more_than_two_decimals(self) -> None:
        for value in (12.345, "0.001", 1.999999):
            with self.assertRaises(GoofishError, msg=repr(value)):
                publish_mod._cents(value, "price")

    def test_rejects_negative_and_non_finite(self) -> None:
        for value in (-1, -0.01, float("inf"), float("nan"), "nan"):
            with self.assertRaises(GoofishError, msg=repr(value)):
                publish_mod._cents(value, "price")

    def test_rejects_non_numeric(self) -> None:
        for value in ("abc", None, [1], {"a": 1}):
            with self.assertRaises(GoofishError, msg=repr(value)):
                publish_mod._cents(value, "price")

    def test_zero_is_allowed_for_default_price(self) -> None:
        self.assertEqual(publish_mod._cents(0, "price"), "0")


if __name__ == "__main__":
    unittest.main()
