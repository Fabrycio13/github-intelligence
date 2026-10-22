'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/github_intelligence/dashboard_assets/app.js'), 'utf8');
const repo = '00000000-0000-0000-0000-000000000001';
const id = n => '00000000-0000-0000-0000-' + String(n).padStart(12, '0');
const report = n => ({id: id(n), repositories: [{id: repo, full_name: 'owner/repo'}], complete: true,
  generated_at: '2026-10-22T12:00:00Z', window_start: '2026-10-01', window_end: '2026-10-22', content: 'Synthetic report ' + n});
const tick = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };
function page() {
  const nodes = new Map(); let activeElement;
  function node(tag = 'div') {
    return {tagName: tag.toUpperCase(), dataset: {}, attributes: {}, listeners: {}, children: [], hidden: false,
      textContent: '', value: '', disabled: false, scrollTop: 0, style: {}, content: 'synthetic-test-token',
      addEventListener(name, fn) { this.listeners[name] = fn; },
      setAttribute(key, value) { this.attributes[key] = value; }, removeAttribute(key) { delete this.attributes[key]; },
      focus() { activeElement = this; }, replaceChildren(...items) { this.children = []; this.append(...items); },
      append(...items) { this.children.push(...items); }, reportValidity() { return true; },
      contains(item) { return this === item || this.children.some(child => child.contains?.(item)); },
      querySelectorAll() { return this.children.filter(child => child.tagName === 'BUTTON'); }, scrollIntoView() {},
    };
  }
  function get(selector) {
    if (['#initial-loading', '#retry-loading', '#continue-loading'].includes(selector)) return null;
    if (!nodes.has(selector)) nodes.set(selector, node()); return nodes.get(selector);
  }
  get('#report-drawer').hidden = true; get('#analysis-new').hidden = true;
  const requests = [], timers = new Map(); let timerId = 0;
  const window = node(), location = {hash: '#/analises'}; window.confirm = () => true;
  vm.runInNewContext(source, {window, location, URLSearchParams, AbortController,
    setTimeout(fn) { const key = ++timerId; timers.set(key, fn); return key; }, clearTimeout(key) { timers.delete(key); },
    requestAnimationFrame(fn) { fn(); }, history: {replaceState(_state, _title, value) { location.hash = value; }},
    fetch(url, options) { return new Promise(resolve => requests.push({url, options, resolve})); },
    document: {querySelector: get, querySelectorAll: () => [], createElement: node, createElementNS: (_ns, tag) => node(tag), get activeElement() { return activeElement; }},
  });
  return {get, requests, window, location,
    async respond(match, data, status = 200) {
      const request = requests.find(item => !item.done && item.url.includes(match));
      assert.ok(request, 'request exists: ' + match); request.done = true;
      request.resolve({ok: status === 200, status, json: async () => data}); await tick();
    },
    async route(hash) { location.hash = hash; window.listeners.hashchange(); await tick(); },
    async click(selector) { get(selector).listeners.click?.(); await tick(); },
    async flushTimers() { const batch = [...timers.values()]; timers.clear(); batch.forEach(fn => fn()); await tick(); },
  };
}
async function ready(total = 20) {
  const app = page();
  await app.respond('/api/reports?', {reports: Array.from({length: 10}, (_, n) => report(n + 10)), total, offset: 0, limit: 10});
  return app;
}
const openCard = (app, index = 0) => app.get('#analyses').children[index].children[1].children[1].listeners.click();
test('initial history requests only ten lightweight summaries, not all repositories', async () => {
  const app = await ready(); assert.equal(app.requests.some(item => item.url === '/api/repositories'), false);
  const summaries = app.requests.filter(item => item.url.startsWith('/api/reports?'));
  assert.equal(summaries.length, 1); assert.match(summaries[0].url, /limit=10/); assert.match(summaries[0].url, /include_options=false/);
  assert.equal(app.get('#analyses').children.length, 10);
});
test('load more appends without replacing cards, search or scroll', async () => {
  const app = await ready(), first = app.get('#analyses').children[0], search = app.get('#analysis-search');
  search.value = 'owner'; app.get('#main-content').scrollTop = 420;
  await app.click('#analysis-more'); assert.equal(app.get('#analyses').children[0], first);
  await app.respond('offset=10', {reports: Array.from({length: 10}, (_, n) => report(n + 20)), total: 20, offset: 10, limit: 10});
  assert.equal(app.get('#analyses').children.length, 20); assert.equal(app.get('#analyses').children[0], first);
  assert.equal(app.get('#analysis-search'), search); assert.equal(search.value, 'owner');
  assert.equal(app.get('#main-content').scrollTop, 420); assert.equal(app.get('#analysis-more').hidden, true);
});
test('drawer reopen preserves cards and reuses the saved detail without a loading flash', async () => {
  const app = await ready(), first = app.get('#analyses').children[0]; openCard(app); await tick();
  await app.respond('/' + id(10) + '/details', report(10)); await app.click('#drawer-close-btn'); await app.flushTimers();
  openCard(app); assert.equal(app.get('#drawer-content').textContent, 'Synthetic report 10'); await tick();
  assert.equal(app.get('#analyses').children[0], first); assert.equal(app.requests.filter(item => item.url.endsWith('/details')).length, 1);
});
test('search is debounced, ignores stale results and selects repositories without reports', async () => {
  const app = await ready(), search = app.get('#analysis-search');
  search.value = 'o'; search.listeners.input(); search.value = 'old'; search.listeners.input(); await app.flushTimers();
  assert.equal(app.requests.some(item => /q=o&/.test(item.url)), false);
  search.value = 'new'; search.listeners.input(); await app.flushTimers();
  await app.respond('q=new', {repositories: [{id: repo, full_name: 'owner/new'}]});
  const result = app.get('#analysis-search-results').children[0];
  await app.respond('q=old', {repositories: [{id: id(2), full_name: 'owner/old'}]});
  assert.equal(app.get('#analysis-search-results').children[0], result); assert.equal(result.textContent, 'owner/new');
  result.listeners.click(); await app.route(app.location.hash);
  await app.respond('repository_id=', {reports: [], total: 0, offset: 0, limit: 10});
  assert.match(app.get('#analyses').children[0].children[0].textContent, /ainda não tem análises/);
});
test('failed pages retry without discarding current cards', async () => {
  const app = await ready(), first = app.get('#analyses').children[0];
  await app.click('#analysis-more'); await app.respond('offset=10', {error: 'Falha temporária'}, 503);
  assert.equal(app.get('#analyses').children[0], first); await app.click('#analysis-more');
  assert.equal(app.requests.filter(item => item.url.includes('offset=10')).length, 2);
  await app.respond('offset=10', {reports: [report(20)], total: 11, offset: 10, limit: 10}); assert.equal(app.get('#analyses').children.length, 11);
});
test('only ten recently read report bodies remain cached', async () => {
  const app = await ready(11); await app.click('#analysis-more');
  await app.respond('offset=10', {reports: [report(20)], total: 11, offset: 10, limit: 10});
  for (let n = 0; n < 11; n++) { openCard(app, n); await tick(); await app.respond('/' + id(n + 10) + '/details', report(n + 10)); }
  openCard(app, 1); await tick(); assert.equal(app.requests.filter(item => item.url.endsWith('/details')).length, 11);
  openCard(app); await tick(); assert.equal(app.requests.filter(item => item.url.includes('/' + id(10) + '/details')).length, 2);
});
test('closing and immediately reopening cancels the old close timer', async () => {
  const app = await ready(); openCard(app); await tick(); await app.respond('/' + id(10) + '/details', report(10));
  await app.click('#drawer-close-btn'); openCard(app); await app.flushTimers();
  assert.equal(app.get('#report-drawer').hidden, false); assert.equal(app.get('#report-drawer').dataset.open, 'true'); assert.equal(app.get('#main-content').inert, true);
});
test('a failed detail retries without changing history', async () => {
  const app = await ready(), card = app.get('#analyses').children[0]; openCard(app); await tick();
  await app.respond('/' + id(10) + '/details', {error: 'Sem conexão'}, 503);
  app.get('#drawer-status-banner').children[0].listeners.click(); await tick(); await app.respond('/' + id(10) + '/details', report(10));
  assert.equal(app.get('#drawer-content').textContent, 'Synthetic report 10'); assert.equal(app.get('#analyses').children[0], card);
});
test('returning from a dedicated report keeps the mounted history', async () => {
  const app = await ready(), card = app.get('#analyses').children[0];
  await app.route('#/analises/' + id(10)); await app.respond('/details', report(10)); await app.route('#/analises');
  assert.equal(app.get('#analyses').children[0], card); assert.equal(app.requests.filter(item => item.url.startsWith('/api/reports?')).length, 1);
});
test('a collection finishing while reading shows a notice without replacing cards or closing the drawer', async () => {
  const app = await ready(), card = app.get('#analyses').children[0];
  await app.route('#/repositorios/' + repo);
  await app.respond('/api/repositories', {repositories: [{id: repo, full_name: 'owner/repo', status: 'active', installation_status: 'active'}], updating: false});
  const article = app.get('#repositories').children[1], form = article.children.find(node => node.tagName === 'FORM');
  form.children[0].children[0].value = '2026-10-01T00:00'; form.children[1].children[0].value = '2026-10-02T00:00';
  form.listeners.submit({preventDefault() {}}); await tick();
  await app.route('#/analises'); openCard(app); await tick(); await app.respond('/details', report(10));
  await app.respond('/api/update', {message: 'Coleta concluída'});
  await app.respond('/api/repositories', {repositories: [], updating: false});
  assert.equal(app.get('#analysis-new').hidden, false); assert.equal(app.get('#analyses').children[0], card);
  assert.equal(app.get('#report-drawer').dataset.open, 'true');
  assert.equal(app.requests.filter(item => item.url.startsWith('/api/reports?')).length, 1);
});
