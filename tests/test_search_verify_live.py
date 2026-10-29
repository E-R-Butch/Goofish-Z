"""搜索侧在售校验接线：死链剔除、透出原因、默认不开。

只测接线契约；渲染判定本身在 test_item_live.py。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from support import OfflineCase, fixture


class VerifyLiveWiringTest(OfflineCase):
    def _fetched(self):
        return {
            "items": [
                fixture(item_id="1111", title="合成 DDR3 32G 甲"),
                fixture(item_id="2222", title="合成 DDR3 32G 乙"),
            ],
            "page": 1,
            "source_count": 2,
            "has_next": False,
        }

    def test_default_off_does_not_render_any_page(self) -> None:
        with patch.object(self.search, "_run", return_value=self._fetched()), \
                patch("goofish_z.commands.item.live.check_items_live") as probe:
            result = self.search.search("DDR3 32G")
        probe.assert_not_called()
        self.assertEqual(result["count"], 2)

    def test_offline_items_are_dropped_with_reason_and_counted(self) -> None:
        states = {"1111": "offline", "2222": "live"}
        with patch.object(self.search, "_run", return_value=self._fetched()), \
                patch("goofish_z.commands.item.live.check_items_live", return_value=states) as probe:
            result = self.search.search("DDR3 32G", verify_live=True, verify_top=2)
        probe.assert_called_once_with(["1111", "2222"])
        self.assertEqual([it["item_id"] for it in result["items"]], ["2222"])
        self.assertTrue(result["items"][0]["live_verified"])
        self.assertEqual(result["offline_dropped"], 1)
        self.assertEqual(result["count"], 1)
        reasons = [r for b in result["blocked"] for r in b.get("reasons", [])]
        self.assertIn("商品已下架或已删除（渲染核验）", reasons)

    def test_unknown_state_keeps_the_item(self) -> None:
        states = {"1111": "unknown", "2222": "live"}
        with patch.object(self.search, "_run", return_value=self._fetched()), \
                patch("goofish_z.commands.item.live.check_items_live", return_value=states):
            result = self.search.search("DDR3 32G", verify_live=True, verify_top=2)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["offline_dropped"], 0)
        # unknown 不代表在售，不能打 live_verified
        self.assertNotIn("live_verified", result["items"][0])

    def test_verify_top_bounds_the_number_of_probes(self) -> None:
        fetched = {
            "items": [fixture(item_id=str(1000 + index), title=f"合成 DDR3 32G {index}")
                      for index in range(6)],
            "page": 1, "source_count": 6, "has_next": False,
        }
        with patch.object(self.search, "_run", return_value=fetched), \
                patch("goofish_z.commands.item.live.check_items_live", return_value={}) as probe:
            self.search.search("DDR3 32G", verify_live=True, verify_top=99)
        probed = probe.call_args.args[0]
        # 站点上限是 8，verify_top=99 也只探这么多次；这里 6 条全部在范围内
        self.assertEqual(len(probed), min(6, self.search.MAX_VERIFY_TOP))

    def test_http_route_forwards_verify_params(self) -> None:
        import importlib

        api = importlib.import_module("goofish_z.api.app")
        from fastapi.testclient import TestClient

        with TestClient(api.app) as client, \
                patch.object(api, "_call_command", return_value={}) as call:
            client.get("/api/search", params={"q": "synthetic", "verify_live": True, "verify_top": 3})
        params = call.call_args.args[1]
        self.assertTrue(params.get("verify_live"))
        self.assertEqual(params.get("verify_top"), 3)


if __name__ == "__main__":
    unittest.main()
