import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from support import OfflineCase
from goofish_z.prices.browser import CollectionError, cookie_allowed
from goofish_z.prices.models import analyze, make_quote, match_title, parse_price
from goofish_z.prices.service import collect
from goofish_z.prices.store import history
from goofish_z.prices.urls import allowed_document, product_url, search_url


def card(platform="jd", **changes):
    urls = {"jd":"https://item.jd.com/100.html", "taobao":"https://item.taobao.com/item.htm?id=100", "pdd":"https://mobile.yangkeduo.com/goods.html?goods_id=100"}
    return {"url":urls[platform],"title":"合成 RTX5060 8G 显卡","price_text":"¥2400","context":"合成商品 ¥2400",**changes}


class MarketPricesTest(OfflineCase):
    def test_equivalent_model_and_capacity_spellings_match_in_both_directions(self):
        for first, second in [("RTX 4070 Ti SUPER 16G", "RTX4070 TiS 16GB"),
                              ("RTX5060 8GB", "RTX5060 8G"),
                              ("ＲＴＸ５０６０ ８ＧＢ", "RTX5060 8G")]:
            for query, title in ((first, second), (second, first)):
                with self.subTest(query=query, title=title):
                    self.assertEqual(match_title(query, title)[0], "title_match")
        self.assertEqual(match_title("RTX4070 Ti SUPER 16GB", "RTX4070 Ti 16G")[0], "mismatch")

    def test_whole_computer_is_excluded_from_gpu_price_distribution(self):
        for title in ("RTX5060 8G 电脑整机", "RTX5060 8G 游戏主机", "RTX5060 8G 水冷头"):
            with self.subTest(title=title):
                quote = make_quote("jd", card(title=title), "RTX5060 8G", "synthetic")
                self.assertEqual(quote.match, "uncertain")
                self.assertEqual(analyze([quote])["groups"], [])
        self.assertEqual(match_title("RTX5060 8G 整机", "RTX5060 8G 电脑整机")[0], "title_match")

    def test_mixed_missing_prices_report_partial_and_keep_unknown_observations(self):
        payload = {"items": [card(), card(url="https://item.jd.com/101.html", price_text="面议")]}
        with patch("goofish_z.prices.service.collect_page", AsyncMock(return_value=payload)):
            result = collect("RTX5060 8G", "jd")
        self.assertEqual(result["status"], "partial")
        source = result["sources"][0]
        self.assertEqual((source["count"], source["priced_count"], source["missing_prices"]), (2, 1, 1))
        self.assertEqual(source["status"], "partial")
        self.assertIn("1 条商品未解析到有效价格", source["message"])
        self.assertEqual(result["quotes"][1]["price_kind"], "unknown")
        self.assertEqual(history("RTX5060 8G")[0]["status"], "partial")

    def test_price_units_ranges_and_unknowns(self):
        for value, expected in [("¥ 1.45 万",(14500,14500,False)),("¥2,400",(2400,2400,False)),
                                ("¥100-200",(100,200,True)),("¥99起",(99,None,True)),
                                ("¥0",(None,None,False)),("满100减20",(None,None,False)),
                                ("定金100",(None,None,False)),("1-0",(None,None,False)),
                                ("NaN",(None,None,False)),("5060 8G",(None,None,False))]:
            with self.subTest(value=value):self.assertEqual(parse_price(value),expected)

    def test_conditional_price_is_not_mistaken_for_unconditional_or_discounted_twice(self):
        q=make_quote("jd",card(context="补贴到手价 ¥2400 新客礼金100 券满2000减100 PLUS会员"),"RTX5060 8G","synthetic")
        self.assertEqual(q.price_low,2400)
        self.assertEqual(q.price_kind,"conditional")
        self.assertGreaterEqual(len(q.conditions),4)
        self.assertFalse(q.specification_confirmed)

    def test_pdd_list_price_is_not_assumed_to_be_single_purchase_price(self):
        q=make_quote("pdd",card("pdd"),"RTX5060 8G","synthetic")
        self.assertEqual(q.price_kind,"conditional")
        self.assertTrue(any("拼单" in s for s in q.conditions))

    def test_gpu_model_capacity_and_whole_pc_mismatches(self):
        self.assertEqual(match_title("4090 48G","RTX4090D 48G")[0],"mismatch")
        self.assertEqual(match_title("4080S 32G","RTX4080 SUPER 32G")[0],"title_match")
        self.assertEqual(match_title("5060 8G","RTX5060 16G")[0],"mismatch")
        for title in ("RTX5060/5070 8G", "RTX5060 8G 台式电脑主机 组装机", "RTX5060 8G/16G", "RTX5060"):
            with self.subTest(title=title):self.assertEqual(match_title("RTX5060 8G",title)[0],"uncertain")

    def test_analysis_separates_conditional_quotes_and_excludes_wrong_specs(self):
        quotes=[make_quote("jd",card(),"RTX5060 8G","synthetic"),
                make_quote("jd",card(price_text="¥100",context="新人优惠到手价"),"RTX5060 8G","synthetic"),
                make_quote("jd",card(price_text="¥1",title="RTX4090 24G"),"RTX5060 8G","synthetic")]
        report=analyze(quotes)
        self.assertEqual(report["title_matched"],2)
        self.assertEqual({g["price_kind"]:g["median"] for g in report["groups"]},{"listed":2400,"conditional":100})
        self.assertIn("不代表同款",report["basis"])

    def test_canonical_urls_drop_tracking_and_reject_foreign_hosts(self):
        self.assertEqual(product_url("jd","https://item.jd.com/100.html?tracking=private"),("https://item.jd.com/100.html","100"))
        for url in ("http://item.jd.com/100.html","https://item.jd.com.evil.invalid/100.html", "https://127.0.0.1/100.html",
                    "https://item.jd.com:8787/100.html","https://secret@item.jd.com/100.html","https://item.jd.com/redirect?url=http://localhost"):
            with self.subTest(url=url),self.assertRaises(ValueError):product_url("jd",url)
        self.assertFalse(allowed_document("jd","https://item.jd.com.evil.invalid/a"))
        self.assertFalse(allowed_document("jd","http://localhost/"))
        self.assertIn("search_key=RTX+5060",search_url("pdd","RTX 5060"))

    def test_cookie_import_never_accepts_another_platform_or_suffix_trick(self):
        base={"name":"synthetic","value":"synthetic"}
        self.assertTrue(cookie_allowed("jd",{**base,"domain":".jd.com"}))
        for host in (".taobao.com",".jd.com.evil.invalid","eviljd.com"):
            self.assertFalse(cookie_allowed("jd",{**base,"domain":host}))

    def test_partial_failure_is_saved_as_a_new_run_and_never_backfilled(self):
        payload={"items":[card()]}
        with patch("goofish_z.prices.service.collect_page",AsyncMock(side_effect=[CollectionError("login_required","synthetic login"),payload,CollectionError("verification_required","synthetic verification")])):
            first=collect("RTX5060 8G")
        self.assertEqual(first["status"],"partial")
        self.assertEqual(len(first["quotes"]),1)
        with patch("goofish_z.prices.service.collect_page",AsyncMock(side_effect=CollectionError("timeout","synthetic timeout"))):
            second=collect("RTX4080S 32G")
        self.assertEqual(second["status"],"failed")
        self.assertEqual(second["quotes"],[])
        self.assertNotEqual(first["id"],second["id"])
        self.assertEqual(history("RTX5060 8G")[0]["id"],first["id"])
        self.assertEqual(history("RTX4080S 32G")[0]["quotes"],[])
        self.assertEqual((self.root/"prices.db").stat().st_mode&0o777,0o600)

    def test_all_missing_prices_are_a_failure_not_success(self):
        with patch("goofish_z.prices.service.collect_page",AsyncMock(return_value={"items":[card(price_text="面议")]})):
            data=collect("RTX5060 8G","jd")
        self.assertEqual(data["status"],"failed")
        self.assertEqual(data["sources"][0]["status"],"parse_failed")
        self.assertEqual(data["analysis"]["groups"][0]["median"],None)

    def test_invalid_requests_do_not_open_browser_or_create_history(self):
        with patch("goofish_z.prices.service.collect_page",AsyncMock()) as browser:
            for args in (("",),("x","unknown"),("x","jd",True),("x","jd,pdd",1,"https://item.jd.com/100.html")):
                with self.subTest(args=args),self.assertRaises(ValueError):collect(*args)
        browser.assert_not_called()
        self.assertFalse((self.root/"prices.db").exists())

    def test_http_registry_shares_the_real_price_command(self):
        import importlib
        api=importlib.import_module("goofish_z.api.app")
        api._COMMANDS=None
        with TestClient(api.app) as client,patch("goofish_z.prices.service.collect_page",AsyncMock(return_value={"items":[card()]})) as read:
            response=client.post("/api/price/collect",json={"query":"RTX5060 8G","platforms":"jd"})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()["quotes"][0]["price_low"],2400)
            read.assert_awaited_once_with("jd","RTX5060 8G",20,"")
            self.assertEqual(client.get("/api/price/history",params={"q":"RTX5060 8G"}).json()["runs"][0]["id"],response.json()["id"])
            self.assertEqual(client.post("/api/price/collect",json={"query":"x","limit":0}).status_code,422)
            self.assertEqual(client.post("/api/price/import-chrome",json={"platform":"unrelated"}).status_code,422)

    def test_documented_cli_collect_and_history_use_positional_keywords(self):
        from typer.testing import CliRunner
        from goofish_z.cli import app
        with patch("goofish_z.prices.service.collect_page", AsyncMock(return_value={"items":[card()]})) as read:
            run = CliRunner().invoke(app, ["price", "collect", "RTX5060 8G", "--platforms", "jd", "--format", "json"])
            self.assertEqual(run.exit_code, 0, run.output)
            saved = json.loads(run.stdout)
            self.assertEqual(saved["quotes"][0]["price_low"], 2400)
            read.assert_awaited_once_with("jd", "RTX5060 8G", 20, "")
            previous = CliRunner().invoke(app, ["price", "history", "RTX5060 8G", "--format", "json"])
            self.assertEqual(previous.exit_code, 0, previous.output)
            self.assertEqual(json.loads(previous.stdout)["runs"][0]["id"], saved["id"])
            self.assertEqual(read.await_count, 1)

    def test_cli_failed_price_collection_has_nonzero_status_and_no_quotes(self):
        from typer.testing import CliRunner
        from goofish_z.cli import app
        with patch("goofish_z.prices.service.collect_page", AsyncMock(side_effect=CollectionError("login_required", "synthetic login"))):
            run = CliRunner().invoke(app, ["price", "collect", "RTX5060 8G", "--platforms", "jd", "--format", "json"])
        self.assertEqual(run.exit_code, 1)
        self.assertEqual(json.loads(run.stdout)["quotes"], [])
        self.assertEqual(json.loads(run.stdout)["sources"][0]["status"], "login_required")
