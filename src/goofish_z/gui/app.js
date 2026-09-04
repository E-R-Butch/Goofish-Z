/* External listing text is always rendered as text, never as HTML. */
'use strict';
const $ = id => document.getElementById(id);
let currentJob = null;
let pollTimer = null;
let searchState = null;
let searchBusy = false;
let filteredOpen = false;
const searchCache = new Map();

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = String(text);
  if (className) element.className = className;
  return element;
}

function message(target, text, error = false) {
  target.replaceChildren(node('div', text, error ? 'alert' : 'sub'));
}

function itemLink(item) {
  const title = String(item.title || '未命名商品');
  try {
    const url = new URL(item.url);
    if (!['http:', 'https:'].includes(url.protocol)) return node('span', title);
    const link = node('a', title);
    link.href = url.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  } catch { return node('span', title); }
}

function action(text, handler) {
  const button = node('button', text);
  button.type = 'button';
  button.addEventListener('click', handler);
  return button;
}

async function api(path, opts = {}) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts});
  const body = await response.json();
  if (!response.ok) {
    const detail = body.detail || body.error;
    const text = Array.isArray(detail) ? detail.map(e => e.msg).join('；') : detail;
    const error = new Error(text || `请求失败 (${response.status})`);
    error.status = response.status;
    const retryAfter = Number(response.headers.get('Retry-After'));
    error.retryAfter = Number.isFinite(retryAfter) && retryAfter > 0 ? Math.ceil(retryAfter) : null;
    throw error;
  }
  return body;
}

async function loadHealth() {
  try {
    await api('/health');
    const d = await api('/api/diagnostics');
    const auth = d.auth.last_check;
    const state = !d.auth.cookies_present ? '尚未登录' : auth ?
      `上次登录验证${auth.valid ? '通过' : '失败'} (${new Date(auth.checked_at * 1000).toLocaleString('zh-CN')})` : '登录状态待验证';
    $('health').className = d.circuit.tripped ? 'status err' : 'status';
    $('healthText').textContent = `API${d.version ? ` v${d.version}` : ''} 在线 · ${state}` +
      (d.circuit.tripped ? ` · 风控冷却 ${d.circuit.remaining_seconds} 秒` : '');
  } catch (e) {
    $('health').className = 'status err';
    $('healthText').textContent = `状态检查失败：${e.message}`;
  }
}

function itemTable(items, history = false) {
  const table = node('table');
  const header = node('tr');
  for (const title of ['价格', '标题', history ? '时间' : '地区']) header.append(node('th', title));
  table.append(header);
  for (const item of items) {
    const row = node('tr');
    const price = node('td', priceLabel(item), 'price');
    if (item.price_text && /[万千]/.test(item.price_text)) {
      price.append(node('div', `页面标价 ${item.price_text}`, 'price-source'));
    }
    row.append(price);
    const title = node('td');
    title.append(itemLink(item));
    row.append(title, node('td', history ? new Date(item.checked_at * 1000).toLocaleString('zh-CN') : item.location || '', 'mono'));
    table.append(row);
  }
  return table;
}

function itemPrice(item) {
  if (item.price_value !== undefined) {
    return typeof item.price_value === 'number' && Number.isFinite(item.price_value) && item.price_value >= 0 ? item.price_value : null;
  }
  const match = String(item.price ?? '').replace(/[\s,，]/g, '').match(/^[¥￥]?(\d+(?:\.\d+)?)(万|千)?元?$/);
  if (!match) return null;
  const amount = Number(match[1]) * ({万: 10000, 千: 1000}[match[2]] || 1);
  return Number.isFinite(amount) ? amount : null;
}

function priceLabel(item) {
  const amount = itemPrice(item);
  return amount == null ? (item.price || '-') : `¥${amount.toLocaleString('zh-CN', {maximumFractionDigits: 2})}`;
}

function toggleFilteredResults() {
  filteredOpen = !filteredOpen;
  renderSearch();
  $('filteredToggle')?.focus?.();
}

function filteredDetails(items) {
  const panel = node('section', null, 'filtered-details');
  panel.id = 'filteredDetails';
  panel.append(node('h4', `已过滤内容（${items.length} 条）`));
  for (const item of items) {
    const entry = node('article', null, 'filtered-item');
    const heading = node('div', null, 'filtered-item-heading');
    heading.append(node('span', priceLabel(item), 'price'), itemLink(item));
    entry.append(heading, node('div', `${item.filterSource} · ${item.location || '地区未标注'}`, 'mono'));
    if (item.price_text && /[万千]/.test(item.price_text)) entry.append(node('div', `页面标价 ${item.price_text}`, 'price-source'));
    const reasons = node('ul');
    for (const reason of item.reasons || []) reasons.append(node('li', reason));
    entry.append(reasons);
    panel.append(entry);
  }
  return panel;
}

function updateSearchControls() {
  $('searchButton').disabled = searchBusy;
  $('searchQ').disabled = searchBusy;
  $('searchPrevButton').disabled = searchBusy || !searchState || searchState.page <= 1;
  $('searchNextButton').disabled = searchBusy || !searchState?.result.has_next;
  $('searchPageLabel').textContent = searchState ?
    `第 ${searchState.page} 页${searchState.result.has_next ? '' : ' · 已到末页'}` : '每页最多 30 条';
}

function renderSearch() {
  if (!searchState || searchBusy) return;
  updateSearchControls();
  const {result, query, page} = searchState;
  const min = $('searchMin').value.trim() === '' ? null : Number($('searchMin').value);
  const max = $('searchMax').value.trim() === '' ? null : Number($('searchMax').value);
  if ([min, max].some(n => n != null && (!Number.isFinite(n) || n < 0)) ||
      (min != null && max != null && min > max)) {
    return message($('searchResults'), '价格范围必须是非负数，最低价不能高于最高价。', true);
  }
  const words = id => $(id).value.trim().toLowerCase().split(/[\s,，]+/).filter(Boolean);
  const include = words('searchInclude'), exclude = words('searchExclude');
  const location = $('searchLocation').value.trim().toLowerCase();
  const hidden = [];
  const items = (result.items || []).filter(item => {
    const title = String(item.title || '').toLowerCase();
    const amount = itemPrice(item);
    const reasons = [];
    for (const word of include) if (!title.includes(word)) reasons.push(`标题未包含“${word}”`);
    for (const word of exclude) if (title.includes(word)) reasons.push(`标题包含排除词“${word}”`);
    if (!String(item.location || '').toLowerCase().includes(location)) reasons.push(`地区“${item.location || '未标注'}”不匹配“${location}”`);
    if (amount == null && (min != null || max != null)) reasons.push('价格不明确，无法判断是否在所选范围内');
    if (amount != null && min != null && amount < min) reasons.push(`${priceLabel(item)} 低于最低价 ¥${min}`);
    if (amount != null && max != null && amount > max) reasons.push(`${priceLabel(item)} 高于最高价 ¥${max}`);
    if (reasons.length) hidden.push({...item, reasons, filterSource: '当前筛选条件'});
    return !reasons.length;
  });
  const sort = $('searchSort').value;
  if (sort === 'price_asc' || sort === 'price_desc') items.sort((a, b) => {
    const x = itemPrice(a), y = itemPrice(b);
    if (x == null) return y == null ? 0 : 1;
    if (y == null) return -1;
    return (x - y) * (sort === 'price_asc' ? 1 : -1);
  });
  const excluded = [
    ...(result.filtered || []).map(item => ({...item, filterSource: '自动过滤'})),
    ...(result.blocked || []).map(item => ({...item, filterSource: '屏蔽规则'})),
    ...hidden,
  ];
  const box = $('searchResults');
  const summary = node('div', null, 'search-summary');
  summary.append(node('p', `“${query}” · 第 ${page} 页 · 显示 ${items.length} / ${result.items?.length || 0} 条 · 自动过滤 ${result.filtered_count || 0} 条 · 屏蔽 ${result.blocked_count || 0} 条` +
    (hidden.length ? ` · 条件筛选 ${hidden.length} 条` : ''), 'sub'));
  const trash = action(`🗑 ${excluded.length}`, toggleFilteredResults);
  trash.id = 'filteredToggle';
  trash.className = 'filter-trash';
  trash.disabled = !excluded.length;
  trash.title = excluded.length ? '查看被过滤的商品和原因' : '暂无被过滤的商品';
  trash.setAttribute('aria-label', `查看 ${excluded.length} 条被过滤的结果`);
  trash.setAttribute('aria-expanded', String(filteredOpen && Boolean(excluded.length)));
  trash.setAttribute('aria-controls', 'filteredDetails');
  summary.append(trash);
  box.replaceChildren(summary);
  if (filteredOpen && excluded.length) box.append(filteredDetails(excluded));
  if (items.length) box.append(itemTable(items));
  else box.append(node('div', '当前页没有符合条件的商品，可调整筛选或查看下一页。', 'sub'));
}

function resetSearchFilters() {
  for (const id of ['searchMin', 'searchMax', 'searchLocation', 'searchInclude', 'searchExclude']) $(id).value = '';
  $('searchSort').value = 'default';
  renderSearch();
}

function waitForSearchSlot(seconds, query) {
  const deadline = Date.now() + seconds * 1000;
  return new Promise(resolve => {
    function tick() {
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      if (!remaining) return resolve();
      $('searchButton').textContent = `等待 ${remaining} 秒`;
      message($('searchResults'), `搜索间隔还剩 ${remaining} 秒，结束后自动继续检索“${query}”。`);
      setTimeout(tick, Math.min(1000, deadline - Date.now()));
    }
    tick();
  });
}

async function searchWithCooldown(path, query) {
  try { return await api(path); }
  catch (error) {
    if (error.status !== 429 || !error.retryAfter) throw error;
    await waitForSearchSlot(error.retryAfter, query);
    $('searchButton').textContent = '搜索中…';
    message($('searchResults'), `正在检索“${query}”…`);
    // Retry this read once; another busy response goes back to the user.
    return api(path);
  }
}

async function doSearch() {
  const query = $('searchQ').value.trim();
  if (!query || searchBusy) return;
  return fetchSearchPage(query, 1, true);
}

async function changeSearchPage(page) {
  if (!searchState || searchBusy || page < 1) return;
  if (searchCache.has(page)) {
    filteredOpen = false;
    searchState = {...searchState, page, result: searchCache.get(page)};
    renderSearch();
    return;
  }
  return fetchSearchPage(searchState.query, page, false);
}

async function fetchSearchPage(query, page, fresh) {
  searchBusy = true;
  updateSearchControls();
  $('searchButton').textContent = '搜索中…';
  message($('searchResults'), `正在检索“${query}”第 ${page} 页…翻页也会等待搜索间隔。`);
  try {
    const result = await searchWithCooldown(`/api/search?q=${encodeURIComponent(query)}&limit=30&page=${page}`, query);
    if (fresh) searchCache.clear();
    searchCache.set(page, result);
    filteredOpen = false;
    searchState = {query, page, result};
    searchBusy = false;
    renderSearch();
  } catch (e) {
    const busy = e.status === 429 && e.retryAfter;
    searchBusy = false;
    renderSearch();
    const text = busy ? `搜索间隔仍需等待 ${e.retryAfter} 秒，请稍后重试。` : e.message;
    if (searchState) $('searchResults').append(node('div', `第 ${page} 页加载失败：${text}`, 'alert'));
    else message($('searchResults'), text, !busy);
  } finally {
    searchBusy = false;
    $('searchButton').textContent = '搜索';
    updateSearchControls();
  }
}

async function loadWatches() {
  try {
    const {watches = []} = await api('/api/watch');
    const box = $('watchList');
    box.replaceChildren();
    if (!watches.length) return message(box, '暂无监控项');
    for (const watch of watches) {
      const row = node('div', null, 'watch-item');
      const info = node('div');
      info.append(node('div', `${watch.keyword}${watch.enabled ? '' : ' · 已停用'}`));
      info.append(node('div', `ID ${watch.id}${watch.max_price == null ? '' : ` · 低价线 ¥${watch.max_price}`}`, 'mono'));
      const controls = node('div');
      controls.append(action('历史', () => showHistory(watch.id)), action('删除', () => delWatch(watch.id)));
      row.append(info, controls);
      box.append(row);
    }
  } catch (e) { message($('watchList'), e.message, true); }
}

async function addWatch() {
  const keyword = $('watchQ').value.trim();
  if (!keyword || $('addWatchButton').disabled) return;
  const max_price = $('watchMax').value === '' ? null : Number($('watchMax').value);
  if (max_price != null && (!Number.isFinite(max_price) || max_price < 0)) {
    return message($('watchError'), '低价线必须是非负数', true);
  }
  $('addWatchButton').disabled = true;
  try {
    await api('/api/watch', {method: 'POST', body: JSON.stringify({keyword, max_price})});
    $('watchQ').value = '';
    $('watchMax').value = '';
    $('watchError').replaceChildren();
    await loadWatches();
  } catch (e) { message($('watchError'), e.message, true); }
  finally { $('addWatchButton').disabled = false; }
}

async function delWatch(id) {
  try {
    await api(`/api/watch/${id}`, {method: 'DELETE'});
    await Promise.all([loadWatches(), loadAlerts()]);
  } catch (e) { message($('watchError'), e.message, true); }
}

function showRun(result) {
  const names = {succeeded: '完成', partial: '部分失败', failed: '失败', cancelled: '已取消'};
  const captured = (result.results || []).reduce((n, r) => n + (r.captured || 0), 0);
  message($('runResult'), `${names[result.status] || result.status} · 成功 ${result.succeeded || 0} 项 · 失败 ${result.failed || 0} 项 · 跳过 ${result.skipped || 0} 项 · 捕获 ${captured} 条`);
  if (result.error) $('runResult').append(node('div', result.error, 'alert'));
  for (const r of result.results || []) {
    if (r.error) $('runResult').append(node('div', `${r.keyword}：${r.error}`, 'alert'));
  }
}

async function pollJob(id) {
  clearTimeout(pollTimer);
  currentJob = id;
  $('runAllButton').disabled = true;
  $('cancelJobButton').hidden = false;
  try {
    const job = await api(`/api/watch/jobs/${id}`);
    if (job.result) {
      showRun(job.result);
      currentJob = null;
      $('runAllButton').disabled = false;
      $('cancelJobButton').hidden = true;
      await Promise.all([loadAlerts(), loadWatches()]);
      return;
    }
    const p = job.progress || {};
    const phase = p.phase === 'waiting' ? `等待搜索间隔，约 ${Math.ceil(p.retry_after)} 秒` : '检查中';
    $('runResult').textContent = `${job.cancel_requested ? '正在取消，等待当前请求结束' : phase} · ${p.completed || 0}/${p.total || 0} · ${p.keyword || ''}`;
    pollTimer = setTimeout(() => pollJob(id), 1000);
  } catch (e) {
    message($('runResult'), `进度读取失败：${e.message}。可点击“刷新进度”重新连接。`, true);
  }
}

async function runAll() {
  if ($('runAllButton').disabled) return;
  $('runAllButton').disabled = true;
  try {
    const job = await api('/api/watch/jobs', {method: 'POST', body: JSON.stringify({all: true})});
    await pollJob(job.id);
  } catch (e) {
    $('runAllButton').disabled = false;
    message($('runResult'), e.message, true);
  }
}

async function resumeJob() {
  try {
    const {jobs = []} = await api('/api/watch/jobs');
    if (jobs[0]) await pollJob(jobs[0].id);
    else {
      clearTimeout(pollTimer);
      currentJob = null;
      $('runAllButton').disabled = false;
      $('cancelJobButton').hidden = true;
      $('runResult').textContent = '暂无运行任务';
    }
  } catch (e) { message($('runResult'), e.message, true); }
}

async function cancelJob() {
  if (!currentJob) return;
  try {
    await api(`/api/watch/jobs/${currentJob}`, {method: 'DELETE'});
    await pollJob(currentJob);
  } catch (e) { message($('runResult'), e.message, true); }
}

async function showHistory(id) {
  try {
    const {items = []} = await api(`/api/watch/${id}/history?limit=100`);
    $('histWatchLabel').textContent = `(监控 ${id})`;
    const latest = new Map();
    for (const item of items) if (!latest.has(item.item_id)) latest.set(item.item_id, item);
    if (!latest.size) return message($('history'), '暂无历史');
    $('history').replaceChildren(itemTable([...latest.values()], true));
  } catch (e) { message($('history'), e.message, true); }
}

async function loadAlerts() {
  try {
    const {alerts = []} = await api('/api/alerts?unread_only=true');
    const box = $('alerts');
    box.replaceChildren();
    if (!alerts.length) return message(box, '暂无未读告警');
    for (const alert of alerts) {
      const row = node('div', null, 'alert');
      row.append(node('b', `¥${alert.price} `), itemLink(alert), node('span', ` · ${alert.reason} `));
      row.append(action('标为已读', async () => {
        try {
          await api(`/api/alerts/${alert.id}/read`, {method: 'POST'});
          await loadAlerts();
        } catch (e) { message(box, e.message, true); }
      }));
      box.append(row);
    }
  } catch (e) { message($('alerts'), e.message, true); }
}

$('searchButton').addEventListener('click', doSearch);
$('searchQ').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
$('searchPrevButton').addEventListener('click', () => changeSearchPage(searchState.page - 1));
$('searchNextButton').addEventListener('click', () => changeSearchPage(searchState.page + 1));
$('resetFiltersButton').addEventListener('click', resetSearchFilters);
for (const id of ['searchMin', 'searchMax', 'searchLocation', 'searchInclude', 'searchExclude']) $(id).addEventListener('input', renderSearch);
$('searchSort').addEventListener('change', renderSearch);
$('addWatchButton').addEventListener('click', addWatch);
$('watchQ').addEventListener('keydown', e => { if (e.key === 'Enter') addWatch(); });
$('runAllButton').addEventListener('click', runAll);
$('refreshJobButton').addEventListener('click', resumeJob);
$('cancelJobButton').addEventListener('click', cancelJob);
loadHealth(); loadWatches(); loadAlerts(); resumeJob();
setInterval(loadHealth, 30000);
