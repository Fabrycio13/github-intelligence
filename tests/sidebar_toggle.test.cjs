'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/github_intelligence/dashboard_assets/app.js'), 'utf8');

function page() {
  const nodes = new Map();
  function node() {
    return {dataset: {}, attributes: {}, listeners: {},
      addEventListener(name, handler) { this.listeners[name] = handler; },
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; },
      focus() {}, replaceChildren() {}, append() {}, scrollIntoView() {}};
  }
  function get(selector) {
    // Keep the pre-existing loader out of this sidebar-only test seam.
    if (['#initial-loading', '#retry-loading', '#continue-loading'].includes(selector)) return null;
    if (!nodes.has(selector)) nodes.set(selector, node());
    return nodes.get(selector);
  }
  const requests = [];
  const window = node();
  const location = {hash: '#/dashboard'};
  vm.runInNewContext(source, {
    window, location, URLSearchParams,
    history: {replaceState(_state, _title, value) { location.hash = value; }},
    fetch(url) { requests.push(url); return new Promise(() => {}); },
    document: {querySelector: get, querySelectorAll: () => [], createElement: node, createElementNS: node},
  });
  return {get, requests, window, location};
}

test('sidebar toggle collapses and expands with an accessible state without network writes', () => {
  const app = page();
  const control = app.get('#sidebar-toggle');
  const shell = app.get('#app-shell');
  const before = app.requests.length;
  assert.equal(typeof control.listeners.click, 'function');
  control.listeners.click();
  assert.equal(shell.dataset.sidebarCollapsed, 'true');
  assert.equal(control.attributes['aria-expanded'], 'false');
  assert.equal(control.attributes['aria-label'], 'Expandir menu lateral');
  control.listeners.click();
  assert.equal(shell.dataset.sidebarCollapsed, 'false');
  assert.equal(control.attributes['aria-expanded'], 'true');
  assert.equal(control.attributes['aria-label'], 'Recolher menu lateral');
  assert.equal(app.requests.length, before);
});

test('changing the route preserves the collapsed menu and cached reads', () => {
  const app = page();
  const control = app.get('#sidebar-toggle');
  assert.equal(typeof control.listeners.click, 'function');
  control.listeners.click();
  const before = app.requests.length;
  app.location.hash = '#/analises';
  app.window.listeners.hashchange();
  assert.equal(app.get('#app-shell').dataset.sidebarCollapsed, 'true');
  assert.equal(app.requests.length, before);
});
