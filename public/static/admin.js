/* Admin dashboard — talks to /admin/api/* with a CSRF header. Vanilla JS. */
(() => {
  'use strict';
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const csrf = ($('meta[name="csrf-token"]') || {}).content || '';
  const anyUse = $('use');
  const SPRITE = (anyUse ? anyUse.getAttribute('href') : '/static/icons.svg').split('#')[0];
  let state = {};
  try { state = JSON.parse($('#adminState').textContent); } catch (e) { state = { repos: [] }; }

  /* ---------------------------------------------------------- utilities */
  function h(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([key, value]) => {
      if (value === false || value == null) return;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (value === true) node.setAttribute(key, '');
      else node.setAttribute(key, value);
    });
    children.flat().forEach((child) => { if (child != null) node.append(child); });
    return node;
  }
  function icon(name) {
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'ic');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS(NS, 'use');
    use.setAttribute('href', SPRITE + '#i-' + name);
    svg.appendChild(use);
    return svg;
  }
  const stack = h('div', { class: 'toast-stack', 'aria-live': 'polite' });
  document.body.appendChild(stack);
  function toast(message, kind = 'ok') {
    const item = h('div', { class: 'toast-item ' + kind, text: message });
    stack.appendChild(item);
    setTimeout(() => item.remove(), 4500);
  }
  async function api(path, { method = 'GET', body } = {}) {
    const init = { method, credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (method !== 'GET') init.headers['X-CSRF-Token'] = csrf;
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (res.status === 401) { location.href = '/admin/login'; throw new Error('Session expired'); }
    if (!res.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = detail.map((d) => (d.loc ? d.loc[d.loc.length - 1] + ': ' : '') + d.msg).join('; ');
      throw new Error(detail || 'HTTP ' + res.status);
    }
    return data;
  }

  /* ------------------------------------------------------ AI providers */
  async function checkAI() {
    $$('#providers li').forEach((li) => { li.className = ''; $('.p-state', li).textContent = 'checking…'; });
    try {
      const data = await api('/admin/api/ai-status');
      data.providers.forEach((p) => {
        const li = $(`#providers li[data-key="${CSS.escape(p.key)}"]`);
        if (!li) return;
        li.className = p.online ? 'is-online' : 'is-offline';
        $('.p-state', li).textContent = p.online ? `online · ${p.latency_ms} ms` : 'offline';
        li.title = p.online ? 'Models: ' + (p.models || []).join(', ') : p.error;
      });
    } catch (e) { toast(e.message, 'err'); }
  }
  $('#checkAI').addEventListener('click', checkAI);

  /* ------------------------------------------------------- repo table */
  const rows = $('#repoRows');
  const LABEL = { social: 'Social impact', industry: 'Industry & startup' };
  const SOURCE = { ai: 'AI', manual: 'Manual', pending: 'Pending' };

  function toggle(repo, field) {
    const input = h('input', { type: 'checkbox', checked: !!repo[field], 'aria-label': `${field} — ${repo.name}` });
    input.addEventListener('change', async () => {
      try {
        const data = await api('/admin/api/visibility', { method: 'POST', body: { name: repo.name, [field]: input.checked } });
        updateRow(data.repo);
        toast(`${repo.name}: ${field} ${input.checked ? 'on' : 'off'}`);
      } catch (e) { input.checked = !input.checked; toast(e.message, 'err'); }
    });
    return h('label', { class: 'switch' }, input, h('span'));
  }

  function rowFor(repo) {
    const flags = [];
    if (repo.fork) flags.push('fork');
    if (repo.archived) flags.push('archived');
    if (repo.gone) flags.push('deleted on GitHub');
    if (!repo.has_readme) flags.push('no README');
    if (repo.readme_changed) flags.push('README changed since AI run');
    const details = h('details', {}, h('summary', { text: repo.status === 'pending' ? 'Preview (not AI yet)' : 'AI summary' }),
      h('p', { text: repo.summary || '—' }),
      repo.reason ? h('p', { text: 'Why: ' + repo.reason }) : null,
      repo.key_points.length ? h('p', { text: '• ' + repo.key_points.join('  • ') }) : null);
    const select = h('select', { class: 'mini-select', 'aria-label': 'Group for ' + repo.name },
      h('option', { value: 'social', text: LABEL.social, selected: repo.category === 'social' }),
      h('option', { value: 'industry', text: LABEL.industry, selected: repo.category === 'industry' }));
    select.addEventListener('change', async () => {
      try {
        const data = await api('/admin/api/override', { method: 'POST', body: { name: repo.name, category: select.value, locked: true } });
        updateRow(data.repo);
        toast(`${repo.name} → ${LABEL[select.value]} (locked)`);
      } catch (e) { select.value = repo.category; toast(e.message, 'err'); }
    });
    const confidence = repo.confidence != null && repo.status !== 'pending' ? ` · ${Math.round(repo.confidence * 100)}%` : '';
    const source = h('span', { class: 'badge-src ' + repo.status, title: repo.model ? `${repo.provider} · ${repo.model}` : '' },
      icon(repo.status === 'ai' ? 'magic' : repo.status === 'manual' ? 'check' : 'clock'),
      (SOURCE[repo.status] || repo.status) + confidence + (repo.locked ? ' · locked' : ''));
    const run = h('button', { class: 'icon-btn', type: 'button', title: 'Run AI on this repo', 'aria-label': 'Run AI on ' + repo.name, disabled: repo.gone }, icon('magic'));
    run.addEventListener('click', () => runOne(repo.name, run));
    const reset = h('button', { class: 'icon-btn', type: 'button', title: 'Forget the AI result (counts as new again)', 'aria-label': 'Reset ' + repo.name, disabled: repo.status === 'pending' }, icon('undo'));
    reset.addEventListener('click', () => resetOne(repo.name));
    const gh = /^https:\/\//.test(repo.html_url)
      ? h('a', { class: 'icon-btn', href: repo.html_url, target: '_blank', rel: 'noopener', title: 'Open on GitHub', 'aria-label': 'Open ' + repo.name + ' on GitHub' }, icon('external-link'))
      : null;
    return h('tr', { 'data-name': repo.name, class: repo.gone ? 'is-gone' : '' },
      h('td', { class: 'repo-cell' }, h('b', { text: repo.title }), h('small', { text: repo.name + (flags.length ? ' · ' + flags.join(' · ') : '') }), details),
      h('td', {}, select),
      h('td', {}, source),
      h('td', {}, toggle(repo, 'featured')),
      h('td', {}, toggle(repo, 'hidden')),
      h('td', {}, h('div', { class: 'row-actions' }, run, reset, gh)));
  }

  function renderCounts() {
    const live = state.repos.filter((r) => !r.gone);
    const n = (s) => live.filter((r) => r.status === s).length;
    const box = $('#counts');
    box.replaceChildren(
      h('span', { text: `${live.length} repos` }),
      h('span', { text: `${n('ai')} AI` }),
      h('span', { text: `${n('manual')} manual` }),
      h('span', { text: `${n('pending')} pending` }),
      h('span', { text: `${live.filter((r) => r.category === 'social').length} social · ${live.filter((r) => r.category === 'industry').length} industry` }));
  }
  function renderRows() {
    rows.replaceChildren(...state.repos.map(rowFor));
    if (!state.repos.length) rows.append(h('tr', {}, h('td', { colspan: '6', text: 'No repositories yet — click “Sync now”.' })));
    renderCounts();
  }
  function renderWarnings() {
    $('#warnings').replaceChildren(...(state.warnings || []).map((w) => h('div', { class: 'admin-alert' }, icon('warning'), h('span', { text: w }))));
  }
  function updateRow(repo) {
    if (!repo) return;
    const index = state.repos.findIndex((r) => r.name === repo.name);
    if (index >= 0) state.repos[index] = repo; else state.repos.push(repo);
    const old = rows.querySelector(`tr[data-name="${CSS.escape(repo.name)}"]`);
    const fresh = rowFor(repo);
    if (old) old.replaceWith(fresh); else rows.append(fresh);
    renderCounts();
  }

  /* --------------------------------------------- the 3 AI/sync actions */
  const progressWrap = $('#progressWrap');
  const fill = $('#progressFill');
  const progressText = $('#progressText');
  const log = $('#log');
  let busy = false;
  function logLine(message, kind = '') {
    log.append(h('li', { class: kind, text: `[${new Date().toLocaleTimeString()}] ${message}` }));
    log.scrollTop = log.scrollHeight;
  }
  function progress(done, total, label) {
    progressWrap.hidden = false;
    const pct = total ? Math.round((done / total) * 100) : 100;
    fill.style.width = pct + '%';
    progressText.textContent = label || `${done} / ${total} repositories (${pct}%)`;
  }
  async function sync() {
    logLine('Syncing repo details, READMEs, issues and activity from GitHub…', 'info');
    const data = await api('/admin/api/sync', { method: 'POST' });
    const r = data.report;
    logLine(`Synced ${r.repos} repos · ${r.readmes_refreshed} README(s) refreshed`
      + (r.added.length ? ` · new: ${r.added.join(', ')}` : '') + (r.gone.length ? ` · gone: ${r.gone.join(', ')}` : ''), 'ok');
    (r.errors || []).forEach((e) => logLine(e, 'err'));
    state = data.state;
    renderRows();
    renderWarnings();
  }
  async function categorize(mode) {
    await sync(); // so brand-new repos are known before planning
    const includeLocked = $('#includeLocked').checked;
    const plan = await api(`/admin/api/plan?mode=${mode}&include_locked=${includeLocked}`);
    const names = plan.repos;
    if (!names.length) {
      logLine(mode === 'new' ? 'No new repositories — everything is already categorised. ✓' : 'Nothing to re-verify.', 'ok');
      progress(1, 1, 'Done ✓');
      return;
    }
    logLine(`${mode === 'new' ? 'Categorising NEW' : 'Re-verifying ALL'}: ${names.length} repo(s). The AI reads each README…`, 'info');
    const queue = names.slice();
    let done = 0;
    let failed = 0;
    let aiDown = false;
    progress(0, names.length);
    const worker = async () => {
      while (queue.length && !aiDown) {
        const name = queue.shift();
        const row = rows.querySelector(`tr[data-name="${CSS.escape(name)}"]`);
        if (row) row.classList.add('is-working');
        logLine(`Reading ${name}…`);
        try {
          const data = await api('/admin/api/categorize', { method: 'POST', body: { name } });
          const a = data.ai;
          logLine(`${name} → ${LABEL[a.category]} (${Math.round(a.confidence * 100)}% · ${a.provider} · ${a.seconds}s)`, 'ok');
          updateRow(data.repo);
        } catch (e) {
          failed += 1;
          logLine(`${name}: ${e.message}`, 'err');
          if (/AI unavailable/i.test(e.message)) aiDown = true;
          if (row) row.classList.remove('is-working');
        } finally {
          done += 1;
          progress(done, names.length);
        }
      }
    };
    await Promise.all([worker(), worker()]);
    if (aiDown) logLine('Stopped: no AI provider is reachable. Start the gateway or the Docker Gemma 3 12B failsafe, then try again.', 'err');
    logLine(`Finished — ${done - failed} categorised, ${failed} failed.`, failed ? 'err' : 'ok');
    toast(failed ? `Finished with ${failed} error(s)` : 'AI categorisation finished', failed ? 'err' : 'ok');
  }
  $$('[data-action]').forEach((btn) => btn.addEventListener('click', async () => {
    if (busy) return;
    const action = btn.dataset.action;
    if (action === 'all' && !window.confirm('Re-verify ALL repositories? The AI re-reads every README and may change existing groups.')) return;
    busy = true;
    $$('[data-action]').forEach((b) => { b.disabled = true; });
    btn.classList.add('is-busy');
    try {
      if (action === 'sync') { progress(0, 1, 'Syncing…'); await sync(); progress(1, 1, 'Synced ✓'); }
      else await categorize(action);
    } catch (e) {
      logLine(e.message, 'err');
      toast(e.message, 'err');
    } finally {
      busy = false;
      $$('[data-action]').forEach((b) => { b.disabled = false; });
      btn.classList.remove('is-busy');
    }
  }));

  async function runOne(name, btn) {
    btn.classList.add('is-busy');
    btn.disabled = true;
    try {
      const data = await api('/admin/api/categorize', { method: 'POST', body: { name } });
      updateRow(data.repo);
      toast(`${name} → ${LABEL[data.ai.category]}`);
    } catch (e) { toast(e.message, 'err'); btn.classList.remove('is-busy'); btn.disabled = false; }
  }
  async function resetOne(name) {
    if (!window.confirm(`Forget the AI result for ${name}? It will be treated as a NEW repository.`)) return;
    try { const data = await api('/admin/api/reset', { method: 'POST', body: { name } }); updateRow(data.repo); toast(`${name} reset`); }
    catch (e) { toast(e.message, 'err'); }
  }
  $('#includeForks').addEventListener('change', async (e) => {
    try { state = await api('/admin/api/settings', { method: 'POST', body: { include_forks: e.target.checked } }); renderRows(); toast('Saved'); }
    catch (err) { e.target.checked = !e.target.checked; toast(err.message, 'err'); }
  });

  /* --------------------------------------------------------- profile */
  const form = $('#profileForm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {};
    new FormData(form).forEach((value, key) => { body[key] = typeof value === 'string' ? value.trim() : value; });
    ['skills', 'interests'].forEach((k) => { body[k] = (body[k] || '').split(',').map((s) => s.trim()).filter(Boolean); });
    ['show_age', 'show_stats', 'show_activity'].forEach((k) => { body[k] = form.elements[k].checked; });
    const msg = $('#profileMsg');
    msg.className = 'form-msg';
    msg.textContent = 'Saving…';
    try {
      await api('/admin/api/profile', { method: 'POST', body });
      msg.className = 'form-msg ok';
      msg.textContent = 'Saved ✓';
      toast('Profile saved');
    } catch (err) {
      msg.className = 'form-msg err';
      msg.textContent = err.message;
    }
  });

  $('#logoutBtn').addEventListener('click', async () => {
    try { await api('/admin/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
    location.href = '/';
  });

  renderRows();
  checkAI();
})();
