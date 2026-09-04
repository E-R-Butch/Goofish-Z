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
  const context = vm.createContext({
    document, URL, setTimeout: () => 1, clearTimeout() {}, setInterval() {},
    fetch: async (url, options) => {
      requests.push([url, options]);
      const value = responses[url];
      if (value === undefined) throw new Error(`Unexpected test request: ${url}`);
      return {ok: !value.testError, status: value.testError ? 400 : 200, json: async () => value};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/goofish_z/gui/app.js'), 'utf8'), context);
  await new Promise(setImmediate);
  return {context, document, requests};
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
