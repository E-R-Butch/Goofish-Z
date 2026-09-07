/* Shared HTTP collector, never fabricated/static sample prices. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const names = {taobao:'淘宝／天猫',jd:'京东',pdd:'拼多多'};
  const kinds = {listed:'页面标价',conditional:'附条件价格',range:'区间／起步价',unknown:'价格未识别'};
  const states = {ok:'采集完成',partial:'部分完成',login_required:'需要登录',verification_required:'需要安全验证',rate_limited:'采集间隔中',busy:'平台正忙',timeout:'加载超时',parse_failed:'解析未完成',source_mismatch:'来源不一致',access_failed:'平台访问失败',failed:'采集失败'};
  let busy = false;
  function element(tag, content, cls) {
    const e = document.createElement(tag);
    if (content != null) e.textContent = String(content);
    if (cls) e.className = cls;
    return e;
  }
  function status(text, error = false) { byId('priceStatus').replaceChildren(element('p',text,error?'alert':'sub')); }
  function lock(value) {
    busy = value;
    for (const e of byId('priceForm').querySelectorAll('input,select,button')) e.disabled = value;
  }
  async function request(path, body) {
    const r = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
    const data = await r.json();
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${r.status})`);
    return data;
  }
  function clear() { for (const id of ['priceSources','priceAnalysis','priceQuotes','priceRuns']) byId(id).replaceChildren(); }
  function money(value) { return value == null ? '—' : `¥${value.toLocaleString('zh-CN',{maximumFractionDigits:2})}`; }
  function link(text, url) {
    const a = element('a',text);
    try {
      const u = new URL(url);
      if (u.protocol !== 'https:') return element('span',text);
      a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';return a;
    } catch { return element('span',text); }
  }
  function render(data, historic = false) {
    byId('priceSources').replaceChildren();byId('priceAnalysis').replaceChildren();byId('priceQuotes').replaceChildren();
    const finished = data.status === 'succeeded' ? '采集完成' : data.status === 'partial' ? '部分平台未完成' : '采集未完成';
    status(`${historic?'历史记录 · ':''}“${data.query}” · ${new Date(data.collected_at).toLocaleString('zh-CN')} · ${finished}`,data.status !== 'succeeded');
    for (const source of data.sources) {
      const card=element('div',null,'market-source');
      card.append(element('strong',`${names[source.platform]} · ${states[source.status] || source.status}`),element('p',`识别 ${source.count} 条${source.priced_count==null?'':` · 有效价格 ${source.priced_count} 条`}${source.retry_after?` · ${source.retry_after} 秒后可重试`:''}`));
      if(source.message) card.append(element('p',source.message));
      card.append(link('打开平台',source.login_url));
      if(['login_required','verification_required','parse_failed','failed'].includes(source.status)) {
        const b=element('button','导入此平台 Chrome 登录态');b.type='button';
        b.addEventListener('click',async()=>{
          if(busy)return;
          if(!confirm(`仅将 Chrome 中${names[source.platform]}的登录态导入本机私有目录，供后台只读采价。不会上传登录态，也不会读取其他平台。确认导入？`))return;
          b.disabled=true;
          try{const r=await request('/api/price/import-chrome',{platform:source.platform});status(`${names[source.platform]}：${r.message}。可重新采集。`);}
          catch(e){status(e.message,true);}finally{b.disabled=false;}
        });card.append(b);
      }
      byId('priceSources').append(card);
    }
    byId('priceAnalysis').append(element('p',data.analysis.basis,'sub'));
    for(const group of data.analysis.groups) {
      byId('priceAnalysis').append(element('p',`${names[group.platform]} · ${kinds[group.price_kind]} · ${group.count} 条 · ${group.price_kind==='range'?'起价范围':'标价范围'} ${money(group.min)} ～ ${money(group.max)}${group.median==null?'':` · 中位数 ${money(group.median)}`}`,'sub'));
    }
    if(!data.quotes.length){byId('priceQuotes').append(element('p','本次没有采集到商品；历史结果不会替代本次结果。','sub'));return;}
    const table=element('table'), head=element('tr');
    for(const h of ['平台','商品与店铺','观察价格','优惠与规格','相关性'])head.append(element('th',h));table.append(head);
    for(const q of data.quotes){
      const tr=element('tr'), title=element('td');title.append(link(q.title,q.url),element('div',q.shop,'sub'));
      const price=element('td');price.append(element('strong',`${money(q.price_low)}${q.price_kind==='range'?q.price_high==null?' 起':` ～ ${money(q.price_high)}`:''}`),element('p',kinds[q.price_kind],'sub'),element('p',`原文 ${q.price_text||'未展示'}`,'mono'));
      const conditions=element('td');for(const t of [...q.conditions,...(q.specification?[`页面规格：${q.specification}`]:[]),'具体 SKU 尚未核实'])conditions.append(element('p',t,'sub'));
      const match=element('td',q.match==='title_match'?'标题相关':q.match==='mismatch'?'不匹配':'待确认');for(const reason of q.reasons)match.append(element('p',reason,'sub'));
      tr.append(element('td',names[q.platform]),title,price,conditions,match);table.append(tr);
    }byId('priceQuotes').append(table);
  }
  byId('priceForm').addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    const query=byId('priceQuery').value.trim(), platforms=[...document.querySelectorAll('[name="pricePlatform"]:checked')].map(e=>e.value).join(',');
    if(!query||!platforms){status('请输入关键词并选择平台。',true);return;}
    const payload={query,platforms,limit:Number(byId('priceLimit').value),url:byId('priceUrl').value.trim()};
    clear();lock(true);status(`正在采集“${query}”… 每个平台会独立打开 Chrome 读取页面，可能需要约一分钟。`);
    try{const data=await request('/api/price/collect',payload);if(data.query!==query)throw new Error('关键词不一致，已丢弃结果');render(data);}
    catch(e){status(`“${query}”采集失败：${e.message}`,true);}finally{lock(false);}
  });
  byId('priceHistoryButton').addEventListener('click',async()=>{
    if(busy)return;const query=byId('priceQuery').value.trim();if(!query){status('请输入要查看历史的关键词。',true);return;}
    clear();lock(true);
    try{const data=await request(`/api/price/history?q=${encodeURIComponent(query)}&limit=20`);status(`“${query}”有 ${data.runs.length} 次历史采集`);
      for(const run of data.runs){const b=element('button',`${new Date(run.collected_at).toLocaleString('zh-CN')} · ${run.status}`,'market-run');b.type='button';b.addEventListener('click',()=>{if(!busy)render(run,true);});byId('priceRuns').append(b);}}
    catch(e){status(e.message,true);}finally{lock(false);}
  });
})();
