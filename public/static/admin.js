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
  // Group labels: built-in + your own categories (the AI never picks "other" or a custom one).
  let LABEL = {};
  function refreshLabels() {
    LABEL = {};
    ((state.categories && state.categories.all) || [{ key: 'social', label: 'Social impact' }, { key: 'industry', label: 'Industry & startup' }, { key: 'other', label: 'Other' }])
      .forEach((c) => { LABEL[c.key] = c.label; });
  }
  refreshLabels();
  const SOURCE = { ai: 'AI', manual: 'Manual', pending: 'Pending' };

  function toggle(repo, field) {
    const input = h('input', { type: 'checkbox', checked: !!repo[field], 'aria-label': `${field} — ${repo.name}` });
    input.addEventListener('change', async () => {
      try {
        const data = await api('/admin/api/visibility', { method: 'POST', body: { name: repo.name, [field]: input.checked } });
        updateRow(data.repo);
        refreshResearch();
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
      Object.keys(LABEL).map((key) => h('option', { value: key, text: LABEL[key], selected: repo.category === key })));
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
      h('span', { text: Object.keys(LABEL).map((key) => `${live.filter((r) => r.category === key).length} ${key}`).join(' · ') }));
  }
  function renderRows() {
    refreshLabels();
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
    renderResearch(true);
    renderTranslations(); renderOccasions(); renderCategories(); renderSecurity();
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
    try { state = await api('/admin/api/settings', { method: 'POST', body: { include_forks: e.target.checked } }); renderRows(); renderResearch(true); toast('Saved'); }
    catch (err) { e.target.checked = !e.target.checked; toast(err.message, 'err'); }
  });

  /* ------------------------------------------------ research-grade section */
  const R = {
    toggle: $('#researchEnabled'), label: $('#researchState'), list: $('#researchList'),
    form: $('#researchForm'), title: $('#researchFormTitle'), repo: $('#r-repo'), rows: $('#r-validators'),
    add: $('#r-addValidator'), cancel: $('#researchCancel'), msg: $('#researchMsg'), editing: '',
  };
  const R_STATUS = {
    shown: ['Shown at the top of the site', 'ok'],
    ready: ['Ready · appears when the switch is ON', 'muted'],
    hidden: ['Not shown · this repo is hidden on the site', 'warn'],
    not_public: ['Not shown · this repo is no longer public', 'warn'],
    fork: ['Not shown · forks are switched off', 'warn'],
  };
  const rs = () => state.research || { enabled: false, entries: [], repos: [], kinds: [], max_validators: 12 };
  const kindLabel = (key) => (rs().kinds.find((k) => k.key === key) || { label: key }).label;

  function validatorRow(v = {}) {
    const kind = h('select', { class: 'field', name: 'kind', 'aria-label': 'Validator type' },
      rs().kinds.map((k) => h('option', { value: k.key, text: k.label, selected: (v.kind || 'professor') === k.key })));
    const name = h('input', { class: 'field', name: 'name', maxlength: '120', placeholder: 'Name (person, team or body)', 'aria-label': 'Validator name' });
    const org = h('input', { class: 'field', name: 'organisation', maxlength: '160', placeholder: 'Organisation (optional)', 'aria-label': 'Organisation' });
    const link = h('input', { class: 'field', name: 'link', type: 'url', maxlength: '300', placeholder: 'Proof link https://… (optional)', 'aria-label': 'Proof link' });
    name.value = v.name || '';
    org.value = v.organisation || '';
    link.value = v.link || '';
    const remove = h('button', { class: 'icon-btn', type: 'button', title: 'Remove this validator', 'aria-label': 'Remove this validator' }, icon('times'));
    const row = h('div', { class: 'vrow' }, kind, name, org, link, remove);
    remove.addEventListener('click', () => { row.remove(); syncValidatorRows(); });
    return row;
  }
  function syncValidatorRows() {
    if (!R.rows.children.length) R.rows.append(validatorRow());
    R.add.disabled = R.rows.children.length >= (rs().max_validators || 12);
  }
  function fillRepoSelect(selected) {
    const keep = selected !== undefined ? selected : R.repo.value;
    const options = [h('option', { value: '', text: '— choose a public repository —' })];
    rs().repos.forEach((r) => {
      const taken = r.taken && r.name !== keep;
      options.push(h('option', { value: r.name, text: `${r.title} (${r.name})${taken ? ' · already has a card' : ''}`, disabled: taken, selected: r.name === keep }));
    });
    if (keep && !rs().repos.some((r) => r.name === keep)) {
      options.push(h('option', { value: keep, text: `${keep} · not shown on the site`, selected: true }));
    }
    R.repo.replaceChildren(...options);
  }
  function resetResearchForm() {
    R.editing = '';
    R.form.reset();
    R.title.textContent = 'Add a research project';
    R.cancel.hidden = true;
    R.rows.replaceChildren(validatorRow());
    fillRepoSelect('');
    syncValidatorRows();
  }
  function editResearch(entry) {
    R.editing = entry.id;
    R.title.textContent = `Edit research card · ${entry.title}`;
    R.cancel.hidden = false;
    fillRepoSelect(entry.repo);
    const f = R.form.elements;
    f.topic.value = entry.topic || '';
    f.usage.value = entry.usage || '';
    f.where.value = entry.where || '';
    f.when.value = entry.when || '';
    R.rows.replaceChildren(...(entry.validators.length ? entry.validators : [{}]).map((v) => validatorRow(v)));
    syncValidatorRows();
    R.msg.className = 'form-msg';
    R.msg.textContent = '';
    R.form.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  async function deleteResearch(entry) {
    if (!window.confirm(`Delete the research card for ${entry.title}? This can't be undone.`)) return;
    try {
      const data = await api('/admin/api/research/delete', { method: 'POST', body: { id: entry.id } });
      state.research = data.research;
      if (R.editing === entry.id) resetResearchForm();
      renderResearch(true);
      toast('Research card deleted');
    } catch (e) { toast(e.message, 'err'); }
  }
  function renderResearch(refreshSelect = false) {
    const r = rs();
    R.toggle.checked = !!r.enabled;
    R.label.textContent = r.enabled ? 'ON' : 'OFF';
    R.label.className = r.enabled ? 'is-on' : 'is-off';
    const items = r.entries.map((entry) => {
      const [text, tone] = R_STATUS[entry.status] || [entry.status, 'muted'];
      const edit = h('button', { class: 'btn-glass btn-sm', type: 'button' }, icon('file'), h('span', { text: 'Edit' }));
      edit.addEventListener('click', () => editResearch(entry));
      const del = h('button', { class: 'btn-glass btn-sm', type: 'button' }, icon('times'), h('span', { text: 'Delete' }));
      del.addEventListener('click', () => deleteResearch(entry));
      const meta = [entry.where, entry.when].filter(Boolean).join(' · ');
      return h('article', { class: 'ritem' },
        h('div', { class: 'ritem-main' },
          h('div', { class: 'ritem-head' }, h('b', { text: entry.title }), h('small', { text: entry.repo }),
            h('span', { class: 'rstatus ' + tone, text: text })),
          entry.topic ? h('p', { class: 'ritem-topic', text: entry.topic }) : null,
          h('ul', { class: 'ritem-validators' }, entry.validators.map((v) => h('li', {},
            icon('check'), h('b', { text: v.name }),
            h('span', { text: ` · ${kindLabel(v.kind)}${v.organisation ? ' · ' + v.organisation : ''}` }),
            v.link ? h('a', { href: v.link, target: '_blank', rel: 'noopener nofollow', text: ' proof ↗' }) : null))),
          meta ? h('p', { class: 'ritem-meta', text: meta }) : null),
        h('div', { class: 'ritem-actions' }, edit, del));
    });
    if (!items.length) {
      items.push(h('p', { class: 'ritem-empty', text: 'No research cards yet, so nothing research-related is shown anywhere.' }));
    } else if (r.enabled && !r.shown) {
      items.unshift(h('div', { class: 'admin-alert' }, icon('warning'),
        h('span', { text: 'The switch is ON, but none of these cards points at a repository that is shown on the site, so nothing appears yet.' })));
    }
    R.list.replaceChildren(...items);
    if (refreshSelect && !R.editing) fillRepoSelect();
  }
  async function refreshResearch() {
    try { const fresh = await api('/admin/api/state'); state.research = fresh.research; renderResearch(true); } catch (e) { /* ignore */ }
  }
  R.add.addEventListener('click', () => { R.rows.append(validatorRow()); syncValidatorRows(); });
  R.cancel.addEventListener('click', () => { resetResearchForm(); R.msg.textContent = ''; });
  R.toggle.addEventListener('change', async () => {
    const on = R.toggle.checked;
    try {
      const data = await api('/admin/api/research/toggle', { method: 'POST', body: { enabled: on } });
      state.research = data.research;
      renderResearch();
      toast(on ? 'Research section is ON' : 'Research section is OFF · hidden everywhere');
    } catch (e) { R.toggle.checked = !on; toast(e.message, 'err'); }
  });
  R.form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = R.form.elements;
    const say = (text, kind = '') => { R.msg.className = 'form-msg' + (kind ? ' ' + kind : ''); R.msg.textContent = text; };
    const validators = $$('.vrow', R.rows).map((row) => ({
      kind: $('[name="kind"]', row).value,
      name: $('[name="name"]', row).value.trim(),
      organisation: $('[name="organisation"]', row).value.trim(),
      link: $('[name="link"]', row).value.trim(),
    })).filter((v) => v.name || v.organisation || v.link);
    if (!f.repo.value) return say('Choose a repository.', 'err');
    if (!validators.length) return say('Add at least one validator.', 'err');
    if (validators.some((v) => !v.name)) return say('Every validator needs a name.', 'err');
    const body = { id: R.editing, repo: f.repo.value, topic: f.topic.value.trim(), validators,
      usage: f.usage.value.trim(), where: f.where.value.trim(), when: f.when.value };
    say('Saving…');
    try {
      const data = await api('/admin/api/research/save', { method: 'POST', body });
      state.research = data.research;
      resetResearchForm();
      renderResearch();
      say('Saved ✓', 'ok');
      toast(state.research.enabled ? 'Research card saved' : 'Research card saved · the section is still OFF');
    } catch (err) { say(err.message, 'err'); }
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
  resetResearchForm();
  renderResearch();
  checkAI();

  /* ================================================== shared helpers */
  const I8 = (name) => `https://img.icons8.com/3d-fluency/94/${encodeURIComponent(name)}.png`;
  function setState(next) {
    if (!next) return;
    state = next;
    renderRows();
    renderTranslations();
    renderOccasions();
    renderCategories();
    renderSecurity();
  }
  function msg(el, text, kind = 'ok') { el.textContent = text; el.className = 'form-msg ' + kind; }
  const when = (iso) => (iso ? new Date(iso).toLocaleString() : 'never');

  /* ===================================================== translations */
  function renderTranslations() {
    const box = $('#translateProgress');
    const tr = state.translations;
    if (!box || !tr) return;
    box.replaceChildren(...Object.entries(tr.languages).map(([lang, v]) => {
      const pct = v.total ? Math.round((v.done / v.total) * 100) : 100;
      return h('span', { class: 'tr-lang' + (pct === 100 ? ' is-done' : ''), title: `${v.done} of ${v.total} projects` },
        h('b', { text: tr.names[lang] || lang }), ` ${pct}%`);
    }));
  }
  const translateBtn = $('#translateRun');
  // Translations are saved in batches: one save per ~10 translations (or every 2 minutes),
  // because the Gist API rate-limits many small saves. Unsaved ones stay in this tab until saved.
  const pendingTranslations = [];
  window.addEventListener('beforeunload', (e) => { if (pendingTranslations.length) { e.preventDefault(); e.returnValue = ''; } });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function flushTranslations(text) {
    if (!pendingTranslations.length) return true;
    for (let attempt = 1; attempt <= 8; attempt += 1) {
      try {
        const batch = pendingTranslations.slice();
        const data = await api('/admin/api/translate/save', { method: 'POST', body: { items: batch } });
        pendingTranslations.splice(0, batch.length);
        state = data.state; renderTranslations();
        return true;
      } catch (e) {
        const limited = /rate-limit|rate limit|HTTP 403|HTTP 429/i.test(e.message);
        if (!limited || attempt === 8) { toast(e.message, 'err'); return false; }
        for (let s = 90; s > 0; s -= 1) {  // GitHub asked us to slow down: wait, then retry the same batch
          text.textContent = `GitHub is limiting saves — ${pendingTranslations.length} translation(s) kept in this tab, saving again in ${s} s… (attempt ${attempt + 1}/8, don't close this tab)`;
          await sleep(1000);
        }
      }
    }
    return false;
  }
  if (translateBtn) translateBtn.addEventListener('click', async () => {
    if (busy) return toast('Another job is running — wait for it to finish.', 'err');
    busy = true; translateBtn.disabled = true;
    const wrap = $('#translateWrap'); const fillT = $('#translateFill'); const text = $('#translateText');
    wrap.hidden = false;
    let done = 0; let failed = 0; let lastSave = Date.now(); let stopped = false;
    try {
      const plan = await api('/admin/api/translate/plan');
      const jobs = plan.jobs;
      if (!jobs.length) { fillT.style.width = '100%'; text.textContent = 'Everything is already translated ✓'; return; }
      for (const job of jobs) {
        text.textContent = `Translating ${job.name === '__profile__' ? 'your profile' : job.name} → ${job.lang} (${done + 1}/${jobs.length})…`
          + (pendingTranslations.length ? ` · ${pendingTranslations.length} waiting to be saved` : '');
        try {
          const data = await api('/admin/api/translate', { method: 'POST', body: job });
          pendingTranslations.push(data.item);
        } catch (e) {
          failed += 1;
          if (/AI unavailable/i.test(e.message)) { toast('The AI is offline — try again when it is online.', 'err'); stopped = true; break; }
          toast(`${job.name} (${job.lang}): ${e.message}`, 'err');
        }
        done += 1;
        fillT.style.width = Math.round((done / jobs.length) * 100) + '%';
        if (pendingTranslations.length >= 10 || Date.now() - lastSave > 120000) {
          if (!(await flushTranslations(text))) { stopped = true; break; }
          lastSave = Date.now();
        }
      }
      const ok = await flushTranslations(text);
      const left = pendingTranslations.length;
      text.textContent = ok && !stopped
        ? `Done ✓ — ${done - failed} translated and saved${failed ? `, ${failed} failed` : ''}.`
        : `Stopped. ${left ? `${left} translation(s) could not be saved yet — press the button again later.` : 'Everything translated so far is saved — press the button again to continue.'}`;
    } catch (e) { toast(e.message, 'err'); }
    finally { busy = false; translateBtn.disabled = false; }
  });

  /* ======================================================== festivals */
  const occ = () => state.occasions || { festivals: [], custom: [], effects: [], settings: {}, calendar: {} };
  function effectOptions(select, chosen, withDefault) {
    const opts = (withDefault ? [h('option', { value: '', text: `Default (${withDefault})` })] : [])
      .concat(occ().effects.map((e) => h('option', { value: e.key, text: e.label, selected: e.key === chosen })));
    select.replaceChildren(...opts);
    if (!chosen) select.value = '';
  }
  function renderOccasions() {
    const card = $('#occasionCard');
    if (!card || !state.occasions) return;
    const o = occ();
    const cal = o.calendar;
    const feeds = Object.entries(cal.feeds || {}).map(([cc, f]) => h('span', {
      class: 'occ-feed ' + (f.ok ? 'is-ok' : f.checked_at ? 'is-bad' : ''),
      title: f.ok ? `${f.count} festival dates · calendar runs to ${f.last_date || '?'}` : (f.error || 'not checked yet'),
    }, f.name));
    $('#occStatus').replaceChildren(...[
      h('p', {}, h('b', { text: 'Google Calendar: ' }),
        cal.checked_at ? `checked ${when(cal.checked_at)} · ${cal.ok}/${cal.total} calendars loaded` : 'not checked yet — press “Refresh dates”',
        cal.known_until ? ` · dates known until ${cal.known_until}` : ''),
      h('div', { class: 'occ-feeds' }, feeds),
      (cal.changes || []).length ? h('details', {}, h('summary', { text: `Date changes found (${cal.changes.length})` }),
        h('ul', {}, cal.changes.map((c) => h('li', { text: c })))) : null].filter(Boolean));
    // mode
    const form = $('#occSettings');
    const st = o.settings;
    $$('input[name="mode"]', form).forEach((r) => { r.checked = r.value === st.mode; });
    const forced = $('#occForced');
    forced.replaceChildren(...[
      h('optgroup', { label: 'Festivals' }, o.festivals.map((f) => h('option', { value: f.key, text: `${f.emoji} ${f.name}`, selected: st.forced === f.key }))),
      o.custom.length ? h('optgroup', { label: 'Your occasions' }, o.custom.map((c) => h('option', { value: c.key, text: `${c.emoji} ${c.name}`, selected: st.forced === c.key }))) : null].filter(Boolean));
    $('#occUntil').value = st.until || '';
    $('#occHome').checked = !!st.home_fallback;
    $('#occForcedRow').hidden = st.mode !== 'forced';
    // now showing per language
    const LANGS = { en: 'English', hi: 'Hindi', mr: 'Marathi', es: 'Spanish', fr: 'French', de: 'German', ja: 'Japanese', ru: 'Russian' };
    $('#occNow').replaceChildren(...Object.keys(LANGS).filter((l) => l in (o.now_showing || {})).map((lang) => [lang, o.now_showing[lang]]).map(([lang, name]) => h('li', {},
      h('b', { text: LANGS[lang] || lang }), h('small', { text: ` (${(o.regions[lang] || []).join(', ')})` }), ' → ',
      h('span', { class: name ? '' : 'muted', text: name || 'nothing today' }))));
    // festival picker
    const pickSel = $('#occPick');
    const current = pickSel.value;
    const groups = {};
    o.festivals.forEach((f) => { const g = (f.countries || []).length > 3 ? 'Everywhere' : (f.countries || []).join(', ').toUpperCase(); (groups[g] = groups[g] || []).push(f); });
    pickSel.replaceChildren(...Object.entries(groups).map(([g, list]) => h('optgroup', { label: g },
      list.map((f) => h('option', { value: f.key, text: `${f.emoji} ${f.name}${f.enabled ? '' : ' (off)'}${f.image || f.greeting || f.effect ? ' ✎' : ''}` })))));
    if (current) pickSel.value = current;
    fillFestival();
    effectOptions($('#oc-effect'), $('#oc-effect').value || 'confetti');
    renderCustom();
  }
  function thumb(box, url) {
    box.replaceChildren();
    if (/^https:\/\//i.test(url || '')) box.append(h('img', { src: url, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }));
    else box.append(h('span', { text: 'Festive colours (no image)' }));
  }
  function fillFestival() {
    const key = $('#occPick').value;
    const f = occ().festivals.find((x) => x.key === key);
    if (!f) return;
    $('#occEnabled').checked = f.enabled !== false;
    $('#occGreeting').value = f.greeting || '';
    $('#occGreeting').placeholder = f.default_greeting;
    $('#occImage').value = f.image || '';
    effectOptions($('#occEffect'), f.effect, (occ().effects.find((e) => e.key === f.default_effect) || {}).label || f.default_effect);
    thumb($('#occThumb'), f.image);
    $('#occPreview').href = `/?occasion=${encodeURIComponent(f.key)}&lang=${encodeURIComponent(f.preview_lang || 'en')}`;
  }
  if ($('#occasionCard')) {
    $('#occPick').addEventListener('change', fillFestival);
    $('#occImage').addEventListener('input', () => thumb($('#occThumb'), $('#occImage').value.trim()));
    $$('input[name="mode"]', $('#occSettings')).forEach((r) => r.addEventListener('change', () => { $('#occForcedRow').hidden = r.value !== 'forced' || !r.checked; }));
    $('#occSettings').addEventListener('submit', async (e) => {
      e.preventDefault();
      const mode = ($('input[name="mode"]:checked', e.target) || {}).value || 'auto';
      try {
        const data = await api('/admin/api/occasions/settings', { method: 'POST', body: { mode, forced: mode === 'forced' ? $('#occForced').value : '', until: mode === 'forced' ? $('#occUntil').value : '', home_fallback: $('#occHome').checked } });
        setState(data.state); msg($('#occSettingsMsg'), 'Saved ✓');
      } catch (err) { msg($('#occSettingsMsg'), err.message, 'err'); }
    });
    async function saveFestival(body) {
      try {
        const data = await api('/admin/api/occasions/festival', { method: 'POST', body });
        setState(data.state); $('#occPick').value = body.key; fillFestival(); msg($('#occFestivalMsg'), 'Saved ✓');
      } catch (err) { msg($('#occFestivalMsg'), err.message, 'err'); }
    }
    $('#occFestival').addEventListener('submit', (e) => {
      e.preventDefault();
      saveFestival({ key: $('#occPick').value, enabled: $('#occEnabled').checked, greeting: $('#occGreeting').value.trim(), image: $('#occImage').value.trim(), effect: $('#occEffect').value });
    });
    $('#occReset').addEventListener('click', () => saveFestival({ key: $('#occPick').value, enabled: true, greeting: '', image: '', effect: '' }));
    $('#occRefresh').addEventListener('click', async (e) => {
      const btn = e.currentTarget; btn.disabled = true;
      try {
        const data = await api('/admin/api/occasions/refresh', { method: 'POST' });
        setState(data.state);
        const r = data.report;
        toast(r.ok ? `Loaded ${r.ok}/${r.total} calendars from Google — dates for the next 5 years ✓` : 'Google Calendar could not be reached — India is used meanwhile.', r.ok ? 'ok' : 'err');
      } catch (err) { toast(err.message, 'err'); }
      finally { btn.disabled = false; }
    });
    // your own occasions
    let editingOcc = '';
    function resetCustom() {
      editingOcc = '';
      $('#occCustom').reset();
      $('#oc-emoji').value = '🎉'; $('#oc-color').value = '#f59e0b'; $('#oc-enabled').checked = true;
      effectOptions($('#oc-effect'), 'confetti');
      $('#occCustomTitle').textContent = 'Add an occasion';
      $('#occCustomCancel').hidden = true;
    }
    window.__renderCustom = function renderCustomList() {
      const list = $('#occCustomList');
      const items = occ().custom;
      list.replaceChildren(...(items.length ? items.map((c) => {
        const edit = h('button', { class: 'icon-btn', type: 'button', title: 'Edit', 'aria-label': 'Edit ' + c.name }, icon('code'));
        edit.addEventListener('click', () => {
          editingOcc = c.key;
          $('#oc-name').value = c.name; $('#oc-emoji').value = c.emoji; $('#oc-greeting').value = c.greeting || '';
          $('#oc-start').value = c.start; $('#oc-end').value = c.end === c.start ? '' : c.end; $('#oc-yearly').checked = !!c.yearly;
          $('#oc-enabled').checked = c.enabled !== false; $('#oc-image').value = c.image || ''; $('#oc-color').value = c.color || '#f59e0b';
          effectOptions($('#oc-effect'), c.effect);
          $('#occCustomTitle').textContent = 'Edit “' + c.name + '”'; $('#occCustomCancel').hidden = false;
          $('#occCustom').scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        const del = h('button', { class: 'icon-btn', type: 'button', title: 'Delete', 'aria-label': 'Delete ' + c.name }, icon('times'));
        del.addEventListener('click', async () => {
          if (!confirm(`Delete “${c.name}”?`)) return;
          try { const data = await api('/admin/api/occasions/custom/delete', { method: 'POST', body: { key: c.key } }); setState(data.state); toast('Deleted'); }
          catch (err) { toast(err.message, 'err'); }
        });
        const preview = h('a', { class: 'icon-btn', href: `/?occasion=${encodeURIComponent(c.key)}`, target: '_blank', rel: 'noopener', title: 'Preview on site', 'aria-label': 'Preview ' + c.name }, icon('external-link'));
        const dates = c.start === c.end ? c.start : `${c.start} → ${c.end}`;
        return h('div', { class: 'ra-item' + (c.enabled === false ? ' is-off' : '') },
          h('div', {}, h('b', { text: `${c.emoji} ${c.name}` }), h('small', { text: ` · ${dates}${c.yearly ? ' · every year' : ''}${c.enabled === false ? ' · off' : ''}` })),
          h('div', { class: 'row-actions' }, preview, edit, del));
      }) : [h('p', { class: 'hint', text: 'None yet — add your birthday, a launch day or anything you like.' })]));
    };
    $('#occCustomCancel').addEventListener('click', resetCustom);
    $('#occCustom').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = {
        key: editingOcc, name: $('#oc-name').value.trim(), emoji: $('#oc-emoji').value.trim() || '🎉',
        start: $('#oc-start').value, end: $('#oc-end').value, yearly: $('#oc-yearly').checked,
        greeting: $('#oc-greeting').value.trim(), image: $('#oc-image').value.trim(), effect: $('#oc-effect').value,
        color: $('#oc-color').value, enabled: $('#oc-enabled').checked,
      };
      try {
        const data = await api('/admin/api/occasions/custom', { method: 'POST', body });
        setState(data.state); resetCustom(); msg($('#occCustomMsg'), 'Saved ✓');
      } catch (err) { msg($('#occCustomMsg'), err.message, 'err'); }
    });
    effectOptions($('#oc-effect'), 'confetti');
  }
  function renderCustom() { if (window.__renderCustom) window.__renderCustom(); }

  /* ======================================================= categories */
  let editingCat = '';
  let pickedIcon = '';
  let pickedColor = '';
  function renderCategories() {
    const c = state.categories;
    if (!c || !$('#catList')) return;
    $('#catList').replaceChildren(...c.all.map((cat) => {
      const custom = c.custom.find((x) => x.key === cat.key);
      const actions = [];
      if (custom) {
        const up = h('button', { class: 'icon-btn', type: 'button', title: 'Move up', 'aria-label': 'Move ' + cat.label + ' up' }, icon('arrow-up'));
        up.addEventListener('click', () => moveCat(cat.key, -1));
        const edit = h('button', { class: 'icon-btn', type: 'button', title: 'Edit', 'aria-label': 'Edit ' + cat.label }, icon('code'));
        edit.addEventListener('click', () => editCat(custom));
        const del = h('button', { class: 'icon-btn', type: 'button', title: 'Delete', 'aria-label': 'Delete ' + cat.label }, icon('times'));
        del.addEventListener('click', () => deleteCat(custom));
        actions.push(up, edit, del);
      }
      return h('div', { class: 'cat-item' },
        h('img', { src: I8(cat.icon), alt: '', width: '34', height: '34', loading: 'lazy' }),
        h('div', {}, h('b', { text: cat.label }), h('small', { text: ` · ${cat.count} project${cat.count === 1 ? '' : 's'}${cat.builtin ? ' · built-in' : ''}` })),
        custom ? h('i', { class: 'cat-swatch', title: custom.color, 'data-color': custom.color }) : null,
        h('div', { class: 'row-actions' }, actions));
    }));
    $$('.cat-swatch[data-color]').forEach((el) => { el.style.background = el.dataset.color; });
    if (!$('#c-icons').childElementCount) buildPickers();
  }
  function buildPickers() {
    const c = state.categories;
    $('#c-icons').replaceChildren(...c.icons.map((name) => {
      const b = h('button', { type: 'button', class: 'icon-choice', role: 'radio', 'aria-checked': 'false', title: name, 'data-icon': name },
        h('img', { src: I8(name), alt: name, width: '30', height: '30', loading: 'lazy' }));
      b.addEventListener('click', () => chooseIcon(name));
      return b;
    }));
    $('#c-colors').replaceChildren(...c.colors.map((hex) => {
      const b = h('button', { type: 'button', class: 'swatch', role: 'radio', 'aria-checked': 'false', title: hex, 'aria-label': hex, 'data-color': hex });
      b.style.background = hex;
      b.addEventListener('click', () => chooseColor(hex));
      return b;
    }));
    chooseIcon(c.icons[0]); chooseColor(c.colors[0]);
  }
  function chooseIcon(name) { pickedIcon = name; $$('#c-icons .icon-choice').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.icon === name))); }
  function chooseColor(hex) { pickedColor = hex; $$('#c-colors .swatch').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.color === hex))); }
  function editCat(cat) {
    editingCat = cat.key;
    $('#c-name').value = cat.name; $('#c-desc').value = cat.description || '';
    chooseIcon(cat.icon); chooseColor(cat.color);
    $('#catFormTitle').textContent = 'Edit “' + cat.name + '”'; $('#catCancel').hidden = false;
    $('#catForm').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  function resetCat() {
    editingCat = ''; $('#catForm').reset(); $('#catFormTitle').textContent = 'Add a category'; $('#catCancel').hidden = true;
  }
  async function moveCat(key, step) {
    try { const data = await api('/admin/api/categories/move', { method: 'POST', body: { key, step } }); setState(data.state); }
    catch (e) { toast(e.message, 'err'); }
  }
  async function deleteCat(cat) {
    if (!confirm(`Delete “${cat.name}”? Its ${cat.count || 0} project(s) move to Other.`)) return;
    try { const data = await api('/admin/api/categories/delete', { method: 'POST', body: { key: cat.key } }); setState(data.state); toast(`Deleted · ${data.moved} moved to Other`); }
    catch (e) { toast(e.message, 'err'); }
  }
  if ($('#catForm')) {
    $('#catCancel').addEventListener('click', resetCat);
    $('#catForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const data = await api('/admin/api/categories/save', { method: 'POST', body: { key: editingCat, name: $('#c-name').value.trim(), description: $('#c-desc').value.trim(), icon: pickedIcon, color: pickedColor } });
        setState(data.state); resetCat(); msg($('#catMsg'), 'Saved ✓');
      } catch (err) { msg($('#catMsg'), err.message, 'err'); }
    });
  }

  /* ========================================================= security */
  function renderSecurity() {
    const box = $('#securityList');
    const sec = state.security;
    if (!box || !sec) return;
    const items = [];
    if (sec.blocked.length) {
      const all = h('button', { class: 'btn-glass btn-sm', type: 'button' }, icon('undo'), h('span', { text: 'Unblock everyone' }));
      all.addEventListener('click', () => unblock('*'));
      items.push(h('div', { class: 'd-flex justify-content-end mb-2' }, all));
    }
    items.push(...(sec.blocked.length ? sec.blocked.map((b) => {
      const btn = h('button', { class: 'btn-glass btn-sm', type: 'button' }, icon('undo'), h('span', { text: 'Unblock' }));
      btn.addEventListener('click', () => unblock(b.key));
      return h('div', { class: 'ra-item is-blocked' },
        h('div', {}, h('b', { text: b.ip + (b.you ? ' (this is you)' : '') }),
          h('small', { text: ` · blocked ${when(b.at)} after ${b.count} wrong passwords` })),
        btn);
    }) : [h('p', { class: 'hint', text: 'No blocked networks. ✓' })]));
    if (sec.watching.length) {
      items.push(h('p', { class: 'hint mt-2', text: 'Recent wrong passwords (not blocked yet):' }));
      sec.watching.forEach((w) => items.push(h('div', { class: 'ra-item' },
        h('div', {}, h('b', { text: w.ip + (w.you ? ' (this is you)' : '') }), h('small', { text: ` · ${w.count} of ${sec.max_failures} · last ${when(w.last)}` })))));
    }
    box.replaceChildren(...items);
  }
  async function unblock(key) {
    if (key === '*' && !confirm('Unblock every network?')) return;
    try {
      const data = await api('/admin/api/security/unblock', { method: 'POST', body: { ip: key } });
      setState(data.state); toast(`Unblocked ${data.removed} network(s)`);
    } catch (e) { toast(e.message, 'err'); }
  }

  renderTranslations();
  renderOccasions();
  renderCategories();
  renderSecurity();

  /* =========================================================== backup */
  // Read-only. The list comes from the server (its token never reaches the browser); ZIP links are
  // github.com's normal public archive URLs, so nothing secret is ever in a link.
  let backupData = null;
  const kb = (n) => (n >= 1024 * 1024 ? (n / 1024 / 1024).toFixed(1) + ' GB' : n >= 1024 ? (n / 1024).toFixed(1) + ' MB' : n + ' KB');
  const zipUrl = (owner, repo, branch) => `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/archive/refs/heads/${branch.split('/').map(encodeURIComponent).join('/')}.zip`;
  function renderBackup() {
    const d = backupData;
    $('#backupTableWrap').hidden = !d;
    if (!d) return;
    const pub = d.repos.filter((r) => !r.private);
    $('#backupStatus').textContent = `${d.repos.length} repositories (${d.repos.filter((r) => r.private).length} private) · ${d.branch_count} branches · about ${kb(d.total_kb)} on GitHub`
      + (d.private_listed ? '' : ' · only public repositories are visible to the site — the full-backup script adds your private ones through GitHub CLI if it is installed and signed in.');
    $('#backupZips').disabled = !pub.length;
    $('#backupRows').replaceChildren(...d.repos.map((r) => h('tr', {},
      h('td', { class: 'repo-cell' }, h('b', { text: r.name }), h('small', { text: [r.fork ? 'fork' : '', r.archived ? 'archived' : ''].filter(Boolean).join(' · ') })),
      h('td', { text: r.private ? 'Private (script only)' : 'Public' }),
      h('td', { text: kb(r.size_kb) }),
      h('td', {}, h('div', { class: 'branch-links' }, r.branches.length ? r.branches.map((b) => (r.private
        ? h('span', { class: 'branch-pill', text: b + (b === r.default_branch ? ' ★' : '') })
        : h('a', { class: 'branch-pill', href: zipUrl(d.owner, r.name, b), rel: 'noreferrer nofollow', text: b + (b === r.default_branch ? ' ★' : '') }))) : h('small', { text: 'no branches (empty repository)' }))))));
  }
  const backupLoad = $('#backupLoad');
  if (backupLoad) backupLoad.addEventListener('click', async () => {
    backupLoad.disabled = true;
    $('#backupStatus').textContent = 'Reading your repositories and branches from GitHub…';
    try { backupData = await api('/admin/api/backup/list'); renderBackup(); }
    catch (e) { $('#backupStatus').textContent = ''; toast(e.message, 'err'); }
    finally { backupLoad.disabled = false; }
  });
  const backupZips = $('#backupZips');
  if (backupZips) backupZips.addEventListener('click', async () => {
    if (!backupData) return;
    const jobs = [];
    backupData.repos.filter((r) => !r.private).forEach((r) => r.branches.forEach((b) => jobs.push(zipUrl(backupData.owner, r.name, b))));
    if (!jobs.length) return;
    if (!confirm(`Download ${jobs.length} ZIP files (every branch of every public repository) to your Downloads folder?`)) return;
    backupZips.disabled = true;
    for (let i = 0; i < jobs.length; i += 1) {
      $('#backupStatus').textContent = `Starting download ${i + 1} of ${jobs.length}…`;
      const a = h('a', { href: jobs[i], rel: 'noreferrer nofollow', hidden: true });
      document.body.appendChild(a); a.click(); a.remove();
      await new Promise((r) => setTimeout(r, 1300)); // gentle pace so the browser keeps every download
    }
    $('#backupStatus').textContent = `Started ${jobs.length} downloads ✓ — check your browser's downloads.`;
    backupZips.disabled = false;
  });
})();
