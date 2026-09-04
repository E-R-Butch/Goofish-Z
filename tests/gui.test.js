const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.text = '';
    this.listeners = {}; this.value = ''; this.disabled = false;
  }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(n => n.textContent).join(''); }
  set innerHTML(_) { throw new Error('Untrusted data must not enter an HTML sink'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  setAttribute(name, value) { (this.attributes ||= {})[name] = String(value); }
}

async function screen(overrides = {}) {
  const elements = new Map();
  const document = {
    createElement: tag => new Element(tag),
    getElementById: id => {
      if (!elements.has(id)) elements.set(id, new Element('div'));
      return elements.get(id);
    },
  };
  const responses = {
    '/health': {status: 'ok'},
    '/api/diagnostics': {auth: {cookies_present: false, last_check: null}, circuit: {tripped: false}},
    '/api/watch': {watches: []},
    '/api/alerts?unread_only=true': {alerts: []},
    '/api/watch/jobs': {jobs: []},
    ...overrides,
  };
  const requests = [];
  let now = 0;
  let nextTimer = 1;
  const timers = new Map();
  class ClockDate extends Date { static now() { return now; } }
  const context = vm.createContext({
    document, URL, Date: ClockDate,
    setTimeout: (fn, delay) => { const id = nextTimer++; timers.set(id, {fn, at: now + delay}); return id; },
    clearTimeout: id => timers.delete(id), setInterval() {},
    fetch: async (url, options) => {
      requests.push([url, options]);
      const value = typeof responses[url] === 'function' ? responses[url]() : responses[url];
      if (value === undefined) throw new Error(`Unexpected test request: ${url}`);
      const status = value.testStatus || (value.testError ? 400 : 200);
      return {ok: status < 400, status, headers: {get: name => value.testHeaders?.[name] ?? null}, json: async () => value};
    },
  });
  async function advance(ms) {
    const end = now + ms;
    for (;;) {
      const next = [...timers.entries()].filter(([,t]) => t.at <= end).sort((a,b) => a[1].at - b[1].at)[0];
      if (!next) break;
      const [id, timer] = next;
      timers.delete(id);
      now = timer.at;
      timer.fn();
      await new Promise(setImmediate);
    }
    now = end;
    await new Promise(setImmediate);
  }
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/goofish_z/gui/app.js'), 'utf8'), context);
  await new Promise(setImmediate);
  return {context, document, requests, advance};
}

test('listing and keyword markup remains literal text', async () => {
  const text = '<img src=x onerror="syntheticExecuted=true">';
  const {context, document} = await screen({
    '/api/watch': {watches: [{id: 1, keyword: text, enabled: 1}]},
    '/api/search?q=synthetic&limit=30&page=1': {count: 1, items: [{title: text, url: 'https://example.invalid/item', price: '80', location: text}]},
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  assert.ok(document.getElementById('watchList').textContent.includes(text));
  assert.ok(document.getElementById('searchResults').textContent.includes(text));
  assert.equal(context.syntheticExecuted, undefined);
});

test('unsafe URL schemes never become clickable links', async () => {
  const {context} = await screen();
  assert.equal(context.itemLink({title: 'synthetic', url: 'javascript:synthetic()'}).tagName, 'SPAN');
  assert.equal(context.itemLink({title: 'synthetic', url: 'data:text/html,synthetic'}).tagName, 'SPAN');
  assert.equal(context.itemLink({title: 'synthetic', url: 'https://example.invalid'}).rel, 'noopener noreferrer');
});

test('errors render literally and release the search button', async () => {
  const detail = '<svg onload="synthetic()">';
  const {context, document} = await screen({
    '/api/search?q=synthetic&limit=30&page=1': {testError: true, detail},
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  assert.equal(document.getElementById('searchResults').textContent, detail);
  assert.equal(document.getElementById('searchButton').disabled, false);
});

test('rate-limited search waits for Retry-After and retries the same query once', async () => {
  const path = '/api/search?q=synthetic&limit=30&page=1';
  let calls = 0;
  const {context, document, advance} = await screen({
    [path]: () => ++calls === 1 ?
      {testStatus: 429, testHeaders: {'Retry-After': '2'}, detail: 'synthetic busy'} :
      {count: 1, items: [{title: 'synthetic item', price: '80', url: 'https://example.invalid/item'}]},
  });
  document.getElementById('searchQ').value = 'synthetic';
  const running = context.doSearch();
  await new Promise(setImmediate);
  assert.equal(calls, 1);
  assert.equal(document.getElementById('searchButton').disabled, true);
  assert.equal(document.getElementById('searchQ').disabled, true);
  assert.match(document.getElementById('searchResults').textContent, /还剩 2 秒/);
  await context.doSearch();
  await advance(1000);
  assert.equal(calls, 1);
  assert.match(document.getElementById('searchButton').textContent, /1 秒/);
  await advance(1000);
  await running;
  assert.equal(calls, 2);
  assert.match(document.getElementById('searchResults').textContent, /synthetic item/);
  assert.equal(document.getElementById('searchButton').disabled, false);
  assert.equal(document.getElementById('searchQ').disabled, false);
});

test('a second busy response stops automatic retries and releases the form', async () => {
  const path = '/api/search?q=synthetic&limit=30&page=1';
  const {context, document, requests, advance} = await screen({
    [path]: {testStatus: 429, testHeaders: {'Retry-After': '1'}, detail: 'synthetic busy'},
  });
  document.getElementById('searchQ').value = 'synthetic';
  const running = context.doSearch();
  await new Promise(setImmediate);
  await advance(1000);
  await running;
  await advance(5000);
  assert.equal(requests.filter(([url]) => url === path).length, 2);
  assert.match(document.getElementById('searchResults').textContent, /仍需等待 1 秒/);
  assert.equal(document.getElementById('searchButton').disabled, false);
});

test('missing or invalid retry headers never schedule an automatic retry', async () => {
  const path = '/api/search?q=synthetic&limit=30&page=1';
  for (const value of [undefined, '0', '-1', 'invalid']) {
    const {context, document, requests, advance} = await screen({
      [path]: {testStatus: 429, testHeaders: {'Retry-After': value}, detail: 'synthetic busy'},
    });
    document.getElementById('searchQ').value = 'synthetic';
    await context.doSearch();
    await advance(5000);
    assert.equal(requests.filter(([url]) => url === path).length, 1);
    assert.equal(document.getElementById('searchButton').disabled, false);
  }
});

test('authentication and circuit failures are not retried', async () => {
  const path = '/api/search?q=synthetic&limit=30&page=1';
  for (const status of [401, 503]) {
    const {context, document, requests, advance} = await screen({
      [path]: {testStatus: status, testHeaders: {'Retry-After': '2'}, detail: 'synthetic error'},
    });
    document.getElementById('searchQ').value = 'synthetic';
    await context.doSearch();
    await advance(5000);
    assert.equal(requests.filter(([url]) => url === path).length, 1);
    assert.equal(document.getElementById('searchResults').textContent, 'synthetic error');
  }
});

test('failed jobs show failure rather than an empty success count', async () => {
  const {context, document} = await screen();
  context.showRun({status: 'failed', failed: 1, results: [{keyword: 'synthetic', error: 'synthetic error'}]});
  const text = document.getElementById('runResult').textContent;
  assert.ok(text.includes('失败 1 项'));
  assert.ok(text.includes('synthetic error'));
});

test('alerts come from alert events without fetching watch histories', async () => {
  const {document, requests} = await screen({
    '/api/alerts?unread_only=true': {alerts: [{id: 1, title: '<b>synthetic</b>', price: 80, reason: 'synthetic event'}]},
  });
  assert.ok(document.getElementById('alerts').textContent.includes('<b>synthetic</b>'));
  assert.ok(!requests.some(([url]) => url.includes('/history')));
});

test('full pages can be browsed and returning to a viewed page needs no request', async () => {
  const first = '/api/search?q=synthetic&limit=30&page=1';
  const second = '/api/search?q=synthetic&limit=30&page=2';
  const items = Array.from({length: 30}, (_, i) => ({title: `synthetic ${i}`, price: '80'}));
  const {context, document, requests} = await screen({
    [first]: {items, count: 30, page: 1, has_next: true},
    [second]: {items: [{title: 'second page', price: '90'}], count: 1, page: 2, has_next: false},
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  assert.match(document.getElementById('searchResults').textContent, /显示 30 \/ 30 条/);
  assert.equal(document.getElementById('searchPrevButton').disabled, true);
  await context.changeSearchPage(2);
  assert.match(document.getElementById('searchResults').textContent, /second page/);
  assert.equal(document.getElementById('searchNextButton').disabled, true);
  await context.changeSearchPage(1);
  assert.equal(requests.filter(([url]) => url === first).length, 1);
  assert.equal(document.getElementById('searchNextButton').disabled, false);
});

test('page filters combine price, title and location without another search', async () => {
  const {context, document, requests} = await screen({
    '/api/search?q=synthetic&limit=30&page=1': {has_next: true, items: [
      {title: 'synthetic GPU white', price: '¥24200', price_value: 24200, price_text: '¥2.42万', location: '上海'},
      {title: 'synthetic GPU parts', price: '¥9500', price_value: 9500, location: '上海'},
      {title: 'synthetic GPU white', price: '¥24500', price_value: 24500, location: '北京'},
    ]},
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  const count = requests.length;
  for (const [id, value] of Object.entries({searchMin: '20000', searchMax: '25000', searchInclude: 'GPU white', searchExclude: 'parts', searchLocation: '上海'})) {
    document.getElementById(id).value = value;
  }
  context.renderSearch();
  const text = document.getElementById('searchResults').textContent;
  assert.match(text, /显示 1 \/ 3 条/);
  assert.match(text, /¥24,200/);
  assert.match(text, /页面标价 ¥2.42万/);
  assert.equal(requests.length, count);
  context.resetSearchFilters();
  assert.match(document.getElementById('searchResults').textContent, /显示 3 \/ 3 条/);
});

test('price sort is numeric, keeps unknown prices last and does not mutate source order', async () => {
  const {context, document} = await screen({
    '/api/search?q=synthetic&limit=30&page=1': {items: [
      {title: 'expensive', price: '¥2.42万'}, {title: 'unknown', price: '面议'}, {title: 'cheap', price: '¥9500'},
    ]},
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  for (const [sort, order] of [['price_asc', ['cheap', 'expensive', 'unknown']], ['price_desc', ['expensive', 'cheap', 'unknown']], ['default', ['expensive', 'unknown', 'cheap']]]) {
    document.getElementById('searchSort').value = sort;
    context.renderSearch();
    const text = document.getElementById('searchResults').textContent;
    assert.ok(text.indexOf(order[0]) < text.indexOf(order[1]));
    assert.ok(text.indexOf(order[1]) < text.indexOf(order[2]));
  }
});

test('empty filtered pages still allow next page and invalid price ranges explain the error', async () => {
  const {context, document} = await screen({
    '/api/search?q=synthetic&limit=30&page=1': {items: [{title: 'synthetic', price: '10'}], has_next: true},
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  document.getElementById('searchMin').value = '100';
  context.renderSearch();
  assert.match(document.getElementById('searchResults').textContent, /没有符合条件/);
  assert.equal(document.getElementById('searchNextButton').disabled, false);
  document.getElementById('searchMax').value = '50';
  context.renderSearch();
  assert.match(document.getElementById('searchResults').textContent, /最低价不能高于最高价/);
});

test('failed next page preserves current results and the current page number', async () => {
  const {context, document} = await screen({
    '/api/search?q=synthetic&limit=30&page=1': {items: [{title: 'original result', price: '10'}], has_next: true},
    '/api/search?q=synthetic&limit=30&page=2': {testStatus: 401, detail: 'synthetic login required'},
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  await context.changeSearchPage(2);
  assert.match(document.getElementById('searchPageLabel').textContent, /第 1 页/);
  assert.match(document.getElementById('searchResults').textContent, /original result/);
  assert.match(document.getElementById('searchResults').textContent, /synthetic login required/);
  assert.equal(document.getElementById('searchNextButton').disabled, false);
});

test('trash button reveals filtered items and reasons only when opened without fetching again', async () => {
  const unsafe = '<img src=x onerror="syntheticExecuted=true">';
  const {context, document, requests} = await screen({
    '/api/search?q=synthetic&limit=30&page=1': {
      items: [{title: 'visible', price: '9500'}],
      filtered_count: 1, filtered: [{title: 'automatic hidden', price: '¥24200', url: 'https://example.invalid/item', reasons: ['收购帖']}],
      blocked_count: 1, blocked: [{title: unsafe, price: '80', url: 'javascript:syntheticExecuted=true', reasons: [unsafe]}],
    },
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  const box = document.getElementById('searchResults');
  const trash = () => box.children[0].children.find(n => n.tagName === 'BUTTON');
  assert.equal(trash().textContent, '🗑 已过滤 2 条 · 查看原因');
  assert.equal(trash().attributes['aria-expanded'], 'false');
  assert.ok(!box.textContent.includes('automatic hidden'));
  const count = requests.length;
  context.toggleFilteredResults();
  assert.equal(trash().attributes['aria-expanded'], 'true');
  assert.match(box.textContent, /automatic hidden/);
  assert.match(box.textContent, /收购帖/);
  assert.match(box.textContent, /¥24,200/);
  assert.ok(box.textContent.includes(unsafe));
  assert.equal(context.syntheticExecuted, undefined);
  const collect = element => [element, ...element.children.flatMap(collect)];
  assert.ok(!collect(box).some(e => e.href?.startsWith('javascript:')));
  context.toggleFilteredResults();
  assert.ok(!box.textContent.includes('automatic hidden'));
  assert.equal(requests.length, count);
});

test('trash explains all matching local conditions and updates when filters are cleared', async () => {
  const {context, document, requests} = await screen({
    '/api/search?q=synthetic&limit=30&page=1': {
      items: [{title: 'synthetic parts', price: '9500', location: '北京'}, {title: 'synthetic GPU', price: '面议', location: '上海'}],
    },
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  for (const [id, value] of Object.entries({searchMin: '20000', searchInclude: 'GPU', searchExclude: 'parts', searchLocation: '上海'})) {
    document.getElementById(id).value = value;
  }
  context.renderSearch();
  const count = requests.length;
  context.toggleFilteredResults();
  const text = document.getElementById('searchResults').textContent;
  assert.match(text, /🗑 已过滤 2 条 · 收起/);
  assert.match(text, /标题未包含“gpu”/);
  assert.match(text, /标题包含排除词“parts”/);
  assert.match(text, /地区“北京”不匹配“上海”/);
  assert.match(text, /低于最低价/);
  assert.match(text, /价格不明确/);
  context.resetSearchFilters();
  const box = document.getElementById('searchResults');
  const trash = box.children[0].children.find(n => n.tagName === 'BUTTON');
  assert.equal(trash.textContent, '🗑 已过滤 0 条');
  assert.equal(trash.disabled, true);
  assert.ok(!box.textContent.includes('已过滤内容'));
  assert.equal(requests.length, count);
});
