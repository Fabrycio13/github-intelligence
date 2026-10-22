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
function read(path) {
  if (!readCache.has(path)) {
    const pending = request(path).then(data => {
      if (readCache.get(path) === pending) lastReads.set(path, data);
      return data;
    }, error => {
      if (readCache.get(path) === pending && [401, 403, 404].includes(error.status)) lastReads.delete(path);
      throw error;
    });
    readCache.set(path, pending);
  }
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
  if (page === 'repositorios') renderRepositories(route.id);
  if (page === 'dashboard') await loadDashboard(version);
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
  open.addEventListener('click', () => openReport(report.id));
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
function renderAnalyses(data, route) {
  const target = document.querySelector('#analyses');
  const filters = document.querySelector('#analysis-filters');
  const count = document.querySelector('#analysis-count');
  const pagination = document.querySelector('#analysis-pagination');
  count.textContent = ''; pagination.replaceChildren(); filters.replaceChildren();
  const options = new Map((repositoryData || []).map(repo => [repo.id, repo.full_name]));
  for (const repo of data.repository_options) options.set(repo.id, repo.full_name);
  if (route.filter && !options.has(route.filter)) options.set(route.filter, 'Repositório selecionado');
  const all = link('Todos os repositórios', '#/analises', 'filter-chip');
  if (!route.filter) all.setAttribute('aria-current', 'true');
  filters.append(all);
  for (const [id, name] of options) {
    const option = link(name, historyHash(id), 'filter-chip');
    if (id === route.filter) option.setAttribute('aria-current', 'true');
    filters.append(option);
  }
  count.textContent = data.total + (data.total === 1 ? ' análise salva' : ' análises salvas') + (data.reports.length ? ' · Exibindo ' + (route.offset + 1) + '–' + (route.offset + data.reports.length) : '');
  target.replaceChildren(...data.reports.map(reportRow));
  if (!data.reports.length) target.append(emptyState('Nenhuma análise nesta seleção', route.offset ? 'Volte à primeira página para consultar o histórico.' : 'As análises aparecerão aqui após uma coleta confirmada.', route.offset ? 'Primeira página' : 'Ver repositórios', route.offset ? historyHash(route.filter) : '#/repositorios'));
  if (route.offset > 0) pagination.append(link('← Anteriores', historyHash(route.filter, Math.max(0, route.offset - 20))));
  if (route.offset + data.reports.length < data.total) pagination.append(link('Próximas →', historyHash(route.filter, route.offset + 20)));
}
async function loadAnalyses(route, version) {
  const target = document.querySelector('#analyses');
  const params = new URLSearchParams({limit: '20', offset: String(route.offset)});
  if (route.filter) params.set('repository_id', route.filter);
  const path = '/api/reports?' + params;
  const previous = lastReads.get(path);
  if (previous) renderAnalyses(previous, route);
  else {
    target.replaceChildren(el('p', 'Carregando histórico…', 'muted'));
    for (const selector of ['#analysis-filters', '#analysis-count', '#analysis-pagination']) document.querySelector(selector).replaceChildren();
  }
  try {
    const data = await read(path);
    if (version !== routeVersion) return;
    renderAnalyses(data, route);
  } catch (error) {
    if (version !== routeVersion) return;
    if (!lastReads.has(path)) {
      target.replaceChildren(emptyState('Histórico indisponível', error.message));
      for (const selector of ['#analysis-filters', '#analysis-count', '#analysis-pagination']) document.querySelector(selector).replaceChildren();
    }
    notify(error.message, true);
  }
}
function svgNode(tag, attrs) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  return node;
}
function dailyChart(data) {
  const chart = el('article', undefined, 'chart-card');
  chart.append(el('h3', 'Análises ao longo do tempo'), el('p', 'Geração de relatórios · Últimos 30 dias · UTC', 'muted'));
  const svg = svgNode('svg', {viewBox: '0 0 640 180', role: 'img', 'aria-label': 'Análises geradas por dia. Valores disponíveis na tabela abaixo.', class: 'daily-chart'});
  const max = Math.max(1, ...data.daily_reports.map(day => day.count));
  for (const y of [20, 85, 150]) svg.append(svgNode('line', {x1: 20, x2: 620, y1: y, y2: y, class: 'chart-grid'}));
  const points = data.daily_reports.map((day, index) => [20 + index * 600 / Math.max(1, data.daily_reports.length - 1), 150 - day.count * 130 / max]);
  if (points.length) {
    const coords = points.map(point => point.join(',')).join(' ');
    svg.append(svgNode('polygon', {points: '20,150 ' + coords + ' 620,150', class: 'chart-area'}));
    svg.append(svgNode('polyline', {points: coords, class: 'chart-line'}));
    data.daily_reports.forEach((day, index) => {
      if (!day.count) return;
      const point = svgNode('circle', {cx: points[index][0], cy: points[index][1], r: 4, class: 'chart-point'});
      const title = svgNode('title', {}); title.textContent = day.date + ': ' + day.count;
      point.append(title); svg.append(point);
    });
  }
  const actualMax = Math.max(0, ...data.daily_reports.map(day => day.count));
  chart.append(svg, el('p', data.period_start + ' → ' + data.period_end + ' · Máximo diário: ' + actualMax, 'chart-caption'));
  const details = el('details'); details.append(el('summary', 'Ver valores por dia'));
  const table = el('table', undefined, 'chart-table');
  const header = el('tr'); header.append(el('th', 'Dia (UTC)'), el('th', 'Análises'));
  const head = el('thead'); head.append(header); table.append(head);
  const body = el('tbody');
  for (const day of data.daily_reports) { const row = el('tr'); row.append(el('td', day.date), el('td', String(day.count))); body.append(row); }
  table.append(body); details.append(table); chart.append(details);
  return chart;
}
function renderDashboard(data) {
  const target = document.querySelector('#dashboard-content');
  const metrics = el('div', undefined, 'metrics');
  for (const [label, value, hint] of [['Repositórios conectados', data.connected_repositories, 'Vínculos e instalações ativos'], ['Análises salvas', data.total_reports, 'Todo o histórico deste projeto'], ['Análises parciais', data.partial_reports, 'Relatórios com limitações de cobertura']]) {
    const card = el('article', undefined, 'metric-card'); card.append(el('p', label), el('strong', String(value)), el('small', hint)); metrics.append(card);
  }
  const charts = el('div', undefined, 'chart-grid-layout');
  charts.append(dailyChart(data));
  const distribution = el('article', undefined, 'chart-card');
  distribution.append(el('h3', 'Análises por repositório'), el('p', 'Histórico completo · Relatórios distintos por repositório', 'muted'));
  const max = Math.max(1, ...data.repository_reports.map(repo => repo.count));
  for (const repo of data.repository_reports) {
    const row = el('div', undefined, 'distribution-row');
    row.append(link(repo.full_name, historyHash(repo.id)), el('strong', String(repo.count)));
    const bar = svgNode('svg', {viewBox: '0 0 200 5', 'aria-hidden': 'true', class: 'distribution-bar', preserveAspectRatio: 'none'});
    bar.append(svgNode('rect', {width: 200, height: 5, rx: 2.5, class: 'bar-track'}), svgNode('rect', {width: repo.count * 200 / max, height: 5, rx: 2.5, class: 'bar-fill'}));
    row.append(bar); distribution.append(row);
  }
  if (!data.repository_reports.length) distribution.append(el('p', 'Sem análises associadas a repositórios.', 'muted'));
  distribution.append(el('p', 'Um relatório pode incluir mais de um repositório.', 'chart-caption'));
  charts.append(distribution);
  const recent = el('div', undefined, 'recent-reports');
  const heading = el('div', undefined, 'toolbar'); heading.append(el('h2', 'Últimas análises'), link('Ver histórico completo →', '#/analises'));
  recent.append(heading, ...data.recent_reports.map(reportRow));
  if (!data.recent_reports.length) recent.append(emptyState('Ainda não há análises salvas', data.connected_repositories ? 'Abra um repositório e confirme a primeira coleta.' : 'Confira seus repositórios ou adicione o primeiro para começar.', 'Ver repositórios', '#/repositorios'));
  target.replaceChildren(metrics, charts, recent);
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
      for (const path of readCache.keys()) if (!path.endsWith('/details')) readCache.delete(path);
    } else readCache.clear();
  }
  loadingRepositories = true;
  const reload = document.querySelector('#reload'); reload.disabled = true;
  const paths = new Set(['/api/repositories', '/api/dashboard', '/api/reports?limit=20&offset=0']);
  const route = currentRoute();
  if (route?.page === 'analises') {
    if (route.id) paths.add('/api/reports/' + route.id + '/details');
    else {
      const params = new URLSearchParams({limit: '20', offset: String(route.offset)});
      if (route.filter) params.set('repository_id', route.filter);
      paths.add('/api/reports?' + params);
    }
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
  const navigation = initial ? null : navigate();
  const results = await Promise.allSettled(reads);
  if (navigation) await navigation;
  const data = lastReads.get('/api/repositories');
  const failed = results.some(result => result.status === 'rejected');
  if (data) {
    repositoryData = data.repositories; serverBusy = data.updating;
  } else repositoryData = null;
  notify(failed ? 'Não foi possível atualizar todos os dados. Os últimos dados disponíveis foram mantidos; tente Recarregar dados.' : '', failed);
  loadingRepositories = false;
  if (currentRoute()?.page === 'repositorios') renderRepositories(currentRoute().id);
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
