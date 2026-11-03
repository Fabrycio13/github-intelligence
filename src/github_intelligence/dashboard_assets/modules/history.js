'use strict';
(function () {
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
    for (const path of evicted) {
      lastReads.delete(path);
      readCache.delete(path);
    }
    while (lastReads.size > readCacheLimit) {
      const path = lastReads.keys().next().value;
      lastReads.delete(path);
      readCache.delete(path);
    }
  }

  async function request(path, options) {
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      throw Object.assign(new Error(data.error || 'Não foi possível concluir a operação.'), {
        status: response.status,
      });
    }
    return data;
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

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function time(value) {
    return value ? new Date(value).toLocaleString('pt-BR') : 'Ainda não registrada';
  }

  function link(text, hash, className = 'text-link') {
    const node = el('a', text, className);
    node.href = hash;
    return node;
  }

  function reportNames(report) {
    return report.repositories.map(repo => repo.full_name).join(', ') || 'Repositório não identificado';
  }

  function completeness(report) {
    return report.complete ? 'Completo dentro da cobertura declarada' : 'Relatório parcial';
  }

  function emptyState(title, description, action, href) {
    const box = el('div', undefined, 'empty-state');
    box.append(el('h3', title), el('p', description));
    if (action) box.append(link(action, href));
    return box;
  }

  function notify(text, error = false) {
    const message = document.querySelector('#message');
    if (!message) return;
    message.textContent = text;
    message.dataset.error = String(error);
    message.hidden = !text;
  }

  function historyHash(filter, offset = 0) {
    const params = new URLSearchParams();
    if (filter) params.set('repository_id', filter);
    if (offset) params.set('offset', String(offset));
    return '#/analises' + (params.size ? '?' + params : '');
  }

  // Keep one mounted history, independent of the report drawer and search controls.
  const analysisState = {
    key: null,
    filter: '',
    offset: 0,
    next: 0,
    total: 0,
    ids: new Set(),
    loading: false,
    request: 0,
    ready: false,
  };
  const analysisNames = new Map();
  let analysisFailure = null;

  function reportsPath(filter, offset = 0) {
    const params = new URLSearchParams({limit: '10', offset: String(offset), include_options: 'false'});
    if (filter) params.set('repository_id', filter);
    return '/api/reports?' + params;
  }

  function analysisSelection(filter) {
    if (!filter) return 'Todos os repositórios';
    return (
      analysisNames.get(filter) ||
      (window.DashboardApp?.getRepositories?.() || []).find(repo => repo.id === filter)?.full_name ||
      'Repositório selecionado'
    );
  }

  function analysisControls() {
    const selNode = document.querySelector('#analysis-selection');
    if (selNode) selNode.textContent = analysisSelection(analysisState.filter);
    const countNode = document.querySelector('#analysis-count');
    if (countNode) {
      countNode.textContent =
        analysisState.total +
        (analysisState.total === 1 ? ' análise salva' : ' análises salvas') +
        ' · Exibindo ' +
        analysisState.ids.size;
    }
    const more = document.querySelector('#analysis-more');
    if (more) {
      more.disabled = analysisState.loading;
      more.hidden = !analysisFailure && (!analysisState.ready || analysisState.next >= analysisState.total);
      more.textContent = analysisState.loading
        ? 'Carregando…'
        : analysisFailure
        ? 'Tentar novamente'
        : 'Carregar mais';
    }
  }

  function reportRow(report) {
    const card = el('article', undefined, 'analysis-row');
    const info = el('div');
    info.append(
      el('h3', reportNames(report)),
      el('p', 'Gerado em ' + time(report.generated_at), 'muted'),
      el('p', 'Intervalo UTC: ' + report.window_start + ' → ' + report.window_end, 'report-period')
    );
    const actions = el('div', undefined, 'analysis-actions');
    const open = el('button', 'Abrir análise →', 'btn-secondary');
    open.type = 'button';
    open.addEventListener('click', () => openDrawerReport(report.id));
    actions.append(
      el('span', completeness(report), 'badge ' + (report.complete ? 'badge-success' : 'badge-warning')),
      open
    );
    card.append(info, actions);
    return card;
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
    if (feedback) feedback.textContent = 'Buscando análises de ' + analysisSelection(route.filter) + '…';
    if (target) target.setAttribute('aria-busy', 'true');
    analysisControls();
    try {
      const data = await read(reportsPath(route.filter, offset));
      if (token !== analysisState.request) return;
      if (!append) {
        analysisState.ids.clear();
        if (target) target.replaceChildren();
        analysisState.filter = route.filter;
        analysisState.offset = route.offset;
      }
      for (const report of data.reports) {
        for (const repo of report.repositories) analysisNames.set(repo.id, repo.full_name);
        if (analysisState.ids.has(report.id)) continue;
        analysisState.ids.add(report.id);
        if (target) target.append(reportRow(report));
      }
      if (!analysisState.ids.size && target) {
        target.append(
          emptyState(
            route.filter ? 'Este repositório ainda não tem análises' : 'Ainda não há análises salvas',
            'As análises aparecerão aqui após uma coleta confirmada.'
          )
        );
      }
      analysisState.next = offset + data.reports.length;
      analysisState.total = data.total;
      analysisState.ready = true;
      if (feedback) feedback.textContent = '';
    } catch (error) {
      if (token !== analysisState.request) return;
      analysisFailure = {route, append, force};
      if (feedback) feedback.textContent = 'Não foi possível carregar esta seleção. Tente novamente. ' + error.message;
    } finally {
      if (token === analysisState.request) {
        analysisState.loading = false;
        if (target) target.setAttribute('aria-busy', 'false');
        analysisControls();
      }
    }
  }

  let searchTimer;
  let searchRequest = 0;
  let searchController;

  function initSearch() {
    const analysisSearch = document.querySelector('#analysis-search');
    const analysisResults = document.querySelector('#analysis-search-results');
    if (!analysisSearch || !analysisResults) return;

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
            const button = el('button', repo.full_name, 'analysis-search-option');
            button.type = 'button';
            button.addEventListener('click', () => {
              analysisResults.hidden = true;
              location.hash = historyHash(repo.id);
            });
            analysisResults.append(button);
          }
          if (!data.repositories.length) {
            analysisResults.append(el('p', 'Nenhum repositório encontrado.', 'muted'));
          }
        } catch (error) {
          if (token !== searchRequest || error.name === 'AbortError') return;
          analysisResults.replaceChildren(
            el('p', 'Não foi possível buscar. Digite novamente para tentar.', 'muted')
          );
        }
      }, 250);
    });
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
    const mainContent = document.querySelector('#main-content');
    if (mainContent) mainContent.inert = false;
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) sidebar.inert = false;
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
    const copyBtn = document.querySelector('#drawer-copy-btn');
    const closeBtn = document.querySelector('#drawer-close-btn');

    drawer.hidden = false;
    backdrop.hidden = false;
    const mainContent = document.querySelector('#main-content');
    if (mainContent) mainContent.inert = true;
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) sidebar.inert = true;
    if (closeBtn) closeBtn.focus({preventScroll: true});
    requestAnimationFrame(() => {
      if (version !== drawerVersion) return;
      drawer.dataset.open = 'true';
      backdrop.dataset.open = 'true';
    });

    if (fullscreenBtn) {
      fullscreenBtn.onclick = () => {
        closeDrawerReport();
        openReport(id);
      };
    }

    const path = '/api/reports/' + encodeURIComponent(id) + '/details';
    function display(report) {
      if (title) title.textContent = reportNames(report);
      if (meta) meta.textContent = 'Gerado em ' + time(report.generated_at);
      if (banner) {
        banner.replaceChildren();
        banner.append(
          el('span', completeness(report), 'badge ' + (report.complete ? 'badge-success' : 'badge-warning')),
          el('span', 'Janela UTC: ' + (report.window_start || '—') + ' → ' + (report.window_end || '—'), 'code-pill')
        );
      }
      if (content) content.textContent = report.content;
      if (copyBtn) copyBtn.disabled = false;
    }
    const previous = cachedRead(path);
    if (copyBtn) copyBtn.disabled = !previous;
    if (previous) {
      display(previous);
      return;
    }
    if (title) title.textContent = 'Carregando análise…';
    if (meta) meta.textContent = '';
    if (banner) banner.replaceChildren();
    if (content) content.textContent = 'Consultando a análise selecionada…';
    try {
      const report = await read(path);
      if (activeDrawerReportId !== id || version !== drawerVersion) return;
      display(report);
    } catch (err) {
      if (activeDrawerReportId !== id || version !== drawerVersion) return;
      if (title) title.textContent = 'Análise indisponível';
      if (content) content.textContent = err.message;
      const retry = el('button', 'Tentar novamente', 'btn-secondary');
      retry.type = 'button';
      retry.addEventListener('click', () => openDrawerReport(id));
      if (banner) banner.replaceChildren(retry);
    }
  }

  function openReport(id) {
    const params = new URLSearchParams({return_to: location.hash.slice(1) || '/dashboard'});
    location.hash = '/analises/' + encodeURIComponent(id) + '?' + params;
  }

  function renderReport(report) {
    const title = document.querySelector('#report-title');
    if (title) title.textContent = reportNames(report);
    const content = document.querySelector('#report-content');
    if (content) content.textContent = report.content;
    const status = document.querySelector('#report-status');
    if (status) {
      status.textContent =
        completeness(report) +
        ' · Gerado em ' +
        time(report.generated_at) +
        ' · Intervalo UTC: ' +
        report.window_start +
        ' → ' +
        report.window_end;
    }
  }

  async function loadReport(id, version) {
    const content = document.querySelector('#report-content');
    const path = '/api/reports/' + encodeURIComponent(id) + '/details';
    const previous = lastReads.get(path);
    if (previous) renderReport(previous);
    else {
      if (content) content.textContent = 'Carregando análise…';
      const title = document.querySelector('#report-title');
      if (title) title.textContent = 'Relatório salvo';
      const status = document.querySelector('#report-status');
      if (status) status.textContent = '';
    }
    try {
      const report = await read(path);
      const currentVer = window.DashboardApp?.getRouteVersion?.() ?? version;
      if (version !== currentVer) return;
      renderReport(report);
    } catch (error) {
      const currentVer = window.DashboardApp?.getRouteVersion?.() ?? version;
      if (version !== currentVer) return;
      if (!lastReads.has(path)) {
        if (content) content.textContent = error.message;
        const title = document.querySelector('#report-title');
        if (title) title.textContent = 'Análise indisponível';
        const status = document.querySelector('#report-status');
        if (status) status.textContent = '';
      }
      notify(error.message, true);
    }
  }

  function initListeners() {
    document.querySelector('#analysis-more')?.addEventListener('click', () => {
      const routeVersion = window.DashboardApp?.getRouteVersion?.() ?? 0;
      if (analysisFailure) {
        const failed = analysisFailure;
        return loadAnalyses(failed.route, routeVersion, {append: failed.append, force: true});
      }
      return loadAnalyses({filter: analysisState.filter, offset: analysisState.offset}, routeVersion, {append: true});
    });

    document.querySelector('#analysis-all')?.addEventListener('click', () => {
      location.hash = historyHash('');
    });

    document.querySelector('#analysis-new')?.addEventListener('click', async () => {
      const button = document.querySelector('#analysis-new');
      if (analysisState.loading) return;
      for (const path of readCache.keys()) {
        if (path.startsWith('/api/reports?')) {
          readCache.delete(path);
          lastReads.delete(path);
        }
      }
      if (button) button.hidden = true;
      const routeVersion = window.DashboardApp?.getRouteVersion?.() ?? 0;
      await loadAnalyses({filter: analysisState.filter, offset: 0}, routeVersion, {force: true});
      if (analysisFailure && button) button.hidden = false;
    });

    initSearch();

    document.querySelector('#drawer-close-btn')?.addEventListener('click', closeDrawerReport);
    document.querySelector('#drawer-backdrop')?.addEventListener('click', closeDrawerReport);

    document.querySelector('#drawer-copy-btn')?.addEventListener('click', async () => {
      const text = document.querySelector('#drawer-content')?.textContent;
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

    const copyReportBtn = document.querySelector('#copy-report');
    if (copyReportBtn) {
      copyReportBtn.addEventListener('click', async () => {
        const text = document.querySelector('#report-content')?.textContent;
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

    document.querySelector('#close-report')?.addEventListener('click', () => {
      const target = window.DashboardApp?.currentRoute?.()?.params.get('return_to') || '/analises';
      location.hash = /^\/(dashboard|repositorios|analises)(\/|\?|$)/.test(target) && !target.includes('return_to')
        ? target
        : '/analises';
    });

    window.addEventListener('keydown', event => {
      const panel = document.querySelector('#report-drawer');
      if (event.key === 'Tab' && panel && !panel.hidden && panel.dataset.open === 'true') {
        const controls = [...panel.querySelectorAll('button:not(:disabled)')];
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (first && event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (last && !event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
      if (event.key === 'Escape') {
        const drawer = document.querySelector('#report-drawer');
        if (drawer && !drawer.hidden && drawer.dataset.open === 'true') {
          closeDrawerReport();
        }
      }
    });
  }

  initListeners();

  window.DashboardHistory = {
    read,
    cachedRead,
    request,
    lastReads,
    readCache,
    trimReads,
    loadAnalyses,
    loadReport,
    renderReport,
    openDrawerReport,
    closeDrawerReport,
    openReport,
    historyHash,
    reportsPath,
    el,
    time,
    link,
    reportNames,
    completeness,
    emptyState,
    notify,
    analysisState,
    analysisNames,
  };
})();
