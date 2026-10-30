(() => {
  // Menu clicks used to land at the top of the next page, which loses the
  // reader's place in long ledgers.  Remember the position on an internal
  // navigation and restore it once, only for the page that was actually
  // requested.  Links keep working exactly as before.
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  const NAV_SCROLL_KEY = 'wendao:nav-scroll';
  const SIDEBAR_SCROLL_KEY = 'wendao:side-nav-scroll';
  const sideNav = document.querySelector('.side-nav');
  const savedSidebar = sessionStorage.getItem(SIDEBAR_SCROLL_KEY);
  if (sideNav && savedSidebar !== null) {
    sideNav.scrollTop = Number(savedSidebar) || 0;
  }

  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link) return;
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== '_self') return;
    if (link.hasAttribute('download')) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin) return;
    if (url.hash && url.pathname === location.pathname && url.search === location.search) return;
    sessionStorage.setItem(
      NAV_SCROLL_KEY,
      JSON.stringify({ path: url.pathname + url.search, y: window.scrollY, at: Date.now() }),
    );
    if (sideNav) sessionStorage.setItem(SIDEBAR_SCROLL_KEY, String(sideNav.scrollTop));
  });

  try {
    const saved = JSON.parse(sessionStorage.getItem(NAV_SCROLL_KEY) || 'null');
    sessionStorage.removeItem(NAV_SCROLL_KEY);
    if (saved && saved.path === location.pathname + location.search && Date.now() - saved.at < 15000) {
      const limit = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      const previous = document.documentElement.style.scrollBehavior;
      document.documentElement.style.scrollBehavior = 'auto';
      window.scrollTo(0, Math.min(Number(saved.y) || 0, limit));
      document.documentElement.style.scrollBehavior = previous;
    }
  } catch (error) {
    // A blocked sessionStorage must never stop the rest of the UI.
  }

  const menu = document.getElementById('menuButton');
  const scrim = document.getElementById('sidebarScrim');
  const closeMenu = () => document.body.classList.remove('sidebar-open');
  menu?.addEventListener('click', () => document.body.classList.toggle('sidebar-open'));
  scrim?.addEventListener('click', closeMenu);
  document.querySelectorAll('[data-open-menu]').forEach(button => {
    button.addEventListener('click', () => document.body.classList.add('sidebar-open'));
  });
  document.querySelector('.main-shell')?.addEventListener('click', event => {
    const drawerMode = window.innerWidth <= 820 || document.body.classList.contains('landing-mode');
    if (drawerMode && document.body.classList.contains('sidebar-open') && !event.target.closest('#menuButton') && !event.target.closest('[data-open-menu]')) {
      closeMenu();
    }
  });

  const livingScene = document.querySelector('[data-living-scene]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  if (livingScene && !reducedMotion.matches && !document.body.classList.contains('motion-reduced')) {
    livingScene.addEventListener('pointermove', event => {
      const rect = livingScene.getBoundingClientRect();
      livingScene.style.setProperty(
        '--scene-x',
        ((event.clientX - rect.left) / rect.width - 0.5).toFixed(3),
      );
      livingScene.style.setProperty(
        '--scene-y',
        ((event.clientY - rect.top) / rect.height - 0.5).toFixed(3),
      );
    });
    livingScene.addEventListener('pointerleave', () => {
      livingScene.style.setProperty('--scene-x', '0');
      livingScene.style.setProperty('--scene-y', '0');
    });
  }

  const homeGrid = document.querySelector('[data-home-layout-grid]');
  if (homeGrid) {
    const toolbar = document.querySelector('[data-home-toolbar]');
    const layoutFab = document.querySelector('[data-home-layout-fab]');
    const closeToolbar = toolbar?.querySelector('[data-home-toolbar-close]');
    const editButton = toolbar?.querySelector('[data-home-edit]');
    const autoButton = toolbar?.querySelector('[data-home-autofit]');
    const form = toolbar?.querySelector('[data-home-layout-form]');
    const input = toolbar?.querySelector('[data-home-layout-input]');
    let dragged = null;
    const blocks = () => [...homeGrid.querySelectorAll(':scope > [data-home-block]')];
    const serialize = () => {
      blocks().forEach((block, index) => block.style.setProperty('--home-order', index + 1));
      if (input) input.value = JSON.stringify(blocks().map(block => ({
        key: block.dataset.homeBlock,
        span: Math.max(4, Math.min(12, Number(block.dataset.span || 12))),
      })));
    };
    const enterEdit = () => {
      toolbar.hidden = false;
      layoutFab?.setAttribute('aria-expanded', 'true');
      homeGrid.classList.toggle('is-editing');
      const editing = homeGrid.classList.contains('is-editing');
      editButton.textContent = editing ? '退出编辑' : '自定义布局';
      autoButton.hidden = !editing;
      form.hidden = !editing;
      blocks().forEach(block => { block.draggable = editing; });
    };
    layoutFab?.addEventListener('click', () => {
      const willOpen = toolbar.hidden;
      toolbar.hidden = !willOpen;
      layoutFab.setAttribute('aria-expanded', String(willOpen));
    });
    closeToolbar?.addEventListener('click', () => {
      if (homeGrid.classList.contains('is-editing')) enterEdit();
      toolbar.hidden = true;
      layoutFab?.setAttribute('aria-expanded', 'false');
    });
    editButton?.addEventListener('click', enterEdit);
    blocks().forEach(block => {
      block.addEventListener('dragstart', event => {
        if (!homeGrid.classList.contains('is-editing')) return event.preventDefault();
        dragged = block;
        block.classList.add('is-dragging');
        event.dataTransfer.effectAllowed = 'move';
      });
      block.addEventListener('dragend', () => {
        block.classList.remove('is-dragging');
        dragged = null;
        serialize();
      });
      block.addEventListener('pointerup', () => {
        if (!homeGrid.classList.contains('is-editing')) return;
        const gridWidth = Math.max(homeGrid.clientWidth, 1);
        const span = Math.max(4, Math.min(12, Math.round(block.getBoundingClientRect().width / gridWidth * 12)));
        block.dataset.span = String(span);
        block.style.setProperty('--home-span', span);
        serialize();
      });
    });
    homeGrid.addEventListener('dragover', event => {
      if (!dragged) return;
      event.preventDefault();
      const target = event.target.closest('[data-home-block]');
      if (!target || target === dragged) return;
      const rect = target.getBoundingClientRect();
      homeGrid.insertBefore(dragged, event.clientY < rect.top + rect.height / 2 ? target : target.nextSibling);
    });
    autoButton?.addEventListener('click', () => {
      const width = window.innerWidth;
      const recommended = width >= 1180 ? {gate: 12, search: 12, shortcuts: 7, workbench: 5, continuity: 12} : {gate: 12, search: 12, shortcuts: 12, workbench: 12, continuity: 12};
      const order = ['gate', 'search', 'shortcuts', 'workbench', 'continuity'];
      order.forEach(key => {
        const block = homeGrid.querySelector(`[data-home-block="${key}"]`);
        if (!block) return;
        block.dataset.span = String(recommended[key]);
        block.style.setProperty('--home-span', recommended[key]);
        homeGrid.appendChild(block);
      });
      serialize();
    });
    form?.addEventListener('submit', serialize);
  }

  const navLayoutEditor = document.querySelector('[data-nav-layout-editor]');
  if (navLayoutEditor) {
    const groupsRoot = navLayoutEditor.querySelector('.nav-layout-groups');
    const layoutInput = document.getElementById('navLayoutInput');
    const layoutStatus = document.getElementById('navLayoutStatus');
    const settingsForm = navLayoutEditor.closest('form');
    let draggedGroup = null;
    let draggedItem = null;

    const groupElements = () => [...groupsRoot.querySelectorAll(':scope > [data-nav-group]')];
    const itemElements = group => [...group.querySelectorAll(':scope > [data-nav-items] > [data-nav-item-key]')];
    const updateGroupCounts = () => {
      groupElements().forEach(group => {
        const countLabel = group.querySelector(':scope > header > span:not(.nav-order-buttons)');
        if (!countLabel) return;
        const items = itemElements(group);
        const visible = items.filter(item => item.querySelector('[data-nav-visible]')?.checked).length;
        countLabel.textContent = `${visible}/${items.length} 显示`;
      });
    };
    const serializeLayout = (dirty = true) => {
      if (!layoutInput) return;
      const layout = groupElements().map(group => ({
        key: group.dataset.navGroup,
        items: itemElements(group).map(item => ({
          key: item.dataset.navItemKey,
          visible: Boolean(item.querySelector('[data-nav-visible]')?.checked),
        })),
      }));
      layoutInput.value = JSON.stringify(layout);
      updateGroupCounts();
      if (dirty && layoutStatus) {
        layoutStatus.textContent = '布局已调整；点击下方“保存设置”后应用到侧栏。';
        layoutStatus.classList.add('is-dirty');
      }
    };
    const setDraggedState = (element, active) => {
      element?.classList.toggle('is-dragging', active);
    };
    const finishDrag = () => {
      setDraggedState(draggedGroup, false);
      setDraggedState(draggedItem, false);
      draggedGroup = null;
      draggedItem = null;
      groupElements().forEach(group => {
        group.draggable = false;
        itemElements(group).forEach(item => { item.draggable = false; });
      });
      serializeLayout();
    };
    const insertByPointer = (root, dragged, target, pointerY) => {
      if (!target || target === dragged) return;
      const rect = target.getBoundingClientRect();
      root.insertBefore(dragged, pointerY < rect.top + rect.height / 2 ? target : target.nextSibling);
    };

    groupElements().forEach(group => {
      group.draggable = false;
      const groupHandle = group.querySelector(':scope > header [data-nav-drag-group]');
      groupHandle?.addEventListener('pointerdown', () => {
        group.draggable = true;
      });
      group.addEventListener('dragstart', event => {
        if (event.target !== group || !group.draggable) return;
        draggedGroup = group;
        setDraggedState(group, true);
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', group.dataset.navGroup || 'nav-group');
      });
      group.addEventListener('dragend', finishDrag);

      itemElements(group).forEach(item => {
        item.draggable = false;
        const itemHandle = item.querySelector('[data-nav-drag-item]');
        itemHandle?.addEventListener('pointerdown', () => {
          group.draggable = false;
          item.draggable = true;
        });
        item.addEventListener('dragstart', event => {
          if (event.target !== item || !item.draggable) return;
          event.stopPropagation();
          draggedItem = item;
          setDraggedState(item, true);
          event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain', item.dataset.navItemKey || 'nav-item');
        });
        item.addEventListener('dragend', event => {
          event.stopPropagation();
          finishDrag();
        });
      });
    });

    groupsRoot?.addEventListener('dragover', event => {
      if (draggedItem) {
        const list = event.target.closest('[data-nav-items]');
        if (!list || list !== draggedItem.parentElement) return;
        event.preventDefault();
        insertByPointer(list, draggedItem, event.target.closest('[data-nav-item-key]'), event.clientY);
        return;
      }
      if (draggedGroup) {
        event.preventDefault();
        insertByPointer(groupsRoot, draggedGroup, event.target.closest('[data-nav-group]'), event.clientY);
      }
    });
    groupsRoot?.addEventListener('drop', event => {
      if (!draggedGroup && !draggedItem) return;
      event.preventDefault();
      finishDrag();
    });
    window.addEventListener('pointerup', () => {
      if (draggedGroup || draggedItem) return;
      groupElements().forEach(group => {
        group.draggable = false;
        itemElements(group).forEach(item => { item.draggable = false; });
      });
    });

    navLayoutEditor.addEventListener('click', event => {
      const moveButton = event.target.closest('[data-nav-move]');
      if (!moveButton) return;
      const action = moveButton.dataset.navMove;
      const item = moveButton.closest('[data-nav-item-key]');
      const group = moveButton.closest('[data-nav-group]');
      if (action === 'item-up' && item?.previousElementSibling) {
        item.parentElement.insertBefore(item, item.previousElementSibling);
      } else if (action === 'item-down' && item?.nextElementSibling) {
        item.parentElement.insertBefore(item.nextElementSibling, item);
      } else if (action === 'group-up' && group?.previousElementSibling) {
        groupsRoot.insertBefore(group, group.previousElementSibling);
      } else if (action === 'group-down' && group?.nextElementSibling) {
        groupsRoot.insertBefore(group.nextElementSibling, group);
      }
      serializeLayout();
    });
    navLayoutEditor.addEventListener('change', event => {
      const checkbox = event.target.closest('[data-nav-visible]');
      if (!checkbox) return;
      checkbox.closest('[data-nav-item-key]')?.classList.toggle('is-hidden', !checkbox.checked);
      serializeLayout();
    });
    navLayoutEditor.querySelector('[data-nav-reset]')?.addEventListener('click', () => {
      groupElements()
        .sort((a, b) => Number(a.dataset.defaultOrder) - Number(b.dataset.defaultOrder))
        .forEach(group => {
          groupsRoot.appendChild(group);
          itemElements(group)
            .sort((a, b) => Number(a.dataset.defaultOrder) - Number(b.dataset.defaultOrder))
            .forEach(item => {
              group.querySelector('[data-nav-items]').appendChild(item);
              const checkbox = item.querySelector('[data-nav-visible]');
              if (checkbox) checkbox.checked = true;
              item.classList.remove('is-hidden');
            });
        });
      serializeLayout();
      if (layoutStatus) layoutStatus.textContent = '已恢复默认布局；点击“保存设置”后生效。';
    });
    settingsForm?.addEventListener('submit', () => serializeLayout(false));
    serializeLayout(false);
  }

  const input = document.getElementById('fileInput');
  const zone = document.getElementById('dropzone');
  const list = document.getElementById('fileList');
  const renderFiles = () => {
    if (!input || !list) return;
    list.replaceChildren();
    [...input.files].forEach(file => {
      const span = document.createElement('span');
      span.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
      list.appendChild(span);
    });
  };
  input?.addEventListener('change', renderFiles);
  if (zone && input) {
    ['dragenter', 'dragover'].forEach(type => zone.addEventListener(type, event => {
      event.preventDefault(); zone.classList.add('dragover');
    }));
    ['dragleave', 'drop'].forEach(type => zone.addEventListener(type, event => {
      event.preventDefault(); zone.classList.remove('dragover');
    }));
    zone.addEventListener('drop', event => {
      const transfer = new DataTransfer();
      [...event.dataTransfer.files].forEach(file => transfer.items.add(file));
      input.files = transfer.files;
      renderFiles();
    });
  }

  const folderInput = document.getElementById('foundationFolderInput');
  const pathsInput = document.getElementById('foundationRelativePaths');
  const preview = document.getElementById('foundationFolderPreview');
  if (folderInput && pathsInput && preview) {
    folderInput.addEventListener('change', () => {
      const files = [...folderInput.files];
      const paths = files.map(file => file.webkitRelativePath || file.name);
      pathsInput.value = JSON.stringify(paths);
      const total = files.reduce((sum, file) => sum + file.size, 0);
      if (!files.length) {
        preview.replaceChildren();
        return;
      }
      const title = document.createElement('strong');
      title.textContent = paths[0]?.split('/')[0] || '文件夹';
      const summary = document.createElement('span');
      summary.textContent = `${files.length} 个文件 · ${(total / 1024 / 1024).toFixed(2)} MB`;
      const detail = document.createElement('small');
      paths.slice(0, 4).forEach((path, index) => {
        if (index) detail.appendChild(document.createElement('br'));
        detail.appendChild(document.createTextNode(path));
      });
      if (paths.length > 4) {
        detail.appendChild(document.createElement('br'));
        detail.appendChild(document.createTextNode('…'));
      }
      preview.replaceChildren(title, document.createElement('br'), summary, document.createElement('br'), detail);
    });
  }

  const copyButton = document.getElementById('copyPromptButton');
  const promptText = document.getElementById('promptText');
  const copyFeedback = document.getElementById('copyFeedback');
  copyButton?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(promptText?.value || '');
      if (copyFeedback) copyFeedback.textContent = '已复制，直接粘贴到 ChatGPT 即可。';
    } catch {
      promptText?.select();
      document.execCommand('copy');
      if (copyFeedback) copyFeedback.textContent = '已复制。';
    }
  });

  const planImportForm = document.getElementById('planImportForm');
  const planImportButton = document.getElementById('planImportButton');
  planImportForm?.addEventListener('submit', () => {
    if (!planImportButton) return;
    planImportButton.disabled = true;
    planImportButton.textContent = '正在解析并启用…';
  });

  const retreatTime = document.getElementById('retreatTime');
  if (retreatTime) {
    const storageKey = 'research-cultivation-retreat-v1';
    const minutesInput = document.getElementById('retreatMinutes');
    const focusInput = document.getElementById('retreatFocus');
    const focusLabel = document.getElementById('retreatFocusLabel');
    const stateLabel = document.getElementById('retreatState');
    const progress = document.getElementById('retreatProgress');
    const startButton = document.getElementById('retreatStart');
    const pauseButton = document.getElementById('retreatPause');
    const resetButton = document.getElementById('retreatReset');
    const finishLinks = document.getElementById('retreatFinishLinks');
    const presets = [...document.querySelectorAll('[data-minutes]')];
    const circumference = 2 * Math.PI * 108;
    let ticker = null;
    let timer = {
      durationMs: 25 * 60 * 1000,
      remainingMs: 25 * 60 * 1000,
      endAt: 0,
      running: false,
      paused: false,
      completed: false,
      focus: '',
    };

    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (saved && typeof saved === 'object') timer = {...timer, ...saved};
    } catch {
      // The timer still works in memory when browser storage is unavailable.
    }
    const retreatParams = new URLSearchParams(window.location.search);
    if (!timer.running && !timer.paused && !timer.completed) {
      const requestedMinutes = Number.parseInt(retreatParams.get('minutes') || '', 10);
      const requestedFocus = (retreatParams.get('focus') || '').trim().slice(0, 80);
      if (Number.isFinite(requestedMinutes) && requestedMinutes > 0) {
        timer.durationMs = Math.max(1, Math.min(480, requestedMinutes)) * 60 * 1000;
        timer.remainingMs = timer.durationMs;
      }
      if (requestedFocus) timer.focus = requestedFocus;
    }

    const persist = () => {
      try { localStorage.setItem(storageKey, JSON.stringify(timer)); } catch {}
    };
    const clampMinutes = value => Math.max(1, Math.min(480, Number.parseInt(value, 10) || 25));
    const remainingNow = () => timer.running ? Math.max(0, timer.endAt - Date.now()) : Math.max(0, timer.remainingMs);
    const formatTime = milliseconds => {
      const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
      const hours = Math.floor(totalSeconds / 3600);
      const minutes = Math.floor((totalSeconds % 3600) / 60);
      const seconds = totalSeconds % 60;
      return hours
        ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
        : `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
    };
    const stopTicker = () => {
      if (ticker) window.clearInterval(ticker);
      ticker = null;
    };
    const markPreset = () => {
      const minutes = clampMinutes(minutesInput.value);
      presets.forEach(button => button.classList.toggle('active', Number(button.dataset.minutes) === minutes));
    };
    const complete = () => {
      stopTicker();
      timer.running = false;
      timer.paused = false;
      timer.completed = true;
      timer.remainingMs = 0;
      timer.endAt = 0;
      persist();
    };
    const render = () => {
      const remaining = remainingNow();
      if (timer.running && remaining <= 0) complete();
      const current = remainingNow();
      retreatTime.textContent = formatTime(current);
      focusLabel.textContent = timer.focus || '请选择闭关所修';
      stateLabel.textContent = timer.completed ? '功行圆满' : timer.running ? '闭关中' : timer.paused ? '暂歇' : '尚未入定';
      startButton.textContent = timer.paused ? '继续闭关' : timer.running ? '闭关中' : '开始闭关';
      startButton.disabled = timer.running;
      pauseButton.disabled = !timer.running;
      finishLinks.hidden = !timer.completed;
      const ratio = timer.durationMs > 0 ? Math.min(1, current / timer.durationMs) : 0;
      progress.style.strokeDashoffset = String(circumference * (1 - ratio));
      document.title = timer.running ? `${formatTime(current)} · 入定闭关` : '入定闭关';
      persist();
    };
    const startTicker = () => {
      stopTicker();
      ticker = window.setInterval(render, 500);
    };

    focusInput.value = timer.focus || '';
    minutesInput.value = String(Math.max(1, Math.round(timer.durationMs / 60000)));
    if (timer.running && timer.endAt <= Date.now()) complete();
    if (timer.running) startTicker();
    markPreset();
    render();

    presets.forEach(button => button.addEventListener('click', () => {
      if (timer.running) return;
      minutesInput.value = button.dataset.minutes;
      timer.durationMs = clampMinutes(button.dataset.minutes) * 60 * 1000;
      timer.remainingMs = timer.durationMs;
      timer.paused = false;
      timer.completed = false;
      markPreset();
      render();
    }));
    minutesInput.addEventListener('change', () => {
      if (timer.running) return;
      const minutes = clampMinutes(minutesInput.value);
      minutesInput.value = String(minutes);
      timer.durationMs = minutes * 60 * 1000;
      timer.remainingMs = timer.durationMs;
      timer.paused = false;
      timer.completed = false;
      markPreset();
      render();
    });
    focusInput.addEventListener('input', () => {
      timer.focus = focusInput.value.trim().slice(0, 80);
      render();
    });
    startButton.addEventListener('click', () => {
      timer.focus = focusInput.value.trim().slice(0, 80);
      if (!timer.paused) {
        const minutes = clampMinutes(minutesInput.value);
        timer.durationMs = minutes * 60 * 1000;
        timer.remainingMs = timer.durationMs;
      }
      timer.endAt = Date.now() + timer.remainingMs;
      timer.running = true;
      timer.paused = false;
      timer.completed = false;
      startTicker();
      render();
    });
    pauseButton.addEventListener('click', () => {
      timer.remainingMs = remainingNow();
      timer.running = false;
      timer.paused = true;
      timer.endAt = 0;
      stopTicker();
      render();
    });
    resetButton.addEventListener('click', () => {
      stopTicker();
      const minutes = clampMinutes(minutesInput.value);
      timer = {
        durationMs: minutes * 60 * 1000,
        remainingMs: minutes * 60 * 1000,
        endAt: 0,
        running: false,
        paused: false,
        completed: false,
        focus: focusInput.value.trim().slice(0, 80),
      };
      render();
    });
    document.addEventListener('visibilitychange', render);
  }

  setTimeout(() => document.querySelectorAll('.flash').forEach(el => el.remove()), 4200);
})();
