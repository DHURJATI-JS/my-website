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
  const LABEL = { social: 'Social impact', industry: 'Industry & startup', other: 'Other' }; // AI never picks "other"
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
})();
