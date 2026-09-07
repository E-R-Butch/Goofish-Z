/* Read visible product cards only. No account state, network calls or page actions. */
({platform, limit, detail = false}) => {
  const visible = e => Boolean(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  const text = e => visible(e) ? (e.innerText || e.textContent || '').trim() : '';
  const one = (root, selectors) => [...root.querySelectorAll(selectors)].find(visible);
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const price = e => text(e).replace(/\s+/g, '');
  const body = document.body?.innerText || '';
  const url = new URL(location.href);
  const sourceQuery = url.searchParams.get({taobao:'q',jd:'keyword',pdd:'search_key'}[platform]) || '';
  const verification = /安全验证|拖动.*滑块|完成验证|访问过于频繁|访问受限|请完成下方验证/.test(body);
  const login = /\/login\.|passport\.jd\.com|login\.taobao\.com/.test(url.href) ||
    [...document.querySelectorAll('iframe')].some(e => visible(e) && /login\.taobao\.com|passport\.jd\.com/.test(e.src)) ||
    /请先登录|登录后查看|登录后搜索|请重新登录|登录后即可|登录查看更多|登录享低价/.test(body) ||
    Boolean(one(document, 'input[type="password"], input[autocomplete="current-password"]')) && /登录/.test(body);
  const empty = /没有找到相关|未找到相关|暂无搜索结果|没有搜索到|抱歉，没有找到/.test(body);
  const items = [];
  const seen = new Set();
  function add(item) {
    if (!item.url || !item.title || seen.has(item.url)) return;
    seen.add(item.url);
    items.push({...item, context:clean(item.context).slice(0,3000)});
  }
  if (detail) {
    const title = text(one(document, 'h1, .sku-name, [class*="mainTitle"], [class*="main-title"], [class*="goods-name"], [class*="goodsName"]'));
    const node = one(document, platform === 'jd' ? '.summary-price .price, .p-price .price, [class*="salePrice"], [class*="price-area"] [class*="price"]' :
      platform === 'taobao' ? '[class*="Price--priceText"], [class*="priceText"], .tb-rmb-num' : '[class*="group-price"], [class*="groupPrice"], [class*="goods-price"]');
    add({url:location.href, title, price_text:price(node), context:clean(node?.parentElement?.innerText || ''),
      specification:text(one(document, '#choose-attrs .selected, [aria-checked="true"][role="radio"], [class*="skuItem"][class*="selected"]'))});
  } else if (platform === 'jd') {
    for (const card of document.querySelectorAll('[data-sku]')) {
      if (!visible(card) || items.length >= limit) continue;
      const id = card.getAttribute('data-sku');
      if (!/^\d+$/.test(id)) continue;
      const titleNode = one(card, '[class*="goods_title_container"] [title], .p-name em');
      const p = one(card, '[class*="_price_"], .p-price strong i, .p-price strong');
      add({url:`https://item.jd.com/${id}.html`, title:clean(text(titleNode)), price_text:price(p),
        context:text(card), shop:text(one(card, '[class*="_limit_"], .p-shop a'))});
    }
  } else if (platform === 'taobao') {
    for (const a of document.querySelectorAll('a[href*="item.taobao.com/item.htm"], a[href*="detail.tmall.com/item.htm"]')) {
      if (!visible(a) || items.length >= limit) continue;
      const card = a.closest('[class*="Card--"], [class*="doubleCard"], .item') || a;
      const titleNode = one(card, '[class*="title"], [class*="Title"]');
      const p = one(card, '[class*="priceWrapper"], [class*="price--"], [class*="Price--"], .price');
      add({url:a.href, title:clean(text(titleNode) || one(card,'img[alt]')?.alt), price_text:price(p),
        context:text(card), shop:text(one(card, '[class*="shopName"], .shop'))});
    }
  } else {
    for (const card of document.querySelectorAll('[data-goods-id], a[href*="goods_id="]')) {
      if (!visible(card) || items.length >= limit) continue;
      const id = card.getAttribute('data-goods-id');
      const href = id && /^\d+$/.test(id) ? `https://mobile.yangkeduo.com/goods.html?goods_id=${id}` : card.href;
      const p = one(card, '[class*="price"], [class*="Price"]');
      const title = text(one(card, '[class*="goods-name"], [class*="goodsName"], [class*="title"], [class*="Title"]')) || one(card,'img[alt]')?.alt;
      add({url:href, title:clean(title), price_text:price(p), context:text(card), shop:''});
    }
  }
  const diagnostics = {
    ready_state:document.readyState,
    page_kind:/login|passport/.test(url.pathname + url.hostname)?'login':'content',
    visible_product_nodes:[...document.querySelectorAll('[data-sku], [data-goods-id], a[href*="item.htm"], a[href*="goods_id="]')].filter(visible).length,
    visible_price_nodes:[...document.querySelectorAll('[class*="price"], [class*="Price"]')].filter(visible).length,
    login_signals:['密码登录','短信登录','扫码登录','请登录','立即登录','请先登录','登录后即可'].filter(s=>body.includes(s)),
  };
  return {source_query:sourceQuery, source_url:url.origin + url.pathname, login, verification, empty, items, diagnostics};
}
