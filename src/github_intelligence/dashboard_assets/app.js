'use strict';
(function () {
  const {
    read,
    request,
    lastReads,
    readCache,
    loadAnalyses,
    loadReport,
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
  } = window.DashboardHistory;

  const listing = document.querySelector('#repositories');
  const statuses = {
    pending: 'Na fila',
    running: 'Em andamento',
    succeeded: 'Concluída',
    partial: 'Parcial',
    failed: 'Falhou',
  };
  let busy = false;
  let repositoryData = null;
  let loadingRepositories = true;
  let refreshing = false;
  let serverBusy = false;
  let routeVersion = 0;

  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

  function currentRoute() {
    const [path, query = ''] = location.hash.slice(1).split('?');
    const parts = (path || '/dashboard').split('/').filter(Boolean);
    const params = new URLSearchParams(query);
    if (
      !['dashboard', 'repositorios', 'analises'].includes(parts[0]) ||
      parts.length > 2 ||
      (parts[1] && (!uuidPattern.test(parts[1]) || parts[0] === 'dashboard'))
    )
      return null;
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
    document.querySelectorAll('[data-page]').forEach(section => (section.hidden = section.dataset.page !== page));
    document.querySelectorAll('[data-nav]').forEach(navLink => {
      if (navLink.dataset.nav === route.page) navLink.setAttribute('aria-current', 'page');
      else navLink.removeAttribute('aria-current');
    });
    const titles = {
      dashboard: ['Dashboard', 'Uma visão geral dos seus repositórios e relatórios.'],
      repositorios: ['Repositórios', 'Gerencie os vínculos e consulte suas coletas.'],
      analises: ['Análises', 'Seu histórico de relatórios de evidências.'],
      relatorio: ['Análise salva', 'Consulte o resultado sem iniciar uma nova coleta.'],
    };
    const titleNode = document.querySelector('#page-title');
    if (titleNode) titleNode.textContent = titles[page][0];
    const descNode = document.querySelector('#page-description');
    if (descNode) descNode.textContent = titles[page][1];
    document.title = titles[page][0] + ' · GitHub Intelligence';
    if (page === 'repositorios' || page === 'dashboard') {
      if (!repositoryData) {
        try {
          const data = await read('/api/repositories');
          repositoryData = data.repositories;
          serverBusy = data.updating;
        } catch (error) {
          notify(error.message, true);
        }
        loadingRepositories = false;
        if (version !== routeVersion) return;
      }
      if (page === 'repositorios') renderRepositories(route.id);
      else await loadDashboard(version);
    }
    if (page === 'analises') await loadAnalyses(route, version);
    if (page === 'relatorio') await loadReport(route.id, version);
  }

  function inputDate(value) {
    return new Date(value).toISOString().slice(0, 16);
  }

  function renderRepositories(id) {
    if (!listing) return;
    listing.replaceChildren();
    if (!repositoryData) {
      listing.append(
        emptyState(
          loadingRepositories ? 'Carregando repositórios…' : 'Não foi possível carregar os repositórios',
          loadingRepositories ? 'Aguarde a consulta dos vínculos.' : 'Use Recarregar dados para tentar novamente.'
        )
      );
      return;
    }
    if (id) {
      listing.append(link('← Todos os repositórios', '#/repositorios'));
      const repo = repositoryData.find(item => item.id === id);
      if (!repo) {
        listing.append(emptyState('Repositório indisponível', 'Confira o vínculo e volte à listagem.'));
        return;
      }
      listing.append(render(repo, serverBusy));
      return;
    }
    if (!repositoryData.length) {
      listing.append(
        emptyState(
          'Seu primeiro repositório começa aqui',
          'Use Adicionar repositório para verificar o acesso e confirmar o vínculo.'
        )
      );
    }
    for (const repo of repositoryData) {
      const card = el('article', undefined, 'repository-row');
      const heading = el('h3');
      heading.append(link(repo.full_name, '#/repositorios/' + repo.id));
      const status =
        repo.status === 'active' && repo.installation_status === 'active'
          ? 'Vínculo ativo'
          : 'Vínculo indisponível para coleta';
      const info = el('div');
      info.append(
        heading,
        el('p', status + ' · Última coleta: ' + (statuses[repo.last_status] || 'Ainda não realizada'), 'muted')
      );
      const actions = el('div', undefined, 'actions');
      actions.append(
        link('Abrir repositório →', '#/repositorios/' + repo.id),
        link('Ver análises', '#/analises?repository_id=' + repo.id)
      );
      card.append(info, actions);
      listing.append(card);
    }
  }

  function render(repo, currentServerBusy) {
    const article = el('article');
    article.append(el('h3', repo.full_name));
    const dl = el('dl');
    const pairs = [
      ['Monitoramento', repo.status === 'active' ? 'Ativo' : 'Inativo'],
      ['Última coleta', statuses[repo.last_status] || 'Nenhuma coleta'],
      ['Início', time(repo.last_started_at)],
      ['Conclusão', time(repo.last_finished_at)],
      ['Intervalo (UTC)', repo.window_start ? repo.window_start + ' → ' + repo.window_end : 'Ainda não registrado'],
    ];
    for (const [label, value] of pairs) {
      const group = el('div');
      group.append(el('dt', label), el('dd', value));
      dl.append(group);
    }
    article.append(dl);
    const actions = el('div', undefined, 'actions');
    if (repo.report_id) {
      const open = el('button', 'Abrir relatório salvo');
      open.type = 'button';
      open.addEventListener('click', () => openReport(repo.report_id));
      actions.append(open);
    } else {
      actions.append(el('p', 'Sem relatório salvo para a última coleta.'));
    }
    actions.append(link('Ver todo o histórico', historyHash(repo.id)));
    article.append(actions);
    const form = el('form');
    const startLabel = el('label', 'Início inclusivo (UTC)');
    const start = el('input');
    start.type = 'datetime-local';
    start.required = true;
    start.name = 'since';
    const now = new Date();
    start.value = inputDate(repo.window_start || new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)));
    const endLabel = el('label', 'Fim exclusivo (UTC)');
    const end = el('input');
    end.type = 'datetime-local';
    end.required = true;
    end.name = 'until';
    end.value = inputDate(now);
    end.max = inputDate(now);
    startLabel.append(start);
    endLabel.append(end);
    const update = el('button', 'Atualizar agora');
    update.type = 'submit';
    update.disabled =
      busy ||
      loadingRepositories ||
      currentServerBusy ||
      repo.status !== 'active' ||
      repo.installation_status !== 'active' ||
      repo.last_status === 'running' ||
      repo.last_status === 'pending';
    form.append(startLabel, endLabel, update);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || loadingRepositories || !form.reportValidity()) return;
      const since = new Date(start.value + 'Z');
      const until = new Date(end.value + 'Z');
      if (!(since < until) || until > new Date()) {
        notify('Use início anterior ao fim, sem datas futuras.', true);
        return;
      }
      if (
        !window.confirm(
          'Consultar o GitHub e gravar uma nova coleta de ' +
            repo.full_name +
            ' no Supabase?\nIntervalo UTC: ' +
            since.toISOString() +
            ' até ' +
            until.toISOString()
        )
      )
        return;
      busy = true;
      document.querySelectorAll('form button').forEach(button => (button.disabled = true));
      notify('Coletando ' + repo.full_name + '… Aguarde; não feche o painel.');
      let result;
      let failure;
      try {
        result = await request('/api/update', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Request-Token': document.querySelector('meta[name="request-token"]').content,
          },
          body: JSON.stringify({repository_id: repo.id, since: since.toISOString(), until: until.toISOString()}),
        });
      } catch (error) {
        failure = error;
      } finally {
        busy = false;
        const inspectBtn = document.querySelector('#inspect-repository');
        if (inspectBtn) inspectBtn.disabled = false;
      }
      const refreshed = await load({changed: true});
      const outcome = failure ? failure.message : result.message;
      notify(outcome + (refreshed ? '' : ' Não foi possível atualizar a tela; use Recarregar dados.'), !!failure || !refreshed);
    });
    article.append(form);
    return article;
  }

  function svgNode(tag, attrs) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  }

  function repoIcon() {
    const icon = svgNode('svg', {
      viewBox: '0 0 24 24',
      width: '15',
      height: '15',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '1.8',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'aria-hidden': 'true',
      class: 'icon-repo',
    });
    icon.append(
      svgNode('path', {d: 'M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z'}),
      svgNode('path', {d: 'M6 6h10M6 10h10'})
    );
    return icon;
  }

  function renderDashboard(data) {
    const target = document.querySelector('#dashboard-content');
    if (!target) return;
    target.replaceChildren();

    // 1. Context Ribbon
    const ribbon = el('div', undefined, 'dashboard-ribbon');
    const ribbonItems = el('div', undefined, 'ribbon-items');

    const repoPill = el('a', undefined, 'ribbon-pill');
    repoPill.href = '#/repositorios';
    const dot = el('span', undefined, 'badge-dot');
    const repoLabel = el(
      'span',
      data.connected_repositories + (data.connected_repositories === 1 ? ' repositório ativo' : ' repositórios ativos')
    );
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

    // 2. High-Density Workstation Grid
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
    const searchIcon = svgNode('svg', {
      viewBox: '0 0 24 24',
      width: '13',
      height: '13',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'aria-hidden': 'true',
      class: 'search-icon',
    });
    searchIcon.append(svgNode('circle', {cx: '11', cy: '11', r: '8'}), svgNode('path', {d: 'm21 21-4.3-4.3'}));
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
    feedControls.append(
      searchWrap,
      link('Ver histórico completo (' + data.total_reports + ') →', '#/analises', 'text-link')
    );
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
        const rLink = link(
          reportNames(report),
          report.repositories[0]?.id ? historyHash(report.repositories[0].id) : '#/analises'
        );
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
        const badge = el(
          'span',
          completeness(report),
          'badge ' + (report.complete ? 'badge-success' : 'badge-warning')
        );
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

      const emptySearchNotice = el(
        'p',
        'Nenhuma evidência recente corresponde ao filtro informado.',
        'filter-empty-state muted'
      );
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
      feedCol.append(
        emptyState(
          'Ainda não há análises salvas',
          data.connected_repositories
            ? 'Inicie a coleta de um dos repositórios monitorados ao lado.'
            : 'Adicione o primeiro repositório para começar a monitorar evidências.',
          'Ver repositórios',
          '#/repositorios'
        )
      );
    }

    // Coluna Lateral: Repositórios Monitorados & Sincronização
    const sideCol = el('aside', undefined, 'dashboard-side-area');
    const sideHeader = el('div', undefined, 'section-heading');
    const sideTitle = el('div');
    sideTitle.append(el('h2', 'Repositórios'), el('p', 'Vínculos ativos monitorados', 'muted'));
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
        const statusBadge = el(
          'span',
          isOnline ? 'Ativo' : 'Inativo',
          'badge ' + (isOnline ? 'badge-success' : 'badge-danger')
        );
        head.append(titleWrap, statusBadge);

        const meta = el('div', undefined, 'quick-repo-meta');
        const lastStatus = statuses[repo.last_status] || 'Ainda não realizada';
        const statusLine = el('p', undefined, 'quick-repo-line');
        statusLine.append(
          el('span', 'Status do sync: ', 'muted'),
          el(
            'span',
            lastStatus,
            repo.last_status === 'succeeded'
              ? 'text-success'
              : repo.last_status === 'failed'
              ? 'text-danger'
              : ''
          )
        );
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
      reposContainer.append(
        emptyState(
          'Nenhum repositório vinculado',
          'Conecte um repositório para iniciar a coleta determinística.',
          'Adicionar repositório',
          '#/repositorios'
        )
      );
    }
    sideCol.append(reposContainer);

    grid.append(feedCol, sideCol);
    target.append(ribbon, grid);
  }

  async function loadDashboard(version) {
    const target = document.querySelector('#dashboard-content');
    if (!target) return;
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

  async function load({initial = false, changed = false} = {}) {
    if (busy || refreshing) return;
    refreshing = true;
    if (!initial) {
      if (changed) {
        for (const path of readCache.keys()) {
          if (!path.endsWith('/details') && !path.startsWith('/api/reports?')) {
            readCache.delete(path);
            lastReads.delete(path);
          }
        }
        if (analysisState.ready) {
          const newBtn = document.querySelector('#analysis-new');
          if (newBtn) newBtn.hidden = false;
        } else {
          for (const path of readCache.keys()) {
            if (path.startsWith('/api/reports?')) {
              readCache.delete(path);
              lastReads.delete(path);
            }
          }
        }
      } else {
        readCache.clear();
        lastReads.clear();
      }
    }
    loadingRepositories = true;
    const reload = document.querySelector('#reload');
    if (reload) reload.disabled = true;
    const route = currentRoute();
    const paths = new Set();
    if (route?.page === 'dashboard') {
      paths.add('/api/repositories');
      paths.add('/api/dashboard');
    }
    if (route?.page === 'repositorios' || changed) paths.add('/api/repositories');
    if (route?.page === 'analises') {
      if (route.id) paths.add('/api/reports/' + route.id + '/details');
      else if (!changed || !analysisState.ready) paths.add(reportsPath(route.filter, route.offset));
    }
    let loader = null;
    if (initial) {
      loader = window.DashboardLoader?.initLoading ? window.DashboardLoader.initLoading() : null;
    }
    let completed = 0;
    const reads = [...paths].map(path =>
      read(path).then(data => {
        completed++;
        if (loader) loader.advance(completed, paths.size);
        return data;
      })
    );
    const navigation = initial || route?.page === 'analises' ? null : navigate();
    const results = await Promise.allSettled(reads);
    if (navigation) await navigation;
    const data = lastReads.get('/api/repositories');
    const failed = results.some(result => result.status === 'rejected');
    if (data) {
      repositoryData = data.repositories;
      serverBusy = data.updating;
    } else if (paths.has('/api/repositories')) repositoryData = null;
    notify(
      failed
        ? 'Não foi possível atualizar todos os dados. Os últimos dados disponíveis foram mantidos; tente Recarregar dados.'
        : '',
      failed
    );
    loadingRepositories = false;
    if (!initial && route?.page === 'analises' && !route.id && !changed) {
      await loadAnalyses(route, routeVersion, {force: true});
    }
    if (currentRoute()?.page === 'repositorios') renderRepositories(currentRoute().id);
    if (currentRoute()?.page === 'dashboard') {
      const dashData = lastReads.get('/api/dashboard');
      if (dashData) renderDashboard(dashData);
    }
    if (reload) reload.disabled = false;
    refreshing = false;
    if (initial) {
      if (loader) loader.finish();
      else if (window.DashboardLoader?.showApp) window.DashboardLoader.showApp();
    }
    return !failed;
  }

  function initApp() {
    document.querySelector('#retry-loading')?.addEventListener('click', () => {
      readCache.clear();
      load({initial: true});
      navigate();
    });

    document.querySelector('#reload')?.addEventListener('click', load);

    document.querySelector('.skip-link')?.addEventListener('click', event => {
      event.preventDefault();
      const main = document.querySelector('#main-content');
      if (main) {
        main.focus();
        main.scrollIntoView();
      }
    });

    initOnboarding();

    window.addEventListener('hashchange', () => {
      const main = document.querySelector('#main-content');
      if (main) main.focus({preventScroll: true});
      navigate();
    });

    if (!location.hash) history.replaceState(null, '', '#/dashboard');
    load({initial: true});
    navigate();
  }

  function initOnboarding() {
    const onboardingSection = document.querySelector('#onboarding');
    const inspectForm = document.querySelector('#inspect-form');
    const linkInput = document.querySelector('#repository-link');
    const inspectButton = document.querySelector('#inspect-repository');
    const onboardingMessage = document.querySelector('#onboarding-message');
    const onboardingResult = document.querySelector('#onboarding-result');
    const addRepoBtn = document.querySelector('#add-repository');

    if (!onboardingSection || !inspectForm) return;

    addRepoBtn?.addEventListener('click', () => {
      if (busy || loadingRepositories) {
        notify('Aguarde a operação atual antes de adicionar um repositório.', true);
        return;
      }
      inspectForm.reset();
      onboardingResult.replaceChildren();
      onboardingMessage.textContent = '';
      inspectButton.textContent = 'Verificar acesso';
      onboardingSection.showModal();
      addRepoBtn.setAttribute('aria-expanded', 'true');
      linkInput.focus();
    });

    function closeOnboarding() {
      if (busy) {
        onboardingMessage.textContent = 'Aguarde a operação terminar antes de fechar.';
        return;
      }
      onboardingSection.close();
    }

    document.querySelector('#close-onboarding')?.addEventListener('click', closeOnboarding);

    onboardingSection.addEventListener('cancel', event => {
      if (busy) {
        event.preventDefault();
        onboardingMessage.textContent = 'Aguarde a operação terminar antes de fechar.';
      }
    });

    onboardingSection.addEventListener('close', () => {
      addRepoBtn?.setAttribute('aria-expanded', 'false');
      addRepoBtn?.focus();
    });

    linkInput?.addEventListener('input', () => {
      onboardingResult.replaceChildren();
      onboardingMessage.textContent = '';
      inspectButton.textContent = 'Verificar acesso';
    });

    function post(path, payload) {
      return request(path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Request-Token': document.querySelector('meta[name="request-token"]').content,
        },
        body: JSON.stringify(payload),
      });
    }

    function showInspection(result, repoLink) {
      onboardingResult.replaceChildren();
      if (result.status === 'authorization_required') {
        onboardingMessage.textContent =
          result.message || 'A App ainda não tem acesso. Autorize no GitHub e depois verifique novamente.';
        const url = new URL(result.authorization_url);
        if (url.origin !== 'https://github.com' || !/^\/apps\/[a-z0-9-]+\/installations\/new$/.test(url.pathname))
          throw new Error('Endereço de autorização inválido.');
        const authorize = el('a', 'Autorizar no GitHub');
        authorize.href = url.href;
        authorize.target = '_blank';
        authorize.rel = 'noopener noreferrer';
        onboardingResult.append(authorize);
        inspectButton.textContent = 'Já autorizei — verificar novamente';
        return;
      }
      if (result.status !== 'ready') throw new Error('Resposta de verificação inesperada.');
      onboardingMessage.textContent =
        result.full_name +
        ' · Acesso confirmado pela instalação ' +
        result.installation_id +
        '. ' +
        (result.message || 'Confirme para vincular ao projeto.');
      const confirm = el('button', 'Confirmar vínculo');
      confirm.type = 'button';
      confirm.disabled = !result.can_bind;
      confirm.addEventListener('click', async () => {
        if (busy || !window.confirm('Vincular ' + result.full_name + ' a este projeto? Nenhuma coleta será iniciada.'))
          return;
        busy = true;
        confirm.disabled = true;
        inspectButton.disabled = true;
        linkInput.disabled = true;
        onboardingMessage.textContent = 'Revalidando acesso e salvando vínculo…';
        try {
          const expected = {
            full_name: result.full_name,
            github_id: result.github_id,
            app_id: result.app_id,
            installation_id: result.installation_id,
          };
          const bound = await post('/api/repositories/bind', {link: repoLink, expected});
          if (bound.status === 'bound') {
            onboardingResult.replaceChildren();
            onboardingMessage.textContent = bound.message;
            busy = false;
            inspectButton.disabled = false;
            linkInput.disabled = false;
            onboardingSection.close();
            const refreshed = await load({changed: true});
            location.hash = '/repositorios/' + bound.repository.id;
            notify(
              bound.message + (refreshed ? '' : ' Não foi possível atualizar a lista; use Recarregar dados.'),
              !refreshed
            );
          } else {
            showInspection(bound, repoLink);
          }
        } catch (error) {
          onboardingMessage.textContent = error.message;
          confirm.disabled = false;
        } finally {
          busy = false;
          inspectButton.disabled = false;
          linkInput.disabled = false;
        }
      });
      onboardingResult.append(confirm);
    }

    inspectForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || !inspectForm.reportValidity()) return;
      const inputLink = linkInput.value;
      busy = true;
      inspectButton.disabled = true;
      linkInput.disabled = true;
      onboardingResult.replaceChildren();
      onboardingMessage.textContent = 'Verificando acesso no GitHub…';
      try {
        showInspection(await post('/api/repositories/inspect', {link: inputLink}), inputLink);
      } catch (error) {
        onboardingMessage.textContent = error.message;
      } finally {
        busy = false;
        inspectButton.disabled = false;
        linkInput.disabled = false;
      }
    });
  }

  initApp();

  window.DashboardApp = {
    load,
    navigate,
    currentRoute,
    getRepositories: () => repositoryData,
    getRouteVersion: () => routeVersion,
    isBusy: () => busy,
  };
})();
