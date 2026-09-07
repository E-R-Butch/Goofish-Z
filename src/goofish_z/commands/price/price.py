from typing import Any

from goofish_z.core import Strategy, command


@command(namespace="price", name="collect", description="只读采集淘宝、京东、拼多多的商品价格并分析；保留规格和优惠条件，部分失败单独报告", strategy=Strategy.PUBLIC)
def collect(query: str, platforms: str = "taobao,jd,pdd", limit: int = 20, url: str = "") -> dict[str, Any]:
    from goofish_z.prices.service import collect as run
    return run(query, platforms, limit, url)


@command(namespace="price", name="history", description="读取本机已保存的电商价格采集和分析历史，不请求电商平台", strategy=Strategy.PUBLIC)
def history(query: str, limit: int = 20) -> dict[str, Any]:
    from goofish_z.prices.store import history as read
    return {"query":query.strip(),"runs":read(query,limit)}


@command(namespace="price", name="import-chrome", description="用户明确选择平台后，仅将该平台的 Chrome 登录态导入本机私有价格采集目录；不打印凭据", strategy=Strategy.PUBLIC, write=True)
def import_chrome(platform: str) -> dict[str, Any]:
    from goofish_z.prices.browser import import_chrome as run
    return run(platform)


@command(namespace="price", name="login", description="打开指定平台的独立 Chrome 采集窗口，由用户手动登录；完成后关闭该标签页，不自动输入账号或处理验证", strategy=Strategy.PUBLIC, write=True)
def login(platform: str, timeout_seconds: int = 300) -> dict[str, Any]:
    from goofish_z.prices.browser import login as run
    return run(platform, timeout_seconds)
