'use strict';

/* ------------------------------------------------------------------ *
 * MailStack admin panel - dependency free single page app.
 * All DOM is built with h(), which only ever sets textContent, so
 * server data can never inject markup.
 * ------------------------------------------------------------------ */

const state = { me: null };
const app = document.getElementById('app');

// ---------- helpers ----------------------------------------------------

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'width') el.style.width = value;
    else if (value === true) el.setAttribute(key, '');
    else el.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(method, path, body) {
  const opts = { method, credentials: 'same-origin', headers: { 'X-Requested-With': 'MailStack' } };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch('/api/v1' + path, opts);
  let data = null;
  if ((res.headers.get('content-type') || '').includes('application/json')) data = await res.json();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/auth/')) {
      state.me = null;
      render();
    }
    const detail = data && data.detail;
    throw new ApiError(res.status, typeof detail === 'string' ? detail : res.statusText || 'Request failed');
  }
  return data;
}

function toast(message, bad = false) {
  const el = h('div', { class: 'toast' + (bad ? ' bad' : '') }, message);
  document.getElementById('toasts').append(el);
  setTimeout(() => el.remove(), bad ? 6000 : 3000);
}

function fail(err) { toast(err.message || String(err), true); }

function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return '–';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(v < 10 ? 1 : 0)) + ' ' + units[i];
}

function fmtMB(mb) {
  if (!mb) return '∞';
  return mb >= 1024 ? (mb / 1024).toFixed(mb % 1024 ? 1 : 0) + ' GB' : mb + ' MB';
}

function fmtDate(value) {
  if (!value) return '–';
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return isNaN(d) ? String(value) : d.toLocaleString();
}

function randomPassword(length = 16) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (b) => chars[b % chars.length]).join('');
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied to clipboard');
  } catch {
    toast('Copy failed - select the text manually', true);
  }
}

function copyField(text) {
  return h('div', { class: 'copy' },
    h('code', {}, text),
    h('button', { class: 'small', title: 'Copy', onclick: () => copy(text) }, 'Copy'));
}

function badge(text, kind = '') { return h('span', { class: 'badge ' + kind }, text); }
function activeBadge(active) { return active ? badge('active', 'ok') : badge('disabled', 'bad'); }

function quotaCell(used, quotaMb) {
  const limit = quotaMb ? quotaMb * 1048576 : null;
  const pct = limit && used !== null ? Math.min(100, (used / limit) * 100) : 0;
  const bar = h('span', { class: pct > 90 ? 'bad' : pct > 75 ? 'warn' : '', width: pct.toFixed(1) + '%' });
  return h('div', { class: 'quota' },
    h('div', { class: 'txt' }, `${fmtBytes(used)} / ${fmtMB(quotaMb)}` + (limit && used !== null ? ` (${pct.toFixed(0)}%)` : '')),
    limit ? h('div', { class: 'bar' }, bar) : null);
}

function table(columns, rows, emptyText = 'Nothing here yet.') {
  if (!rows.length) return h('div', { class: 'table-wrap' }, h('div', { class: 'empty' }, emptyText));
  return h('div', { class: 'table-wrap' },
    h('table', {},
      h('thead', {}, h('tr', {}, columns.map((c) => h('th', {}, c.label)))),
      h('tbody', {}, rows.map((row) => h('tr', {}, columns.map((c) =>
        h('td', { class: c.class || null }, c.render ? c.render(row) : row[c.key])))))));
}

function pageHead(title, sub, ...actions) {
  return h('div', { class: 'page-head' },
    h('div', {}, h('h1', {}, title), sub ? h('div', { class: 'sub' }, sub) : null),
    h('div', { class: 'toolbar' }, actions));
}

// ---------- dialogs ----------------------------------------------------

function fieldInput(f) {
  const common = { name: f.name, id: 'f-' + f.name, required: f.required, placeholder: f.placeholder };
  if (f.type === 'checkbox') {
    return h('label', { class: 'check' }, h('input', { type: 'checkbox', ...common, checked: !!f.value }), f.label);
  }
  let input;
  if (f.type === 'select' || f.type === 'multiselect') {
    const multi = f.type === 'multiselect';
    const chosen = new Set((multi ? f.value || [] : [f.value]).map(String));
    input = h('select', { ...common, multiple: multi, size: multi ? Math.min(8, Math.max(3, f.options.length)) : null },
      f.options.map((o) => {
        const [value, label] = Array.isArray(o) ? o : [o, o];
        return h('option', { value, selected: chosen.has(String(value)) }, label);
      }));
  } else if (f.type === 'textarea') {
    input = h('textarea', common, f.value ?? '');
  } else {
    input = h('input', {
      type: f.type || 'text', ...common, value: f.value ?? '', min: f.min,
      autocomplete: f.type === 'password' ? 'new-password' : 'off', spellcheck: 'false',
    });
  }
  if (f.generate) {
    input = h('div', { class: 'inline' }, input, h('button', {
      type: 'button',
      onclick: (e) => {
        const target = e.target.closest('.inline').querySelector('input');
        target.type = 'text';
        target.value = randomPassword();
      },
    }, 'Generate'));
  }
  return h('label', { class: 'field' }, h('span', {}, f.label), input, f.help ? h('small', {}, f.help) : null);
}

function readForm(form, fields) {
  const out = {};
  for (const f of fields) {
    const el = form.elements[f.name];
    if (!el) continue;
    if (f.type === 'checkbox') out[f.name] = el.checked;
    else if (f.type === 'multiselect') out[f.name] = Array.from(el.selectedOptions, (o) => o.value);
    else if (f.type === 'number') out[f.name] = el.value === '' ? null : Number(el.value);
    else out[f.name] = el.value;
  }
  return out;
}

/** Modal form. onSubmit may throw to keep the dialog open with an error. */
function openForm({ title, fields, submitLabel = 'Save', danger = false, intro, onSubmit }) {
  const errorBox = h('div', { class: 'error-box', hidden: true });
  const submit = h('button', { type: 'submit', class: danger ? 'primary danger' : 'primary' }, submitLabel);
  const form = h('form', { method: 'dialog' },
    h('header', {}, title),
    h('div', { class: 'body' }, errorBox, intro ? h('p', {}, intro) : null, fields.map(fieldInput)),
    h('footer', {}, h('button', { type: 'button', onclick: () => dialog.close() }, 'Cancel'), submit));
  const dialog = h('dialog', {}, form);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    submit.disabled = true;
    errorBox.hidden = true;
    try {
      await onSubmit(readForm(form, fields));
      dialog.close();
    } catch (err) {
      errorBox.textContent = err.message || String(err);
      errorBox.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  const first = form.querySelector('input:not([type=checkbox]), select, textarea');
  if (first) first.focus();
}

function confirmAction(title, message, label, action) {
  openForm({ title, intro: message, fields: [], submitLabel: label, danger: true, onSubmit: action });
}

// ---------- routing ----------------------------------------------------

const adminRoutes = [
  { path: '', label: 'Overview', view: viewOverview },
  { path: 'domains', label: 'Domains', view: viewDomains },
  { path: 'mailboxes', label: 'Mailboxes', view: viewMailboxes },
  { path: 'aliases', label: 'Aliases', view: viewAliases },
  { path: 'alias-domains', label: 'Alias domains', view: viewAliasDomains },
  { path: 'dkim', label: 'DKIM keys', view: viewDkim },
  { path: 'spam', label: 'Spam filter', view: viewSpam, section: 'System' },
  { path: 'admins', label: 'Administrators', view: viewAdmins, superadmin: true },
  { path: 'audit', label: 'Audit log', view: viewAudit },
  { path: 'security', label: 'Security', view: viewSecurity, section: 'Account' },
];

const userRoutes = [
  { path: '', label: 'My account', view: viewAccount },
  { path: 'security', label: 'Security', view: viewSecurity },
];

const isSuper = () => state.me && state.me.superadmin;

function currentPath() { return location.hash.replace(/^#\/?/, ''); }

async function render() {
  if (!state.me) {
    try {
      state.me = await api('GET', '/auth/me');
    } catch {
      return renderLogin();
    }
  }
  const routes = state.me.role === 'admin' ? adminRoutes.filter((r) => !r.superadmin || isSuper()) : userRoutes;
  const path = currentPath();
  const [base, ...rest] = path.split('?')[0].split('/');
  const route = routes.find((r) => r.path === base) || routes[0];
  const main = h('main', { class: 'main' }, h('div', { class: 'muted' }, 'Loading…'));
  app.replaceChildren(h('div', { class: 'shell' }, sidebar(routes, route), main));
  try {
    const content = await route.view(rest.map(decodeURIComponent));
    if (currentPath() === path) main.replaceChildren(content);
  } catch (err) {
    if (err.status !== 401) main.replaceChildren(h('div', { class: 'card error-box' }, err.message));
  }
}

function sidebar(routes, active) {
  const items = [];
  for (const r of routes) {
    if (r.section) items.push(h('div', { class: 'section' }, r.section));
    items.push(h('a', { href: '#/' + r.path, class: r === active ? 'active' : null }, r.label));
  }
  items.push(h('div', { class: 'section' }, 'Links'));
  items.push(h('a', { href: '/webmail/', target: '_blank', rel: 'noopener' }, 'Webmail ↗'));
  if (isSuper()) {
    items.push(h('a', { href: '/rspamd/', target: '_blank', rel: 'noopener' }, 'Rspamd UI ↗'));
    items.push(h('a', { href: '/api/docs', target: '_blank', rel: 'noopener' }, 'API docs ↗'));
  }
  return h('aside', { class: 'sidebar' },
    h('div', { class: 'brand' }, h('img', { src: '/static/favicon.svg', alt: '' }), 'MailStack'),
    h('nav', { class: 'nav' }, items),
    h('div', { class: 'spacer' }),
    h('div', { class: 'who' },
      h('div', {}, h('div', { class: 'muted' }, state.me.role === 'user' ? 'Mailbox' : isSuper() ? 'Superadmin' : 'Domain admin'),
        h('div', { class: 'name' }, state.me.username)),
      h('button', { class: 'link', onclick: logout }, 'Sign out')));
}

async function logout() {
  try { await api('POST', '/auth/logout'); } catch { /* ignore */ }
  state.me = null;
  location.hash = '#/';
  render();
}

window.addEventListener('hashchange', render);

// ---------- login ------------------------------------------------------

function renderLogin() {
  const errorBox = h('div', { class: 'error-box', hidden: true });
  const submit = h('button', { type: 'submit', class: 'primary' }, 'Sign in');
  const form = h('form', { class: 'card login' },
    h('div', { class: 'brand' }, h('img', { src: '/static/favicon.svg', alt: '' }), 'MailStack'),
    h('div', { class: 'host' }, location.hostname),
    errorBox,
    h('label', { class: 'field' }, h('span', {}, 'Username or email'),
      h('input', { name: 'username', autocomplete: 'username', required: true, autofocus: true })),
    h('label', { class: 'field' }, h('span', {}, 'Password'),
      h('input', { name: 'password', type: 'password', autocomplete: 'current-password', required: true })),
    submit,
    h('p', { class: 'muted' }, 'Administrators sign in with their username, mailbox users with their email address.'));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    submit.disabled = true;
    errorBox.hidden = true;
    try {
      const res = await api('POST', '/auth/login', { username: form.username.value, password: form.password.value });
      if (res.totp_required) return renderTotpStep();
      state.me = res;
      render();
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
      submit.disabled = false;
    }
  });
  app.replaceChildren(h('div', { class: 'login-wrap' }, form));
  form.username.focus();
}

function renderTotpStep() {
  const errorBox = h('div', { class: 'error-box', hidden: true });
  const submit = h('button', { type: 'submit', class: 'primary' }, 'Verify');
  const code = h('input', { name: 'code', autocomplete: 'one-time-code', inputmode: 'numeric', required: true, maxlength: '16', class: 'otp' });
  const form = h('form', { class: 'card login' },
    h('div', { class: 'brand' }, h('img', { src: '/static/favicon.svg', alt: '' }), 'MailStack'),
    h('div', { class: 'host' }, 'Two-factor authentication'),
    errorBox,
    h('label', { class: 'field' }, h('span', {}, 'Code from your authenticator app'), code,
      h('small', {}, 'Lost your device? Enter one of your recovery codes instead.')),
    submit,
    h('p', {}, h('button', { type: 'button', class: 'link', onclick: renderLogin }, '← Back')));
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    submit.disabled = true;
    errorBox.hidden = true;
    try {
      state.me = await api('POST', '/auth/login/totp', { code: code.value });
      render();
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
      submit.disabled = false;
      code.select();
    }
  });
  app.replaceChildren(h('div', { class: 'login-wrap' }, form));
  code.focus();
}

// ---------- overview ---------------------------------------------------

function stat(label, value) {
  return h('div', { class: 'card stat' }, h('div', { class: 'label' }, label), h('div', { class: 'value' }, value));
}

async function viewOverview() {
  const o = await api('GET', '/overview');
  const c = o.counts;
  const down = o.services.filter((s) => !s.ok).length;
  const spamBody = h('div', { class: 'muted' }, 'Loading…');
  if (isSuper()) api('GET', '/spam/stats').then((r) => {
    const actions = r.actions || {};
    spamBody.replaceWith(h('div', { class: 'kv' },
      h('dt', {}, 'Messages scanned'), h('dd', {}, r.scanned),
      h('dt', {}, 'Rejected'), h('dd', {}, actions.reject ?? 0),
      h('dt', {}, 'Marked as spam'), h('dd', {}, actions['add header'] ?? 0),
      h('dt', {}, 'Greylisted'), h('dd', {}, actions.greylist ?? 0),
      h('dt', {}, 'Clean'), h('dd', {}, actions['no action'] ?? 0),
      h('dt', {}, 'Learned'), h('dd', {}, r.learned)));
  }).catch(() => { spamBody.textContent = 'Rspamd is not reachable.'; });
  return h('div', {},
    pageHead('Overview', o.mail_hostname,
      h('a', { class: 'btn', href: '#/domains' }, 'Manage domains'),
      h('a', { class: 'btn primary', href: '#/mailboxes' }, 'Mailboxes')),
    h('div', { class: 'grid stats' },
      stat('Domains', c.domains),
      stat('Mailboxes', c.mailboxes),
      stat('Aliases', c.aliases),
      stat('Alias domains', c.alias_domains),
      stat('Quota allocated', fmtMB(c.quota_allocated_mb))),
    h('div', { class: 'grid two' },
      h('div', { class: 'card' },
        h('h2', {}, 'Services ', down ? badge(`${down} down`, 'bad') : badge('all healthy', 'ok')),
        h('div', { class: 'kv' }, o.services.map((s) => [
          h('dt', {}, h('span', { class: 'dot' + (s.ok ? ' ok' : '') }), s.service),
          h('dd', {}, s.check, ' ', s.ok ? '' : badge('unreachable', 'bad')),
        ]))),
      isSuper() ? h('div', { class: 'card' },
        h('h2', {}, 'Spam filter'),
        spamBody,
        h('p', {}, h('a', { href: '#/spam' }, 'View history →')))
        : h('div', { class: 'card' },
          h('h2', {}, 'Your domains'),
          state.me.domains.length ? h('ul', {}, state.me.domains.map((d) => h('li', {}, d))) : h('p', { class: 'muted' }, 'No domains assigned yet.'))));
}

// ---------- domains ----------------------------------------------------

function domainFields(d = {}, isNew = false) {
  const fields = [];
  if (isNew) fields.push({ name: 'name', label: 'Domain name', required: true, placeholder: 'example.com' });
  fields.push(
    { name: 'description', label: 'Description', value: d.description || '' },
    { name: 'default_quota_mb', label: 'Default mailbox quota (MB)', type: 'number', min: 0, value: d.default_quota_mb ?? 1024, help: '0 = unlimited' },
    { name: 'max_quota_mb', label: 'Max quota per mailbox (MB)', type: 'number', min: 0, value: d.max_quota_mb ?? 0, help: '0 = no limit' },
    { name: 'total_quota_mb', label: 'Total quota for the domain (MB)', type: 'number', min: 0, value: d.total_quota_mb ?? 0, help: '0 = no limit' },
    { name: 'max_mailboxes', label: 'Max mailboxes', type: 'number', min: 0, value: d.max_mailboxes ?? 0, help: '0 = no limit' },
    { name: 'max_aliases', label: 'Max aliases', type: 'number', min: 0, value: d.max_aliases ?? 0, help: '0 = no limit' },
    { name: 'active', label: 'Active', type: 'checkbox', value: d.active ?? true });
  if (isNew) fields.push({ name: 'generate_dkim', label: 'Generate a DKIM key', type: 'checkbox', value: true });
  return fields;
}

async function viewDomains() {
  const domains = await api('GET', '/domains');
  const add = () => openForm({
    title: 'Add domain', fields: domainFields({}, true), submitLabel: 'Add domain',
    onSubmit: async (v) => {
      const d = await api('POST', '/domains', v);
      toast(`Domain ${d.name} added - now publish its DNS records`);
      location.hash = '#/domains/' + encodeURIComponent(d.name);
    },
  });
  return h('div', {},
    pageHead('Domains', isSuper() ? 'Mail domains hosted on this server' : 'Domains you manage',
      isSuper() ? h('button', { class: 'primary', onclick: add }, '+ Add domain') : null),
    table([
      { label: 'Domain', render: (d) => h('a', { href: '#/domains/' + encodeURIComponent(d.name) }, d.name) },
      { label: 'Mailboxes', render: (d) => `${d.mailbox_count} / ${d.max_mailboxes || '∞'}` },
      { label: 'Aliases', render: (d) => `${d.alias_count} / ${d.max_aliases || '∞'}` },
      { label: 'Quota allocated', render: (d) => `${d.quota_allocated_mb ? fmtMB(d.quota_allocated_mb) : '0 MB'} / ${fmtMB(d.total_quota_mb)}` },
      { label: 'DKIM', render: (d) => d.dkim_selector ? badge(d.dkim_selector, 'ok') : badge('none', 'warn') },
      { label: 'Status', render: (d) => activeBadge(d.active) },
      { label: '', class: 'actions', render: (d) => [
        h('a', { class: 'btn small', href: '#/domains/' + encodeURIComponent(d.name) }, 'DNS'),
        isSuper() ? [' ', h('button', { class: 'small', onclick: () => editDomain(d) }, 'Edit'), ' ',
          h('button', { class: 'small danger', onclick: () => deleteDomain(d) }, 'Delete')] : null,
      ] },
    ], domains, isSuper() ? 'No domains yet. Add your first domain to get started.' : 'No domains are assigned to you.'));
}

function editDomain(d) {
  openForm({
    title: 'Edit ' + d.name, fields: domainFields(d),
    onSubmit: async (v) => { await api('PATCH', '/domains/' + encodeURIComponent(d.name), v); toast('Domain updated'); render(); },
  });
}

function deleteDomain(d) {
  confirmAction('Delete ' + d.name,
    `This permanently deletes the domain, its ${d.mailbox_count} mailbox(es), ${d.alias_count} alias(es), alias domains and DKIM key. Stored mail stays on disk until purged.`,
    'Delete domain',
    async () => { await api('DELETE', '/domains/' + encodeURIComponent(d.name)); toast('Domain deleted'); location.hash = '#/domains'; render(); });
}

const statusKinds = { ok: 'ok', missing: 'bad', mismatch: 'warn', error: 'warn', unknown: '' };

async function viewDomainDns(name) {
  const data = await api('GET', `/domains/${encodeURIComponent(name)}/dns`);
  const required = data.records.filter((r) => r.required);
  const okCount = required.filter((r) => r.status === 'ok').length;
  const cols = [
    { label: 'Type', render: (r) => h('strong', {}, r.type) },
    { label: 'Name', class: 'wrap name', render: (r) => h('div', {}, copyField(r.name), h('div', { class: 'muted' }, r.purpose)) },
    { label: 'Value', class: 'wrap', render: (r) => h('div', {}, copyField(r.value),
      r.current.length && r.status !== 'ok' ? h('div', { class: 'muted' }, 'Current: ' + r.current.join(' | ')) : null,
      r.error ? h('div', { class: 'muted' }, r.error) : null) },
    { label: 'Status', render: (r) => badge(r.status, statusKinds[r.status]) },
  ];
  return h('div', {},
    pageHead('DNS for ' + data.domain, `${okCount} of ${required.length} required records are correct`,
      h('a', { class: 'btn', href: '#/domains' }, '← Domains'),
      h('button', { onclick: render }, 'Re-check')),
    h('h2', {}, 'Required'),
    table(cols, required),
    h('div', { class: 'card' }, h('h2', {}, 'Recommended'),
      h('p', { class: 'muted' }, 'Autoconfiguration, SRV and MTA-STS records make client setup automatic and harden delivery. Long TXT values may need to be split into 255-character strings by your DNS provider.')),
    table(cols, data.records.filter((r) => !r.required)));
}

// Domain detail route shares the "domains" nav entry.
adminRoutes[1].view = (args) => (args[0] ? viewDomainDns(args[0]) : viewDomains());

// ---------- mailboxes --------------------------------------------------

async function viewMailboxes() {
  const params = new URLSearchParams(location.hash.split('?')[1] || '');
  const domains = await api('GET', '/domains');
  const filterDomain = params.get('domain') || '';
  const q = params.get('q') || '';
  const query = new URLSearchParams();
  if (filterDomain) query.set('domain', filterDomain);
  if (q) query.set('q', q);
  const rows = await api('GET', '/mailboxes?' + query);

  const applyFilter = (d, s) => {
    const p = new URLSearchParams();
    if (d) p.set('domain', d);
    if (s) p.set('q', s);
    location.hash = '#/mailboxes' + (p.toString() ? '?' + p : '');
  };
  const domainSelect = h('select', { onchange: (e) => applyFilter(e.target.value, q) },
    h('option', { value: '' }, 'All domains'),
    domains.map((d) => h('option', { value: d.name, selected: d.name === filterDomain }, d.name)));
  const search = h('input', { type: 'search', placeholder: 'Search…', value: q });
  search.addEventListener('keydown', (e) => { if (e.key === 'Enter') applyFilter(filterDomain, search.value.trim()); });

  const add = () => {
    if (!domains.length) return toast('Add a domain first', true);
    const def = domains.find((d) => d.name === filterDomain) || domains[0];
    openForm({
      title: 'Add mailbox', submitLabel: 'Create mailbox',
      fields: [
        { name: 'local_part', label: 'Address (part before @)', required: true, placeholder: 'jane' },
        { name: 'domain', label: 'Domain', type: 'select', options: domains.map((d) => d.name), value: def.name },
        { name: 'name', label: 'Display name', placeholder: 'Jane Doe' },
        { name: 'password', label: 'Password', type: 'password', required: true, generate: true, help: 'At least 8 characters' },
        { name: 'quota_mb', label: 'Quota (MB)', type: 'number', min: 0, placeholder: 'Domain default', help: 'Leave empty for the domain default; 0 = unlimited' },
        { name: 'active', label: 'Active', type: 'checkbox', value: true },
      ],
      onSubmit: async (v) => {
        const m = await api('POST', '/mailboxes', v);
        toast(`Mailbox ${m.username} created`);
        render();
      },
    });
  };

  return h('div', {},
    pageHead('Mailboxes', `${rows.length} mailbox(es)`, domainSelect, search,
      h('button', { class: 'primary', onclick: add }, '+ Add mailbox')),
    table([
      { label: 'Address', render: (m) => h('div', {}, h('strong', {}, m.username), m.name ? h('div', { class: 'muted' }, m.name) : null) },
      { label: 'Usage', render: (m) => quotaCell(m.used_bytes, m.quota_mb) },
      { label: 'Messages', render: (m) => m.messages ?? '–' },
      { label: 'Last login', render: (m) => fmtDate(m.last_login) },
      { label: 'Security', render: (m) => [m.is_2fa ? badge('2FA', 'ok') : null, ' ',
        m.app_passwords ? badge(`${m.app_passwords} app pw`, 'info') : null] },
      { label: 'Status', render: (m) => activeBadge(m.active) },
      { label: '', class: 'actions', render: (m) => [
        h('button', { class: 'small', onclick: () => editMailbox(m) }, 'Edit'), ' ',
        h('button', { class: 'small danger', onclick: () => deleteMailbox(m) }, 'Delete'),
      ] },
    ], rows, domains.length ? 'No mailboxes found.' : 'Add a domain before creating mailboxes.'));
}

function editMailbox(m) {
  openForm({
    title: 'Edit ' + m.username,
    fields: [
      { name: 'name', label: 'Display name', value: m.name },
      { name: 'password', label: 'New password', type: 'password', generate: true, help: 'Leave empty to keep the current password' },
      { name: 'quota_mb', label: 'Quota (MB)', type: 'number', min: 0, value: m.quota_mb, help: '0 = unlimited' },
      { name: 'active', label: 'Active', type: 'checkbox', value: m.active },
      m.is_2fa ? { name: 'disable_2fa', label: 'Reset two-factor authentication', type: 'checkbox' } : null,
      m.app_passwords ? { name: 'revoke_app_passwords', label: `Revoke all ${m.app_passwords} app password(s)`, type: 'checkbox' } : null,
    ].filter(Boolean),
    onSubmit: async (v) => {
      if (!v.password) delete v.password;
      await api('PATCH', '/mailboxes/' + encodeURIComponent(m.username), v);
      toast('Mailbox updated');
      render();
    },
  });
}

function deleteMailbox(m) {
  confirmAction('Delete ' + m.username,
    'The mailbox is removed immediately and the address will no longer receive mail. Stored messages stay on disk until purged (./mailstack.sh purge-orphans).',
    'Delete mailbox',
    async () => { await api('DELETE', '/mailboxes/' + encodeURIComponent(m.username)); toast('Mailbox deleted'); render(); });
}

// ---------- aliases ----------------------------------------------------

async function viewAliases() {
  const [aliases, domains] = await Promise.all([api('GET', '/aliases'), api('GET', '/domains')]);
  const add = () => {
    if (!domains.length) return toast('Add a domain first', true);
    openForm({
      title: 'Add alias', submitLabel: 'Create alias',
      fields: [
        { name: 'address', label: 'Alias address', required: true, placeholder: `info@${domains[0].name}`,
          help: `Use @${domains[0].name} for a catch-all that receives mail for every unknown address.` },
        { name: 'goto', label: 'Deliver to', type: 'textarea', required: true, placeholder: 'one address per line', help: 'Local mailboxes or external addresses' },
        { name: 'description', label: 'Description' },
        { name: 'active', label: 'Active', type: 'checkbox', value: true },
      ],
      onSubmit: async (v) => {
        v.goto = v.goto.split(/[\s,;]+/).filter(Boolean);
        const a = await api('POST', '/aliases', v);
        toast(`Alias ${a.address} created`);
        render();
      },
    });
  };
  return h('div', {},
    pageHead('Aliases', 'Forward addresses to one or more mailboxes', h('button', { class: 'primary', onclick: add }, '+ Add alias')),
    table([
      { label: 'Alias', render: (a) => h('div', {}, h('strong', {}, a.address), ' ', a.is_catch_all ? badge('catch-all', 'info') : null) },
      { label: 'Delivers to', class: 'wrap', render: (a) => a.goto.join(', ') },
      { label: 'Description', render: (a) => a.description || h('span', { class: 'muted' }, '–') },
      { label: 'Status', render: (a) => activeBadge(a.active) },
      { label: '', class: 'actions', render: (a) => [
        h('button', { class: 'small', onclick: () => editAlias(a) }, 'Edit'), ' ',
        h('button', { class: 'small danger', onclick: () => confirmAction('Delete ' + a.address, 'Mail to this address will be rejected.', 'Delete alias',
          async () => { await api('DELETE', '/aliases/' + a.id); toast('Alias deleted'); render(); }) }, 'Delete'),
      ] },
    ], aliases, 'No aliases yet.'));
}

function editAlias(a) {
  openForm({
    title: 'Edit ' + a.address,
    fields: [
      { name: 'goto', label: 'Deliver to', type: 'textarea', value: a.goto.join('\n'), required: true },
      { name: 'description', label: 'Description', value: a.description },
      { name: 'active', label: 'Active', type: 'checkbox', value: a.active },
    ],
    onSubmit: async (v) => {
      v.goto = v.goto.split(/[\s,;]+/).filter(Boolean);
      await api('PATCH', '/aliases/' + a.id, v);
      toast('Alias updated');
      render();
    },
  });
}

// ---------- alias domains ----------------------------------------------

async function viewAliasDomains() {
  const [rows, domains] = await Promise.all([api('GET', '/alias-domains'), api('GET', '/domains')]);
  const add = () => {
    if (!domains.length) return toast('Add a domain first', true);
    openForm({
      title: 'Add alias domain', submitLabel: 'Add alias domain',
      intro: 'Every address of the alias domain is delivered to the same address at the target domain (jane@alias.com → jane@target.com).',
      fields: [
        { name: 'alias_domain', label: 'Alias domain', required: true, placeholder: 'example.net' },
        { name: 'target_domain', label: 'Target domain', type: 'select', options: domains.map((d) => d.name) },
        { name: 'active', label: 'Active', type: 'checkbox', value: true },
        { name: 'generate_dkim', label: 'Generate a DKIM key', type: 'checkbox', value: true },
      ],
      onSubmit: async (v) => { await api('POST', '/alias-domains', v); toast('Alias domain added'); render(); },
    });
  };
  return h('div', {},
    pageHead('Alias domains', 'Mirror all addresses of one domain onto another',
      isSuper() ? h('button', { class: 'primary', onclick: add }, '+ Add alias domain') : null),
    table([
      { label: 'Alias domain', render: (r) => h('a', { href: '#/domains/' + encodeURIComponent(r.alias_domain) }, r.alias_domain) },
      { label: 'Target', render: (r) => r.target_domain },
      { label: 'DKIM', render: (r) => r.dkim_selector ? badge(r.dkim_selector, 'ok') : badge('none', 'warn') },
      { label: 'Status', render: (r) => activeBadge(r.active) },
      { label: '', class: 'actions', render: (r) => !isSuper() ? null : [
        h('button', { class: 'small', onclick: async () => {
          try { await api('PATCH', '/alias-domains/' + encodeURIComponent(r.alias_domain), { active: !r.active }); render(); } catch (e) { fail(e); }
        } }, r.active ? 'Disable' : 'Enable'), ' ',
        h('button', { class: 'small danger', onclick: () => confirmAction('Delete ' + r.alias_domain, 'Mail to this domain will be rejected.', 'Delete',
          async () => { await api('DELETE', '/alias-domains/' + encodeURIComponent(r.alias_domain)); toast('Alias domain deleted'); render(); }) }, 'Delete'),
      ] },
    ], rows, 'No alias domains.'));
}

// ---------- DKIM -------------------------------------------------------

async function viewDkim() {
  const rows = await api('GET', '/dkim');
  const generate = (r) => openForm({
    title: (r.key ? 'Rotate' : 'Generate') + ' DKIM key for ' + r.domain,
    intro: r.key ? 'The old key is replaced immediately. Publish the new DNS record right away, or use a new selector and keep the old record until it has propagated.' : null,
    submitLabel: r.key ? 'Rotate key' : 'Generate key',
    fields: [
      { name: 'selector', label: 'Selector', value: r.key ? r.key.selector : 'dkim', required: true },
      { name: 'bits', label: 'Key size', type: 'select', options: [[1024, '1024 bit'], [2048, '2048 bit (recommended)'], [3072, '3072 bit'], [4096, '4096 bit']], value: r.key ? r.key.bits : 2048 },
    ],
    onSubmit: async (v) => {
      await api('POST', '/dkim/' + encodeURIComponent(r.domain), { selector: v.selector, bits: Number(v.bits) });
      toast('DKIM key generated - update DNS');
      render();
    },
  });
  if (!rows.length) return h('div', {}, pageHead('DKIM keys', null), h('div', { class: 'card empty' }, 'Add a domain first.'));
  return h('div', {},
    pageHead('DKIM keys', 'Outgoing mail is signed with these keys by Rspamd'),
    rows.map((r) => h('div', { class: 'card' },
      h('div', { class: 'page-head' },
        h('div', {}, h('h2', {}, r.domain, ' ', r.kind === 'alias' ? badge('alias domain', 'info') : null),
          r.key ? h('div', { class: 'muted' }, `selector "${r.key.selector}" · ${r.key.bits} bit · created ${fmtDate(r.key.created_at)}`)
            : h('div', { class: 'muted' }, 'No key - mail from this domain is not signed.')),
        h('div', { class: 'toolbar' },
          h('button', { class: r.key ? '' : 'primary', onclick: () => generate(r) }, r.key ? 'Rotate' : 'Generate'),
          r.key ? h('button', { class: 'danger', onclick: () => confirmAction('Delete DKIM key', `Mail from ${r.domain} will no longer be signed.`, 'Delete key',
            async () => { await api('DELETE', '/dkim/' + encodeURIComponent(r.domain)); toast('Key deleted'); render(); }) }, 'Delete') : null)),
      r.key ? h('div', {},
        h('label', { class: 'field' }, h('span', {}, 'TXT record name'), copyField(r.key.dns_name)),
        h('label', { class: 'field' }, h('span', {}, 'TXT record value'), copyField(r.key.dns_value))) : null)));
}

// ---------- spam filter ------------------------------------------------

const actionKinds = { reject: 'bad', 'add header': 'warn', 'rewrite subject': 'warn', greylist: 'info', 'soft reject': 'info', 'no action': 'ok' };

async function viewSpam() {
  const [stats, history] = await Promise.all([
    isSuper() ? api('GET', '/spam/stats').catch(() => null) : Promise.resolve(null),
    api('GET', '/spam/history?limit=200').catch(() => []),
  ]);
  const a = (stats && stats.actions) || {};
  return h('div', {},
    pageHead('Spam filter', isSuper() ? 'Rspamd statistics and recent scans' : 'Recent scans of mail for your domains',
      isSuper() ? h('a', { class: 'btn', href: '/rspamd/', target: '_blank', rel: 'noopener' }, 'Open Rspamd UI ↗') : null),
    !isSuper() ? null : stats ? h('div', { class: 'grid stats' },
      stat('Scanned', stats.scanned), stat('Rejected', a.reject ?? 0), stat('Spam (header)', a['add header'] ?? 0),
      stat('Greylisted', a.greylist ?? 0), stat('Clean', a['no action'] ?? 0), stat('Learned', stats.learned))
      : h('div', { class: 'card error-box' }, 'Rspamd is not reachable.'),
    h('h2', {}, 'Recent messages'),
    table([
      { label: 'Time', render: (r) => fmtDate(r.time) },
      { label: 'From', class: 'wrap', render: (r) => r.sender || '–' },
      { label: 'To', class: 'wrap', render: (r) => (r.rcpt || []).join(', ') },
      { label: 'Subject', class: 'wrap', render: (r) => r.subject || '' },
      { label: 'Action', render: (r) => badge(r.action || '?', actionKinds[r.action] || '') },
      { label: 'Score', render: (r) => r.score !== undefined && r.score !== null ? `${Number(r.score).toFixed(2)} / ${r.required_score}` : '–' },
    ], history, 'No messages scanned yet.'));
}

// ---------- admins -----------------------------------------------------

const roleOptions = [['superadmin', 'Superadmin - everything'], ['domainadmin', 'Domain admin - selected domains only']];

function adminFields(a, domains, isNew) {
  return [
    isNew ? { name: 'username', label: 'Username', required: true } : null,
    { name: 'password', label: isNew ? 'Password' : 'New password', type: 'password', required: isNew, generate: true,
      help: isNew ? null : 'Leave empty to keep the current password' },
    { name: 'role', label: 'Role', type: 'select', options: roleOptions, value: a.role || 'domainadmin' },
    { name: 'domains', label: 'Managed domains', type: 'multiselect', options: domains.map((d) => d.name), value: a.domains || [],
      help: 'Only for domain admins. Ctrl/Cmd-click to select several. Alias domains of these domains are included.' },
    { name: 'active', label: 'Active', type: 'checkbox', value: a.active ?? true },
    a.is_2fa ? { name: 'disable_2fa', label: 'Reset two-factor authentication', type: 'checkbox' } : null,
  ].filter(Boolean);
}

async function viewAdmins() {
  const [rows, domains] = await Promise.all([api('GET', '/admins'), api('GET', '/domains')]);
  const add = () => openForm({
    title: 'Add administrator', submitLabel: 'Create', fields: adminFields({}, domains, true),
    onSubmit: async (v) => { await api('POST', '/admins', v); toast('Administrator created'); render(); },
  });
  const edit = (a) => openForm({
    title: 'Edit ' + a.username, fields: adminFields(a, domains, false),
    onSubmit: async (v) => {
      if (!v.password) delete v.password;
      await api('PATCH', '/admins/' + encodeURIComponent(a.username), v);
      toast('Administrator updated');
      if (a.username === state.me.username) state.me = null;
      render();
    },
  });
  return h('div', {},
    pageHead('Administrators', 'Superadmins manage everything; domain admins only their domains',
      h('button', { class: 'primary', onclick: add }, '+ Add administrator')),
    table([
      { label: 'Username', render: (a) => h('strong', {}, a.username, a.username === state.me.username ? ' (you)' : '') },
      { label: 'Role', render: (a) => a.role === 'superadmin' ? badge('superadmin', 'info') : badge('domain admin') },
      { label: 'Domains', class: 'wrap', render: (a) => a.role === 'superadmin' ? h('span', { class: 'muted' }, 'all') : (a.domains.join(', ') || h('span', { class: 'muted' }, 'none')) },
      { label: '2FA', render: (a) => a.is_2fa ? badge('on', 'ok') : badge('off', 'warn') },
      { label: 'Last login', render: (a) => fmtDate(a.last_login) },
      { label: 'Status', render: (a) => activeBadge(a.active) },
      { label: '', class: 'actions', render: (a) => [
        h('button', { class: 'small', onclick: () => edit(a) }, 'Edit'), ' ',
        a.username !== state.me.username ? h('button', { class: 'small danger', onclick: () => confirmAction('Delete ' + a.username, 'This account will lose access immediately.', 'Delete',
          async () => { await api('DELETE', '/admins/' + encodeURIComponent(a.username)); toast('Administrator deleted'); render(); }) }, 'Delete') : null,
      ] },
    ], rows));
}

// ---------- audit ------------------------------------------------------

async function viewAudit() {
  const rows = await api('GET', '/audit?limit=500');
  const kind = (action) => action.startsWith('login.failed') ? 'bad' : action.endsWith('.delete') || action === '2fa.disable' ? 'warn' : '';
  return h('div', {},
    pageHead('Audit log', isSuper() ? 'The last 500 administrative actions and logins' : 'Recent actions concerning your domains'),
    table([
      { label: 'Time', render: (r) => fmtDate(r.created_at) },
      { label: 'Actor', render: (r) => r.actor },
      { label: 'Action', render: (r) => badge(r.action, kind(r.action)) },
      { label: 'Target', class: 'wrap', render: (r) => r.target },
      { label: 'Details', class: 'wrap', render: (r) => r.details },
      { label: 'IP', render: (r) => h('span', { class: 'mono' }, r.ip) },
    ], rows, 'No entries yet.'));
}

// ---------- security (password + 2FA, any account) --------------------

/** Informational dialog with extra content appended below the intro. */
function infoDialog(title, intro, ...content) {
  openForm({ title, intro, submitLabel: 'Done', fields: [], onSubmit: async () => {} });
  document.querySelector('dialog .body').append(...content);
}

function showRecoveryCodes(codes) {
  const text = codes.join('\n');
  infoDialog('Save your recovery codes',
    'Each code signs you in once if you lose your authenticator. They will not be shown again.',
    h('pre', { class: 'codes' }, text),
    h('p', {}, h('button', { type: 'button', onclick: () => copy(text) }, 'Copy codes')));
}

function changePasswordForm() {
  openForm({
    title: 'Change password', submitLabel: 'Change password',
    intro: 'Your other sessions will be signed out.' + (state.me.role === 'user' ? ' Update the password in mail apps that do not use an app password.' : ''),
    fields: [
      { name: 'current_password', label: 'Current password', type: 'password', required: true },
      { name: 'new_password', label: 'New password', type: 'password', required: true, generate: true, help: 'At least 8 characters' },
    ],
    onSubmit: async (v) => { await api('POST', '/account/password', v); toast('Password changed'); },
  });
}

async function startTotpSetup() {
  let setup;
  try { setup = await api('POST', '/account/2fa/setup'); } catch (err) { return fail(err); }
  openForm({
    title: 'Enable two-factor authentication', submitLabel: 'Enable',
    intro: 'Scan the QR code with an authenticator app (Aegis, 2FAS, Google Authenticator, 1Password, …), then enter the 6-digit code it shows.',
    fields: [
      { name: 'code', label: 'Code from the app', required: true, placeholder: '123456' },
      { name: 'password', label: 'Your password', type: 'password', required: true },
    ],
    onSubmit: async (v) => {
      const res = await api('POST', '/account/2fa/enable', v);
      render();
      setTimeout(() => showRecoveryCodes(res.recovery_codes), 50);
    },
  });
  const body = document.querySelector('dialog .body');
  body.insertBefore(h('div', { class: 'qr' },
    h('img', { src: setup.qr, alt: 'QR code for your authenticator app' }),
    h('div', {}, h('div', { class: 'muted' }, 'Or enter this key manually:'),
      h('code', { class: 'secret' }, setup.secret.replace(/(.{4})/g, '$1 ').trim()))),
  body.querySelector('label.field'));
}

async function viewSecurity() {
  const acct = await api('GET', '/account');
  const passwordPrompt = (title, label, danger, action) => openForm({
    title, submitLabel: label, danger,
    fields: [{ name: 'password', label: 'Your password', type: 'password', required: true }],
    onSubmit: action,
  });
  const regenerate = () => passwordPrompt('New recovery codes', 'Generate new codes', false, async (v) => {
    const res = await api('POST', '/account/2fa/recovery-codes', v);
    render();
    setTimeout(() => showRecoveryCodes(res.recovery_codes), 50);
  });
  const disable = () => passwordPrompt('Disable two-factor authentication', 'Disable 2FA', true, async (v) => {
    await api('POST', '/account/2fa/disable', v);
    toast('Two-factor authentication disabled');
    render();
  });
  return h('div', {},
    pageHead('Security', acct.username),
    h('div', { class: 'grid two' },
      h('div', { class: 'card' },
        h('h2', {}, 'Two-factor authentication ', acct.totp_enabled ? badge('enabled', 'ok') : badge('disabled', 'warn')),
        h('p', { class: 'muted' }, acct.totp_enabled
          ? `Signing in to this panel requires a code from your authenticator app. ${acct.recovery_codes_left} recovery code(s) left.`
          : 'Protect this account with a one-time code from an authenticator app in addition to your password.'),
        acct.totp_enabled
          ? h('div', { class: 'toolbar' },
            h('button', { onclick: regenerate }, 'New recovery codes'),
            h('button', { class: 'danger', onclick: disable }, 'Disable'))
          : h('button', { class: 'primary', onclick: startTotpSetup }, 'Enable two-factor authentication'),
        state.me.role === 'user' ? h('p', { class: 'muted' },
          'Two-factor protects this panel. Mail and calendar apps sign in with your password or, better, an app password from "My account".') : null),
      h('div', { class: 'card' },
        h('h2', {}, 'Password'),
        h('p', { class: 'muted' }, 'Changing your password signs out every other session.'),
        h('button', { onclick: changePasswordForm }, 'Change password'))));
}

// ---------- mailbox self-service --------------------------------------

async function viewAccount() {
  const [m, appPasswords] = await Promise.all([api('GET', '/me'), api('GET', '/me/app-passwords')]);
  const c = m.client;
  const addAppPassword = () => openForm({
    title: 'New app password', submitLabel: 'Create',
    intro: 'Use a separate password for each app or device so you can revoke it on its own.',
    fields: [{ name: 'name', label: 'Name', required: true, placeholder: 'e.g. iPhone, Thunderbird at work' }],
    onSubmit: async (v) => {
      const res = await api('POST', '/me/app-passwords', v);
      render();
      setTimeout(() => infoDialog(`App password for "${res.name}"`,
        'Enter it in the app instead of your normal password. It will not be shown again.', copyField(res.password)), 50);
    },
  });
  const server = (label, s, extra) => [h('dt', {}, label), h('dd', {}, h('span', { class: 'mono' }, `${s.host}:${s.port}`), ' ', badge(s.security), extra || null)];
  return h('div', {},
    pageHead(m.username, m.name || null,
      h('a', { class: 'btn primary', href: '/webmail/', target: '_blank', rel: 'noopener' }, 'Open webmail ↗'),
      h('button', { onclick: changePasswordForm }, 'Change password')),
    h('div', { class: 'grid two' },
      h('div', { class: 'card' },
        h('h2', {}, 'Storage'),
        quotaCell(m.used_bytes, m.quota_mb),
        h('p', { class: 'muted' }, `${m.messages ?? '–'} messages`),
        h('h2', {}, 'Addresses that deliver here'),
        m.aliases.length ? h('ul', {}, m.aliases.map((a) => h('li', {}, a))) : h('p', { class: 'muted' }, 'No aliases.')),
      h('div', { class: 'card' },
        h('h2', {}, 'App settings'),
        h('dl', { class: 'kv' },
          h('dt', {}, 'Username'), h('dd', {}, h('span', { class: 'mono' }, m.username)),
          server('IMAP', c.imap),
          server('POP3', c.pop3),
          server('SMTP', c.smtp, [' or ', h('span', { class: 'mono' }, `${c.smtp.alt_port}`), ' ', badge(c.smtp.alt_security)]),
          server('Sieve', c.sieve),
          h('dt', {}, 'Calendar & contacts'), h('dd', {}, h('span', { class: 'mono' }, c.dav), ' ', badge('CalDAV / CardDAV'))),
        h('p', {}, h('a', { class: 'btn', href: '/api/v1/me/mobileconfig' }, 'Download iPhone / Mac profile')),
        h('p', { class: 'muted' }, 'The profile sets up mail, calendar and contacts. Thunderbird and Outlook configure mail automatically once the autoconfig DNS records exist.'))),
    h('div', { class: 'page-head section-head' },
      h('div', {}, h('h2', {}, 'App passwords'), h('div', { class: 'sub' }, 'Separate passwords for mail, calendar and contacts apps. They cannot sign in to this panel.')),
      h('div', { class: 'toolbar' }, h('button', { class: 'primary', onclick: addAppPassword }, '+ New app password'))),
    table([
      { label: 'Name', render: (p) => h('strong', {}, p.name) },
      { label: 'Created', render: (p) => fmtDate(p.created_at) },
      { label: '', class: 'actions', render: (p) => h('button', { class: 'small danger', onclick: () => confirmAction('Revoke ' + p.name,
        'Apps using this password will stop syncing.', 'Revoke',
        async () => { await api('DELETE', '/me/app-passwords/' + p.id); toast('App password revoked'); render(); }) }, 'Revoke') },
    ], appPasswords, 'No app passwords yet.'));
}

render();
