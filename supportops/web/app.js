import { icon } from './icons.js';
import {
  init,
  request,
  isDemo,
  config,
  account,
  authenticate,
  refreshAccount,
  signOut,
} from './adapter.js';

const $ = (s) => document.querySelector(s);
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
const statusNames = {
  new: 'New',
  processing: 'Processing',
  review: 'In review',
  manual: 'Needs an agent',
  approved: 'Approved',
  escalated: 'Escalated',
};
const categoryNames = {
  delivery: 'Delivery',
  cancellation: 'Cancellation',
  returns: 'Returns',
  damage: 'Damage',
  payment: 'Payment',
  other: 'General inquiry',
};
const reasonNames = {
  order_id_mismatch:
    'The order number in the message does not match the selected order. Please clarify it with the customer.',
  orders_unavailable: 'The orders API timed out. Retry analysis once the service recovers.',
  order_not_found: 'Order not found. Ask the customer to confirm the order number.',
  invalid_order_data: 'The orders API returned invalid data. Check the integration.',
  missing_order_id: 'No order number was provided. Ask the customer for it.',
  ownership_review: 'The customer name does not match the order. Verify order ownership.',
  template_unavailable:
    'The selected template is disabled. Ask an admin to enable it before retrying.',
  order_integration_not_connected:
    'This request needs live order or account data. Check it in your business system before replying.',
  missing_sources: 'Required current policies are missing. A verified draft cannot be prepared.',
  conflicting_documents:
    'Multiple policy versions are active. Resolve the conflict in the knowledge base.',
  invalid_model_output: 'The model returned invalid output twice. The draft has been blocked.',
  model_unavailable: 'The model provider is unavailable. Retry analysis later.',
  model_not_configured: 'The model or API key is missing. Check the server configuration.',
  damage_requires_review: 'Damaged items require a review of photographs and an agent’s decision.',
  payment_requires_review:
    'Payment details must be checked in the payment system. Order data alone is insufficient.',
  action_requires_review: 'This action needs separate approval from an agent.',
  return_eligibility_review: 'Verify the delivery date and return eligibility.',
  unsupported_intent:
    'This request is outside the available policies. An agent needs to review it.',
  untrusted_instructions:
    'The message attempts to override instructions. Manual review is required.',
  workflow_failed: 'Processing was interrupted. Check the service logs and retry analysis.',
  validation_failed: 'The reply failed required checks. An agent needs to review it.',
};
const stepNames = {
  classify: 'Classify the request',
  orders_api: 'Look up the order',
  retrieval: 'Retrieve current policies',
  generate: 'Compose the reply',
  validate: 'Validate facts and actions',
};
const state = {
  page: 'tickets',
  filter: 'all',
  query: '',
  selected: 'SO-1284',
  tab: 'draft',
  tickets: [],
  docs: [],
  templates: [],
  members: [],
  invitations: [],
  audit: [],
  metrics: {},
  evaluation: { status: 'not_run' },
  detail: null,
  busy: false,
  drafts: {},
  sort: 'newest',
  docQuery: '',
  showArchived: false,
};
let creationKey = null;
let modalTrigger = null;
const initials = (name) =>
  name
    .split(' ')
    .map((x) => x[0])
    .slice(0, 2)
    .join('');
const badge = (status) =>
  `<span class="badge badge-${esc(status)}">${icon(status === 'review' ? 'spark' : status === 'approved' ? 'check' : status === 'manual' || status === 'escalated' ? 'human' : status === 'processing' ? 'clock' : 'inbox')}${statusNames[status] || esc(status)}</span>`;
const fmtDate = (value, opts = { day: 'numeric', month: 'short' }) =>
  value ? new Date(value).toLocaleDateString('en-GB', opts) : '—';
const ms = (value) =>
  value == null ? '—' : value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
const channelIcon = (c) => (c === 'email' ? 'mail' : c === 'chat' ? 'chat' : 'globe');
const pages = {
  overview: 'Overview',
  tickets: 'Tickets',
  knowledge: 'Knowledge base',
  quality: 'Reply quality',
  monitoring: 'Monitoring',
  settings: 'Settings',
  templates: 'Reply templates',
  team: 'Team',
};

function toast(text, error = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (error ? ' error' : '');
  el.innerHTML = icon(error ? 'warning' : 'checkcircle') + `<span>${esc(text)}</span>`;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 5000);
}
function fail(e) {
  if (e.status === 401 && !isDemo) {
    refreshAccount().then(() => {
      resetWorkspace();
      closeModal();
      location.hash = 'login';
      render();
    });
  }

  const names = {
    revision_conflict: 'This ticket has changed. Refresh to see the latest version.',
    'Workspace changed. Refresh before continuing.':
      'This tab belongs to a different workspace. Reload the page before continuing.',
    analysis_in_progress: 'Analysis is already running. Please wait for the result.',
    fault_injection_disabled: 'Fault scenarios are disabled in the server configuration.',
  };
  toast(names[e.message] || e.message || 'The request could not be completed', true);
}
async function reload() {
  const [tickets, docs, metrics, evaluation, templates, members, invitations, audit] =
    await Promise.all([
      request('/tickets'),
      request('/knowledge'),
      request('/metrics'),
      request('/evaluation'),
      isDemo ? [] : request('/templates'),
      canManage() ? request('/members') : [],
      canManage() ? request('/invitations') : [],
      canManage() ? request('/audit') : [],
    ]);
  Object.assign(state, {
    tickets,
    docs,
    metrics,
    evaluation,
    templates,
    members: members.map((m) => ({ ...m, id: m.user_id })),
    invitations,
    audit,
  });
  if (!tickets.some((t) => t.id === state.selected)) state.selected = tickets[0]?.id;
  state.detail = state.selected ? await request('/tickets/' + state.selected) : null;
}
function sidebar() {
  return `<aside class="sidebar"><a class="brand" href="#tickets" aria-label="SupportOps AI · home"><span class="brand-mark">${icon('logo')}</span><span>SupportOps</span><em>AI</em></a><button class="workspace" data-action="switch-workspace"><span class="store-mark">${icon('leaf')}</span><div><strong>${esc(businessName())}</strong><small>${retailDemo() ? 'Sample workspace' : esc(account?.organization?.role || 'Choose workspace')}</small></div>${icon('down')}</button><p class="nav-label">WORKSPACE</p><nav>${[
    ['overview', 'grid'],
    ['tickets', 'inbox'],
    ['knowledge', 'book'],
    ['templates', 'file'],
    ...(canManage() ? [['team', 'human']] : []),
  ]
    .map(([p, i]) => nav(p, i))
    .join('')}<div class="nav-separator"></div><p class="nav-label">QUALITY & OPERATIONS</p>${[
    ['quality', 'shield'],
    ['monitoring', 'activity'],
  ]
    .map(([p, i]) => nav(p, i))
    .join(
      '',
    )}</nav><div class="sidebar-bottom">${nav('settings', 'settings')}<div class="system-note"><b>${icon('shield')}Human in the loop</b>Every reply gets a human review before it reaches a customer.<a href="#quality">How review works ${icon('arrow')}</a></div><div class="profile"><div class="avatar">${esc(initials(account.user.name))}</div><div><strong>${esc(account.user.name)}</strong><small>${esc(account.organization?.role || 'Account')}</small></div>${!isDemo ? `<button class="icon-btn" data-action="logout" aria-label="Sign out" title="Sign out">${icon('external')}</button>` : ''}</div></div></aside>`;
}
function nav(p, i) {
  return `<a href="#${p}" class="nav-item ${state.page === p ? 'active' : ''}" aria-label="${pages[p]}" ${state.page === p ? 'aria-current="page"' : ''}>${icon(i)}<span>${pages[p]}</span>${p === 'tickets' ? `<b class="nav-count">${state.tickets.filter((t) => !['approved', 'escalated'].includes(t.status)).length}</b>` : ''}</a>`;
}
function render() {
  if (!account) {
    $('#app').innerHTML = publicPage();
    return;
  }
  document.title = `SupportOps AI · ${pages[state.page]}`;
  $('#app').innerHTML =
    `<div class="app-shell">${sidebar()}<main class="main"><header class="topbar"><div class="breadcrumbs"><span>Workspace</span>${icon('chevron')}<b>${pages[state.page]}</b></div><div class="top-actions"><span class="system-status"><i class="status-dot"></i>${isDemo ? 'Browser demo' : 'API connected'}</span><i class="top-divider"></i><button class="icon-btn" data-action="activity" aria-label="Recent activity">${icon('bell')}</button><div class="avatar" aria-label="${esc(account.user.name)}">${esc(initials(account.user.name))}</div></div></header>${isDemo ? `<div class="demo-banner"><span>${icon('info')}Interactive demo · synthetic data · changes are saved in this browser</span><button data-action="demo-info">About the demo ${icon('arrow')}</button></div>` : ''}<div class="content">${pageContent()}</div></main></div>`;
}
function heading(title, sub, actions = '') {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${sub}</p></div><div class="heading-actions">${actions}</div></div>`;
}
function metric(label, value, sub, ic, highlight = false, tag = '') {
  return `<div class="metric ${highlight ? 'metric-highlight' : ''}"><div class="metric-top"><span>${label}</span>${icon(ic)}</div><div class="metric-value"><strong>${value}</strong>${tag ? `<span class="mini-tag">${tag}</span>` : ''}</div><div class="metric-label">${sub}</div></div>`;
}
function metricsRow() {
  const m = state.metrics;
  return `<div class="metrics-row">${metric('Total tickets', m.total ?? 0, 'In this workspace', 'inbox')}${metric('Ready for review', m.review ?? 0, 'Drafts awaiting your decision', 'spark', true, 'AI + human')}${metric('Need attention', m.manual ?? 0, 'Manual reviews and escalations', 'human')}${metric('Preparation time · p50', ms(m.p50_ms), isDemo ? 'Demo: latency is not measured' : `Across ${m.analyzed ?? 0} analyzed ticket${m.analyzed === 1 ? '' : 's'}`, 'clock')}</div>`;
}
function pageContent() {
  switch (state.page) {
    case 'tickets':
      return ticketsPage();
    case 'knowledge':
      return knowledgePage();
    case 'quality':
      return qualityPage();
    case 'monitoring':
      return monitoringPage();
    case 'overview':
      return overviewPage();
    case 'settings':
      return isDemo ? settingsPage() : workspaceSettings();
    case 'templates':
      return templatesPage();
    case 'team':
      return teamPage();
  }
}
function ticketsPage() {
  return `${onboarding()}${heading('Tickets', 'A little less busywork. A lot more thoughtful support.', `<span class="date-label">${icon('clock')}${new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}</span><button class="btn btn-primary" data-action="new">${icon('plus')}New ticket</button>`)}${metricsRow()}<div class="queue-toolbar"><div class="filter-tabs" role="group" aria-label="Filter tickets">${[
    ['all', 'All tickets'],
    ['review', 'In review'],
    ['manual', 'Needs an agent'],
    ['approved', 'Resolved'],
  ]
    .map(
      ([id, label]) =>
        `<button class="filter-tab ${state.filter === id ? 'active' : ''}" data-filter="${id}" aria-pressed="${state.filter === id}">${label}<span>${filteredByStatus(id).length}</span></button>`,
    )
    .join(
      '',
    )}</div><div class="toolbar-right"><label class="search-wrap">${icon('search')}<input id="ticket-search" aria-label="Search tickets" placeholder="Search tickets…" value="${esc(state.query)}"><kbd>⌘ K</kbd></label><button class="btn" data-action="sort" title="Change sort order">${icon('filter')}<span>${state.sort === 'newest' ? 'Newest' : 'Priority'}</span></button></div></div><section class="workbench" aria-label="Ticket workspace"><div class="ticket-list"><div class="list-heading"><b>Ticket queue</b>${icon('down')}</div><div id="ticket-items">${ticketItems()}</div></div><div class="ticket-detail" id="ticket-detail">${detailContent()}</div></section><div class="bottom-line"><span><i class="status-dot"></i>${esc(businessName())} · ${retailDemo() ? 'Synthetic sample data' : 'Your workspace'}</span><span><kbd>N</kbd> New ticket <kbd>⌘ K</kbd> Search</span></div>`;
}
function filteredByStatus(id) {
  return state.tickets.filter(
    (t) =>
      id === 'all' ||
      (id === 'manual'
        ? ['manual', 'escalated'].includes(t.status)
        : id === 'approved'
          ? t.status === 'approved'
          : t.status === id),
  );
}
function filteredTickets() {
  const q = state.query.toLowerCase();
  let rows = filteredByStatus(state.filter).filter((t) =>
    [t.id, t.customer, t.subject, t.message, t.order_id].some((v) =>
      String(v || '')
        .toLowerCase()
        .includes(q),
    ),
  );
  if (state.sort === 'priority')
    rows.sort((a, b) => (b.priority === 'high') - (a.priority === 'high'));
  return rows;
}
function ticketItems() {
  const rows = filteredTickets();
  return rows.length
    ? rows
        .map(
          (t, i) =>
            `<button class="ticket-item ${t.id === state.selected ? 'selected' : ''}" data-ticket="${esc(t.id)}" aria-label="${esc(t.subject)} · ${esc(t.customer)}" aria-pressed="${t.id === state.selected}"><div class="ticket-item-top"><div class="avatar" style="background:${['#eee8df', '#e7edde', '#e2e9ee', '#ece4e7'][state.tickets.indexOf(t) % 4]}">${esc(initials(t.customer))}</div><span class="ticket-name">${esc(t.customer)}</span><time class="ticket-time">${new Date(t.created_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</time></div><div class="ticket-subject">${esc(t.subject)}</div><div class="ticket-snippet">${esc(t.message)}</div><div class="ticket-item-bottom">${badge(t.status)}<span class="ticket-channel">${icon(channelIcon(t.channel))}${esc(t.id)}</span></div></button>`,
        )
        .join('')
    : `<div class="empty">${icon('search')}No tickets found<button class="btn" data-action="clear-search">Clear filters</button></div>`;
}
function detailContent() {
  const t = state.detail;
  if (!t) return `<div class="empty">Select a ticket from the queue</div>`;
  const r = t.result;
  return `<div class="detail-top"><div class="ticket-id">${esc(t.id)}<span>•</span>${icon(channelIcon(t.channel))}${{ email: 'Email', chat: 'Chat', web: 'Web form' }[t.channel]}</div><div class="detail-top-right">${t.priority === 'high' ? '<span class="priority"><i class="priority-dot"></i>High priority</span>' : ''}<button class="icon-btn" data-action="copy-id" title="Copy ticket ID" aria-label="Copy ticket ID">${icon('copy')}</button></div></div><div class="detail-grid"><div class="conversation"><h2 class="ticket-title">${esc(t.subject)}</h2><div class="category-line">${(r?.categories?.length ? r.categories : [t.category]).map((c) => `<span class="category-tag">${categoryNames[c] || esc(c)}</span>`).join('')}${t.order_id ? `<span class="category-tag">Order #${esc(t.order_id)}</span>` : ''}</div><article class="message-card"><div class="message-head"><div class="avatar">${esc(initials(t.customer))}</div><div><strong>${esc(t.customer)}</strong><small>${esc(t.email || 'Store customer')}</small></div><time class="message-time">${fmtDate(t.created_at)}</time></div><p>${esc(t.message).replace(/\n/g, '<br>')}</p></article><section class="ai-panel"><div class="ai-heading"><div class="ai-heading-title">${icon('spark')}SupportOps assistant</div><span class="badge">${isDemo ? 'Demo' : config.provider === 'baseline' ? 'Verified baseline' : 'LLM + validation'}</span></div>${state.busy ? `<div class="loading-line"></div><div class="empty">${icon('refresh', 'spinner')}Checking the order and gathering sources…</div>` : !r ? emptyDraft() : analysisContent(t, r)}</section>${reviewActions(t)}<p class="review-caption">${icon('shield')}Replies are never sent to customers automatically</p></div>${contextPanel(t, r)}</div>`;
}
function emptyDraft() {
  return `<div class="empty"><div class="waiting-illustration">${icon('spark')}</div><strong>From a question to a grounded reply</strong>Check the order, find the right policies<br>and prepare a draft with clear sources.<br><button class="btn btn-primary" data-action="analyze">${icon('spark')}Prepare a reply</button></div>`;
}
function analysisContent(t, r) {
  return `<div class="detail-tabs" role="tablist" aria-label="Analysis results">${[
    ['draft', 'Reply'],
    ['sources', 'Sources'],
    ['workflow', 'Workflow'],
    ['history', 'History'],
  ]
    .map(
      ([id, label]) =>
        `<button role="tab" aria-label="${label}" aria-selected="${state.tab === id}" class="detail-tab ${state.tab === id ? 'active' : ''}" data-tab="${id}">${label}${id === 'sources' ? `<span>${r.sources?.length || 0}</span>` : ''}</button>`,
    )
    .join(
      '',
    )}</div>${state.tab === 'draft' ? draftContent(t, r) : state.tab === 'sources' ? sourcesContent(r) : state.tab === 'workflow' ? workflowContent(r) : historyContent(t)}`;
}
function draftContent(t, r) {
  if (r.reason)
    return `<div class="manual-box">${icon('human')}<b>An agent needs to take a look</b><p>${esc(reasonNames[r.reason] || r.reason)}</p><p style="margin-top:10px;font-size:10px">No verified draft was created. Available context and sources are preserved in this ticket.</p>${!['approved', 'escalated'].includes(t.status) ? `<button class="btn" data-action="analyze">${icon('refresh')}Retry analysis</button>` : ''}</div>`;
  const latest = (t.reviews || []).findLast?.((x) => x.decision === 'approve');
  const text = state.drafts[t.id] ?? latest?.text ?? r.draft;
  return `<div class="draft-body"><div class="verified-note">${icon('checkcircle')}Facts and policies verified · ready for your review</div>${state.drafts[t.id] && state.drafts[t.id] !== r.draft ? '<div class="revision-note">This draft was edited by an agent. Review any changed facts before approving.</div>' : ''}<label class="sr-only" for="draft-text">Reply draft</label><textarea id="draft-text" class="draft-text" ${t.status === 'approved' || t.status === 'escalated' ? 'readonly' : ''}>${esc(text || '')}</textarea><div class="draft-footer"><span>${icon('clock')}${ms(r.duration_ms)}${isDemo ? ' · simulated' : ''}</span><span>${t.status === 'approved' ? 'Approved by an agent' : 'Edit before approving'}<button class="icon-btn" data-action="copy-draft" aria-label="Copy reply" title="Copy reply">${icon('copy')}</button></span></div></div><div class="sources-inline">Sources${(
    r.sources || []
  )
    .slice(0, 3)
    .map(
      (d, i) =>
        `<button class="source-chip" data-doc="${esc(d.id)}">${icon('file')}${i + 1}. ${esc(d.title)}</button>`,
    )
    .join(
      '',
    )}${r.order ? `<span class="source-chip">${icon('box')}Order API #${esc(r.order.id)}</span>` : ''}</div>`;
}
function sourcesContent(r) {
  return `<div class="draft-body">${r.order ? `<div class="source-card" data-action="order-info"><h4>${icon('box')} Store API · order #${esc(r.order.id)}</h4><p>Order status, carrier, tracking number and items were retrieved from ${isDemo ? 'the synthetic dataset' : 'Orders API'}.</p><div class="source-meta"><span>Authoritative source for order status</span></div></div>` : ''}${(r.sources || []).map((d) => `<button class="source-card" data-doc="${esc(d.id)}" style="display:block;width:100%;text-align:left"><h4>${icon('file')} ${esc(d.title)}</h4><p>${esc(d.text)}</p><div class="source-meta"><span>v${esc(d.version)}</span><span>From ${fmtDate(d.effective_from)}</span><span>${icon('external')} Open</span></div></button>`).join('') || '<div class="empty">No sources found yet</div>'}</div>`;
}
function workflowContent(r) {
  return `<div class="draft-body">${(r.steps || []).map((s, i) => `<div class="workflow-node">${icon(s.status === 'error' ? 'warning' : 'checkcircle')}<span>${s.name === 'orders_api' && !retailDemo() ? 'Check workspace context' : stepNames[s.name] || esc(s.name)}</span><small>${ms(s.duration_ms)}</small></div>`).join('')}<div class="workflow-node">${icon('human')}<span>Human review</span><small>${['approved', 'escalated'].includes(state.detail.status) ? 'Completed' : 'Pending'}</small></div><p class="context-note">${isDemo ? 'Workflow steps are simulated in the browser. These are not server traces.' : 'Workflow state is saved in the persistent LangGraph checkpointer.'}</p><pre class="code-block">${esc(JSON.stringify(r.manifest || {}, null, 2))}</pre><button class="btn" data-action="export-trace">${icon('download')}Download result JSON</button></div>`;
}
function historyContent(t) {
  return `<div class="draft-body"><div class="history-event">Ticket created<small>${fmtDate(t.created_at, { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}</small></div>${t.result ? `<div class="history-event">${t.result.reason ? 'Sent for manual review' : 'Draft prepared'}<small>Revision ${t.revision} · ${esc(t.result.manifest?.model || 'workflow')}</small></div>` : ''}${(t.reviews || []).map((r) => `<div class="history-event">${r.decision === 'approve' ? 'Reply approved' : 'Ticket escalated'}<small>${fmtDate(r.created_at, { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })} · ${esc(r.actor || 'Agent')}</small>${r.note ? `<p>${esc(r.note)}</p>` : ''}</div>`).join('')}</div>`;
}
function reviewActions(t) {
  if (t.status === 'approved')
    return `<div class="completed-box">${icon('checkcircle')}Reply approved. You can copy it to send to the customer.</div>`;
  if (t.status === 'escalated')
    return `<div class="completed-box">${icon('human')}Ticket escalated. The decision is saved in its history.</div>`;
  if (!t.result || state.busy) return '';
  return `<div class="review-actions">${t.status === 'review' ? `<button class="btn btn-primary" data-action="approve">${icon('check')}Approve reply</button>` : ''}<button class="btn" data-action="escalate">${icon('human')}Escalate to an agent</button>${config.faults_enabled && retailDemo() ? `<button class="icon-btn" data-action="faults" title="Test a failure scenario" aria-label="Test a failure scenario">${icon('code')}</button>` : ''}</div>`;
}
function contextPanel(t, r) {
  if (!retailDemo())
    return `<aside class="context-panel"><div class="context-section"><h3 class="context-heading">${icon('human')}Customer</h3><div class="context-customer"><div class="avatar">${esc(initials(t.customer))}</div><div><strong>${esc(t.customer)}</strong><small>${esc(t.email || 'No contact provided')}</small></div></div><div class="kv"><span>Channel</span><strong>${esc(t.channel)}</strong></div></div><div class="context-section"><h3 class="context-heading">${icon('book')}Business knowledge</h3><strong>${esc(businessName())}</strong><p class="context-note">${r?.sources?.length || 0} relevant policy excerpts${r?.template ? ' · template revision ' + r.template.revision : ''}</p><p class="context-note">Replies use this workspace’s current policies and templates. Check the sources before approving.</p></div><div class="context-section"><h3 class="context-heading">${icon('shield')}Original draft checks</h3>${r?.checks?.length ? `<div class="check-list">${r.checks.map((c) => `<div class="check-item ${c.passed ? '' : 'failed'}">${icon(c.passed ? 'checkcircle' : 'warning')}<span>${esc(c.label)}</span></div>`).join('')}</div>` : '<p class="context-note">Checks run when you prepare a reply.</p>'}${r?.trace_id ? `<button class="trace-link" data-action="export-trace">${icon('activity')}View analysis record ${icon('external')}</button>` : ''}</div></aside>`;

  const o = r?.order;
  const statuses = {
    delayed: 'Delivery delayed',
    shipped: 'In transit',
    processing: 'Processing',
    delivered: 'Delivered',
    cancelled: 'Cancelled',
  };
  return `<aside class="context-panel"><div class="context-section"><h3 class="context-heading">${icon('box')}Order details</h3>${o ? `<div class="order-title">#${esc(o.id)}<button class="icon-btn" data-action="order-info" aria-label="View order data">${icon('external')}</button></div><span class="badge ${o.status === 'delayed' ? 'badge-manual' : 'badge-review'}">${icon('truck')}${statuses[o.status]}</span>${o.items.map((item) => `<div class="order-product"><div class="product-icon">${icon('box')}</div><div><b>${esc(item.title)}</b><small>${item.quantity} item(s) · ${esc(businessName())}</small></div></div>`).join('')}<div class="kv"><span>Order total</span><strong>${o.total.toLocaleString('en-GB')} ₽</strong></div><div class="kv"><span>Order placed</span><strong>${fmtDate(o.placed_at)}</strong></div><div class="kv"><span>Expected delivery</span><strong>${fmtDate(o.expected_at)}</strong></div><div class="kv"><span>Carrier</span><strong>${esc(o.carrier)}</strong></div>${o.tracking ? `<div class="kv"><span>Tracking number</span><strong>${esc(o.tracking)}</strong></div>` : ''}<p class="context-note">${icon('link')} ${isDemo ? 'Synthetic store data' : 'Retrieved from the store API'}</p>` : `<div class="context-note">${t.order_id ? `Order #${esc(t.order_id)}` : 'No order number provided'}<br>${retailDemo() ? 'Order details will appear after analysis.' : 'No live order integration is connected. Your policies are used as the source of truth.'}</div>`}</div><div class="context-section"><h3 class="context-heading">${icon('human')}Customer</h3><div class="context-customer"><div class="avatar">${esc(initials(t.customer))}</div><div><strong>${esc(t.customer)}</strong><small>${esc(t.email || 'No contact provided')}</small></div></div><div class="kv"><span>Channel</span><strong>${{ email: 'Email', chat: 'Chat', web: 'Web form' }[t.channel]}</strong></div><div class="kv"><span>Business</span><strong>${esc(businessName())}</strong></div></div><div class="context-section"><h3 class="context-heading">${icon('shield')}Original draft checks</h3>${r?.checks?.length ? `<div class="check-list">${r.checks.map((c) => `<div class="check-item ${c.passed ? '' : 'failed'}">${icon(c.passed ? 'checkcircle' : 'warning')}<span>${esc(c.label)}</span></div>`).join('')}</div>` : '<p class="context-note">Checks run when you prepare a reply.</p>'}${r?.trace_id ? `<button class="trace-link" data-action="export-trace">${icon('activity')}<code>trace: ${esc(r.trace_id.slice(0, 16))}…</code>${icon('external')}</button>` : ''}</div></aside>`;
}

function overviewPage() {
  const m = state.metrics;
  return `${heading('Your support, at a glance', 'A clear view of your queue, reply quality and operations.', `<button class="btn btn-primary" data-action="new">${icon('plus')}New ticket</button>`)}${metricsRow()}<div class="section-grid"><section class="panel"><h2>Queue by status</h2><p>A live snapshot of your ticket workspace</p>${[
    'new',
    'review',
    'manual',
    'approved',
    'escalated',
  ]
    .map((status) => {
      let count = state.tickets.filter((t) => t.status === status).length;
      return `<div class="bar-row"><span>${statusNames[status]}</span><div class="bar-track"><div class="bar-fill" style="width:${m.total ? (count / m.total) * 100 : 0}%"></div></div><b>${count}</b></div>`;
    })
    .join(
      '',
    )}</section><section class="panel"><h2>From question to resolution</h2><p>Every step is visible and explainable</p>${[
    ['inbox', 'Receive a ticket', 'Validate input and prevent duplicate requests'],
    ['box', 'Gather context', 'Order data from the API and current store policies'],
    ['spark', 'Prepare a reply', 'Verified facts and permitted next steps only'],
    ['human', 'Review with a person', 'Edit, approve or escalate'],
  ]
    .map(
      ([ic, title, sub]) =>
        `<div class="workflow-node">${icon(ic)}<span>${title}<small style="display:block;margin-top:4px">${sub}</small></span></div>`,
    )
    .join(
      '',
    )}</section></div><section class="panel"><h2>Need attention</h2><p>Tickets waiting for an agent’s decision</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Ticket</th><th>Customer</th><th>Status</th><th></th></tr></thead><tbody>${state.tickets
    .filter((t) => !['approved', 'escalated'].includes(t.status))
    .slice(0, 5)
    .map(
      (t) =>
        `<tr><td><strong>${esc(t.subject)}</strong><br><code>${esc(t.id)}</code></td><td>${esc(t.customer)}</td><td>${badge(t.status)}</td><td><button class="btn" data-open-ticket="${esc(t.id)}">Open ${icon('arrow')}</button></td></tr>`,
    )
    .join('')}</tbody></table></div></section>`;
}
function knowledgePage() {
  const day = new Date().toISOString().slice(0, 10);
  const active = (d) =>
    d.effective_from <= day && (!d.effective_to || d.effective_to > day) && !d.archived;
  const docs = state.docs.filter(
    (d) =>
      (state.showArchived || active(d)) &&
      [d.title, d.text].some((v) => v.toLowerCase().includes(state.docQuery.toLowerCase())),
  );
  return `${heading('Knowledge base', 'Your business knowledge, with clear sources, versions and effective dates.', `<button class="btn" data-action="export-knowledge">${icon('download')}Export JSON</button>${canManage() ? `<button class="btn" data-action="upload-policy">${icon('download')}Upload file</button><button class="btn btn-primary" data-action="add-policy">${icon('plus')}Add policy</button>` : ''}`)}<div class="info-strip">${icon('shield')}Only current, unarchived policies from this workspace are used in replies. Earlier versions remain available for reference.</div><div class="queue-toolbar"><div class="filter-tabs"><button class="filter-tab ${!state.showArchived ? 'active' : ''}" data-archive="false">Current policies <span>${state.docs.filter(active).length}</span></button><button class="filter-tab ${state.showArchived ? 'active' : ''}" data-archive="true">All versions <span>${state.docs.length}</span></button></div><label class="search-wrap">${icon('search')}<input id="doc-search" value="${esc(state.docQuery)}" placeholder="Search policies…" aria-label="Search the knowledge base"></label></div><div id="document-cards" class="document-grid">${docs.map((d) => `<button class="document-card" data-doc="${esc(d.id)}"><span class="document-icon">${icon('file')}</span><h3>${esc(d.title)}</h3><p>${esc(d.text)}</p><div class="source-meta"><span>v${esc(d.version)}</span><span>From ${fmtDate(d.effective_from)}</span><span class="badge ${active(d) ? 'badge-review' : ''}">${active(d) ? 'Active' : 'Archived'}</span></div></button>`).join('') || '<div class="empty">No policies found</div>'}</div>`;
}
function qualityPage() {
  if (!retailDemo())
    return `${heading('Reply quality', 'Evidence and human judgment, on every customer reply.')}<div class="section-grid"><section class="panel"><h2>Your review process</h2><div class="check-list">${['Only this workspace’s current policies are retrieved', 'Verified excerpts are inserted into your reply template', 'Missing evidence routes a request to an agent', 'An employee reviews and edits every reply', 'Approved replies retain their sources and template version'].map((c) => `<div class="check-item">${icon('checkcircle')}<span>${c}</span></div>`).join('')}</div></section><section class="panel"><h2>Business evaluation</h2><p>No business-specific evaluation dataset is configured yet.</p><div class="notice-card">${icon('info')}Build a set of representative customer questions with expected answers before assessing accuracy for your business. The sample retail benchmark does not measure your policies.</div></section></div>`;
  const e = state.evaluation;
  const ready = e.status === 'passed' || e.status === 'failed';
  return `${heading('AI Release Guard', 'Evaluate changes before they ship. Release with evidence.', `<button class="btn" data-action="export-eval">${icon('download')}Download report</button>`)}<div class="info-strip">${icon('info')}${isDemo ? 'A snapshot of local evaluation results. The demo does not call a live model.' : 'Evaluation runs separately and gates the CI build.'}</div>${ready ? `<div class="metrics-row">${metric('Factual accuracy', `${Math.round(e.metrics.factual_accuracy * 100)}%`, 'Checks for order status, facts and sources', 'checkcircle', true)}${metric('Classification · macro-F1', e.metrics.macro_f1.toFixed(2), 'Against hand-labeled synthetic cases', 'inbox')}${metric('Escalation recall', `${Math.round(e.metrics.escalation_recall * 100)}%`, 'Requests that must reach a human', 'human')}${metric('Latency · p95', ms(e.metrics.p95_ms), `${e.cases} cases · sequential execution`, 'clock')}</div><div class="section-grid"><section class="panel"><h2>Release evaluation</h2><p>Dataset ${esc(e.dataset_version)} · ${esc(e.split)}</p><div class="quality-score"><div class="score-ring"><b>${e.passed}/${e.cases}</b><small>cases passed</small></div><div><h3>${e.status === 'passed' ? 'All checks passed' : 'Release blocked'}</h3><p>Model: ${esc(e.model)}<br>${fmtDate(e.created_at, { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' })}<br>${e.live ? 'Live run with an external model' : 'Deterministic baseline · no LLM spend'}</p></div></div><button class="btn" data-action="eval-info">${icon('terminal')}Run the evaluation</button></section><section class="panel"><h2>What the guard checks</h2><p>A failed critical check blocks the pipeline</p><div class="check-list">${['Current return policies are applied', 'Shipped orders are never automatically cancelled', 'API outages route tickets to an agent', 'The model cannot add unsupported facts', 'Repeated approvals do not create duplicates'].map((c) => `<div class="check-item">${icon('checkcircle')}<span>${c}</span></div>`).join('')}</div><div class="notice-card">${icon('info')}These metrics cover the synthetic dataset. Agent productivity and live-model quality require a separate study.</div></section></div><section class="panel"><h2>Scenario results</h2><p>Isolated ${esc(e.split)} dataset · seed ${e.seed}</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Scenario</th><th>Expected category</th><th>Result</th><th>Time</th></tr></thead><tbody>${(e.results || []).map((r) => `<tr><td>${esc(r.id)}<br><code>${esc(r.description)}</code></td><td>${(r.expected_categories || []).map((c) => categoryNames[c]).join(', ')}</td><td><span class="badge ${r.passed ? 'badge-review' : 'badge-manual'}">${icon(r.passed ? 'check' : 'warning')}${r.passed ? 'Passed' : 'Failed'}</span></td><td>${ms(r.duration_ms)}</td></tr>`).join('')}</tbody></table></div></section>` : `<section class="panel"><div class="empty">${icon('shield')}<strong>No evaluation run yet</strong>Run evaluation to generate a report.<pre class="code-block">python -m eval.run --split holdout</pre><button class="btn" data-action="refresh">${icon('refresh')}Refresh</button></div></section>`}`;
}
function monitoringPage() {
  const m = state.metrics;
  return `${heading('Monitoring', 'Follow every ticket through the workflow and understand what went wrong.', `<button class="btn" data-action="refresh">${icon('refresh')}Refresh</button>`)}${isDemo ? `<div class="info-strip">${icon('info')}The demo shows workflow structure. Real timings and traces are available in the local application.</div>` : ''}<div class="metrics-row">${metric('Analyzed tickets', m.analyzed ?? 0, 'Including manual review', 'activity')}${metric('Latency · p95', ms(m.p95_ms), 'Sequential runs in this environment', 'clock')}${metric('LLM tokens', (m.tokens ?? 0).toLocaleString('en-GB'), config.provider === 'baseline' ? 'The baseline does not call an LLM' : 'Input and output tokens', 'spark')}${metric('LLM spend', `$${(m.cost_usd || 0).toFixed(4)}`, 'Infrastructure costs are separate', 'grid')}</div><section class="panel"><h2>Recent workflow runs</h2><p>One trace ID connects every step of a run</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Ticket / trace</th><th>Status</th><th>Reason / model</th><th>Latency</th><th></th></tr></thead><tbody>${(m.runs || []).map((r) => `<tr><td><strong>${esc(r.ticket_id)}</strong><br><code>${esc(r.trace_id?.slice(0, 18) || '—')}</code></td><td>${badge(r.status)}</td><td style="max-width:330px">${esc(r.reason ? reasonNames[r.reason] || r.reason : r.manifest?.model || '—')}</td><td>${ms(r.duration_ms)}</td><td><button class="btn" data-open-ticket="${esc(r.ticket_id)}" data-workflow="true">Trace ${icon('arrow')}</button></td></tr>`).join('') || '<tr><td colspan="5">Analyze a ticket to see its workflow here.</td></tr>'}</tbody></table></div></section><div class="section-grid" ${!retailDemo() ? 'hidden' : ''}><section class="panel"><h2>Test failure handling</h2><p>Controlled scenarios on a new synthetic ticket</p>${[
    ['orders_timeout', 'Orders API timeout', 'Bounded timeout → manual review'],
    ['invalid_model', 'Invalid model output', 'Two attempts → safe handoff'],
    ['missing_sources', 'Missing required policies', 'No sources → no draft'],
  ]
    .map(
      ([id, title, sub]) =>
        `<div class="setting-row"><div><h3>${title}</h3><p>${sub}</p></div><button class="btn" data-fault="${id}" ${!config.faults_enabled || !retailDemo() ? 'disabled' : ''}>${icon('play')}Run</button></div>`,
    )
    .join(
      '',
    )}</section><section class="panel"><h2>Diagnose and recover</h2><p>Local and cloud environments</p><div class="modal-prose">OpenTelemetry connects the API, retrieval, generation and validation. Configure export through OTLP.<br><br>Restore the failed dependency and retry analysis. Approved tickets are protected against repeat processing.</div><button class="btn" data-action="runbook" style="margin-top:18px">${icon('book')}Open the runbook</button></section></div>`;
}
function settingsPage() {
  return `${heading('Sample settings', 'An offline walkthrough with synthetic data.')}<section class="panel"><h2>Northstar Goods</h2><p>This standalone sample saves changes in this browser. To register a business, upload policies or invite a team, run the full SupportOps API.</p><button class="btn" data-action="reset-demo">Reset sample data</button></section>`;
}

const canManage = () => !isDemo && ['owner', 'admin'].includes(account?.organization?.role);
const businessName = () => account?.organization?.name || 'Your business';
const retailDemo = () => isDemo || account?.organization?.is_demo;
function resetWorkspace() {
  Object.assign(state, {
    tickets: [],
    docs: [],
    templates: [],
    members: [],
    invitations: [],
    audit: [],
    metrics: {},
    evaluation: {},
    detail: null,
    selected: null,
    drafts: {},
    filter: 'all',
    query: '',
    page: 'tickets',
    docQuery: '',
    showArchived: false,
  });
}
function publicPage() {
  const join = location.hash.startsWith('#join/');
  const mode = join ? 'join' : location.hash === '#login' ? 'login' : 'register';
  document.title = 'SupportOps AI · Support that knows your business';
  return `<div class="public-shell"><header class="public-header"><a class="brand" href="#register"><span class="brand-mark">${icon('logo')}</span><span>SupportOps</span><em>AI</em></a><a class="btn" href="${mode === 'login' ? '#register' : '#login'}">${mode === 'login' ? 'Create a workspace' : 'Sign in'} ${icon('arrow')}</a></header><main class="welcome-grid"><section class="welcome-story"><span class="eyebrow">YOUR BUSINESS. YOUR KNOWLEDGE.</span><h1>Great support starts <br>with knowing<br><em>your business.</em></h1><p>One workspace for your policies, your people and thoughtful customer replies. Built around the way your team works.</p><div class="welcome-steps">${[
    ['01', 'Bring your knowledge', 'Upload your rules, procedures and service policies.'],
    ['02', 'Make it sound like you', 'Create reply templates that match your brand.'],
    ['03', 'Work better, together', 'Invite your team. Review every reply before sharing.'],
  ]
    .map(([n, t, d]) => `<div><span>${n}</span><section><h3>${t}</h3><p>${d}</p></section></div>`)
    .join(
      '',
    )}</div><div class="welcome-proof">${icon('shield')}A separate workspace for every business.<br>Your team decides what reaches your customers.</div></section><section class="signup-card"><span class="badge badge-review">${icon(join ? 'human' : 'spark')}${join ? 'You’re invited' : mode === 'login' ? 'Welcome back' : 'A fresh start for your team'}</span><h2>${join ? 'Join your team' : mode === 'login' ? 'Sign in to your workspace' : 'Create your workspace'}</h2><p>${join ? 'Use the email address your invitation was sent to. Already have an account? Enter your existing password.' : mode === 'login' ? 'Your policies, tickets and team are right where you left them.' : 'Start with your business. Add your knowledge as you go.'}</p><form id="platform-auth" data-mode="${mode}">${mode !== 'login' ? '<label class="field">Your full name<input name="name" required minlength="2" maxlength="100" autocomplete="name" placeholder="Alex Morgan"></label>' : ''}${mode === 'register' ? '<label class="field">Business name<input name="business_name" required minlength="2" maxlength="120" autocomplete="organization" placeholder="Acme Services"></label><label class="field">Industry<input name="industry" maxlength="80" placeholder="e.g. Hospitality, consulting, retail" value=""></label>' : ''}<label class="field">Work email<input name="email" type="email" required maxlength="160" autocomplete="email" placeholder="alex@yourcompany.com"></label><label class="field">Password<input name="password" type="password" required minlength="${mode === 'login' ? 1 : 12}" maxlength="128" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" placeholder="${mode === 'login' ? 'Your password' : 'At least 12 characters'}"></label><div class="form-error" role="alert"></div><button class="btn btn-primary auth-submit" type="submit">${join ? 'Accept invitation' : mode === 'login' ? 'Sign in' : 'Create workspace'} ${icon('arrow')}</button></form>${config.demo_available && !join ? '<div class="auth-divider"><span>Want a look around first?</span></div><button class="btn auth-submit" data-action="start-demo">Explore a sample workspace</button><p class="auth-footnote">A separate demo with synthetic tickets. Your business starts with a clean workspace.</p>' : ''}<p class="auth-footnote">${join ? 'Your workspace owner sets your role and access.' : mode === 'login' ? 'Need access to a team? Ask your workspace owner for an invitation link.' : 'You’ll be the workspace owner. Invite your team whenever you’re ready.'}</p></section></main><footer class="public-footer"><span>SupportOps AI</span><span>Your knowledge. A human touch.</span></footer></div>`;
}
function onboarding() {
  if (retailDemo() || state.tickets.length || !canManage()) return '';
  return `<section class="onboarding"><div><span class="eyebrow">MAKE YOURSELF AT HOME</span><h2>Let’s set up ${esc(businessName())}</h2><p>Build a knowledge base your team can trust.</p></div><div class="onboarding-steps"><a href="#knowledge"><b>${state.docs.some((d) => !d.archived) ? '✓' : '1'}</b><span>Add your policies<small>Upload a file or write a rule</small></span>${icon('arrow')}</a><a href="#templates"><b>2</b><span>Set your voice<small>Customize reply templates</small></span>${icon('arrow')}</a><a href="#team"><b>3</b><span>Invite your people<small>Give your team access</small></span>${icon('arrow')}</a></div></section>`;
}
function templatesPage() {
  return `${heading('Reply templates', 'Your voice, with verified facts woven in.', canManage() ? '<button class="btn" data-action="import-templates">Import templates</button><button class="btn btn-primary" data-action="new-template">' + icon('plus') + 'New template</button>' : '')}<div class="info-strip">${icon('info')}Templates control the wording. The {{facts}} placeholder adds verified excerpts from your current policies.</div><div class="document-grid">${state.templates.map((t) => `<button class="document-card" data-edit-template="${esc(t.id)}"><span class="document-icon">${icon('file')}</span><h3>${esc(t.name)}</h3><p class="template-preview">${esc(t.body)}</p><div class="source-meta"><span>${esc(t.category)}</span><span>Revision ${t.revision}</span><span class="badge ${t.active ? 'badge-review' : ''}">${t.active ? 'Active' : 'Disabled'}</span></div></button>`).join('') || '<div class="empty">No templates yet. Create one or import your existing templates.</div>'}</div>`;
}
function teamPage() {
  if (!canManage())
    return `${heading('Team', 'Your workspace members.')}<section class="panel">Ask your workspace owner to manage team access.</section>`;
  const owner = account.organization.role === 'owner';
  return `${heading('Your team', 'Bring everyone onto the same page. Give each person the right access.', '<button class="btn btn-primary" data-action="invite">' + icon('plus') + 'Invite a teammate</button>')}<section class="panel"><h2>Members <span class="mini-tag">${state.members.filter((m) => m.active).length} active</span></h2><p>Owners manage the business. Admins maintain content and invite agents. Agents work on tickets.</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Person</th><th>Email</th><th>Role</th><th>Access</th><th></th></tr></thead><tbody>${state.members.map((m) => `<tr><td><strong>${esc(m.name)}</strong>${m.id === account.user.id ? ' <span class="mini-tag">You</span>' : ''}</td><td>${esc(m.email)}</td><td>${esc(m.role)}</td><td><span class="badge ${m.active ? 'badge-review' : ''}">${m.active ? 'Active' : 'Removed'}</span></td><td>${m.id !== account.user.id && m.role !== 'owner' && (owner || m.role !== 'admin') ? `<button class="btn" data-member="${esc(m.id)}">Manage</button>` : ''}</td></tr>`).join('')}</tbody></table></div></section><section class="panel"><h2>Invitations</h2><p>Share invitation links yourself. Invitations expire after 7 days and can be used once.</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Email</th><th>Role</th><th>Expires</th><th>Status</th><th></th></tr></thead><tbody>${state.invitations.map((i) => `<tr><td>${esc(i.email)}</td><td>${esc(i.role)}</td><td>${fmtDate(i.expires_at)}</td><td>${i.accepted ? 'Accepted' : i.revoked ? 'Revoked' : new Date(i.expires_at) < new Date() ? 'Expired' : 'Pending'}</td><td>${!i.accepted && !i.revoked && new Date(i.expires_at) > new Date() ? `<button class="btn" data-revoke="${esc(i.id)}">Revoke</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="5">No invitations yet.</td></tr>'}</tbody></table></div></section>`;
}
function workspaceSettings() {
  const org = account.organization;
  return `${heading('Workspace settings', 'A home for your business and your team.')}<section class="panel"><h2>${esc(businessName())}</h2><p>${esc(org?.role || 'No workspace')} access · ${esc(account.user.email)}</p>${org && canManage() ? `<form id="workspace-settings"><div class="form-grid"><label class="field">Business name<input name="name" value="${esc(org.name)}" required minlength="2" maxlength="120"></label><label class="field">Industry<input name="industry" value="${esc(org.industry || '')}" required maxlength="80"></label></div><button class="btn btn-primary">Save changes</button></form>` : '<p>Your owner or admin manages business settings.</p>'}</section><section class="panel"><h2>Your workspaces</h2><p>Each business has its own policies, templates, tickets and team.</p>${account.memberships.map((m) => `<div class="setting-row"><div><h3>${esc(m.name)}</h3><p>${esc(m.industry)} · ${esc(m.role)}${m.is_demo ? ' · Sample workspace' : ''}</p></div>${m.id === org?.id ? '<span class="badge badge-review">Current</span>' : `<button class="btn" data-switch="${esc(m.id)}">Switch workspace</button>`}</div>`).join('')}<button class="btn" data-action="${org?.is_demo ? 'register-business' : 'new-workspace'}" style="margin-top:18px">${icon('plus')}${org?.is_demo ? 'Register your business' : 'Create another business'}</button></section><section class="panel"><h2>How replies work</h2><p>Current policies are retrieved only from this workspace. Your selected template wraps the verified excerpts. An employee reviews every reply before copying it to a customer.</p><div class="notice-card">${icon('info')}Customer account and order systems are not connected to new workspaces. Requests that need live account facts require a human check. Approved replies are saved here; they are not sent automatically.</div></section>${canManage() ? `<section class="panel"><h2>Recent workspace activity</h2><div class="table-scroll"><table class="data-table"><thead><tr><th>Action</th><th>When</th><th>Details</th></tr></thead><tbody>${state.audit.map((a) => `<tr><td>${esc(a.action.replaceAll('_', ' '))}</td><td>${fmtDate(a.created_at)}</td><td><code>${esc(JSON.stringify(a.details))}</code></td></tr>`).join('') || '<tr><td colspan="3">Activity will appear as your team uses the workspace.</td></tr>'}</tbody></table></div></section>` : ''}`;
}
function policyEditor(source = null, upload = false) {
  modal(
    source ? 'Publish a new version' : upload ? 'Upload a policy' : 'Add a policy',
    'Only your workspace can use this content. Publish clear, factual rules your team can rely on.',
    `<form id="policy-form" data-upload="${upload}" data-key="${esc(source?.key || '')}"><div class="form-grid"><label class="field">Policy title<input name="title" required minlength="3" maxlength="180" value="${esc(source?.title || '')}" placeholder="Appointment cancellation policy"></label><label class="field">Category<input name="category" required maxlength="80" value="${esc(source?.category || 'other')}"></label></div><div class="form-grid"><label class="field">Version<input name="version" required maxlength="30" value="${source ? '' : '1'}" placeholder="e.g. 2"></label><label class="field">Effective from<input type="date" name="effective_from" required value="${new Date().toISOString().slice(0, 10)}"></label></div>${upload ? '<label class="field">Policy file<input name="file" type="file" required accept=".txt,.md,.pdf,.docx,.json"></label><p class="form-hint">TXT, Markdown, PDF, DOCX or JSON · up to 2 MB / 100,000 text characters. Scanned PDFs need text recognition before upload.</p>' : `<label class="field">Policy text<textarea name="text" required minlength="20" maxlength="100000" rows="9" placeholder="Customers may cancel appointments up to 48 hours before their scheduled time…">${esc(source?.text || '')}</textarea></label>`}<div class="notice-card">${icon('info')}${source ? 'A new version replaces the previous version from its effective date. Earlier ticket sources stay intact.' : 'Upload one policy per document. Include the subject in the text so related questions can find it.'}</div><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn" type="button" data-action="close-modal">Cancel</button><button class="btn btn-primary">Publish policy</button></div></form>`,
    true,
  );
}
function templateEditor(id) {
  const t = state.templates.find((t) => t.id === id);
  modal(
    t ? 'Edit reply template' : 'New reply template',
    'Use {{facts}} exactly once. Your verified policy excerpts will appear there.',
    `<form id="template-form" data-id="${esc(id || '')}"><div class="form-grid"><label class="field">Template name<input name="name" required minlength="3" maxlength="120" value="${esc(t?.name || '')}" placeholder="Friendly customer reply"></label><label class="field">Category<input name="category" required maxlength="80" value="${esc(t?.category || 'other')}"></label></div><label class="field">Reply body<textarea name="body" required maxlength="10000" rows="9" ${!canManage() ? 'readonly' : ''}>${esc(t?.body || 'Hi {{customer_name}},\n\n{{facts}}\n\nBest regards,\n{{agent_name}}\n{{business_name}}')}</textarea></label><p class="form-hint">Available variables: {{facts}}, {{customer_name}}, {{business_name}}, {{agent_name}}, {{subject}}, {{ticket_id}}. The “other” category is the default for all inquiries.</p><label class="field">Status<select name="active"><option value="true" ${t?.active !== false ? 'selected' : ''}>Active</option><option value="false" ${t?.active === false ? 'selected' : ''}>Disabled</option></select></label><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn" type="button" data-action="close-modal">Close</button>${canManage() ? '<button class="btn btn-primary">Save template</button>' : ''}</div></form>`,
    true,
  );
}
async function enteredWorkspace() {
  resetWorkspace();
  closeModal();
  history.replaceState(null, '', '#tickets');
  if (account.organization) await reload();
  else state.page = 'settings';
  render();
}
async function handlePlatformAction(el) {
  const action = el.dataset.action;
  if (el.dataset.editTemplate) {
    templateEditor(el.dataset.editTemplate);
    return true;
  }
  if (el.dataset.version) {
    policyEditor(state.docs.find((d) => d.id === el.dataset.version));
    return true;
  }
  if (el.dataset.archivePolicy) {
    await request(`/knowledge/${el.dataset.archivePolicy}/archive`, { method: 'POST' });
    closeModal();
    await reload();
    render();
    toast('Policy archived. Previous ticket sources are preserved.');
    return true;
  }
  if (el.dataset.switch) {
    await authenticate('/auth/switch', { organization_id: el.dataset.switch });
    await enteredWorkspace();
    return true;
  }
  if (el.dataset.revoke) {
    await request(`/invitations/${el.dataset.revoke}/revoke`, { method: 'POST' });
    await reload();
    render();
    toast('Invitation revoked');
    return true;
  }
  if (el.dataset.member) {
    const m = state.members.find((m) => m.id === el.dataset.member);
    modal(
      'Manage access',
      m.email,
      `<form id="member-form" data-id="${esc(m.id)}"><label class="field">Role<select name="role"><option value="agent" ${m.role === 'agent' ? 'selected' : ''}>Agent — work on tickets</option>${account.organization.role === 'owner' ? `<option value="admin" ${m.role === 'admin' ? 'selected' : ''}>Admin — manage content and agents</option>` : ''}</select></label><label class="field">Access<select name="active"><option value="true" ${m.active ? 'selected' : ''}>Active</option><option value="false" ${!m.active ? 'selected' : ''}>Remove workspace access</option></select></label><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn btn-primary">Save access</button></div></form>`,
    );
    return true;
  }
  switch (action) {
    case 'reload-page':
      location.reload();
      return true;
    case 'start-demo':
      el.disabled = true;
      try {
        await authenticate('/auth/demo');
        await enteredWorkspace();
      } finally {
        el.disabled = false;
      }
      return true;
    case 'register-business':
      await signOut();
      closeModal();
      resetWorkspace();
      location.hash = 'register';
      render();
      return true;
    case 'logout':
      await signOut();
      closeModal();
      resetWorkspace();
      location.hash = 'login';
      render();
      return true;
    case 'switch-workspace':
      state.page = 'settings';
      location.hash = 'settings';
      render();
      return true;
    case 'add-policy':
      policyEditor();
      return true;
    case 'upload-policy':
      policyEditor(null, true);
      return true;
    case 'new-template':
      templateEditor();
      return true;
    case 'import-templates':
      modal(
        'Import reply templates',
        'Import a JSON list of templates, or a text file containing one template.',
        '<form id="template-import"><label class="field">Template file<input name="file" type="file" accept=".json,.txt,.md,.docx,.pdf" required></label><p class="form-hint">JSON: [{"name":"Friendly reply","category":"other","body":"Hi {{customer_name}}, {{facts}}"}]. Up to 50 templates; {{facts}} is required in each.</p><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn btn-primary">Import templates</button></div></form>',
      );
      return true;
    case 'invite':
      modal(
        'Invite a teammate',
        'Create a personal link and share it directly with your teammate.',
        `<form id="invite-form"><label class="field">Teammate’s email<input name="email" type="email" required maxlength="160" placeholder="james@yourcompany.com"></label><label class="field">Role<select name="role"><option value="agent">Agent — work on tickets and review replies</option>${account.organization.role === 'owner' ? '<option value="admin">Admin — manage policies, templates and agents</option>' : ''}</select></label><div class="notice-card">${icon('link')}You’ll receive a one-time link. No email is sent automatically.</div><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn btn-primary">Create invitation link</button></div></form>`,
      );
      return true;
    case 'copy-invite':
      await navigator.clipboard.writeText($('#invite-link').value);
      toast('Invitation link copied');
      return true;
    case 'new-workspace':
      modal(
        'Create another workspace',
        'A separate business with its own team and knowledge.',
        '<form id="new-workspace-form"><label class="field">Business name<input name="name" required minlength="2" maxlength="120"></label><label class="field">Industry<input name="industry" required maxlength="80" value="General business"></label><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn btn-primary">Create workspace</button></div></form>',
      );
      return true;
  }
  return false;
}
async function handlePlatformSubmit(form) {
  const ids = [
    'platform-auth',
    'policy-form',
    'template-form',
    'template-import',
    'invite-form',
    'member-form',
    'workspace-settings',
    'new-workspace-form',
  ];
  if (!ids.includes(form.id)) return false;
  const button = form.querySelector('button[type="submit"],button.btn-primary');
  button.disabled = true;
  const error = form.querySelector('.form-error');
  if (error) error.textContent = '';
  try {
    const data = Object.fromEntries(new FormData(form));
    if (form.id === 'platform-auth') {
      const mode = form.dataset.mode;
      if (mode === 'register' && !data.industry) data.industry = 'General business';
      if (mode === 'join') data.token = decodeURIComponent(location.hash.slice(6));
      await authenticate(mode === 'join' ? '/auth/accept-invite' : `/auth/${mode}`, data);
      await enteredWorkspace();
      return true;
    }
    if (form.id === 'policy-form') {
      if (form.dataset.upload === 'true') {
        const multipart = new FormData(form);
        if (form.dataset.key) multipart.set('key', form.dataset.key);
        await request('/knowledge/upload', { method: 'POST', body: multipart });
      } else {
        if (form.dataset.key) data.key = form.dataset.key;
        await request('/knowledge', { method: 'POST', body: data });
      }
    }
    if (form.id === 'template-form') {
      data.active = data.active === 'true';
      const id = form.dataset.id;
      if (id) data.revision = state.templates.find((t) => t.id === id).revision;
      await request('/templates' + (id ? '/' + id : ''), {
        method: id ? 'PATCH' : 'POST',
        body: data,
      });
    }
    if (form.id === 'template-import')
      await request('/templates/upload', { method: 'POST', body: new FormData(form) });
    if (form.id === 'invite-form') {
      const invite = await request('/invitations', { method: 'POST', body: data });
      const url = location.origin + '/#join/' + invite.token;
      await reload();
      render();
      modal(
        'Your invitation is ready',
        'Share this link with ' + data.email + '. It expires in 7 days.',
        `<label class="field">Invitation link<input id="invite-link" readonly value="${esc(url)}"></label><div class="notice-card">${icon('shield')}This link is shown once. Copy it before closing. No email has been sent.</div><div class="modal-actions"><button class="btn" data-action="close-modal">Done</button><button class="btn btn-primary" data-action="copy-invite">${icon('copy')}Copy link</button></div>`,
      );
      return true;
    }
    if (form.id === 'member-form') {
      data.active = data.active === 'true';
      await request('/members/' + form.dataset.id, { method: 'PATCH', body: data });
    }
    if (form.id === 'workspace-settings') {
      await request('/organization', { method: 'PATCH', body: data });
      await refreshAccount();
    }
    if (form.id === 'new-workspace-form') {
      await authenticate('/organizations', data);
      await enteredWorkspace();
      return true;
    }
    closeModal();
    await reload();
    render();
    toast('Changes saved');
  } catch (e) {
    if (error) error.textContent = e.message;
    else fail(e);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
  return true;
}

function modal(title, subtitle, body, wide = false) {
  modalTrigger = document.activeElement;
  document.querySelector('#app').inert = true;
  $('#overlay').innerHTML =
    `<div class="modal-backdrop"><section class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="modal-head"><h2>${esc(title)}</h2><button class="icon-btn" data-action="close-modal" aria-label="Close">${icon('x')}</button></div><p>${esc(subtitle)}</p>${body}</section></div>`;
  setTimeout(() => $('#overlay input, #overlay textarea, #overlay button')?.focus(), 30);
}
function closeModal() {
  $('#overlay').innerHTML = '';
  document.querySelector('#app').inert = false;
  if (modalTrigger?.isConnected) modalTrigger.focus({ preventScroll: true });
}
function newTicket() {
  creationKey = crypto.randomUUID();
  modal(
    'New ticket',
    'Create a customer request for ' + businessName() + '.',
    `<form id="ticket-form"><div class="form-grid"><label class="field">Customer name<input name="customer" required minlength="2" maxlength="100" placeholder="Emma Wilson"></label><label class="field">Email<input name="email" type="email" placeholder="customer@example.test" maxlength="160"></label></div><div class="form-grid">${retailDemo() ? `<label class="field">Order number<input name="order_id" inputmode="numeric" pattern="[0-9]{4,12}" placeholder="1042"></label>` : '<input name="order_id" type="hidden" value="">'}<label class="field">Channel<select name="channel"><option value="email">Email</option><option value="chat">Chat</option><option value="web">Web form</option></select></label></div><label class="field">Subject<input name="subject" required minlength="3" maxlength="180" placeholder="For example, a delayed delivery"></label><label class="field">Customer message<textarea name="message" required minlength="10" maxlength="5000" placeholder="Describe the customer’s request…"></textarea></label><label class="field">Priority<select name="priority"><option value="normal">Normal</option><option value="high">High</option></select></label>${retailDemo() ? `<div class="notice-card">${icon('info')}Sample orders: 1042–1049. For order 1042, use Emma Wilson.</div>` : `<div class="notice-card">${icon('book')}Replies use your uploaded policies. No order system is connected yet.</div>`}<label class="field">Reply template<select name="template_id"><option value="">Automatic — matching category or default</option>${state.templates
      .filter((t) => t.active)
      .map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`)
      .join(
        '',
      )}</select></label><div class="modal-actions"><button class="btn" type="button" data-action="fill-example">Use an example</button><button class="btn btn-primary" type="submit">${icon('plus')}Create ticket</button></div></form>`,
  );
}
function showDoc(id) {
  const d =
    state.docs.find((d) => d.id === id) || state.detail?.result?.sources?.find((d) => d.id === id);
  if (!d) return;
  modal(
    d.title,
    businessName() + ' · policy source',
    `<div class="source-meta"><span class="badge badge-review">v${esc(d.version)}</span><span>Effective from ${fmtDate(d.effective_from, { day: 'numeric', month: 'long', year: 'numeric' })}</span>${d.effective_to ? `<span>Until ${fmtDate(d.effective_to)}</span>` : ''}</div><div class="modal-prose">${esc(d.text)}<h3 style="font-size:13px;margin-top:24px">Facts available for a reply</h3>${Object.entries(
      d.facts || {},
    )
      .map(
        ([k, v]) =>
          `<div class="doc-fact"><code style="font-size:9px">${esc(k)}</code><br>${esc(v)}</div>`,
      )
      .join(
        '',
      )}</div><div class="modal-actions">${canManage() && state.docs.some((x) => x.id === d.id) ? `<button class="btn" data-version="${esc(d.id)}">Publish new version</button>${!d.archived ? `<button class="btn" data-archive-policy="${esc(d.id)}">Archive policy</button>` : ''}` : ''}<button class="btn" data-action="close-modal">Close</button></div>`,
    true,
  );
}
function download(name, value) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }),
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function selectTicket(id, workflow = false) {
  state.selected = id;
  state.tab = workflow ? 'workflow' : 'draft';
  try {
    state.detail = await request('/tickets/' + id);
    render();
  } catch (e) {
    fail(e);
  }
}
async function analyze(fault = 'none') {
  if (state.busy || !state.detail) return;
  const id = state.selected;
  state.busy = true;
  render();
  try {
    await request(`/tickets/${id}/analyze`, { method: 'POST', body: { fault } });
    await reload();
    state.tab = 'draft';
    toast(
      state.detail.result?.reason
        ? 'This ticket needs manual review'
        : 'Your draft is ready for review',
    );
  } catch (e) {
    fail(e);
  } finally {
    state.busy = false;
    render();
  }
}
async function review(decision, note = '') {
  const t = state.detail;
  const text = state.drafts[t.id] ?? t.result?.draft ?? '';
  const button = $(`[data-action="${decision === 'approve' ? 'approve' : 'escalate'}"]`);
  if (button) button.disabled = true;
  try {
    await request(`/tickets/${t.id}/review`, {
      method: 'POST',
      body: { decision, text, note, revision: t.revision },
    });
    closeModal();
    await reload();
    render();
    toast(
      decision === 'approve'
        ? 'Reply approved. Your decision has been saved.'
        : 'Ticket escalated to an agent',
    );
  } catch (e) {
    fail(e);
    if (button) button.disabled = false;
  }
}
async function runFault(fault) {
  closeModal();
  try {
    const t = await request('/tickets', {
      method: 'POST',
      body: {
        customer: 'Emma Wilson',
        email: 'customer1@example.test',
        order_id: '1042',
        channel: 'web',
        priority: 'high',
        subject: `Failure test · ${{ orders_timeout: 'Orders API', invalid_model: 'model output', missing_sources: 'sources' }[fault]}`,
        message:
          'Order #1042 was supposed to arrive yesterday. Where is it, and can I cancel the purchase?',
      },
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    });
    state.selected = t.id;
    state.filter = 'all';
    state.page = 'tickets';
    history.replaceState(null, '', '#tickets');
    await reload();
    await analyze(fault);
  } catch (e) {
    fail(e);
  }
}

document.addEventListener('click', async (e) => {
  const el = e.target.closest('button,[data-action],a');
  if (!el) return;
  if (el.dataset.ticket) {
    await selectTicket(el.dataset.ticket);
    return;
  }
  if (el.dataset.openTicket) {
    state.page = 'tickets';
    state.filter = 'all';
    history.replaceState(null, '', '#tickets');
    await selectTicket(el.dataset.openTicket, !!el.dataset.workflow);
    return;
  }
  if (el.dataset.filter) {
    state.filter = el.dataset.filter;
    render();
    return;
  }
  if (el.dataset.tab) {
    state.tab = el.dataset.tab;
    $('#ticket-detail').innerHTML = detailContent();
    document.querySelector(`[data-tab="${state.tab}"]`)?.focus({ preventScroll: true });
    return;
  }
  if (el.dataset.doc) {
    showDoc(el.dataset.doc);
    return;
  }
  if (el.dataset.archive) {
    state.showArchived = el.dataset.archive === 'true';
    render();
    return;
  }
  if (el.dataset.fault) {
    await runFault(el.dataset.fault);
    return;
  }
  const a = el.dataset.action;
  try {
    if (await handlePlatformAction(el)) return;
    switch (a) {
      case 'new':
        newTicket();
        break;
      case 'close-modal':
        closeModal();
        break;
      case 'clear-search':
        state.filter = 'all';
        state.query = '';
        render();
        break;
      case 'sort':
        state.sort = state.sort === 'newest' ? 'priority' : 'newest';
        render();
        break;
      case 'analyze':
        await analyze();
        break;
      case 'approve':
        await review('approve');
        break;
      case 'escalate':
        modal(
          'Escalate to an agent',
          'Add context for the agent who will review this ticket.',
          `<form id="escalate-form"><label class="field">Reason and notes<textarea name="note" required minlength="5" maxlength="1000" placeholder="What should the agent check?">${esc(reasonNames[state.detail.result?.reason] || 'The reply needs additional review by an agent.')}</textarea></label><div class="modal-actions"><button class="btn" type="button" data-action="close-modal">Cancel</button><button class="btn btn-primary" type="submit">Escalate to an agent</button></div></form>`,
        );
        break;
      case 'copy-id':
        await navigator.clipboard.writeText(state.selected);
        toast('Ticket ID copied');
        break;
      case 'copy-draft':
        await navigator.clipboard.writeText(
          state.drafts[state.selected] ??
            (state.detail.reviews || []).findLast?.((r) => r.decision === 'approve')?.text ??
            state.detail.result?.draft ??
            '',
        );
        toast('Reply copied');
        break;
      case 'refresh':
        await reload();
        render();
        toast('Workspace refreshed');
        break;
      case 'export-trace':
        download(`${state.selected}-trace.json`, state.detail);
        break;
      case 'export-knowledge':
        download('workspace-knowledge.json', state.docs);
        break;
      case 'export-eval':
        download('supportops-evaluation.json', state.evaluation);
        break;
      case 'fill-example': {
        const form = $('#ticket-form');
        const values = {
          customer: 'Emma Wilson',
          email: 'customer1@example.test',
          order_id: '1042',
          subject: 'Where is my order, and can I cancel it?',
          message:
            'Order #1042 was supposed to arrive yesterday. Where is it, and can I cancel the purchase?',
        };
        if (!retailDemo()) {
          values.order_id = '';
          values.subject = 'Question about your policy';
          values.message =
            'Could you explain your ' +
            (state.docs.find((d) => !d.archived)?.title || 'cancellation policy') +
            '?';
        }
        for (const [k, v] of Object.entries(values)) form.elements[k].value = v;
        break;
      }
      case 'activity':
        state.page = 'monitoring';
        location.hash = 'monitoring';
        render();
        break;
      case 'order-info':
        modal(
          'Order data',
          'Synthetic store API. Read-only view.',
          `<pre class="code-block">${esc(JSON.stringify(state.detail.result?.order, null, 2))}</pre>`,
          true,
        );
        break;
      case 'faults':
        modal(
          'Test failure handling',
          'This creates a separate synthetic ticket for the scenario.',
          `<div class="check-list">${[
            ['orders_timeout', 'Orders API timeout'],
            ['invalid_model', 'Invalid model response'],
            ['missing_sources', 'Missing required sources'],
          ]
            .map(
              ([id, label]) =>
                `<button class="btn" data-fault="${id}">${icon('play')}${label}</button>`,
            )
            .join('')}</div>`,
        );
        break;
      case 'demo-info':
        modal(
          'About this demo',
          'An interactive workflow using synthetic data.',
          `<div class="modal-prose">Create tickets, prepare replies, inspect sources, edit text and save review decisions.<br><br>All changes stay in your browser. No external LLM or AWS services are connected. Latency and time savings are not simulated.<br><br>The full project includes FastAPI, LangGraph, PostgreSQL / pgvector, evaluation, Docker, CI/CD and AWS configuration.</div>`,
        );
        break;
      case 'eval-info':
        modal(
          'Run evaluation',
          'Run these commands from the local project root.',
          `<pre class="code-block">python -m eval.run --split dev
python -m eval.run --split holdout
pytest

# Live evaluation: configure LLM_* in .env
python -m eval.run --split holdout --live</pre><p class="modal-prose">Dev and holdout sets are separate. Reports record the model, prompt, document and dataset versions. Critical factual errors block release.</p>`,
        );
        break;
      case 'runbook':
        modal(
          'Recover from a failure',
          'Practical steps for diagnosis and recovery.',
          `<div class="modal-prose"><b>1. Orders API unavailable</b><br>Check the orders service at /health/live, connectivity and ORDERS_API_URL. Restore the service, then select Retry analysis.<br><br><b>2. Invalid model output</b><br>Inspect the trace and manifest. Check the prompt version and schema. After two invalid attempts, the ticket goes to an agent.<br><br><b>3. Failed release</b><br>AI Guard blocks regressions. The ECS circuit breaker rolls back unhealthy deployments. Content failures require a return to a verified image digest.<br><br>Full commands and recovery checks are in docs/runbook.md.</div>`,
          true,
        );
        break;
      case 'reset-demo':
        modal(
          'Reset the demo',
          'All tickets and review decisions created in this demo will be removed from this browser.',
          `<div class="modal-actions"><button class="btn" data-action="close-modal">Cancel</button><button class="btn btn-primary" data-action="confirm-reset">Reset data</button></div>`,
        );
        break;
      case 'confirm-reset':
        localStorage.removeItem('supportops-demo-v2-en');
        location.reload();
        break;
    }
  } catch (err) {
    fail(err);
  }
});
document.addEventListener('input', (e) => {
  if (e.target.id === 'ticket-search') {
    state.query = e.target.value;
    $('#ticket-items').innerHTML = ticketItems();
  }
  if (e.target.id === 'draft-text') {
    state.drafts[state.selected] = e.target.value;
    const note = document.querySelector('.verified-note');
    if (note) note.textContent = 'Draft edited · verify changed facts before approval';
  }
  if (e.target.id === 'doc-search') {
    state.docQuery = e.target.value;
    const cursor = e.target.selectionStart;
    render();
    $('#doc-search').focus();
    $('#doc-search').setSelectionRange(cursor, cursor);
  }
});
document.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  if (await handlePlatformSubmit(form)) return;
  if (form.id === 'ticket-form') {
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(form));
      values.order_id = values.order_id || null;
      values.template_id = values.template_id || null;
      const t = await request('/tickets', {
        method: 'POST',
        body: values,
        headers: { 'Idempotency-Key': creationKey },
      });
      closeModal();
      state.page = 'tickets';
      state.filter = 'all';
      state.query = '';
      state.selected = t.id;
      state.tab = 'draft';
      history.replaceState(null, '', '#tickets');
      await reload();
      render();
      toast('Ticket created');
    } catch (err) {
      fail(err);
      button.disabled = false;
    }
  }
  if (form.id === 'escalate-form') await review('escalate', new FormData(form).get('note'));
});
document.addEventListener('keydown', (e) => {
  if (!account) return;
  if (e.key === 'Escape') {
    closeModal();
    return;
  }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (state.page !== 'tickets') {
      state.page = 'tickets';
      render();
    }
    $('#ticket-search')?.focus();
  }
  if (
    e.key.toLowerCase() === 'n' &&
    !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) &&
    !e.metaKey &&
    !e.ctrlKey &&
    !$('#overlay').children.length
  ) {
    e.preventDefault();
    newTicket();
  }
  if (e.key === 'Tab' && $('#overlay').children.length) {
    const list = [...$('#overlay').querySelectorAll('button,input,textarea,select,a[href]')].filter(
      (x) => !x.disabled,
    );
    if (e.shiftKey && document.activeElement === list[0]) {
      e.preventDefault();
      list.at(-1)?.focus();
    } else if (!e.shiftKey && document.activeElement === list.at(-1)) {
      e.preventDefault();
      list[0]?.focus();
    }
  }
});
window.addEventListener('hashchange', () => {
  if (!account) {
    render();
    return;
  }
  if (location.hash.startsWith('#join/')) {
    renderJoinPrompt();
    return;
  }
  const p = location.hash.slice(1);
  if (pages[p]) {
    state.page = p;
    render();
  }
});
function renderJoinPrompt() {
  modal(
    'Join another team',
    'An invitation must be accepted with the email address it was sent to.',
    '<p class="modal-prose">Sign out, then open this invitation link again to join the workspace. If you already have an account, use your existing password.</p><div class="modal-actions"><button class="btn" data-action="close-modal">Cancel</button><button class="btn btn-primary" data-action="logout">Sign out</button></div>',
  );
}
try {
  await init();
  if (account?.organization) await reload();
  state.page =
    account && !account.organization
      ? 'settings'
      : pages[location.hash.slice(1)]
        ? location.hash.slice(1)
        : 'tickets';
  render();
  if (account && location.hash.startsWith('#join/')) renderJoinPrompt();
} catch (e) {
  $('#app').innerHTML =
    `<div class="error-screen"><h2>Could not open the workspace</h2><p>${esc(e.message)}</p><p>Check that the SupportOps server is running and reachable.</p><button class="btn" data-action="reload-page">Try again</button></div>`;
}
