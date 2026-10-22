'use strict';
const message = document.querySelector('#message');
const listing = document.querySelector('#repositories');
const statuses = {pending:'Na fila', running:'Em andamento', succeeded:'Concluída', partial:'Parcial', failed:'Falhou'};
let busy = false;
let repositoryData = null;
let loadingRepositories = true;
let refreshing = false;
let serverBusy = false;
let routeVersion = 0;
// Page-lifetime reads only: no persistent storage or background polling.
const readCache = new Map();
const lastReads = new Map();
const detailCacheLimit = 10;
const readCacheLimit = 40;
function cachedRead(path) {
  const data = lastReads.get(path);
  if (data) {
    lastReads.delete(path);
    lastReads.set(path, data);
  }
  return data;
}
function trimReads() {
  const details = [...lastReads.keys()].filter(path => path.endsWith('/details'));
  const evicted = details.slice(0, Math.max(0, details.length - detailCacheLimit));
  for (const path of evicted) { lastReads.delete(path); readCache.delete(path); }
  while (lastReads.size > readCacheLimit) {
    const path = lastReads.keys().next().value;
    lastReads.delete(path); readCache.delete(path);
  }
}
function read(path) {
  if (!readCache.has(path)) {
    const pending = request(path).then(data => {
      if (readCache.get(path) === pending) {
        lastReads.set(path, data);
        trimReads();
      }
      return data;
    }, error => {
      if (readCache.get(path) === pending) {
        readCache.delete(path);
        if ([401, 403, 404].includes(error.status)) lastReads.delete(path);
      }
      throw error;
    });
    readCache.set(path, pending);
  }
  cachedRead(path);
  return readCache.get(path);
}
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function currentRoute() {
  const [path, query = ''] = location.hash.slice(1).split('?');
  const parts = (path || '/dashboard').split('/').filter(Boolean);
  const params = new URLSearchParams(query);
  if (!['dashboard', 'repositorios', 'analises'].includes(parts[0]) || parts.length > 2 || (parts[1] && (!uuidPattern.test(parts[1]) || parts[0] === 'dashboard'))) return null;
  const filter = params.get('repository_id') || '';
  const offset = params.get('offset') || '0';
  if ((filter && !uuidPattern.test(filter)) || !/^\d{1,7}$/.test(offset)) return null;
  return {page: parts[0], id: parts[1] || '', filter, offset: Number(offset), params};
}
async function navigate() {
  closeDrawerReport();
  let route = currentRoute();
  if (!route) {
    history.replaceState(null, '', '#/dashboard');
    route = currentRoute();
  }
  const version = ++routeVersion;
  const page = route.page === 'analises' && route.id ? 'relatorio' : route.page;
  document.querySelectorAll('[data-page]').forEach(section => section.hidden = section.dataset.page !== page);
  document.querySelectorAll('[data-nav]').forEach(link => {
    if (link.dataset.nav === route.page) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  const titles = {dashboard: ['Dashboard', 'Uma visão geral dos seus repositórios e relatórios.'], repositorios: ['Repositórios', 'Gerencie os vínculos e consulte suas coletas.'], analises: ['Análises', 'Seu histórico de relatórios de evidências.'], relatorio: ['Análise salva', 'Consulte o resultado sem iniciar uma nova coleta.']};
  document.querySelector('#page-title').textContent = titles[page][0];
  document.querySelector('#page-description').textContent = titles[page][1];
  document.title = titles[page][0] + ' · GitHub Intelligence';
  if (page === 'repositorios' || page === 'dashboard') {
    if (!repositoryData) {
      try {
        const data = await read('/api/repositories');
        repositoryData = data.repositories; serverBusy = data.updating;
      } catch (error) { notify(error.message, true); }
      loadingRepositories = false;
      if (version !== routeVersion) return;
    }
    if (page === 'repositorios') renderRepositories(route.id);
    else await loadDashboard(version);
  }
  if (page === 'analises') await loadAnalyses(route, version);
  if (page === 'relatorio') await loadReport(route.id, version);
}
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
const sidebarToggle = document.querySelector('#sidebar-toggle');
sidebarToggle.addEventListener('click', () => {
  const shell = document.querySelector('#app-shell');
  const collapsed = shell.dataset.sidebarCollapsed !== 'true';
  shell.dataset.sidebarCollapsed = String(collapsed);
  const label = collapsed ? 'Expandir menu lateral' : 'Recolher menu lateral';
  sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
  sidebarToggle.setAttribute('aria-label', label);
  sidebarToggle.setAttribute('title', label);
});
function notify(text, error=false) { message.textContent = text; message.dataset.error = String(error); message.hidden = !text; }
async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.error || 'Não foi possível concluir a operação.'), {status: response.status});
  return data;
}
function time(value) { return value ? new Date(value).toLocaleString('pt-BR') : 'Ainda não registrada'; }
function inputDate(value) { return new Date(value).toISOString().slice(0,16); }
function link(text, hash, className = 'text-link') {
  const node = el('a', text, className);
  node.href = hash;
  return node;
}
function reportNames(report) { return report.repositories.map(repo => repo.full_name).join(', ') || 'Repositório não identificado'; }
function completeness(report) { return report.complete ? 'Completo dentro da cobertura declarada' : 'Relatório parcial'; }
function emptyState(title, description, action, href) {
  const box = el('div', undefined, 'empty-state');
  box.append(el('h3', title), el('p', description));
  if (action) box.append(link(action, href));
  return box;
}
function renderRepositories(id) {
  listing.replaceChildren();
  if (!repositoryData) {
    listing.append(emptyState(loadingRepositories ? 'Carregando repositórios…' : 'Não foi possível carregar os repositórios', loadingRepositories ? 'Aguarde a consulta dos vínculos.' : 'Use Recarregar dados para tentar novamente.'));
    return;
  }
  if (id) {
    listing.append(link('← Todos os repositórios', '#/repositorios'));
    const repo = repositoryData.find(item => item.id === id);
    if (!repo) { listing.append(emptyState('Repositório indisponível', 'Confira o vínculo e volte à listagem.')); return; }
    listing.append(render(repo, serverBusy));
    return;
  }
  if (!repositoryData.length) listing.append(emptyState('Seu primeiro repositório começa aqui', 'Use Adicionar repositório para verificar o acesso e confirmar o vínculo.'));
  for (const repo of repositoryData) {
    const card = el('article', undefined, 'repository-row');
    const heading = el('h3');
    heading.append(link(repo.full_name, '#/repositorios/' + repo.id));
    const status = repo.status === 'active' && repo.installation_status === 'active' ? 'Vínculo ativo' : 'Vínculo indisponível para coleta';
    const info = el('div');
    info.append(heading, el('p', status + ' · Última coleta: ' + (statuses[repo.last_status] || 'Ainda não realizada'), 'muted'));
    const actions = el('div', undefined, 'actions');
    actions.append(link('Abrir repositório →', '#/repositorios/' + repo.id), link('Ver análises', '#/analises?repository_id=' + repo.id));
    card.append(info, actions);
    listing.append(card);
  }
}
function reportRow(report) {
  const card = el('article', undefined, 'analysis-row');
  const info = el('div');
  info.append(el('h3', reportNames(report)), el('p', 'Gerado em ' + time(report.generated_at), 'muted'), el('p', 'Intervalo UTC: ' + report.window_start + ' → ' + report.window_end, 'report-period'));
  const actions = el('div', undefined, 'analysis-actions');
  const open = el('button', 'Abrir análise →', 'btn-secondary'); open.type = 'button';
  open.addEventListener('click', () => openDrawerReport(report.id));
  actions.append(el('span', completeness(report), 'badge ' + (report.complete ? 'badge-success' : 'badge-warning')), open);
  card.append(info, actions);
  return card;
}
function historyHash(filter, offset = 0) {
  const params = new URLSearchParams();
  if (filter) params.set('repository_id', filter);
  if (offset) params.set('offset', String(offset));
  return '#/analises' + (params.size ? '?' + params : '');
}
// Keep one mounted history, independent of the report drawer and search controls.
const analysisState = {key: null, filter: '', offset: 0, next: 0, total: 0, ids: new Set(), loading: false, request: 0, ready: false};
const analysisNames = new Map();
let analysisFailure = null;
function reportsPath(filter, offset = 0) {
  const params = new URLSearchParams({limit: '10', offset: String(offset), include_options: 'false'});
  if (filter) params.set('repository_id', filter);
  return '/api/reports?' + params;
}
function analysisSelection(filter) {
  return filter ? analysisNames.get(filter) || (repositoryData || []).find(repo => repo.id === filter)?.full_name || 'Repositório selecionado' : 'Todos os repositórios';
}
function analysisControls() {
  document.querySelector('#analysis-selection').textContent = analysisSelection(analysisState.filter);
  document.querySelector('#analysis-count').textContent = analysisState.total + (analysisState.total === 1 ? ' análise salva' : ' análises salvas') + ' · Exibindo ' + analysisState.ids.size;
  const more = document.querySelector('#analysis-more');
  more.disabled = analysisState.loading;
  more.hidden = !analysisFailure && (!analysisState.ready || analysisState.next >= analysisState.total);
  more.textContent = analysisState.loading ? 'Carregando…' : analysisFailure ? 'Tentar novamente' : 'Carregar mais';
}
async function loadAnalyses(route, _version, {append = false, force = false} = {}) {
  const key = route.filter + ':' + route.offset;
  if (!append && !force && !analysisFailure && analysisState.key === key && (analysisState.ready || analysisState.loading)) return;
  if (append && analysisState.loading) return;
  const target = document.querySelector('#analyses');
  const feedback = document.querySelector('#analysis-feedback');
  const token = ++analysisState.request;
  const offset = append ? analysisState.next : route.offset;
  analysisState.loading = true;
  analysisFailure = null;
  analysisState.key = key;
  feedback.textContent = 'Buscando análises de ' + analysisSelection(route.filter) + '…';
  target.setAttribute('aria-busy', 'true');
  analysisControls();
  try {
    const data = await read(reportsPath(route.filter, offset));
    if (token !== analysisState.request) return;
    if (!append) {
      analysisState.ids.clear();
      target.replaceChildren();
      analysisState.filter = route.filter;
      analysisState.offset = route.offset;
    }
    for (const report of data.reports) {
      for (const repo of report.repositories) analysisNames.set(repo.id, repo.full_name);
      if (analysisState.ids.has(report.id)) continue;
      analysisState.ids.add(report.id);
      target.append(reportRow(report));
    }
    if (!analysisState.ids.size) target.append(emptyState(route.filter ? 'Este repositório ainda não tem análises' : 'Ainda não há análises salvas', 'As análises aparecerão aqui após uma coleta confirmada.'));
    analysisState.next = offset + data.reports.length;
    analysisState.total = data.total;
    analysisState.ready = true;
    feedback.textContent = '';
  } catch (error) {
    if (token !== analysisState.request) return;
    analysisFailure = {route, append, force};
    feedback.textContent = 'Não foi possível carregar esta seleção. Tente novamente. ' + error.message;
  } finally {
    if (token === analysisState.request) {
      analysisState.loading = false;
      target.setAttribute('aria-busy', 'false');
      analysisControls();
    }
  }
}
document.querySelector('#analysis-more').addEventListener('click', () => {
  if (analysisFailure) {
    const failed = analysisFailure;
    return loadAnalyses(failed.route, routeVersion, {append: failed.append, force: true});
  }
  return loadAnalyses({filter: analysisState.filter, offset: analysisState.offset}, routeVersion, {append: true});
});
document.querySelector('#analysis-all').addEventListener('click', () => { location.hash = historyHash(''); });
document.querySelector('#analysis-new').addEventListener('click', async () => {
  const button = document.querySelector('#analysis-new');
  if (analysisState.loading) return;
  for (const path of readCache.keys()) {
    if (path.startsWith('/api/reports?')) { readCache.delete(path); lastReads.delete(path); }
  }
  button.hidden = true;
  await loadAnalyses({filter: analysisState.filter, offset: 0}, routeVersion, {force: true});
  if (analysisFailure) button.hidden = false;
});
let searchTimer;
let searchRequest = 0;
let searchController;
const analysisSearch = document.querySelector('#analysis-search');
const analysisResults = document.querySelector('#analysis-search-results');
analysisSearch.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchController?.abort();
  const token = ++searchRequest;
  const query = analysisSearch.value.trim();
  analysisResults.replaceChildren();
  analysisResults.hidden = !query;
  if (!query) return;
  analysisResults.append(el('p', 'Buscando repositórios…', 'muted'));
  searchTimer = setTimeout(async () => {
    searchController = new AbortController();
    try {
      const params = new URLSearchParams({q: query, limit: '10'});
      const data = await request('/api/repositories/search?' + params, {signal: searchController.signal});
      if (token !== searchRequest) return;
      analysisResults.replaceChildren();
      for (const repo of data.repositories) {
        analysisNames.set(repo.id, repo.full_name);
        const button = el('button', repo.full_name, 'analysis-search-option'); button.type = 'button';
        button.addEventListener('click', () => {
          analysisResults.hidden = true;
          location.hash = historyHash(repo.id);
        });
        analysisResults.append(button);
      }
      if (!data.repositories.length) analysisResults.append(el('p', 'Nenhum repositório encontrado.', 'muted'));
    } catch (error) {
      if (token !== searchRequest || error.name === 'AbortError') return;
      analysisResults.replaceChildren(el('p', 'Não foi possível buscar. Digite novamente para tentar.', 'muted'));
    }
  }, 250);
});
function svgNode(tag, attrs) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}
function repoIcon() {
  const icon = svgNode('svg', {viewBox: '0 0 24 24', width: '15', height: '15', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', class: 'icon-repo'});
  icon.append(
    svgNode('path', {d: 'M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z'}),
    svgNode('path', {d: 'M6 6h10M6 10h10'})
  );
  return icon;
}
function renderDashboard(data) {
  const target = document.querySelector('#dashboard-content');
  target.replaceChildren();

  // 1. Context Ribbon (Alta densidade, substitui os cards clichês de IA)
  const ribbon = el('div', undefined, 'dashboard-ribbon');
  const ribbonItems = el('div', undefined, 'ribbon-items');

  const repoPill = el('a', undefined, 'ribbon-pill');
  repoPill.href = '#/repositorios';
  const dot = el('span', undefined, 'badge-dot');
  const repoLabel = el('span', data.connected_repositories + (data.connected_repositories === 1 ? ' repositório ativo' : ' repositórios ativos'));
  repoPill.append(dot, repoLabel);

  const reportPill = el('a', undefined, 'ribbon-pill');
  reportPill.href = '#/analises';
  reportPill.append(el('span', data.total_reports + (data.total_reports === 1 ? ' análise salva' : ' análises salvas')));

  const auditPill = el('div', undefined, 'ribbon-pill ribbon-pill-muted');
  if (data.partial_reports > 0) {
    auditPill.textContent = '▲ ' + data.partial_reports + ' com cobertura parcial';
    auditPill.classList.add('ribbon-pill-warning');
  } else {
    auditPill.textContent = '✓ 100% Determinístico · Sem IA';
  }

  ribbonItems.append(repoPill, reportPill, auditPill);

  const ribbonMeta = el('div', undefined, 'ribbon-meta');
  if (data.period_start && data.period_end) {
    ribbonMeta.append(el('span', 'Janela: ' + data.period_start + ' → ' + data.period_end + ' UTC', 'muted'));
  }
  ribbon.append(ribbonItems, ribbonMeta);

  // 2. High-Density Workstation Grid (Linear Style: Feed Principal + Atalhos Operacionais)
  const grid = el('div', undefined, 'dashboard-grid');

  // Coluna Principal: Feed de Evidências Recentes
  const feedCol = el('section', undefined, 'dashboard-feed-area');
  const feedHeader = el('div', undefined, 'section-heading');
  const feedTitle = el('div', undefined, 'heading-title-group');
  feedTitle.append(
    el('h2', 'Feed de Evidências Recentes'),
    el('p', 'Relatórios determinísticos e evidências coletadas do GitHub.', 'muted')
  );

  const feedControls = el('div', undefined, 'heading-controls-group');
  const searchWrap = el('div', undefined, 'feed-search-wrap');
  const searchIcon = svgNode('svg', {viewBox: '0 0 24 24', width: '13', height: '13', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'aria-hidden': 'true', class: 'search-icon'});
  searchIcon.append(
    svgNode('circle', {cx: '11', cy: '11', r: '8'}),
    svgNode('path', {d: 'm21 21-4.3-4.3'})
  );
  const searchInput = el('input', undefined, 'feed-search-input');
  searchInput.type = 'text';
  searchInput.placeholder = 'Filtrar evidências (repo, data)…';
  searchInput.setAttribute('aria-label', 'Filtrar evidências recentes');
  searchInput.autocomplete = 'off';
  searchInput.spellcheck = false;

  const searchClear = el('button', '✕', 'search-clear-btn');
  searchClear.type = 'button';
  searchClear.title = 'Limpar filtro';
  searchClear.hidden = true;

  searchWrap.append(searchIcon, searchInput, searchClear);
  feedControls.append(searchWrap, link('Ver histórico completo (' + data.total_reports + ') →', '#/analises', 'text-link'));
  feedHeader.append(feedTitle, feedControls);
  feedCol.append(feedHeader);

  if (data.recent_reports && data.recent_reports.length) {
    const tableWrap = el('div', undefined, 'table-responsive');
    const table = el('table', undefined, 'evidence-table');

    const thead = el('thead');
    const trHead = el('tr');
    trHead.append(
      el('th', 'Repositório'),
      el('th', 'Janela UTC'),
      el('th', 'Cobertura'),
      el('th', 'Gerado em'),
      el('th', 'Ação')
    );
    thead.append(trHead);
    table.append(thead);

    const tbody = el('tbody');
    for (const report of data.recent_reports) {
      const tr = el('tr', undefined, 'evidence-row');

      // Repositório
      const tdRepo = el('td', undefined, 'td-repo');
      const repoBox = el('div', undefined, 'repo-cell');
      repoBox.append(repoIcon());
      const rLink = link(reportNames(report), report.repositories[0]?.id ? historyHash(report.repositories[0].id) : '#/analises');
      rLink.className = 'repo-link-strong';
      repoBox.append(rLink);
      tdRepo.append(repoBox);

      // Janela UTC
      const tdWindow = el('td', undefined, 'td-window');
      const wStart = report.window_start ? report.window_start.slice(0, 10) : '—';
      const wEnd = report.window_end ? report.window_end.slice(0, 10) : '—';
      tdWindow.append(el('span', wStart + ' → ' + wEnd, 'code-pill'));

      // Cobertura
      const tdStatus = el('td', undefined, 'td-status');
      const badge = el('span', completeness(report), 'badge ' + (report.complete ? 'badge-success' : 'badge-warning'));
      tdStatus.append(badge);

      // Data de Geração
      const tdDate = el('td', time(report.generated_at), 'td-date muted');

      // Ação: abre no drawer lateral sem sair do dashboard
      const tdAction = el('td', undefined, 'td-action');
      const btn = el('button', 'Abrir análise →', 'btn-secondary btn-sm');
      btn.type = 'button';
      btn.addEventListener('click', () => openDrawerReport(report.id));
      tdAction.append(btn);

      tr.append(tdRepo, tdWindow, tdStatus, tdDate, tdAction);
      tbody.append(tr);
    }
    table.append(tbody);

    const emptySearchNotice = el('p', 'Nenhuma evidência recente corresponde ao filtro informado.', 'filter-empty-state muted');
    emptySearchNotice.hidden = true;

    searchInput.addEventListener('input', () => {
      const query = searchInput.value.trim().toLowerCase();
      searchClear.hidden = !query;
      let visible = 0;
      const rows = tbody.querySelectorAll('.evidence-row');
      rows.forEach(row => {
        const match = !query || row.textContent.toLowerCase().includes(query);
        row.hidden = !match;
        if (match) visible++;
      });
      emptySearchNotice.hidden = visible > 0 || rows.length === 0;
    });

    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      searchInput.dispatchEvent(new Event('input'));
      searchInput.focus();
    });

    tableWrap.append(table, emptySearchNotice);
    feedCol.append(tableWrap);
  } else {
    feedCol.append(emptyState(
      'Ainda não há análises salvas',
      data.connected_repositories ? 'Inicie a coleta de um dos repositórios monitorados ao lado.' : 'Adicione o primeiro repositório para começar a monitorar evidências.',
      'Ver repositórios', '#/repositorios'
    ));
  }

  // Coluna Lateral: Repositórios Monitorados & Sincronização
  const sideCol = el('aside', undefined, 'dashboard-side-area');
  const sideHeader = el('div', undefined, 'section-heading');
  const sideTitle = el('div');
  sideTitle.append(
    el('h2', 'Repositórios'),
    el('p', 'Vínculos ativos monitorados', 'muted')
  );
  sideHeader.append(sideTitle, link('Gerenciar →', '#/repositorios', 'text-link'));
  sideCol.append(sideHeader);

  const reposContainer = el('div', undefined, 'quick-repos-list');
  if (repositoryData && repositoryData.length) {
    for (const repo of repositoryData) {
      const card = el('article', undefined, 'quick-repo-card');

      const head = el('div', undefined, 'quick-repo-head');
      const titleWrap = el('div', undefined, 'quick-repo-title-wrap');
      titleWrap.append(repoIcon(), link(repo.full_name, '#/repositorios/' + repo.id, 'quick-repo-name'));
      const isOnline = repo.status === 'active' && repo.installation_status === 'active';
      const statusBadge = el('span', isOnline ? 'Ativo' : 'Inativo', 'badge ' + (isOnline ? 'badge-success' : 'badge-danger'));
      head.append(titleWrap, statusBadge);

      const meta = el('div', undefined, 'quick-repo-meta');
      const lastStatus = statuses[repo.last_status] || 'Ainda não realizada';
      const statusLine = el('p', undefined, 'quick-repo-line');
      statusLine.append(el('span', 'Status do sync: ', 'muted'), el('span', lastStatus, repo.last_status === 'succeeded' ? 'text-success' : (repo.last_status === 'failed' ? 'text-danger' : '')));
      meta.append(statusLine);

      if (repo.last_finished_at) {
        meta.append(el('p', 'Última coleta: ' + time(repo.last_finished_at), 'muted quick-repo-time'));
      }

      const actions = el('div', undefined, 'quick-repo-actions');
      actions.append(
        link('Ver análises', historyHash(repo.id), 'btn-secondary btn-sm'),
        link('Coletar / Abrir →', '#/repositorios/' + repo.id, 'btn-primary btn-sm')
      );

      card.append(head, meta, actions);
      reposContainer.append(card);
    }
  } else {
    reposContainer.append(emptyState('Nenhum repositório vinculado', 'Conecte um repositório para iniciar a coleta determinística.', 'Adicionar repositório', '#/repositorios'));
  }
  sideCol.append(reposContainer);

  grid.append(feedCol, sideCol);
  target.append(ribbon, grid);
}
async function loadDashboard(version) {
  const target = document.querySelector('#dashboard-content');
  const previous = lastReads.get('/api/dashboard');
  if (previous) renderDashboard(previous);
  else target.replaceChildren(el('p', 'Carregando indicadores…', 'muted'));
  try {
    const data = await read('/api/dashboard');
    if (version !== routeVersion) return;
    renderDashboard(data);
  } catch (error) {
    if (version !== routeVersion) return;
    if (!lastReads.has('/api/dashboard')) target.replaceChildren(emptyState('Indicadores indisponíveis', error.message));
    notify(error.message, true);
  }
}
let activeDrawerReportId = null;
let drawerCloseTimer;
let drawerTrigger;
let drawerVersion = 0;

function closeDrawerReport() {
  const drawer = document.querySelector('#report-drawer');
  const backdrop = document.querySelector('#drawer-backdrop');
  if (!drawer || !backdrop || drawer.hidden) return;
  activeDrawerReportId = null;
  ++drawerVersion;
  drawer.dataset.open = 'false';
  backdrop.dataset.open = 'false';
  document.querySelector('#main-content').inert = false;
  document.querySelector('.sidebar').inert = false;
  drawerTrigger?.focus({preventScroll: true});
  clearTimeout(drawerCloseTimer);
  drawerCloseTimer = setTimeout(() => {
    drawer.hidden = true;
    backdrop.hidden = true;
  }, 280);
}

async function openDrawerReport(id) {
  const drawer = document.querySelector('#report-drawer');
  const backdrop = document.querySelector('#drawer-backdrop');
  if (!drawer || !backdrop) return;
  clearTimeout(drawerCloseTimer);
  const version = ++drawerVersion;
  if (drawer.hidden || drawer.dataset.open !== 'true') drawerTrigger = document.activeElement;
  activeDrawerReportId = id;
  const title = document.querySelector('#drawer-title');
  const meta = document.querySelector('#drawer-meta');
  const banner = document.querySelector('#drawer-status-banner');
  const content = document.querySelector('#drawer-content');
  const fullscreenBtn = document.querySelector('#drawer-fullscreen-btn');

  drawer.hidden = false;
  backdrop.hidden = false;
  document.querySelector('#main-content').inert = true;
  document.querySelector('.sidebar').inert = true;
  document.querySelector('#drawer-close-btn').focus({preventScroll: true});
  requestAnimationFrame(() => {
    if (version !== drawerVersion) return;
    drawer.dataset.open = 'true';
    backdrop.dataset.open = 'true';
  });

  fullscreenBtn.onclick = () => {
    closeDrawerReport();
    openReport(id);
  };

  const path = '/api/reports/' + encodeURIComponent(id) + '/details';
  function display(report) {
    title.textContent = reportNames(report);
    meta.textContent = 'Gerado em ' + time(report.generated_at);
    banner.replaceChildren();
    banner.append(el('span', completeness(report), 'badge ' + (report.complete ? 'badge-success' : 'badge-warning')), el('span', 'Janela UTC: ' + (report.window_start || '—') + ' → ' + (report.window_end || '—'), 'code-pill'));
    content.textContent = report.content;
    document.querySelector('#drawer-copy-btn').disabled = false;
  }
  const previous = cachedRead(path);
  document.querySelector('#drawer-copy-btn').disabled = !previous;
  if (previous) { display(previous); return; }
  title.textContent = 'Carregando análise…';
  meta.textContent = '';
  banner.replaceChildren();
  content.textContent = 'Consultando a análise selecionada…';
  try {
    const report = await read(path);
    if (activeDrawerReportId !== id || version !== drawerVersion) return;
    display(report);
  } catch (err) {
    if (activeDrawerReportId !== id || version !== drawerVersion) return;
    title.textContent = 'Análise indisponível';
    content.textContent = err.message;
    const retry = el('button', 'Tentar novamente', 'btn-secondary'); retry.type = 'button';
    retry.addEventListener('click', () => openDrawerReport(id));
    banner.replaceChildren(retry);
  }
}

function openReport(id) {
  const params = new URLSearchParams({return_to: location.hash.slice(1) || '/dashboard'});
  location.hash = '/analises/' + encodeURIComponent(id) + '?' + params;
}
function renderReport(report) {
  document.querySelector('#report-title').textContent = reportNames(report);
  document.querySelector('#report-content').textContent = report.content;
  document.querySelector('#report-status').textContent = completeness(report) + ' · Gerado em ' + time(report.generated_at) + ' · Intervalo UTC: ' + report.window_start + ' → ' + report.window_end;
}
async function loadReport(id, version) {
  const content = document.querySelector('#report-content');
  const path = '/api/reports/' + encodeURIComponent(id) + '/details';
  const previous = lastReads.get(path);
  if (previous) renderReport(previous);
  else {
    content.textContent = 'Carregando análise…';
    document.querySelector('#report-title').textContent = 'Relatório salvo';
    document.querySelector('#report-status').textContent = '';
  }
  try {
    const report = await read(path);
    if (version !== routeVersion) return;
    renderReport(report);
  } catch (error) {
    if (version !== routeVersion) return;
    if (!lastReads.has(path)) {
      content.textContent = error.message;
      document.querySelector('#report-title').textContent = 'Análise indisponível';
      document.querySelector('#report-status').textContent = '';
    }
    notify(error.message, true);
  }
}
function render(repo, serverBusy) {
  const article = el('article');
  article.append(el('h3', repo.full_name));
  const dl = el('dl');
  const pairs = [
    ['Monitoramento', repo.status === 'active' ? 'Ativo' : 'Inativo'],
    ['Última coleta', statuses[repo.last_status] || 'Nenhuma coleta'],
    ['Início', time(repo.last_started_at)],
    ['Conclusão', time(repo.last_finished_at)],
    ['Intervalo (UTC)', repo.window_start ? repo.window_start + ' → ' + repo.window_end : 'Ainda não registrado']
  ];
  for (const [label, value] of pairs) { const group = el('div'); group.append(el('dt', label), el('dd', value)); dl.append(group); }
  article.append(dl);
  const actions = el('div', undefined, 'actions');
  if (repo.report_id) {
    const open = el('button', 'Abrir relatório salvo'); open.type = 'button';
    open.addEventListener('click', () => openReport(repo.report_id)); actions.append(open);
  } else { actions.append(el('p', 'Sem relatório salvo para a última coleta.')); }
  actions.append(link('Ver todo o histórico', historyHash(repo.id)));
  article.append(actions);
  const form = el('form');
  const startLabel = el('label', 'Início inclusivo (UTC)');
  const start = el('input'); start.type = 'datetime-local'; start.required = true; start.name = 'since';
  const now = new Date();
  start.value = inputDate(repo.window_start || new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(),1)));
  const endLabel = el('label', 'Fim exclusivo (UTC)');
  const end = el('input'); end.type = 'datetime-local'; end.required = true; end.name = 'until';
  end.value = inputDate(now); end.max = inputDate(now);
  startLabel.append(start); endLabel.append(end);
  const update = el('button', 'Atualizar agora'); update.type = 'submit';
  update.disabled = busy || loadingRepositories || serverBusy || repo.status !== 'active' || repo.installation_status !== 'active' || repo.last_status === 'running' || repo.last_status === 'pending';
  form.append(startLabel, endLabel, update);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || loadingRepositories || !form.reportValidity()) return;
    const since = new Date(start.value + 'Z'); const until = new Date(end.value + 'Z');
    if (!(since < until) || until > new Date()) { notify('Use início anterior ao fim, sem datas futuras.',true); return; }
    if (!window.confirm('Consultar o GitHub e gravar uma nova coleta de ' + repo.full_name + ' no Supabase?\nIntervalo UTC: ' + since.toISOString() + ' até ' + until.toISOString())) return;
    busy = true;
    document.querySelectorAll('form button').forEach(button => button.disabled = true);
    notify('Coletando ' + repo.full_name + '… Aguarde; não feche o painel.');
    let result;
    let failure;
    try {
      result = await request('/api/update', {
        method:'POST', headers: {'Content-Type':'application/json', 'X-Request-Token':document.querySelector('meta[name="request-token"]').content},
        body:JSON.stringify({repository_id:repo.id, since:since.toISOString(), until:until.toISOString()})
      });
    } catch (error) { failure = error; }
    finally { busy = false; document.querySelector('#inspect-repository').disabled = false; }
    const refreshed = await load({changed: true});
    const outcome = failure ? failure.message : result.message;
    notify(outcome + (refreshed ? '' : ' Não foi possível atualizar a tela; use Recarregar dados.'), !!failure || !refreshed);
  });
  article.append(form);
  return article;
}
async function load({initial = false, changed = false} = {}) {
  if (busy || refreshing) return;
  refreshing = true;
  if (!initial) {
    if (changed) {
      for (const path of readCache.keys()) {
        if (!path.endsWith('/details') && !path.startsWith('/api/reports?')) { readCache.delete(path); lastReads.delete(path); }
      }
      if (analysisState.ready) document.querySelector('#analysis-new').hidden = false;
      else for (const path of readCache.keys()) if (path.startsWith('/api/reports?')) { readCache.delete(path); lastReads.delete(path); }
    } else { readCache.clear(); lastReads.clear(); }
  }
  loadingRepositories = true;
  const reload = document.querySelector('#reload'); reload.disabled = true;
  const route = currentRoute();
  const paths = new Set();
  if (route?.page === 'dashboard') { paths.add('/api/repositories'); paths.add('/api/dashboard'); }
  if (route?.page === 'repositorios' || changed) paths.add('/api/repositories');
  if (route?.page === 'analises') {
    if (route.id) paths.add('/api/reports/' + route.id + '/details');
    else if (!changed || !analysisState.ready) paths.add(reportsPath(route.filter, route.offset));
  }
    let loader = null;
  if (initial) {
    loader = initLoading();
  }
  let completed = 0;
  const reads = [...paths].map(path => read(path).then(data => {
    completed++;
    if (loader) loader.advance(completed, paths.size);
    return data;
  }));
  const navigation = initial || route?.page === 'analises' ? null : navigate();
  const results = await Promise.allSettled(reads);
  if (navigation) await navigation;
  const data = lastReads.get('/api/repositories');
  const failed = results.some(result => result.status === 'rejected');
  if (data) {
    repositoryData = data.repositories; serverBusy = data.updating;
  } else if (paths.has('/api/repositories')) repositoryData = null;
  notify(failed ? 'Não foi possível atualizar todos os dados. Os últimos dados disponíveis foram mantidos; tente Recarregar dados.' : '', failed);
  loadingRepositories = false;
  if (!initial && route?.page === 'analises' && !route.id && !changed) await loadAnalyses(route, routeVersion, {force: true});
  if (currentRoute()?.page === 'repositorios') renderRepositories(currentRoute().id);
  if (currentRoute()?.page === 'dashboard') {
    const dashData = lastReads.get('/api/dashboard');
    if (dashData) renderDashboard(dashData);
  }
  reload.disabled = false;
  refreshing = false;
  if (initial) {
    if (loader) loader.finish();
    else showApp();
  }
  return !failed;
}

function initLoading() {
  const loading = document.querySelector('#initial-loading');
  const appShell = document.querySelector('#app-shell');

  if (!loading) return { finish: () => {}, advance: () => {} };

  loading.hidden = false;
  if (appShell) appShell.hidden = true;

  let isClosed = false;
  const startTime = Date.now();
  const MIN_DISPLAY_MS = 1400;

  function dismiss() {
    if (isClosed) return;
    isClosed = true;
    loading.style.pointerEvents = 'none';
    loading.style.transition = 'opacity 0.45s ease, transform 0.45s ease';
    loading.style.opacity = '0';
    loading.style.transform = 'scale(0.96)';
    setTimeout(() => {
      loading.hidden = true;
      loading.style.display = 'none';
      if (appShell) appShell.hidden = false;
      const main = document.querySelector('#main-content');
      if (main) main.focus({ preventScroll: true });
    }, 450);
  }

  const safety = setTimeout(() => {
    dismiss();
  }, 3200);

  function finish() {
    clearTimeout(safety);
    const elapsed = Date.now() - startTime;
    const remaining = Math.max(0, MIN_DISPLAY_MS - elapsed);
    setTimeout(dismiss, remaining);
  }

  function advance() {}

  return { finish, advance };
}

function showApp() {
  const loading = document.querySelector('#initial-loading');
  const appShell = document.querySelector('#app-shell');
  if (loading && appShell) {
    loading.style.pointerEvents = 'none';
    loading.style.transition = 'opacity 0.45s ease, transform 0.45s ease';
    loading.style.opacity = '0';
    loading.style.transform = 'scale(0.96)';
    setTimeout(() => {
      loading.hidden = true;
      loading.style.display = 'none';
      appShell.hidden = false;
      const main = document.querySelector('#main-content');
      if (main) main.focus({ preventScroll: true });
    }, 450);
  } else if (appShell) {
    appShell.hidden = false;
  }
}

document.querySelector('#retry-loading')?.addEventListener('click', () => {
  readCache.clear();
  load({initial: true}); navigate();
});
document.querySelector('#continue-loading')?.addEventListener('click', showApp);
document.querySelector('#reload').addEventListener('click', load);
document.querySelector('.skip-link').addEventListener('click', event => {
  event.preventDefault();
  const main = document.querySelector('#main-content');
  main.focus(); main.scrollIntoView();
});
const copyReportBtn = document.querySelector('#copy-report');
if (copyReportBtn) {
  copyReportBtn.addEventListener('click', async () => {
    const text = document.querySelector('#report-content').textContent;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      const originalText = copyReportBtn.textContent;
      copyReportBtn.textContent = 'Copiado!';
      copyReportBtn.disabled = true;
      setTimeout(() => {
        copyReportBtn.textContent = originalText;
        copyReportBtn.disabled = false;
      }, 2000);
    } catch {
      notify('Não foi possível copiar o relatório para a área de transferência.', true);
    }
  });
}
document.querySelector('#drawer-close-btn')?.addEventListener('click', closeDrawerReport);
document.querySelector('#drawer-backdrop')?.addEventListener('click', closeDrawerReport);
document.querySelector('#drawer-copy-btn')?.addEventListener('click', async () => {
  const text = document.querySelector('#drawer-content').textContent;
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    const btnText = document.querySelector('#drawer-copy-btn .btn-text');
    if (btnText) {
      const orig = btnText.textContent;
      btnText.textContent = 'Copiado!';
      setTimeout(() => { btnText.textContent = orig; }, 2000);
    }
  } catch {
    notify('Não foi possível copiar o relatório para a área de transferência.', true);
  }
});
window.addEventListener('keydown', event => {
  const panel = document.querySelector('#report-drawer');
  if (event.key === 'Tab' && panel && !panel.hidden && panel.dataset.open === 'true') {
    const controls = [...panel.querySelectorAll('button:not(:disabled)')];
    const first = controls[0]; const last = controls[controls.length - 1];
    if (first && event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (last && !event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
  if (event.key === 'Escape') {
    const drawer = document.querySelector('#report-drawer');
    if (drawer && !drawer.hidden && drawer.dataset.open === 'true') {
      closeDrawerReport();
    }
  }
});

document.querySelector('#close-report').addEventListener('click', () => {
  const target = currentRoute()?.params.get('return_to') || '/analises';
  location.hash = /^\/(dashboard|repositorios|analises)(\/|\?|$)/.test(target) && !target.includes('return_to') ? target : '/analises';
});
const onboardingSection = document.querySelector('#onboarding');
const inspectForm = document.querySelector('#inspect-form');
const linkInput = document.querySelector('#repository-link');
const inspectButton = document.querySelector('#inspect-repository');
const onboardingMessage = document.querySelector('#onboarding-message');
const onboardingResult = document.querySelector('#onboarding-result');
document.querySelector('#add-repository').addEventListener('click', () => {
  if (busy || loadingRepositories) { notify('Aguarde a operação atual antes de adicionar um repositório.', true); return; }
  inspectForm.reset(); onboardingResult.replaceChildren(); onboardingMessage.textContent = '';
  inspectButton.textContent = 'Verificar acesso';
  onboardingSection.showModal();
  document.querySelector('#add-repository').setAttribute('aria-expanded', 'true');
  linkInput.focus();
});
function closeOnboarding() {
  if (busy) { onboardingMessage.textContent = 'Aguarde a operação terminar antes de fechar.'; return; }
  onboardingSection.close();
}
document.querySelector('#close-onboarding').addEventListener('click', closeOnboarding);
onboardingSection.addEventListener('cancel', event => { if (busy) { event.preventDefault(); onboardingMessage.textContent = 'Aguarde a operação terminar antes de fechar.'; } });
onboardingSection.addEventListener('close', () => {
  document.querySelector('#add-repository').setAttribute('aria-expanded', 'false');
  document.querySelector('#add-repository').focus();
});
linkInput.addEventListener('input', () => {
  onboardingResult.replaceChildren(); onboardingMessage.textContent = ''; inspectButton.textContent = 'Verificar acesso';
});
function post(path, payload) {
  return request(path, {method:'POST', headers:{'Content-Type':'application/json',
    'X-Request-Token':document.querySelector('meta[name="request-token"]').content}, body:JSON.stringify(payload)});
}
function showInspection(result, link) {
  onboardingResult.replaceChildren();
  if (result.status === 'authorization_required') {
    onboardingMessage.textContent = result.message || 'A App ainda não tem acesso. Autorize no GitHub e depois verifique novamente.';
    const url = new URL(result.authorization_url);
    if (url.origin !== 'https://github.com' || !/^\/apps\/[a-z0-9-]+\/installations\/new$/.test(url.pathname)) throw new Error('Endereço de autorização inválido.');
    const authorize = el('a', 'Autorizar no GitHub'); authorize.href = url.href; authorize.target = '_blank'; authorize.rel = 'noopener noreferrer';
    onboardingResult.append(authorize);
    inspectButton.textContent = 'Já autorizei — verificar novamente';
    return;
  }
  if (result.status !== 'ready') throw new Error('Resposta de verificação inesperada.');
  onboardingMessage.textContent = result.full_name + ' · Acesso confirmado pela instalação ' + result.installation_id + '. ' + (result.message || 'Confirme para vincular ao projeto.');
  const confirm = el('button', 'Confirmar vínculo'); confirm.type = 'button'; confirm.disabled = !result.can_bind;
  confirm.addEventListener('click', async () => {
    if (busy || !window.confirm('Vincular ' + result.full_name + ' a este projeto? Nenhuma coleta será iniciada.')) return;
    busy = true; confirm.disabled = true; inspectButton.disabled = true; linkInput.disabled = true;
    onboardingMessage.textContent = 'Revalidando acesso e salvando vínculo…';
    try {
      const expected = {full_name:result.full_name, github_id:result.github_id,
        app_id:result.app_id, installation_id:result.installation_id};
      const bound = await post('/api/repositories/bind', {link, expected});
      if (bound.status === 'bound') {
        onboardingResult.replaceChildren(); onboardingMessage.textContent = bound.message;
        busy = false; inspectButton.disabled = false; linkInput.disabled = false;
        onboardingSection.close();
        const refreshed = await load({changed: true});
        location.hash = '/repositorios/' + bound.repository.id;
        notify(bound.message + (refreshed ? '' : ' Não foi possível atualizar a lista; use Recarregar dados.'), !refreshed);
      } else { showInspection(bound, link); }
    } catch (error) { onboardingMessage.textContent = error.message; confirm.disabled = false; }
    finally { busy = false; inspectButton.disabled = false; linkInput.disabled = false; }
  });
  onboardingResult.append(confirm);
}
inspectForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (busy || !inspectForm.reportValidity()) return;
  const link = linkInput.value;
  busy = true; inspectButton.disabled = true; linkInput.disabled = true; onboardingResult.replaceChildren();
  onboardingMessage.textContent = 'Verificando acesso no GitHub…';
  try { showInspection(await post('/api/repositories/inspect', {link}), link); }
  catch (error) { onboardingMessage.textContent = error.message; }
  finally { busy = false; inspectButton.disabled = false; linkInput.disabled = false; }
});
window.addEventListener('hashchange', () => {
  document.querySelector('#main-content').focus({preventScroll: true});
  navigate();
});
if (!location.hash) history.replaceState(null, '', '#/dashboard');
load({initial: true});
navigate();
