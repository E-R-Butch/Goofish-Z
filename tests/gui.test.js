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
    '/api/search?q=synthetic&limit=15': {count: 1, items: [{title: text, url: 'https://example.invalid/item', price: '80', location: text}]},
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
    '/api/search?q=synthetic&limit=15': {testError: true, detail},
  });
  document.getElementById('searchQ').value = 'synthetic';
  await context.doSearch();
  assert.equal(document.getElementById('searchResults').textContent, detail);
  assert.equal(document.getElementById('searchButton').disabled, false);
});

test('rate-limited search waits for Retry-After and retries the same query once', async () => {
  const path = '/api/search?q=synthetic&limit=15';
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
  const path = '/api/search?q=synthetic&limit=15';
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
  const path = '/api/search?q=synthetic&limit=15';
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
  const path = '/api/search?q=synthetic&limit=15';
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
