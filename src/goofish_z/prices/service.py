"""One collection/analysis implementation shared by HTTP, CLI and MCP."""
from __future__ import annotations

import asyncio
import uuid

from loguru import logger

from .browser import CollectionError, collect_page
from .models import PLATFORMS, analyze, make_quote, utc_now
from .store import save
from .urls import HOME, check_platform, product_url, search_url


def parse_platforms(value: str) -> list[str]:
    values = list(dict.fromkeys(p.strip() for p in value.split(",")))
    if not 1 <= len(values) <= 3:
        raise ValueError("请选择 1 到 3 个平台")
    for platform in values:
        check_platform(platform)
    return values


def collect(query: str, platforms: str = "taobao,jd,pdd", limit: int = 20, url: str = "") -> dict:
    query = query.strip()
    sources = parse_platforms(platforms)
    if not query or len(query) > 200 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("关键词长度须为 1 到 200，每个平台条数须为 1 到 50")
    if url:
        if len(sources) != 1:
            raise ValueError("采集商品链接时只能选择对应的一个平台")
        url = product_url(sources[0], url)[0]

    async def run():
        results, quotes = [], []
        for platform in sources:
            result = {"platform":platform,"name":PLATFORMS[platform],"collected_at":utc_now(),
                      "source_url":url or search_url(platform,query),"login_url":HOME[platform],"count":0}
            try:
                payload = await collect_page(platform,query,limit,url)
                observed_at = utc_now()
                collected, invalid = [], 0
                for item in payload["items"]:
                    try:
                        collected.append(make_quote(platform,item,query,observed_at))
                    except ValueError:
                        invalid += 1
                priced = sum(q.price_low is not None for q in collected)
                missing = len(collected) - priced
                messages = []
                if invalid:
                    messages.append(f"{invalid} 条缺少可核实的标题或商品链接")
                if missing:
                    messages.append(f"{missing} 条商品未解析到有效价格")
                incomplete = invalid or missing
                result.update(status="partial" if incomplete and priced else "parse_failed" if incomplete else "ok",
                              count=len(collected), priced_count=priced, missing_prices=missing,
                              skipped=invalid, collected_at=observed_at, message="；".join(messages))
                quotes.extend(collected)
            except CollectionError as exc:
                result.update(status=exc.status, message=str(exc), retry_after=exc.retry_after, diagnostics=exc.diagnostics)
            except Exception:
                # No raw page, URL parameters, cookies or exception locals in user output.
                logger.warning("Price collection failed for {}", platform)
                result.update(status="failed",message="本平台采集未完成，请检查本机浏览器及登录态")
            results.append(result)
        return results, quotes

    results, quotes = asyncio.run(run())
    succeeded = sum(r["status"] == "ok" for r in results)
    result = {"id":uuid.uuid4().hex,"query":query,"collected_at":utc_now(),
              "status":"succeeded" if succeeded == len(results) else "partial" if any(r["status"] in ("ok","partial") for r in results) else "failed",
              "sources":results,"quotes":[q.model_dump() for q in quotes],"analysis":analyze(quotes)}
    save(result)
    return result
