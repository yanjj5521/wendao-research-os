(() => {
  // Keep the reading position across same-origin navigation and form redirects.
  // The personal app uses the same session key, so entering or leaving /team/
  // also restores the position that the other shell saved.
  const NAV_SCROLL_KEY = 'wendao:nav-scroll';
  const MAX_AGE_MS = 15000;
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';

  let storageAvailable = true;
  const readStorage = key => {
    try {
      return sessionStorage.getItem(key);
    } catch (_) {
      storageAvailable = false;
      return null;
    }
  };
  const removeStorage = key => {
    try {
      sessionStorage.removeItem(key);
    } catch (_) {
      storageAvailable = false;
    }
  };

  const rememberScroll = path => {
    if (!storageAvailable) return;
    const payload = JSON.stringify({ path, y: window.scrollY, at: Date.now() });
    try {
      sessionStorage.setItem(NAV_SCROLL_KEY, payload);
      window.setTimeout(() => {
        try {
          if (sessionStorage.getItem(NAV_SCROLL_KEY) === payload) {
            sessionStorage.removeItem(NAV_SCROLL_KEY);
          }
        } catch (_) {
          // Storage can be disabled independently of navigation.
        }
      }, MAX_AGE_MS);
    } catch (_) {
      storageAvailable = false;
    }
  };

  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== '_self') return;
    if (link.hasAttribute('download')) return;
    let url;
    try {
      url = new URL(link.href, location.href);
    } catch (_) {
      return;
    }
    if (url.origin !== location.origin) return;
    if (url.hash && url.pathname === location.pathname && url.search === location.search) return;
    rememberScroll(url.pathname + url.search);
  });

  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (event.defaultPrevented || form.dataset.noScrollRestore === '1') return;
    if (form.target && form.target !== '_self') return;
    rememberScroll(null);
  });

  let pending = null;
  const raw = readStorage(NAV_SCROLL_KEY);
  removeStorage(NAV_SCROLL_KEY);
  if (raw) {
    try {
      pending = JSON.parse(raw);
    } catch (_) {
      pending = null;
    }
  }

  const applyPendingScroll = () => {
    if (!pending) return;
    const targetPath = location.pathname + location.search;
    if (pending.path !== null && pending.path !== targetPath) return;
    if (Date.now() - Number(pending.at || 0) >= MAX_AGE_MS) {
      pending = null;
      return;
    }
    const limit = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    const previous = document.documentElement.style.scrollBehavior;
    document.documentElement.style.scrollBehavior = 'auto';
    window.scrollTo(0, Math.max(0, Math.min(Number(pending.y) || 0, limit)));
    document.documentElement.style.scrollBehavior = previous;
  };

  applyPendingScroll();
  window.setTimeout(applyPendingScroll, 0);
  window.setTimeout(applyPendingScroll, 90);
  window.addEventListener('load', applyPendingScroll);
  window.addEventListener('pageshow', applyPendingScroll);
})();
