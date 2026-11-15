/* Portfolio front-end — vanilla JS, no build step, CSP-safe (no inline handlers/styles). */
(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const root = document.documentElement;
  const lang = root.lang || 'en';
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const qsLang = new URLSearchParams(location.search).get('lang');
  let I18N = {};
  try { I18N = JSON.parse(($('#i18n') || {}).textContent || '{}'); } catch (e) { I18N = {}; }
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /* ---------------------------------------------------------------- theme */
  const themeMeta = $('#themeColor');
  const paintThemeColor = () => { if (themeMeta) themeMeta.content = root.getAttribute('data-mdb-theme') === 'light' ? '#f4f6fc' : '#060913'; };
  paintThemeColor();
  const themeBtn = $('#themeToggle');
  if (themeBtn) themeBtn.addEventListener('click', () => {
    const next = root.getAttribute('data-mdb-theme') === 'light' ? 'dark' : 'light';
    root.classList.add('theme-anim');
    root.setAttribute('data-mdb-theme', next);
    try { localStorage.setItem('pf-theme', next); } catch (e) { /* private mode */ }
    paintThemeColor();
    setTimeout(() => root.classList.remove('theme-anim'), 600);
  });

  /* -------------------------------------------------------- dates & time */
  const rtf = 'RelativeTimeFormat' in Intl ? new Intl.RelativeTimeFormat(lang, { numeric: 'auto' }) : null;
  const UNITS = [['year', 31536e6], ['month', 2592e6], ['week', 6048e5], ['day', 864e5], ['hour', 36e5], ['minute', 6e4]];
  function rel(ms) {
    const diff = ms - Date.now();
    for (const [unit, size] of UNITS) {
      if (Math.abs(diff) >= size || unit === 'minute') return rtf.format(Math.round(diff / size), unit);
    }
    return '';
  }
  function formatTimes(scope = document) {
    $$('time[datetime]', scope).forEach((el) => {
      const ms = Date.parse(el.getAttribute('datetime'));
      if (!ms) return;
      const d = new Date(ms);
      el.title = d.toLocaleString(lang);
      if (el.classList.contains('rel') && rtf) el.textContent = rel(ms);
      else if (el.classList.contains('date')) el.textContent = d.toLocaleDateString(lang, { year: 'numeric', month: 'short', day: 'numeric' });
    });
  }
  formatTimes();

  /* ------------------------------------- CSSOM helpers (no inline styles) */
  function paintMeters(scope = document) {
    $$('[data-w]', scope).forEach((el) => {
      const w = Math.max(0, Math.min(100, Number(el.dataset.w) || 0));
      requestAnimationFrame(() => requestAnimationFrame(() => { el.style.width = w + '%'; }));
    });
  }
  function paintLabels(scope = document) {
    $$('[data-color]', scope).forEach((el) => {
      if (/^[0-9a-f]{6}$/i.test(el.dataset.color)) el.style.setProperty('--lc', '#' + el.dataset.color);
    });
  }
  paintLabels();

  /* -------------------------------------------- reveal on scroll + counters */
  const fmt = new Intl.NumberFormat(lang);
  const counted = new WeakSet();
  function countUp(el) {
    if (counted.has(el)) return;
    counted.add(el);
    const target = Number(el.dataset.count) || 0;
    if (reduce || target < 2) { el.textContent = fmt.format(target); return; }
    const start = performance.now();
    const tick = (now) => {
      const p = Math.min(1, (now - start) / 1400);
      el.textContent = fmt.format(Math.round(target * (1 - Math.pow(1 - p, 4))));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  const revealables = $$('.reveal');
  if ('IntersectionObserver' in window && !reduce) {
    const io = new IntersectionObserver((entries) => {
      let i = 0;
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        el.style.setProperty('--delay', Math.min(i++, 6) * 70 + 'ms');
        el.classList.add('in');
        $$('[data-count]', el).forEach(countUp);
        io.unobserve(el);
      });
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.06 });
    revealables.forEach((el) => io.observe(el));
  } else {
    revealables.forEach((el) => el.classList.add('in'));
    $$('[data-count]').forEach(countUp);
  }

  /* ------------------------------------ scroll: progress, nav, back-to-top */
  const nav = $('#topnav');
  const bar = $('#scrollBar');
  const toTop = $('#toTop');
  let ticking = false;
  function onScroll() {
    ticking = false;
    const y = window.scrollY;
    const p = Math.min(1, y / Math.max(1, root.scrollHeight - window.innerHeight)).toFixed(4);
    if (bar) bar.style.setProperty('--p', p);
    if (nav) nav.classList.toggle('is-scrolled', y > 24);
    if (toTop) { toTop.classList.toggle('is-visible', y > 600); toTop.style.setProperty('--p', p); }
  }
  window.addEventListener('scroll', () => { if (!ticking) { ticking = true; requestAnimationFrame(onScroll); } }, { passive: true });
  onScroll();
  if (toTop) toTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' }));
  $$('#navMenu .nav-link:not(.dropdown-toggle)').forEach((a) => a.addEventListener('click', () => {
    const menu = $('#navMenu');
    const instance = window.mdb && mdb.Collapse && mdb.Collapse.getInstance(menu);
    if (instance && menu.classList.contains('show')) instance.hide();
  }));

  /* ----------------------------- pointer effects: spotlight + hero parallax */
  if (!reduce && window.matchMedia('(hover: hover)').matches) {
    document.addEventListener('pointermove', (e) => {
      const card = e.target.closest && e.target.closest('.pcard, .contact-card');
      if (!card) return;
      const r = card.getBoundingClientRect();
      card.style.setProperty('--mx', e.clientX - r.left + 'px');
      card.style.setProperty('--my', e.clientY - r.top + 'px');
    }, { passive: true });
    const stage = $('#avatarStage');
    const hero = stage && stage.closest('.hero');
    if (hero) hero.addEventListener('pointermove', (e) => {
      const r = hero.getBoundingClientRect();
      stage.style.setProperty('--px', ((e.clientX - r.left) / r.width - 0.5).toFixed(3));
      stage.style.setProperty('--py', ((e.clientY - r.top) / r.height - 0.5).toFixed(3));
    }, { passive: true });
  }

  /* ------------------------------------------------------------ copy buttons */
  $$('[data-copy]').forEach((btn) => btn.addEventListener('click', async (e) => {
    e.preventDefault();
    e.stopPropagation();
    try { await navigator.clipboard.writeText(btn.dataset.copy); } catch (err) { return; }
    btn.dataset.tip = I18N.copied || 'Copied!';
    btn.classList.add('is-done');
    setTimeout(() => btn.classList.remove('is-done'), 1600);
  }));

  /* --------------------------------------- projects: filter / search / sort */
  const tabs = $('#catTabs');
  if (tabs) {
    const glider = $('.seg-glider', tabs);
    const buttons = $$('.seg-btn', tabs);
    const groups = $$('.group');
    const search = $('#projectSearch');
    const sortSelect = $('#projectSort');
    const none = $('#noResults');
    const state = { cat: 'all', q: '', sort: 'updated' };
    const searchText = new WeakMap(); // built from text already on the page (keeps the HTML small)
    const haystack = (card) => {
      if (!searchText.has(card)) {
        const tpl = document.getElementById('tpl-' + card.dataset.slug);
        searchText.set(card, (card.textContent + ' ' + (tpl ? tpl.content.textContent : '')).replace(/\s+/g, ' ').toLowerCase());
      }
      return searchText.get(card);
    };
    let placed = false;
    const moveGlider = () => {
      const active = buttons.find((b) => b.classList.contains('is-active'));
      if (!active || !glider || !active.offsetWidth) return;
      if (!placed) glider.style.transition = 'none'; // first placement is instant (no grow-from-zero)
      glider.style.width = active.offsetWidth + 'px';
      glider.style.height = active.offsetHeight + 'px';
      glider.style.transform = `translate(${active.offsetLeft}px, ${active.offsetTop}px)`;
      tabs.classList.add('has-glider');
      if (!placed) { void glider.offsetWidth; glider.style.transition = ''; placed = true; }
    };
    const byUpdated = (a, b) => (b.dataset.updated || '').localeCompare(a.dataset.updated || '');
    const compare = {
      updated: byUpdated,
      stars: (a, b) => Number(b.dataset.stars) - Number(a.dataset.stars) || byUpdated(a, b),
      name: (a, b) => a.dataset.name.localeCompare(b.dataset.name, lang, { sensitivity: 'base' }),
    };
    const apply = () => {
      const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      groups.forEach((group) => {
        const list = $('.cards', group);
        const cards = $$('.pcard', list);
        cards.sort((a, b) => Number(b.dataset.featured) - Number(a.dataset.featured) || compare[state.sort](a, b))
          .forEach((card) => list.appendChild(card));
        const inCategory = state.cat === 'all' || state.cat === group.dataset.group;
        let n = 0;
        cards.forEach((card) => {
          const ok = inCategory && (!terms.length || terms.every((t) => haystack(card).includes(t)));
          card.hidden = !ok;
          if (ok) { n += 1; card.classList.add('in'); }
        });
        group.hidden = n === 0;
        const count = $('.group-count', group);
        if (count) count.textContent = n;
        shown += n;
      });
      if (none) none.hidden = shown > 0;
    };
    buttons.forEach((btn) => btn.addEventListener('click', () => {
      buttons.forEach((b) => { const on = b === btn; b.classList.toggle('is-active', on); b.setAttribute('aria-selected', String(on)); });
      state.cat = btn.dataset.filter;
      moveGlider();
      apply();
    }));
    let timer;
    if (search) search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.q = search.value.trim(); apply(); }, 120);
    });
    if (sortSelect) sortSelect.addEventListener('change', () => { state.sort = sortSelect.value; apply(); });
    window.addEventListener('resize', () => requestAnimationFrame(moveGlider), { passive: true });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveGlider);
    moveGlider();
  }

  /* ----------------------------------------------------------- MDB modals */
  const modalFor = (el) => (el && window.mdb && mdb.Modal ? mdb.Modal.getOrCreateInstance(el) : null);
  const readmeCache = new Map();
  async function fetchJSON(url) {
    const res = await fetch(url, { headers: { Accept: 'application/json' }, credentials: 'same-origin' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }
  function wireTabs(scope, onTab) {
    const tabButtons = $$('.m-tab', scope);
    tabButtons.forEach((btn) => btn.addEventListener('click', () => {
      tabButtons.forEach((b) => { const on = b === btn; b.classList.toggle('is-active', on); b.setAttribute('aria-selected', String(on)); });
      $$('.m-panel', scope).forEach((panel) => { panel.hidden = panel.dataset.panel !== btn.dataset.tab; });
      if (onTab) onTab(btn.dataset.tab);
    }));
  }
  async function loadReadme(box) {
    const name = box.dataset.readme;
    if (!name || box.dataset.state === 'done' || box.dataset.state === 'loading') return;
    box.dataset.state = 'loading';
    try {
      if (!readmeCache.has(name)) {
        const data = await fetchJSON('/api/projects/' + encodeURIComponent(name));
        readmeCache.set(name, data.readme_html || '');
      }
      const html = readmeCache.get(name);
      box.innerHTML = html || `<p class="readme-empty">${esc(I18N.readme_missing || '')}</p>`; // sanitised server-side (nh3)
      box.dataset.state = 'done';
    } catch (err) {
      box.dataset.state = '';
      box.innerHTML = `<p class="readme-empty">${esc(I18N.readme_error || 'Error')}</p>`;
    }
  }

  const repoModalEl = $('#repoModal');
  function openRepo(name, updateHash = true, tab = 'overview') {
    const tpl = document.getElementById('tpl-' + name.replace(/[^A-Za-z0-9_-]/g, '-'));
    if (!tpl) return;
    const modal = modalFor(repoModalEl);
    if (!modal) { if (/^https:\/\//.test(tpl.dataset.url)) window.open(tpl.dataset.url, '_blank', 'noopener'); return; }
    const body = $('#repoModalBody');
    body.replaceChildren(tpl.content.cloneNode(true));
    $('#repoModalTitle').textContent = tpl.dataset.title;
    const repoLink = $('#repoModalRepo');
    repoLink.href = tpl.dataset.url;
    $('span', repoLink).textContent = tpl.dataset.name;
    $('#repoModalGithub').href = tpl.dataset.url;
    const home = $('#repoModalHome');
    home.hidden = !tpl.dataset.home;
    if (tpl.dataset.home) home.href = tpl.dataset.home;
    $('#repoModalIssues').href = '/issues?state=all&repo=' + encodeURIComponent(tpl.dataset.name) + (qsLang ? '&lang=' + encodeURIComponent(qsLang) : '');
    const box = $('[data-readme]', body);
    const hashFor = (t) => '#project=' + encodeURIComponent(tpl.dataset.name) + (t === 'readme' ? ':readme' : '');
    wireTabs(body, (t) => {
      if (t === 'readme' && box) loadReadme(box);
      history.replaceState(null, '', hashFor(t)); // shareable deep link, e.g. #project=AuraMind:readme
    });
    formatTimes(body);
    paintMeters(body);
    paintLabels(body);
    body.scrollTop = 0;
    if (updateHash) history.replaceState(null, '', hashFor('overview'));
    if (tab === 'readme') { const readmeTab = $('.m-tab[data-tab="readme"]', body); if (readmeTab) readmeTab.click(); }
    modal.show();
  }
  document.addEventListener('click', (e) => {
    const opener = e.target.closest && e.target.closest('[data-open]');
    if (opener) { e.preventDefault(); openRepo(opener.dataset.open); }
  });
  if (repoModalEl) {
    const clearHash = () => { if (location.hash.startsWith('#project=')) history.replaceState(null, '', location.pathname + location.search); };
    repoModalEl.addEventListener('hidden.mdb.modal', clearHash);
    repoModalEl.addEventListener('hidden.bs.modal', clearHash);
    const fromHash = () => {
      const m = location.hash.match(/^#project=([^:]+)(?::(readme))?$/);
      if (m) openRepo(decodeURIComponent(m[1]), false, m[2] || 'overview');
    };
    window.addEventListener('hashchange', fromHash);
    fromHash();
  }

  /* --------------------------------- navbar AI badge (gateway / failsafe) */
  const aiBadge = $('#aiStatus');
  if (aiBadge) {
    const label = $('.ai-label', aiBadge);
    const paint = (state, text, detail) => {
      aiBadge.dataset.state = state;
      label.textContent = text;
      aiBadge.title = detail ? `${text} · ${detail}` : text;
    };
    const refreshAI = async () => {
      if (document.hidden) return;
      try {
        const s = await fetchJSON('/api/ai-status');
        if (s.online) {
          const tier = s.tier === 'failsafe' ? ` (${I18N.ai_failsafe || 'failsafe'})` : '';
          const ms = s.latency_ms != null ? ` · ${s.latency_ms} ms` : '';
          paint('online', I18N.ai_online || 'AI online', `${s.model}${tier}${ms}`);
        } else {
          paint('offline', I18N.ai_offline || 'AI offline');
        }
      } catch (err) {
        paint('offline', I18N.ai_offline || 'AI offline');
      }
    };
    refreshAI();
    setInterval(refreshAI, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshAI(); });
  }

  /* profile README (GitHub stats live there) */
  const profileBtn = $('#openProfileReadme');
  if (profileBtn) profileBtn.addEventListener('click', async () => {
    const modal = modalFor($('#readmeModal'));
    if (!modal) return;
    modal.show();
    const box = $('#profileReadme');
    if (box.dataset.state === 'done') return;
    try {
      const data = await fetchJSON('/api/profile-readme');
      box.innerHTML = data.html || `<p class="readme-empty">${esc(I18N.readme_missing || '')}</p>`; // sanitised server-side
      box.dataset.state = 'done';
    } catch (err) {
      box.innerHTML = `<p class="readme-empty">${esc(I18N.readme_error || 'Error')}</p>`;
    }
  });

  /* issues page */
  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-issue]');
    if (!btn) return;
    const tpl = document.getElementById('itpl-' + btn.dataset.issue);
    if (!tpl) return;
    const modal = modalFor($('#issueModal'));
    if (!modal) { if (/^https:\/\//.test(tpl.dataset.url)) window.open(tpl.dataset.url, '_blank', 'noopener'); return; }
    $('#issueModalTitle').textContent = tpl.dataset.title;
    $('#issueModalRef').textContent = tpl.dataset.ref;
    $('#issueModalLink').href = tpl.dataset.url;
    const body = $('#issueModalBody');
    body.replaceChildren(tpl.content.cloneNode(true));
    formatTimes(body);
    paintLabels(body);
    modal.show();
  });
  $$('select[data-autosubmit]').forEach((sel) => sel.addEventListener('change', () => sel.form && sel.form.submit()));
})();
