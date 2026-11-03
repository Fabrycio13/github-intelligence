'use strict';
(function () {
  function toggle() {
    const shell = document.querySelector('#app-shell');
    const sidebarToggle = document.querySelector('#sidebar-toggle');
    if (!shell || !sidebarToggle) return;
    const collapsed = shell.dataset.sidebarCollapsed !== 'true';
    shell.dataset.sidebarCollapsed = String(collapsed);
    const label = collapsed ? 'Expandir menu lateral' : 'Recolher menu lateral';
    sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
    sidebarToggle.setAttribute('aria-label', label);
    sidebarToggle.setAttribute('title', label);
  }

  function init() {
    const sidebarToggle = document.querySelector('#sidebar-toggle');
    if (sidebarToggle && !sidebarToggle._sidebarBound) {
      sidebarToggle._sidebarBound = true;
      sidebarToggle.addEventListener('click', toggle);
    }
  }

  init();

  window.DashboardSidebar = {
    toggle,
    init,
  };
})();
