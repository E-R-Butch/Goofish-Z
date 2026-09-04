/* External listing text is always rendered as text, never as HTML. */
'use strict';
const $ = id => document.getElementById(id);
let currentJob = null;
let pollTimer = null;

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
    throw new Error(text || `请求失败 (${response.status})`);
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
    $('healthText').textContent = `API 在线 · ${state}` +
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
    row.append(node('td', item.price == null ? '-' : item.price, 'price'));
    const title = node('td');
    title.append(itemLink(item));
    row.append(title, node('td', history ? new Date(item.checked_at * 1000).toLocaleString('zh-CN') : item.location || '', 'mono'));
    table.append(row);
  }
  return table;
}

async function doSearch() {
  const query = $('searchQ').value.trim();
  if (!query || $('searchButton').disabled) return;
  $('searchButton').disabled = true;
  message($('searchResults'), '搜索中…');
  try {
    const result = await api(`/api/search?q=${encodeURIComponent(query)}&limit=15`);
    const box = $('searchResults');
    box.replaceChildren(node('p', `展示 ${result.count || 0} 条 · 屏蔽 ${result.blocked_count || 0} 条`, 'sub'));
    if (result.items?.length) box.append(itemTable(result.items));
    for (const blocked of result.blocked || []) {
      box.append(node('div', `${blocked.title}：${(blocked.reasons || []).join('；')}`, 'alert'));
    }
  } catch (e) { message($('searchResults'), e.message, true); }
  finally { $('searchButton').disabled = false; }
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
$('addWatchButton').addEventListener('click', addWatch);
$('watchQ').addEventListener('keydown', e => { if (e.key === 'Enter') addWatch(); });
$('runAllButton').addEventListener('click', runAll);
$('refreshJobButton').addEventListener('click', resumeJob);
$('cancelJobButton').addEventListener('click', cancelJob);
loadHealth(); loadWatches(); loadAlerts(); resumeJob();
setInterval(loadHealth, 30000);
