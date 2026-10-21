import { icon } from './icons.js';
import { init, request, isDemo, config } from './adapter.js';

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
  quality: 'AI Release Guard',
  monitoring: 'Monitoring',
  settings: 'Settings',
};

function toast(text, error = false) {
  const el = document.createElement('div');
  el.className = 'toast' + (error ? ' error' : '');
  el.innerHTML = icon(error ? 'warning' : 'checkcircle') + `<span>${esc(text)}</span>`;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 5000);
}
function fail(e) {
  const names = {
    revision_conflict: 'This ticket has changed. Refresh to see the latest version.',
    invalid_api_key: 'An API key is required. Enter it in Settings.',
    analysis_in_progress: 'Analysis is already running. Please wait for the result.',
    fault_injection_disabled: 'Fault scenarios are disabled in the server configuration.',
  };
  toast(names[e.message] || e.message || 'The request could not be completed', true);
}
async function reload() {
  const [tickets, docs, metrics, evaluation] = await Promise.all([
    request('/tickets'),
    request('/knowledge'),
    request('/metrics'),
    request('/evaluation'),
  ]);
  Object.assign(state, { tickets, docs, metrics, evaluation });
  if (!tickets.some((t) => t.id === state.selected)) state.selected = tickets[0]?.id;
  state.detail = state.selected ? await request('/tickets/' + state.selected) : null;
}
function sidebar() {
  return `<aside class="sidebar"><a class="brand" href="#tickets" aria-label="SupportOps AI · home"><span class="brand-mark">${icon('logo')}</span><span>SupportOps</span><em>AI</em></a><div class="workspace"><span class="store-mark">${icon('leaf')}</span><div><strong>Listva</strong><small>Workspace</small></div>${icon('down')}</div><p class="nav-label">WORKSPACE</p><nav>${[
    ['overview', 'grid'],
    ['tickets', 'inbox'],
    ['knowledge', 'book'],
  ]
    .map(([p, i]) => nav(p, i))
    .join('')}<div class="nav-separator"></div><p class="nav-label">QUALITY & OPERATIONS</p>${[
    ['quality', 'shield'],
    ['monitoring', 'activity'],
  ]
    .map(([p, i]) => nav(p, i))
    .join(
      '',
    )}</nav><div class="sidebar-bottom">${nav('settings', 'settings')}<div class="system-note"><b>${icon('shield')}Human in the loop</b>Every reply gets a human review before it reaches a customer.<a href="#quality">How review works ${icon('arrow')}</a></div><div class="profile"><div class="avatar">KB</div><div><strong>Ksenia</strong><small>Support agent</small></div>${icon('down')}</div></div></aside>`;
}
function nav(p, i) {
  return `<a href="#${p}" class="nav-item ${state.page === p ? 'active' : ''}" aria-label="${pages[p]}" ${state.page === p ? 'aria-current="page"' : ''}>${icon(i)}<span>${pages[p]}</span>${p === 'tickets' ? `<b class="nav-count">${state.tickets.filter((t) => !['approved', 'escalated'].includes(t.status)).length}</b>` : ''}</a>`;
}
function render() {
  document.title = `SupportOps AI · ${pages[state.page]}`;
  $('#app').innerHTML =
    `<div class="app-shell">${sidebar()}<main class="main"><header class="topbar"><div class="breadcrumbs"><span>Workspace</span>${icon('chevron')}<b>${pages[state.page]}</b></div><div class="top-actions"><span class="system-status"><i class="status-dot"></i>${isDemo ? 'Browser demo' : 'API connected'}</span><i class="top-divider"></i><button class="icon-btn" data-action="activity" aria-label="Recent activity">${icon('bell')}</button><div class="avatar" aria-label="Ksenia’s profile">KB</div></div></header>${isDemo ? `<div class="demo-banner"><span>${icon('info')}Interactive demo · synthetic data · changes are saved in this browser</span><button data-action="demo-info">About the demo ${icon('arrow')}</button></div>` : ''}<div class="content">${pageContent()}</div></main></div>`;
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
      return settingsPage();
  }
}
function ticketsPage() {
  return `${heading('Tickets', 'A little less busywork. A lot more thoughtful support.', `<span class="date-label">${icon('clock')}${new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}</span><button class="btn btn-primary" data-action="new">${icon('plus')}New ticket</button>`)}${metricsRow()}<div class="queue-toolbar"><div class="filter-tabs" role="group" aria-label="Filter tickets">${[
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
    )}</div><div class="toolbar-right"><label class="search-wrap">${icon('search')}<input id="ticket-search" aria-label="Search tickets" placeholder="Search tickets…" value="${esc(state.query)}"><kbd>⌘ K</kbd></label><button class="btn" data-action="sort" title="Change sort order">${icon('filter')}<span>${state.sort === 'newest' ? 'Newest' : 'Priority'}</span></button></div></div><section class="workbench" aria-label="Ticket workspace"><div class="ticket-list"><div class="list-heading"><b>Ticket queue</b>${icon('down')}</div><div id="ticket-items">${ticketItems()}</div></div><div class="ticket-detail" id="ticket-detail">${detailContent()}</div></section><div class="bottom-line"><span><i class="status-dot"></i>${isDemo ? 'Listva store demo' : 'Listva synthetic store'} · synthetic data, no personal information</span><span><kbd>N</kbd> New ticket <kbd>⌘ K</kbd> Search</span></div>`;
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
  return `<div class="draft-body">${(r.steps || []).map((s, i) => `<div class="workflow-node">${icon(s.status === 'error' ? 'warning' : 'checkcircle')}<span>${stepNames[s.name] || esc(s.name)}</span><small>${ms(s.duration_ms)}</small></div>`).join('')}<div class="workflow-node">${icon('human')}<span>Human review</span><small>${['approved', 'escalated'].includes(state.detail.status) ? 'Completed' : 'Pending'}</small></div><p class="context-note">${isDemo ? 'Workflow steps are simulated in the browser. These are not server traces.' : 'Workflow state is saved in the persistent LangGraph checkpointer.'}</p><pre class="code-block">${esc(JSON.stringify(r.manifest || {}, null, 2))}</pre><button class="btn" data-action="export-trace">${icon('download')}Download result JSON</button></div>`;
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
  return `<div class="review-actions">${t.status === 'review' ? `<button class="btn btn-primary" data-action="approve">${icon('check')}Approve reply</button>` : ''}<button class="btn" data-action="escalate">${icon('human')}Escalate to an agent</button>${config.faults_enabled ? `<button class="icon-btn" data-action="faults" title="Test a failure scenario" aria-label="Test a failure scenario">${icon('code')}</button>` : ''}</div>`;
}
function contextPanel(t, r) {
  const o = r?.order;
  const statuses = {
    delayed: 'Delivery delayed',
    shipped: 'In transit',
    processing: 'Processing',
    delivered: 'Delivered',
    cancelled: 'Cancelled',
  };
  return `<aside class="context-panel"><div class="context-section"><h3 class="context-heading">${icon('box')}Order details</h3>${o ? `<div class="order-title">#${esc(o.id)}<button class="icon-btn" data-action="order-info" aria-label="View order data">${icon('external')}</button></div><span class="badge ${o.status === 'delayed' ? 'badge-manual' : 'badge-review'}">${icon('truck')}${statuses[o.status]}</span>${o.items.map((item) => `<div class="order-product"><div class="product-icon">${icon('box')}</div><div><b>${esc(item.title)}</b><small>${item.quantity} item(s) · Listva store</small></div></div>`).join('')}<div class="kv"><span>Order total</span><strong>${o.total.toLocaleString('en-GB')} ₽</strong></div><div class="kv"><span>Order placed</span><strong>${fmtDate(o.placed_at)}</strong></div><div class="kv"><span>Expected delivery</span><strong>${fmtDate(o.expected_at)}</strong></div><div class="kv"><span>Carrier</span><strong>${esc(o.carrier)}</strong></div>${o.tracking ? `<div class="kv"><span>Tracking number</span><strong>${esc(o.tracking)}</strong></div>` : ''}<p class="context-note">${icon('link')} ${isDemo ? 'Synthetic store data' : 'Retrieved from the store API'}</p>` : `<div class="context-note">${t.order_id ? `Order #${esc(t.order_id)}` : 'No order number provided'}<br>Order details will appear after analysis.</div>`}</div><div class="context-section"><h3 class="context-heading">${icon('human')}Customer</h3><div class="context-customer"><div class="avatar">${esc(initials(t.customer))}</div><div><strong>${esc(t.customer)}</strong><small>${esc(t.email || 'No contact provided')}</small></div></div><div class="kv"><span>Channel</span><strong>${{ email: 'Email', chat: 'Chat', web: 'Web form' }[t.channel]}</strong></div><div class="kv"><span>Store</span><strong>Listva · demo</strong></div></div><div class="context-section"><h3 class="context-heading">${icon('shield')}Original draft checks</h3>${r?.checks?.length ? `<div class="check-list">${r.checks.map((c) => `<div class="check-item ${c.passed ? '' : 'failed'}">${icon(c.passed ? 'checkcircle' : 'warning')}<span>${esc(c.label)}</span></div>`).join('')}</div>` : '<p class="context-note">Checks run when you prepare a reply.</p>'}${r?.trace_id ? `<button class="trace-link" data-action="export-trace">${icon('activity')}<code>trace: ${esc(r.trace_id.slice(0, 16))}…</code>${icon('external')}</button>` : ''}</div></aside>`;
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
  const active = (d) => d.effective_from <= day && (!d.effective_to || d.effective_to > day);
  const docs = state.docs.filter(
    (d) =>
      (state.showArchived || active(d)) &&
      [d.title, d.text].some((v) => v.toLowerCase().includes(state.docQuery.toLowerCase())),
  );
  return `${heading('Knowledge base', 'Store policies with clear sources, versions and effective dates.', `<button class="btn" data-action="export-knowledge">${icon('download')}Export JSON</button>`)}<div class="info-strip">${icon('shield')}Only current policies are used in replies. The archived 14-day return policy is excluded from retrieval.</div><div class="queue-toolbar"><div class="filter-tabs"><button class="filter-tab ${!state.showArchived ? 'active' : ''}" data-archive="false">Current policies <span>${state.docs.filter(active).length}</span></button><button class="filter-tab ${state.showArchived ? 'active' : ''}" data-archive="true">All versions <span>${state.docs.length}</span></button></div><label class="search-wrap">${icon('search')}<input id="doc-search" value="${esc(state.docQuery)}" placeholder="Search policies…" aria-label="Search the knowledge base"></label></div><div id="document-cards" class="document-grid">${docs.map((d) => `<button class="document-card" data-doc="${esc(d.id)}"><span class="document-icon">${icon('file')}</span><h3>${esc(d.title)}</h3><p>${esc(d.text)}</p><div class="source-meta"><span>v${esc(d.version)}</span><span>From ${fmtDate(d.effective_from)}</span><span class="badge ${active(d) ? 'badge-review' : ''}">${active(d) ? 'Active' : 'Archived'}</span></div></button>`).join('') || '<div class="empty">No policies found</div>'}</div>`;
}
function qualityPage() {
  const e = state.evaluation;
  const ready = e.status === 'passed' || e.status === 'failed';
  return `${heading('AI Release Guard', 'Evaluate changes before they ship. Release with evidence.', `<button class="btn" data-action="export-eval">${icon('download')}Download report</button>`)}<div class="info-strip">${icon('info')}${isDemo ? 'A snapshot of local evaluation results. The demo does not call a live model.' : 'Evaluation runs separately and gates the CI build.'}</div>${ready ? `<div class="metrics-row">${metric('Factual accuracy', `${Math.round(e.metrics.factual_accuracy * 100)}%`, 'Checks for order status, facts and sources', 'checkcircle', true)}${metric('Classification · macro-F1', e.metrics.macro_f1.toFixed(2), 'Against hand-labeled synthetic cases', 'inbox')}${metric('Escalation recall', `${Math.round(e.metrics.escalation_recall * 100)}%`, 'Requests that must reach a human', 'human')}${metric('Latency · p95', ms(e.metrics.p95_ms), `${e.cases} cases · sequential execution`, 'clock')}</div><div class="section-grid"><section class="panel"><h2>Release evaluation</h2><p>Dataset ${esc(e.dataset_version)} · ${esc(e.split)}</p><div class="quality-score"><div class="score-ring"><b>${e.passed}/${e.cases}</b><small>cases passed</small></div><div><h3>${e.status === 'passed' ? 'All checks passed' : 'Release blocked'}</h3><p>Model: ${esc(e.model)}<br>${fmtDate(e.created_at, { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' })}<br>${e.live ? 'Live run with an external model' : 'Deterministic baseline · no LLM spend'}</p></div></div><button class="btn" data-action="eval-info">${icon('terminal')}Run the evaluation</button></section><section class="panel"><h2>What the guard checks</h2><p>A failed critical check blocks the pipeline</p><div class="check-list">${['Current return policies are applied', 'Shipped orders are never automatically cancelled', 'API outages route tickets to an agent', 'The model cannot add unsupported facts', 'Repeated approvals do not create duplicates'].map((c) => `<div class="check-item">${icon('checkcircle')}<span>${c}</span></div>`).join('')}</div><div class="notice-card">${icon('info')}These metrics cover the synthetic dataset. Agent productivity and live-model quality require a separate study.</div></section></div><section class="panel"><h2>Scenario results</h2><p>Isolated ${esc(e.split)} dataset · seed ${e.seed}</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Scenario</th><th>Expected category</th><th>Result</th><th>Time</th></tr></thead><tbody>${(e.results || []).map((r) => `<tr><td>${esc(r.id)}<br><code>${esc(r.description)}</code></td><td>${(r.expected_categories || []).map((c) => categoryNames[c]).join(', ')}</td><td><span class="badge ${r.passed ? 'badge-review' : 'badge-manual'}">${icon(r.passed ? 'check' : 'warning')}${r.passed ? 'Passed' : 'Failed'}</span></td><td>${ms(r.duration_ms)}</td></tr>`).join('')}</tbody></table></div></section>` : `<section class="panel"><div class="empty">${icon('shield')}<strong>No evaluation run yet</strong>Run evaluation to generate a report.<pre class="code-block">python -m eval.run --split holdout</pre><button class="btn" data-action="refresh">${icon('refresh')}Refresh</button></div></section>`}`;
}
function monitoringPage() {
  const m = state.metrics;
  return `${heading('Monitoring', 'Follow every ticket through the workflow and understand what went wrong.', `<button class="btn" data-action="refresh">${icon('refresh')}Refresh</button>`)}${isDemo ? `<div class="info-strip">${icon('info')}The demo shows workflow structure. Real timings and traces are available in the local application.</div>` : ''}<div class="metrics-row">${metric('Analyzed tickets', m.analyzed ?? 0, 'Including manual review', 'activity')}${metric('Latency · p95', ms(m.p95_ms), 'Sequential runs in this environment', 'clock')}${metric('LLM tokens', (m.tokens ?? 0).toLocaleString('en-GB'), config.provider === 'baseline' ? 'The baseline does not call an LLM' : 'Input and output tokens', 'spark')}${metric('LLM spend', `$${(m.cost_usd || 0).toFixed(4)}`, 'Infrastructure costs are separate', 'grid')}</div><section class="panel"><h2>Recent workflow runs</h2><p>One trace ID connects every step of a run</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Ticket / trace</th><th>Status</th><th>Reason / model</th><th>Latency</th><th></th></tr></thead><tbody>${(m.runs || []).map((r) => `<tr><td><strong>${esc(r.ticket_id)}</strong><br><code>${esc(r.trace_id?.slice(0, 18) || '—')}</code></td><td>${badge(r.status)}</td><td style="max-width:330px">${esc(r.reason ? reasonNames[r.reason] || r.reason : r.manifest?.model || '—')}</td><td>${ms(r.duration_ms)}</td><td><button class="btn" data-open-ticket="${esc(r.ticket_id)}" data-workflow="true">Trace ${icon('arrow')}</button></td></tr>`).join('') || '<tr><td colspan="5">Analyze a ticket to see its workflow here.</td></tr>'}</tbody></table></div></section><div class="section-grid"><section class="panel"><h2>Test failure handling</h2><p>Controlled scenarios on a new synthetic ticket</p>${[
    ['orders_timeout', 'Orders API timeout', 'Bounded timeout → manual review'],
    ['invalid_model', 'Invalid model output', 'Two attempts → safe handoff'],
    ['missing_sources', 'Missing required policies', 'No sources → no draft'],
  ]
    .map(
      ([id, title, sub]) =>
        `<div class="setting-row"><div><h3>${title}</h3><p>${sub}</p></div><button class="btn" data-fault="${id}" ${!config.faults_enabled ? 'disabled' : ''}>${icon('play')}Run</button></div>`,
    )
    .join(
      '',
    )}</section><section class="panel"><h2>Diagnose and recover</h2><p>Local and cloud environments</p><div class="modal-prose">OpenTelemetry connects the API, retrieval, generation and validation. Configure export through OTLP.<br><br>Restore the failed dependency and retry analysis. Approved tickets are protected against repeat processing.</div><button class="btn" data-action="runbook" style="margin-top:18px">${icon('book')}Open the runbook</button></section></div>`;
}
function settingsPage() {
  return `${heading('Settings', 'Environment, model and access configuration.')}<section class="panel"><h2>Workspace</h2><p>Synthetic online store</p>${[
    ['Store name', 'A publishable synthetic dataset', 'Listva'],
    [
      'Application mode',
      isDemo
        ? 'State is saved in this browser’s localStorage'
        : 'Tickets, reviews and checkpoints are saved on the server',
      isDemo ? 'Browser demo' : 'FastAPI',
    ],
    ['Model provider', 'Configured on the server using MODEL_PROVIDER', config.provider],
    [
      'Application version',
      'Code, prompt and document versions are saved with every result',
      config.version,
    ],
  ]
    .map(
      ([a, b, c]) =>
        `<div class="setting-row"><div><h3>${a}</h3><p>${b}</p></div><code>${esc(c)}</code></div>`,
    )
    .join(
      '',
    )}</section>${!isDemo ? `<section class="panel"><h2>API access</h2><p>The key is stored in sessionStorage and sent to the server in the X-API-Key header.</p><form id="auth-form"><label class="field">API key<input type="password" name="key" placeholder="APP_API_KEY" autocomplete="off" value="${esc(sessionStorage.getItem('supportops-api-key') || '')}"></label><button class="btn btn-primary" type="submit">Save and connect</button></form></section>` : ''}<section class="panel"><h2>Connect an external model</h2><p>Set secrets through the server environment or Secrets Manager. They are never sent to the browser.</p><pre class="code-block">MODEL_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-model-id
LLM_API_KEY=…</pre><div class="notice-card">${icon('shield')}The model selects verified fact IDs and tone. Trusted code renders facts and permitted actions from the order API and current policies.</div></section>${isDemo ? `<section class="panel"><h2>Demo data</h2><p>Reset removes only this demo’s local changes in the current browser.</p><button class="btn" data-action="reset-demo">${icon('refresh')}Reset demo</button></section>` : ''}`;
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
    'Create a ticket for a Listva customer.',
    `<form id="ticket-form"><div class="form-grid"><label class="field">Customer name<input name="customer" required minlength="2" maxlength="100" placeholder="Anna Smirnova"></label><label class="field">Email<input name="email" type="email" placeholder="customer@example.test" maxlength="160"></label></div><div class="form-grid"><label class="field">Order number<input name="order_id" inputmode="numeric" pattern="[0-9]{4,12}" placeholder="1042"></label><label class="field">Channel<select name="channel"><option value="email">Email</option><option value="chat">Chat</option><option value="web">Web form</option></select></label></div><label class="field">Subject<input name="subject" required minlength="3" maxlength="180" placeholder="For example, a delayed delivery"></label><label class="field">Customer message<textarea name="message" required minlength="10" maxlength="5000" placeholder="Describe the customer’s request…"></textarea></label><label class="field">Priority<select name="priority"><option value="normal">Normal</option><option value="high">High</option></select></label><div class="notice-card">${icon('info')}Test orders: 1042–1049. The customer must match the order. For order 1042, use Anna Smirnova.</div><div class="modal-actions"><button class="btn" type="button" data-action="fill-example">Use an example</button><button class="btn btn-primary" type="submit">${icon('plus')}Create ticket</button></div></form>`,
  );
}
function showDoc(id) {
  const d =
    state.docs.find((d) => d.id === id) || state.detail?.result?.sources?.find((d) => d.id === id);
  if (!d) return;
  modal(
    d.title,
    'Listva store policy source',
    `<div class="source-meta"><span class="badge badge-review">v${esc(d.version)}</span><span>Effective from ${fmtDate(d.effective_from, { day: 'numeric', month: 'long', year: 'numeric' })}</span>${d.effective_to ? `<span>Until ${fmtDate(d.effective_to)}</span>` : ''}</div><div class="modal-prose">${esc(d.text)}<h3 style="font-size:13px;margin-top:24px">Facts available for a reply</h3>${Object.entries(
      d.facts || {},
    )
      .map(
        ([k, v]) =>
          `<div class="doc-fact"><code style="font-size:9px">${esc(k)}</code><br>${esc(v)}</div>`,
      )
      .join(
        '',
      )}</div><div class="modal-actions"><button class="btn" data-action="close-modal">Close</button></div>`,
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
        customer: 'Anna Smirnova',
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
        download('listva-knowledge.json', state.docs);
        break;
      case 'export-eval':
        download('supportops-evaluation.json', state.evaluation);
        break;
      case 'fill-example': {
        const form = $('#ticket-form');
        const values = {
          customer: 'Anna Smirnova',
          email: 'customer1@example.test',
          order_id: '1042',
          subject: 'Where is my order, and can I cancel it?',
          message:
            'Order #1042 was supposed to arrive yesterday. Where is it, and can I cancel the purchase?',
        };
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
  if (form.id === 'ticket-form') {
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(form));
      values.order_id = values.order_id || null;
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
  if (form.id === 'auth-form') {
    sessionStorage.setItem('supportops-api-key', new FormData(form).get('key'));
    try {
      await reload();
      render();
      toast('Connection established');
    } catch (err) {
      fail(err);
    }
  }
});
document.addEventListener('keydown', (e) => {
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
  const p = location.hash.slice(1);
  if (pages[p]) {
    state.page = p;
    render();
  }
});
try {
  await init();
  try {
    await reload();
  } catch (err) {
    if (err.status === 401) {
      state.page = 'settings';
      render();
      toast('Enter your API key in Settings', true);
    } else throw err;
  }
  if (state.page !== 'settings') {
    state.page = pages[location.hash.slice(1)] ? location.hash.slice(1) : 'tickets';
    render();
  }
} catch (e) {
  $('#app').innerHTML =
    `<div class="error-screen"><h2>Could not open the workspace</h2><p>${esc(e.message)}</p><p>Check that the SupportOps server is running and reachable.</p><button class="btn" data-action="refresh">${icon('refresh')}Try again</button></div>`;
}
