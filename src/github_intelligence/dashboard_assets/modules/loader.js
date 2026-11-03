'use strict';
(function () {
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

  document.querySelector('#continue-loading')?.addEventListener('click', showApp);

  window.DashboardLoader = {
    initLoading,
    showApp,
  };
})();
