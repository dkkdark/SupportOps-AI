import { icon } from './icons.js';
import { createViewState } from './view-state.js';
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
  agent_handoff: 'The agent needs a person to investigate or make a decision.',
  grounding_failed: 'The independent review could not confirm this draft against its sources.',
  unsupported_citations: 'The draft cited evidence that was not available in this workspace.',
  agent_budget_exceeded:
    'The investigation reached its time or token budget. A person can review the available context.',
  knowledge_changed:
    'The knowledge base changed during processing. Retry with the current documents.',
  model_rate_limited: 'The model provider rate limit was reached. Try again shortly.',
  order_integration_not_connected:
    'This request needs live order or account data. Check it in your business system before replying.',
  missing_sources: 'Required current policies are missing. A verified draft cannot be prepared.',
  conflicting_documents:
    'Multiple policy versions are active. Resolve the conflict in the knowledge base.',
  invalid_model_output: 'The model returned invalid output twice. The draft has been blocked.',
  model_unavailable: 'The model provider is unavailable. Retry analysis later.',
  model_not_configured: 'AI processing is temporarily unavailable. Contact support for help.',
  vector_store_unavailable:
    'Knowledge search is temporarily unavailable. Your request is saved; retry when the service recovers.',
  vector_store_auth_failed:
    'Knowledge search is temporarily unavailable. Contact support for help.',
  vector_index_missing:
    'The knowledge index needs to be restored or rebuilt. Ask an admin to check indexing before retrying.',
  vector_store_profile_mismatch:
    'The knowledge index configuration does not match the selected embedding model.',
  damage_requires_review: 'Damaged items require a review of photographs and an agent’s decision.',
  payment_requires_review:
    'Payment details must be checked in the payment system. Order data alone is insufficient.',
  action_requires_review: 'This action needs separate approval from an agent.',
  return_eligibility_review: 'Verify the delivery date and return eligibility.',
  unsupported_intent:
    'This request is outside the available policies. An agent needs to review it.',
  untrusted_instructions:
    'The message attempts to override instructions. Manual review is required.',
  workflow_failed:
    'Processing was interrupted. Your request is saved. Try preparing the reply again.',
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
  types: [],
  intakes: [],
  people: [],
  billing: {},
  plans: [],
  members: [],
  invitations: [],
  audit: [],
  metrics: {},
  evaluation: { status: 'not_run' },
  detail: null,
  detailLoading: false,
  detailError: false,
  busy: false,
  drafts: {},
  sort: 'newest',
  docQuery: '',
  showArchived: false,
};
let creationKey = null;
let modalTrigger = null;
let workspaceEpoch = 0;
let reloadVersion = 0;
let selectionVersion = 0;
let switchingWorkspace = false;
const workspaceToken = () => `${workspaceEpoch}:${account?.user?.id}:${account?.organization?.id}`;
const viewState = createViewState(window, document);
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
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
  tickets: 'Requests',
  knowledge: 'Knowledge base',
  quality: 'Reply quality',
  monitoring: 'Monitoring',
  settings: 'Settings',
  templates: 'Reply templates',
  team: 'Team',
  types: 'Request types',
  sources: 'Request sources',
  billing: 'AI usage',
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
  const workspace = workspaceToken();
  const version = ++reloadVersion;
  const [
    tickets,
    docs,
    metrics,
    evaluation,
    templates,
    members,
    invitations,
    audit,
    types,
    intakes,
    people,
    billing,
    plans,
  ] = await Promise.all([
    request('/tickets'),
    request('/knowledge'),
    request('/metrics'),
    request('/evaluation'),
    isDemo ? [] : request('/templates'),
    canManage() ? request('/members') : [],
    canManage() ? request('/invitations') : [],
    canManage() ? request('/audit') : [],
    isDemo ? [] : request('/request-types'),
    canManage() ? request('/intakes') : [],
    isDemo ? [] : request('/directory'),
    isDemo ? {} : request('/billing'),
    isDemo ? [] : request('/plans'),
  ]).catch((error) => {
    if (workspace !== workspaceToken() || version !== reloadVersion) return [];
    throw error;
  });
  if (workspace !== workspaceToken() || version !== reloadVersion) return false;
  Object.assign(state, {
    tickets,
    docs,
    metrics,
    evaluation,
    types,
    intakes,
    people,
    billing,
    plans,
    templates,
    members: members.map((m) => ({ ...m, id: m.user_id })),
    invitations,
    audit,
  });
  const rows = filteredTickets();
  if (!rows.some((t) => t.id === state.selected)) state.selected = rows[0]?.id || null;
  const selected = state.selected;
  const selection = selectionVersion;
  const detail = selected
    ? await request('/tickets/' + selected).catch((error) => {
        if (
          workspace !== workspaceToken() ||
          version !== reloadVersion ||
          selection !== selectionVersion
        )
          return null;
        throw error;
      })
    : null;
  if (workspace !== workspaceToken() || version !== reloadVersion) return false;
  if (selected === state.selected && selection === selectionVersion) {
    state.detail = detail;
    state.detailLoading = false;
    state.detailError = false;
  }
  return true;
}
function sidebar() {
  return `<aside class="sidebar"><a class="brand" href="#tickets" aria-label="SupportOps AI · home"><span class="brand-mark">${icon('logo')}</span><span>SupportOps</span><em>AI</em></a><button class="workspace" data-action="switch-workspace"><span class="store-mark">${icon('leaf')}</span><div><strong>${esc(businessName())}</strong><small>${retailDemo() ? 'Sample workspace' : esc(account?.organization?.role || 'Choose workspace')}</small></div>${icon('down')}</button><p class="nav-label">WORKSPACE</p><nav data-scroll-key="workspace-nav">${[
    ['overview', 'grid'],
    ['tickets', 'inbox'],
    ['knowledge', 'book'],
    ['templates', 'file'],
    ...(!isDemo ? [['types', 'grid']] : []),
    ...(canManage()
      ? [
          ['sources', 'link'],
          ['billing', 'activity'],
        ]
      : []),
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
    )}</nav><div class="sidebar-bottom">${nav('settings', 'settings')}<a class="nav-item" href="#home" aria-label="Product overview">${icon('globe')}<span>Product overview</span></a><div class="system-note"><b>${icon('shield')}Human in the loop</b>Every reply gets a human review before it reaches a customer.<a href="#quality">How review works ${icon('arrow')}</a></div><div class="profile"><div class="avatar">${esc(initials(account.user.name))}</div><div><strong>${esc(account.user.name)}</strong><small>${esc(account.organization?.role || 'Account')}</small></div>${!isDemo ? `<button class="icon-btn" data-action="logout" aria-label="Sign out" title="Sign out">${icon('external')}</button>` : ''}</div></div></aside>`;
}
function nav(p, i) {
  return `<a href="#${p}" class="nav-item ${state.page === p ? 'active' : ''}" aria-label="${pages[p]}" ${state.page === p ? 'aria-current="page"' : ''}>${icon(i)}<span>${pages[p]}</span>${p === 'tickets' ? `<b class="nav-count">${state.tickets.filter((t) => !['approved', 'escalated'].includes(t.status)).length}</b>` : ''}</a>`;
}
let signupWidget = null;
function mountSignupCaptcha() {
  if (!document.getElementById('signup-captcha') || !config.captcha_site_key) return;
  const mount = () => {
    if (document.getElementById('signup-captcha'))
      signupWidget = window.turnstile.render('#signup-captcha', {
        sitekey: config.captcha_site_key,
        action: 'register',
      });
  };
  if (window.turnstile) {
    mount();
    return;
  }
  let script = document.getElementById('turnstile-script');
  if (!script) {
    script = document.createElement('script');
    script.id = 'turnstile-script';
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.onload = mount;
    document.head.appendChild(script);
  }
}
let indexingPoll = null;
function render() {
  const workspace = account?.organization?.id || 'public';
  const page = location.hash || state.page;
  viewState.render(`${workspace}:${page}`, renderContent);
}
function renderContent() {
  if (signupWidget != null && window.turnstile) {
    window.turnstile.remove(signupWidget);
    signupWidget = null;
  }
  clearTimeout(indexingPoll);
  if (
    location.hash === '#forgot' ||
    location.hash.startsWith('#reset/') ||
    location.hash.startsWith('#verify/')
  ) {
    $('#app').innerHTML = recoveryPage();
    return;
  }
  if (!account || location.hash === '#home') {
    $('#app').innerHTML = publicPage();
    mountSignupCaptcha();
    return;
  }
  document.title = `SupportOps AI · ${pages[state.page]}`;
  $('#app').innerHTML =
    `<div class="app-shell">${sidebar()}<main class="main"><header class="topbar"><div class="breadcrumbs"><span>Workspace</span>${icon('chevron')}<b>${pages[state.page]}</b></div><div class="top-actions"><span class="system-status"><i class="status-dot"></i>${isDemo ? 'Browser demo' : 'Workspace online'}</span><i class="top-divider"></i><button class="icon-btn" data-action="activity" aria-label="Recent activity">${icon('bell')}</button><div class="avatar" aria-label="${esc(account.user.name)}">${esc(initials(account.user.name))}</div></div></header>${isDemo ? `<div class="demo-banner"><span>${icon('info')}Interactive demo · synthetic data · changes are saved in this browser</span><button data-action="demo-info">About the demo ${icon('arrow')}</button></div>` : ''}<div class="content">${demoBudgetBanner()}${!isDemo && config.provider === 'openai' && !config.ai_configured ? `<div class="info-strip">${icon('info')}AI processing is temporarily unavailable. You can continue working with requests manually. Contact support if the issue continues.</div>` : ''}${account.user.email_verified === false && config.email_delivery_enabled ? `<div class="verification-strip">${icon('mail')}Verify your email to enable AI processing for your workspace.<button class="btn" data-action="verify-email">Send verification link</button></div>` : ''}${pageContent()}</div></main></div>`;
  if (
    account &&
    state.page === 'knowledge' &&
    state.docs.some((d) => ['pending', 'indexing'].includes(d.indexing?.status))
  )
    indexingPoll = setTimeout(async () => {
      try {
        await reload();
        render();
      } catch (e) {
        fail(e);
      }
    }, 2500);
}
function heading(title, sub, actions = '') {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${sub}</p></div><div class="heading-actions">${actions}</div></div>`;
}
function metric(label, value, sub, ic, highlight = false, tag = '') {
  return `<div class="metric ${highlight ? 'metric-highlight' : ''}"><div class="metric-top"><span>${label}</span>${icon(ic)}</div><div class="metric-value"><strong>${value}</strong>${tag ? `<span class="mini-tag">${tag}</span>` : ''}</div><div class="metric-label">${sub}</div></div>`;
}
function metricsRow() {
  const m = state.metrics;
  return `<div class="metrics-row">${metric('Total requests', m.total ?? 0, 'In this workspace', 'inbox')}${metric('Ready for review', m.review ?? 0, 'Drafts awaiting your decision', 'spark', true, 'AI + human')}${metric('Need attention', m.manual ?? 0, 'Manual reviews and escalations', 'human')}${metric('Preparation time · p50', ms(m.p50_ms), isDemo ? 'Demo: latency is not measured' : `Across ${m.analyzed ?? 0} analyzed ticket${m.analyzed === 1 ? '' : 's'}`, 'clock')}</div>`;
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
    case 'types':
      return requestTypesPage();
    case 'sources':
      return sourcesPage();
    case 'billing':
      return billingPage();
  }
}
function ticketsPage() {
  return `${onboarding()}${heading('Requests', 'A little less busywork. A lot more thoughtful support.', `<span class="date-label">${icon('clock')}${new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}</span><button class="btn btn-primary" data-action="new">${icon('plus')}New request</button>`)}${metricsRow()}<div class="queue-toolbar"><div class="filter-tabs" role="group" aria-label="Filter tickets">${[
    ['all', 'All requests'],
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
    )}</div><div class="toolbar-right"><label class="search-wrap">${icon('search')}<input id="ticket-search" aria-label="Search requests" placeholder="Search requests…" value="${esc(state.query)}"><kbd>⌘ K</kbd></label><button class="btn" data-action="sort" title="Change sort order">${icon('filter')}<span>${state.sort === 'newest' ? 'Newest' : 'Priority'}</span></button></div></div><section class="workbench" aria-label="Ticket workspace"><div class="ticket-list" data-scroll-key="${esc(account.organization?.id)}:queue"><div class="list-heading"><b>Request queue</b>${icon('down')}</div><div id="ticket-items">${ticketItems()}</div></div><div class="ticket-detail" id="ticket-detail" data-scroll-key="${esc(account.organization?.id)}:${esc(state.selected)}:detail">${detailContent()}</div></section><div class="bottom-line"><span><i class="status-dot"></i>${esc(businessName())} · ${retailDemo() ? 'Synthetic sample data' : 'Your workspace'}</span><span><kbd>N</kbd> New request <kbd>⌘ K</kbd> Search</span></div>`;
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
    : `<div class="empty">${icon('search')}No requests found<button class="btn" data-action="clear-search">Clear filters</button></div>`;
}
function detailContent() {
  if (state.detailLoading)
    return `<div class="empty" role="status">${icon('refresh', 'spinner')}Loading request…</div>`;
  if (state.detailError)
    return `<div class="empty" role="alert">Could not load this request.<button class="btn" data-action="retry-ticket">Try again</button></div>`;
  const t = state.detail;
  if (!t) return `<div class="empty">Select a request from the queue</div>`;
  const r = t.result;
  return `<div class="detail-top"><div class="ticket-id">${esc(t.id)}<span>•</span>${icon(channelIcon(t.channel))}${{ email: 'Email', chat: 'Chat', web: 'Web form', api: 'Inbound API', internal: 'Internal' }[t.channel] || esc(t.channel)}</div><div class="detail-top-right">${t.priority === 'high' ? '<span class="priority"><i class="priority-dot"></i>High priority</span>' : ''}<button class="icon-btn" data-action="copy-id" title="Copy ticket ID" aria-label="Copy ticket ID">${icon('copy')}</button></div></div><div class="detail-grid"><div class="conversation"><h2 class="ticket-title">${esc(t.subject)}</h2><div class="category-line">${(r?.categories?.length ? r.categories : [t.category]).map((c) => `<span class="category-tag">${categoryNames[c] || esc(c)}</span>`).join('')}${t.order_id ? `<span class="category-tag">Order #${esc(t.order_id)}</span>` : ''}</div><article class="message-card"><div class="message-head"><div class="avatar">${esc(initials(t.customer))}</div><div><strong>${esc(t.customer)}</strong><small>${esc(t.email || 'Requester')}</small></div><time class="message-time">${fmtDate(t.created_at)}</time></div><p>${esc(t.message).replace(/\n/g, '<br>')}</p></article><section class="ai-panel"><div class="ai-heading"><div class="ai-heading-title">${icon('spark')}SupportOps assistant</div><span class="badge">${retailDemo() ? 'Sample workflow' : r?.manifest?.engine === 'openai-agent-v1' ? 'OpenAI agent' : config.provider === 'baseline' ? 'Offline baseline' : 'OpenAI + verification'}</span></div>${state.busy === t.id ? `<div class="loading-line"></div><div class="empty">${icon('refresh', 'spinner')}Investigating the request and gathering evidence…</div>` : !r ? emptyDraft() : analysisContent(t, r)}</section>${reviewActions(t)}<p class="review-caption">${icon('shield')}Replies are never sent to customers automatically</p></div>${contextPanel(t, r)}</div>`;
}
function emptyDraft() {
  return `<div class="empty"><div class="waiting-illustration">${icon('spark')}</div><strong>From a question to a grounded reply</strong>Find the relevant policies<br>and prepare a reply with clear sources.<br><button class="btn btn-primary" data-action="analyze" ${state.busy ? 'disabled title="Wait for the current analysis to finish"' : config.demo_release && state.billing?.limit_reached && !retailDemo() ? 'disabled title="Demo AI allowance reached"' : ''}>${icon('spark')}Prepare a reply</button></div>`;
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
    return `<div class="manual-box">${icon('human')}<b>An agent needs to take a look</b><p>${esc(reasonNames[r.reason] || r.reason)}</p>${r.handoff_reason ? `<p>${esc(r.handoff_reason)}</p>` : ''}${r.verifier?.issues?.length ? `<p>${esc(r.verifier.issues.join(' '))}</p>` : ''}<p style="margin-top:10px;font-size:10px">No verified draft was created. Available context and sources are preserved in this ticket.</p>${!['approved', 'escalated'].includes(t.status) ? `<button class="btn" data-action="analyze" ${state.busy ? 'disabled title="Wait for the current analysis to finish"' : config.demo_release && state.billing?.limit_reached && !retailDemo() ? 'disabled title="Demo AI allowance reached"' : ''}>${icon('refresh')}Retry analysis</button>` : ''}</div>`;
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
  return `<div class="draft-body">${r.summary ? `<p class="agent-summary">${esc(r.summary)}</p>` : ''}${(r.tool_calls || []).map((c) => `<div class="workflow-node">${icon('link')}<span>${esc(c.name.replaceAll('_', ' '))}${c.result_count != null ? ' · ' + c.result_count + ' passages' : ''}</span><small>${ms(c.duration_ms)}</small></div>`).join('')}${(r.steps || []).map((s, i) => `<div class="workflow-node">${icon(s.status === 'error' ? 'warning' : 'checkcircle')}<span>${s.name === 'orders_api' && !retailDemo() ? 'Check workspace context' : stepNames[s.name] || esc(s.name)}</span><small>${ms(s.duration_ms)}</small></div>`).join('')}<div class="workflow-node">${icon('human')}<span>Human review</span><small>${['approved', 'escalated'].includes(state.detail.status) ? 'Completed' : 'Pending'}</small></div><p class="context-note">${isDemo ? 'Workflow steps are simulated in the browser. These are not server traces.' : 'Workflow state is saved in the persistent LangGraph checkpointer.'}</p><pre class="code-block">${esc(JSON.stringify(r.manifest || {}, null, 2))}</pre><button class="btn" data-action="export-trace">${icon('download')}Download result JSON</button></div>`;
}
function historyContent(t) {
  return `<div class="draft-body"><div class="history-event">Request created<small>${fmtDate(t.created_at, { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}</small></div>${t.result ? `<div class="history-event">${t.result.reason ? 'Sent for manual review' : 'Draft prepared'}<small>Revision ${t.revision} · ${esc(t.result.manifest?.model || 'workflow')}</small></div>` : ''}${(t.reviews || []).map((r) => `<div class="history-event">${r.decision === 'approve' ? 'Reply approved' : 'Request escalated'}<small>${fmtDate(r.created_at, { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })} · ${esc(r.actor || 'Agent')}</small>${r.note ? `<p>${esc(r.note)}</p>` : ''}</div>`).join('')}</div>`;
}
function reviewActions(t) {
  if (t.status === 'approved')
    return `<div class="completed-box">${icon('checkcircle')}Reply approved. You can copy it to send to the customer.</div>`;
  if (t.status === 'escalated')
    return `<div class="completed-box">${icon('human')}Request escalated. The decision is saved in its history.</div>`;
  if (!t.result || state.busy) return '';
  return `<div class="review-actions">${t.status === 'review' ? `<button class="btn btn-primary" data-action="approve">${icon('check')}Approve reply</button>` : ''}<button class="btn" data-action="escalate">${icon('human')}Escalate to an agent</button>${config.faults_enabled && retailDemo() ? `<button class="icon-btn" data-action="faults" title="Test a failure scenario" aria-label="Test a failure scenario">${icon('code')}</button>` : ''}</div>`;
}
function contextPanel(t, r) {
  if (!retailDemo())
    return `<aside class="context-panel"><div class="context-section"><h3 class="context-heading">${icon('human')}Customer</h3><div class="context-customer"><div class="avatar">${esc(initials(t.customer))}</div><div><strong>${esc(t.customer)}</strong><small>${esc(t.email || 'No contact provided')}</small></div></div><div class="kv"><span>Channel</span><strong>${esc(t.channel)}</strong></div></div><div class="context-section"><h3 class="context-heading">${icon('grid')}Request workflow</h3><div class="kv"><span>Type</span><strong>${esc(state.types.find((x) => x.id === t.request_type_id)?.name || 'General')}</strong></div><div class="kv"><span>Owner</span><strong>${esc(state.people.find((x) => x.id === t.assigned_to)?.name || (t.assigned_to ? 'Former member' : 'Unassigned'))}</strong></div><button class="btn" data-action="route-request">Route request</button></div><div class="context-section"><h3 class="context-heading">${icon('book')}Business knowledge</h3><strong>${esc(businessName())}</strong><p class="context-note">${r?.sources?.length || 0} relevant policy excerpts${r?.template ? ' · template revision ' + r.template.revision : ''}</p><p class="context-note">Replies use this workspace’s current policies and templates. Check the sources before approving.</p></div><div class="context-section"><h3 class="context-heading">${icon('shield')}Original draft checks</h3>${r?.checks?.length ? `<div class="check-list">${r.checks.map((c) => `<div class="check-item ${c.passed ? '' : 'failed'}">${icon(c.passed ? 'checkcircle' : 'warning')}<span>${esc(c.label)}</span></div>`).join('')}</div>` : '<p class="context-note">Checks run when you prepare a reply.</p>'}${r?.trace_id ? `<button class="trace-link" data-action="export-trace">${icon('activity')}View analysis record ${icon('external')}</button>` : ''}</div></aside>`;

  const o = r?.order;
  const statuses = {
    delayed: 'Delivery delayed',
    shipped: 'In transit',
    processing: 'Processing',
    delivered: 'Delivered',
    cancelled: 'Cancelled',
  };
  return `<aside class="context-panel"><div class="context-section"><h3 class="context-heading">${icon('box')}Order details</h3>${o ? `<div class="order-title">#${esc(o.id)}<button class="icon-btn" data-action="order-info" aria-label="View order data">${icon('external')}</button></div><span class="badge ${o.status === 'delayed' ? 'badge-manual' : 'badge-review'}">${icon('truck')}${statuses[o.status]}</span>${o.items.map((item) => `<div class="order-product"><div class="product-icon">${icon('box')}</div><div><b>${esc(item.title)}</b><small>${item.quantity} item(s) · ${esc(businessName())}</small></div></div>`).join('')}<div class="kv"><span>Order total</span><strong>${o.total.toLocaleString('en-GB')} ₽</strong></div><div class="kv"><span>Order placed</span><strong>${fmtDate(o.placed_at)}</strong></div><div class="kv"><span>Expected delivery</span><strong>${fmtDate(o.expected_at)}</strong></div><div class="kv"><span>Carrier</span><strong>${esc(o.carrier)}</strong></div>${o.tracking ? `<div class="kv"><span>Tracking number</span><strong>${esc(o.tracking)}</strong></div>` : ''}<p class="context-note">${icon('link')} ${isDemo ? 'Synthetic store data' : 'Retrieved from the store API'}</p>` : `<div class="context-note">${t.order_id ? `Order #${esc(t.order_id)}` : 'No order number provided'}<br>${retailDemo() ? 'Order details will appear after analysis.' : 'No live order integration is connected. Your policies are used as the source of truth.'}</div>`}</div><div class="context-section"><h3 class="context-heading">${icon('human')}Customer</h3><div class="context-customer"><div class="avatar">${esc(initials(t.customer))}</div><div><strong>${esc(t.customer)}</strong><small>${esc(t.email || 'No contact provided')}</small></div></div><div class="kv"><span>Channel</span><strong>${{ email: 'Email', chat: 'Chat', web: 'Web form', api: 'Inbound API', internal: 'Internal' }[t.channel] || esc(t.channel)}</strong></div><div class="kv"><span>Business</span><strong>${esc(businessName())}</strong></div></div><div class="context-section"><h3 class="context-heading">${icon('shield')}Original draft checks</h3>${r?.checks?.length ? `<div class="check-list">${r.checks.map((c) => `<div class="check-item ${c.passed ? '' : 'failed'}">${icon(c.passed ? 'checkcircle' : 'warning')}<span>${esc(c.label)}</span></div>`).join('')}</div>` : '<p class="context-note">Checks run when you prepare a reply.</p>'}${r?.trace_id ? `<button class="trace-link" data-action="export-trace">${icon('activity')}<code>trace: ${esc(r.trace_id.slice(0, 16))}…</code>${icon('external')}</button>` : ''}</div></aside>`;
}

function overviewPage() {
  const m = state.metrics;
  return `${heading('Your support, at a glance', 'A clear view of your queue, reply quality and operations.', `<button class="btn btn-primary" data-action="new">${icon('plus')}New request</button>`)}${metricsRow()}<div class="section-grid"><section class="panel"><h2>Queue by status</h2><p>A live snapshot of your ticket workspace</p>${[
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
  return `${heading('Knowledge base', 'Your business knowledge, with clear sources, versions and effective dates.', `<button class="btn" data-action="export-knowledge">${icon('download')}Export JSON</button>${canManage() ? `<button class="btn" data-action="upload-policy">${icon('download')}Upload file</button><button class="btn btn-primary" data-action="add-policy">${icon('plus')}Add policy</button>` : ''}`)}<div class="info-strip">${icon('shield')}Only current, unarchived policies from this workspace are used in replies. Earlier versions remain available for reference.</div><div class="queue-toolbar"><div class="filter-tabs"><button class="filter-tab ${!state.showArchived ? 'active' : ''}" data-archive="false">Current policies <span>${state.docs.filter(active).length}</span></button><button class="filter-tab ${state.showArchived ? 'active' : ''}" data-archive="true">All versions <span>${state.docs.length}</span></button></div><label class="search-wrap">${icon('search')}<input id="doc-search" value="${esc(state.docQuery)}" placeholder="Search policies…" aria-label="Search the knowledge base"></label></div><div id="document-cards" class="document-grid">${docs.map((d) => `<button class="document-card" data-doc="${esc(d.id)}"><span class="document-icon">${icon('file')}</span><h3>${esc(d.title)}</h3><p>${esc(d.text)}</p><div class="source-meta"><span>v${esc(d.version)}</span><span>From ${fmtDate(d.effective_from)}</span>${!retailDemo() ? `<span class="index-state ${d.indexing?.status === 'ready' ? 'ready' : ''}">${esc(d.indexing?.status === 'ready' ? 'Semantic index ready' : d.indexing?.status || 'Not indexed')}</span>` : ''}<span class="badge ${active(d) ? 'badge-review' : ''}">${active(d) ? 'Active' : 'Archived'}</span></div></button>`).join('') || '<div class="empty">No policies found</div>'}</div>`;
}
function qualityPage() {
  if (!retailDemo())
    return `${heading('Reply quality', 'Evidence and human judgment, on every proposed reply.')}<div class="section-grid"><section class="panel"><h2>Your review process</h2><div class="check-list">${['Only this workspace’s current policies are retrieved', 'The agent drafts an answer with paragraph-level citations', 'Missing evidence routes a request to an agent', 'A separate model checks grounding; an employee reviews the result', 'Approved replies retain their sources and template version'].map((c) => `<div class="check-item">${icon('checkcircle')}<span>${c}</span></div>`).join('')}</div></section><section class="panel"><h2>Business evaluation</h2><p>Each proposed reply is checked against your current workspace sources.</p><div class="notice-card">${icon('info')}Review the cited sources and the proposed answer before approving it. If evidence is missing or a check fails, the request is sent for manual review.</div></section></div>`;
  const e = state.evaluation;
  const ready = e.status === 'passed' || e.status === 'failed';
  return `${heading('AI Release Guard', 'Evaluate changes before they ship. Release with evidence.', `<button class="btn" data-action="export-eval">${icon('download')}Download report</button>`)}<div class="info-strip">${icon('info')}${isDemo ? 'A snapshot of local evaluation results. The demo does not call a live model.' : 'Evaluation runs separately and gates the CI build.'}</div>${ready ? `<div class="metrics-row">${metric('Factual accuracy', `${Math.round(e.metrics.factual_accuracy * 100)}%`, 'Checks for order status, facts and sources', 'checkcircle', true)}${metric('Classification · macro-F1', e.metrics.macro_f1.toFixed(2), 'Against hand-labeled synthetic cases', 'inbox')}${metric('Escalation recall', `${Math.round(e.metrics.escalation_recall * 100)}%`, 'Requests that must reach a human', 'human')}${metric('Latency · p95', ms(e.metrics.p95_ms), `${e.cases} cases · sequential execution`, 'clock')}</div><div class="section-grid"><section class="panel"><h2>Release evaluation</h2><p>Dataset ${esc(e.dataset_version)} · ${esc(e.split)}</p><div class="quality-score"><div class="score-ring"><b>${e.passed}/${e.cases}</b><small>cases passed</small></div><div><h3>${e.status === 'passed' ? 'All checks passed' : 'Release blocked'}</h3><p>Model: ${esc(e.model)}<br>${fmtDate(e.created_at, { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' })}<br>${e.live ? 'Live run with an external model' : 'Deterministic baseline · no LLM spend'}</p></div></div><button class="btn" data-action="eval-info">${icon('info')}About the sample checks</button></section><section class="panel"><h2>What the guard checks</h2><p>A failed critical check blocks the pipeline</p><div class="check-list">${['Current return policies are applied', 'Shipped orders are never automatically cancelled', 'API outages route tickets to an agent', 'The model cannot add unsupported facts', 'Repeated approvals do not create duplicates'].map((c) => `<div class="check-item">${icon('checkcircle')}<span>${c}</span></div>`).join('')}</div><div class="notice-card">${icon('info')}These metrics cover the synthetic dataset. Agent productivity and live-model quality require a separate study.</div></section></div><section class="panel"><h2>Scenario results</h2><p>Isolated ${esc(e.split)} dataset · seed ${e.seed}</p><div class="table-scroll"><table class="data-table"><thead><tr><th>Scenario</th><th>Expected category</th><th>Result</th><th>Time</th></tr></thead><tbody>${(e.results || []).map((r) => `<tr><td>${esc(r.id)}<br><code>${esc(r.description)}</code></td><td>${(r.expected_categories || []).map((c) => categoryNames[c]).join(', ')}</td><td><span class="badge ${r.passed ? 'badge-review' : 'badge-manual'}">${icon(r.passed ? 'check' : 'warning')}${r.passed ? 'Passed' : 'Failed'}</span></td><td>${ms(r.duration_ms)}</td></tr>`).join('')}</tbody></table></div></section>` : `<section class="panel"><div class="empty">${icon('shield')}<strong>No sample report available</strong>The sample report is temporarily unavailable. Your workspace's source checks and human review remain available.<button class="btn" data-action="refresh">${icon('refresh')}Refresh</button></div></section>`}`;
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
  workspaceEpoch++;
  reloadVersion++;
  selectionVersion++;
  viewState.clear();
  Object.assign(state, {
    tickets: [],
    docs: [],
    templates: [],
    types: [],
    intakes: [],
    people: [],
    billing: {},
    members: [],
    invitations: [],
    audit: [],
    metrics: {},
    evaluation: {},
    detail: null,
    detailLoading: false,
    detailError: false,
    busy: false,
    selected: null,
    drafts: {},
    filter: 'all',
    sort: 'newest',
    tab: 'draft',
    query: '',
    page: 'tickets',
    docQuery: '',
    showArchived: false,
  });
}
function publicPage() {
  const join = location.hash.startsWith('#join/');
  const mode = join ? 'join' : location.hash === '#login' ? 'login' : 'register';
  const registrationClosed =
    !account && mode === 'register' && config.registration_available === false;
  document.title = 'SupportOps AI · Support that knows your business';
  const page = `<div class="public-shell"><header class="public-header"><a class="brand" href="#register"><span class="brand-mark">${icon('logo')}</span><span>SupportOps</span><em>AI</em></a><nav class="landing-nav"><a href="#how-it-works" data-scrollto="how-it-works">How it works</a><a href="#request-sources" data-scrollto="request-sources">Channels</a><a href="#${config.demo_release ? 'demo-access' : 'pricing'}" data-scrollto="${config.demo_release ? 'demo-access' : 'pricing'}">${config.demo_release ? 'Demo access' : 'Pricing'}</a></nav><a class="btn" href="${account ? '#tickets' : mode === 'login' ? '#register' : '#login'}">${account ? 'Open workspace' : mode === 'login' ? 'Create a workspace' : 'Sign in'} ${icon('arrow')}</a></header><main class="welcome-grid"><section class="welcome-story"><span class="eyebrow">${config.demo_release ? 'DEMO VERSION · ' : ''}YOUR BUSINESS. YOUR KNOWLEDGE.</span><h1>Every request. <br>The right context. <br><em>A clearer next step.</em></h1><p>An AI workspace for the requests that keep your business moving. Connect incoming work, bring your knowledge and keep your people in control.</p><div class="welcome-steps">${[
    ['01', 'Bring your knowledge', 'Upload your rules, procedures and service policies.'],
    ['02', 'Make it sound like you', 'Create reply templates that match your brand.'],
    ['03', 'Work better, together', 'Invite your team. Review every reply before sharing.'],
  ]
    .map(([n, t, d]) => `<div><span>${n}</span><section><h3>${t}</h3><p>${d}</p></section></div>`)
    .join(
      '',
    )}</div><div class="welcome-proof">${icon('shield')}A separate workspace for every business.<br>Your team decides what reaches your customers.</div></section><section class="signup-card" id="signup"><span class="badge badge-review">${icon(join ? 'human' : 'spark')}${join ? 'You’re invited' : mode === 'login' ? 'Welcome back' : 'A fresh start for your team'}</span><h2>${account ? 'Your business workspace' : join ? 'Join your team' : mode === 'login' ? 'Sign in to your workspace' : 'Create your workspace'}</h2><p>${join ? 'Use the email address your invitation was sent to. Already have an account? Enter your existing password.' : mode === 'login' ? 'Your requests, knowledge and team are right where you left them.' : 'Start with your business. Add your knowledge as you go.'}</p><form id="platform-auth" data-mode="${mode}">${mode !== 'login' ? '<label class="field">Your full name<input name="name" required minlength="2" maxlength="100" autocomplete="name" placeholder="Alex Morgan"></label>' : ''}${mode === 'register' ? '<label class="field">Business name<input name="business_name" required minlength="2" maxlength="120" autocomplete="organization" placeholder="Acme Services"></label><label class="field">Industry<input name="industry" maxlength="80" placeholder="e.g. Hospitality, consulting, retail" value="General business"></label>' : ''}<label class="field">Work email<input name="email" type="email" required maxlength="160" autocomplete="email" placeholder="alex@yourcompany.com"></label><label class="field">Password<input name="password" type="password" required minlength="${mode === 'login' ? 1 : 12}" maxlength="128" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" placeholder="${mode === 'login' ? 'Your password' : 'At least 12 characters'}"></label>${mode === 'register' && config.captcha_site_key && !account ? '<div id="signup-captcha"></div>' : ''}<div class="form-error" role="alert"></div><button class="btn btn-primary auth-submit" type="submit">${join ? 'Accept invitation' : mode === 'login' ? 'Sign in' : 'Create workspace'} ${icon('arrow')}</button></form>${mode === 'login' ? '<a class="forgot-link" href="#forgot">Forgot your password?</a>' : ''}${config.demo_available && !join && !account ? '<div class="auth-divider"><span>Want a look around first?</span></div><button class="btn auth-submit" data-action="start-demo">Explore a sample workspace</button><p class="auth-footnote">A separate demo with synthetic tickets. Your business starts with a clean workspace.</p>' : ''}<p class="auth-footnote">${join ? 'Your workspace owner sets your role and access.' : mode === 'login' ? 'Need access to a team? Ask your workspace owner for an invitation link.' : 'By creating an account, you agree to the <a href="/terms" target="_blank" rel="noopener">Service terms</a> and acknowledge the <a href="/privacy" target="_blank" rel="noopener">Privacy notice</a>.'}</p></section></main>${mode === 'register' ? landingSections() : ''}<footer class="public-footer"><span>SupportOps AI</span><span><a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · Your people in control.</span></footer></div>`;
  const visiblePage = registrationClosed
    ? page.replace(
        /<section class="signup-card" id="signup">[\s\S]*?<\/section><\/main>/,
        `<section class="signup-card" id="signup"><span class="badge">Limited early access</span><h2>Registration is currently unavailable</h2><p>All business workspace places in this demo release are taken. Please check back later.</p><p>Existing users can sign in. Invited employees can still join their team using an invitation link.</p><a class="btn btn-primary auth-submit" href="#login">Sign in ${icon('arrow')}</a></section></main>`,
      )
    : page;
  return account
    ? visiblePage.replace(
        /<form id="platform-auth"[\s\S]*?<\/form>/,
        `<div class="account-welcome"><h3>Welcome back, ${esc(account.user.name)}</h3><p>Your workspace is ready.</p><a class="btn btn-primary auth-submit" href="#tickets">Open ${esc(account.organization?.name || 'workspace')} ${icon('arrow')}</a></div>`,
      )
    : visiblePage;
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
  return `${heading('Workspace settings', 'A home for your business and your team.')}<section class="panel"><h2>${esc(businessName())}</h2><p>${esc(org?.role || 'No workspace')} access · ${esc(account.user.email)}</p>${org && canManage() ? `<form id="workspace-settings"><div class="form-grid"><label class="field">Business name<input name="name" value="${esc(org.name)}" required minlength="2" maxlength="120"></label><label class="field">Industry<input name="industry" value="${esc(org.industry || '')}" required maxlength="80"></label></div><button class="btn btn-primary">Save changes</button></form>` : '<p>Your owner or admin manages business settings.</p>'}</section><section class="panel"><h2>Your workspaces</h2><p>Each business has its own policies, templates, tickets and team.</p>${account.memberships.map((m) => `<div class="setting-row"><div><h3>${esc(m.name)}</h3><p>${esc(m.industry)} · ${esc(m.role)}${m.is_demo ? ' · Sample workspace' : ''}</p></div>${m.id === org?.id ? '<span class="badge badge-review">Current</span>' : `<button class="btn" data-switch="${esc(m.id)}">Switch workspace</button>`}</div>`).join('')}<button class="btn" data-action="${org?.is_demo ? 'register-business' : 'new-workspace'}" style="margin-top:18px">${icon('plus')}${org?.is_demo ? 'Register your business' : 'Create another business'}</button></section><section class="panel"><h2>Your account and data</h2><p>Download your account information or record a deletion request for operator review. Shared business records are handled separately.</p><button class="btn" data-action="export-account">Export my account</button> <button class="btn" data-action="email-deliveries">Email delivery status</button> <button class="btn" data-action="request-deletion">Request account deletion</button></section><section class="panel"><h2>How replies work</h2><p>Current policies are retrieved only from this workspace. Your selected template wraps the verified excerpts. An employee reviews every reply before copying it to a customer.</p><div class="notice-card">${icon('info')}Customer account and order systems are not connected to new workspaces. Requests that need live account facts require a human check. Approved replies are saved here; they are not sent automatically.</div></section>${canManage() ? `<section class="panel"><h2>Business data</h2><p>Owners can export the workspace’s requests, knowledge, routing and review history.</p><button class="btn" data-action="export-workspace">Export workspace JSON</button></section><section class="panel"><h2>Service status</h2><p>Check background processing and email delivery for this workspace.</p><button class="btn" data-action="operations-status">Background jobs</button></section><section class="panel"><h2>Recent workspace activity</h2><div class="table-scroll"><table class="data-table"><thead><tr><th>Action</th><th>When</th><th>Details</th></tr></thead><tbody>${state.audit.map((a) => `<tr><td>${esc(a.action.replaceAll('_', ' '))}</td><td>${fmtDate(a.created_at)}</td><td><code>${esc(JSON.stringify(a.details))}</code></td></tr>`).join('') || '<tr><td colspan="3">Activity will appear as your team uses the workspace.</td></tr>'}</tbody></table></div></section>` : ''}`;
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
    if (switchingWorkspace) return true;
    switchingWorkspace = true;
    document.querySelectorAll('[data-switch]').forEach((button) => {
      button.disabled = true;
    });
    try {
      await authenticate('/auth/switch', { organization_id: el.dataset.switch });
      await enteredWorkspace();
    } finally {
      switchingWorkspace = false;
      document.querySelectorAll('[data-switch]').forEach((button) => {
        button.disabled = false;
      });
    }
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
  if (button.disabled) return true;
  button.disabled = true;
  const error = form.querySelector('.form-error');
  if (error) error.textContent = '';
  try {
    const data = Object.fromEntries(new FormData(form));
    if (form.id === 'platform-auth') {
      const mode = form.dataset.mode;
      if (mode === 'register' && signupWidget != null && window.turnstile) {
        data.captcha_token = window.turnstile.getResponse(signupWidget);
        delete data['cf-turnstile-response'];
      }
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
    if (
      form.id === 'platform-auth' &&
      form.dataset.mode === 'register' &&
      e.status === 503 &&
      e.message.startsWith('Registration is currently unavailable.')
    ) {
      config.registration_available = false;
      render();
      return true;
    }
    if (form.id === 'platform-auth' && signupWidget != null && window.turnstile)
      window.turnstile.reset(signupWidget);
    if (error) error.textContent = e.message;
    else fail(e);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
  return true;
}

function landingSections() {
  return `<section class="landing-section" id="how-it-works"><div class="section-intro"><span class="eyebrow">FROM REQUEST TO A REVIEWED RESULT</span><h2>One clear process.<br>A thousand kinds of requests.</h2><p>Bring the question, the context and the right people together. SupportOps helps your team investigate, prepare and review.</p></div><div class="landing-steps">${[
    [
      '01',
      'Connect your incoming work',
      'Share a request form, connect your website or CRM through the inbound API, or let employees create requests internally.',
      'globe',
    ],
    [
      '02',
      'Give AI your business context',
      'Upload procedures, policies and playbooks. Semantic search finds relevant passages, even when people use different words.',
      'book',
    ],
    [
      '03',
      'Let the agent investigate',
      'The agent searches, reads sources and prepares a grounded answer. A separate model checks it before your team sees a draft.',
      'spark',
    ],
    [
      '04',
      'Keep the decision with your team',
      'Assign a request, check the evidence, edit the result and approve or escalate. No external action happens automatically.',
      'human',
    ],
  ]
    .map(
      ([n, t, d, i]) =>
        `<article><span class="step-number">${n}</span>${icon(i)}<h3>${t}</h3><p>${d}</p></article>`,
    )
    .join(
      '',
    )}</div></section><section class="landing-section usecases-section" id="use-cases"><div class="section-intro"><span class="eyebrow">BUILT AROUND YOUR BUSINESS</span><h2>Every team has a queue.<br>Make yours work smarter.</h2></div><div class="usecase-grid">${[
    [
      'Operations',
      'Vendor questions, travel expenses, facilities and purchasing requests.',
      'grid',
    ],
    [
      'Sales & customer teams',
      'Product questions, service inquiries and requests that need a thoughtful response.',
      'chat',
    ],
    [
      'People & IT',
      'Internal procedures, onboarding questions and employee service requests.',
      'human',
    ],
  ]
    .map(
      ([t, d, i]) =>
        `<article>${icon(i)}<h3>${t}</h3><p>${d}</p><span>Your types. Your process. Your reviewers.</span></article>`,
    )
    .join(
      '',
    )}</div><p class="landing-footnote">AI supports investigation and drafting. Sensitive decisions, approvals and actions stay with authorized people.</p></section><section class="landing-section" id="request-sources"><div class="section-intro"><span class="eyebrow">WHERE REQUESTS COME FROM</span><h2>Meet work where it starts.</h2><p>New requests arrive in your company’s shared queue, with their source and request type attached.</p></div><div class="source-channels"><article><span class="channel-state ${config.public_forms_available === false ? '' : 'live'}">${config.public_forms_available === false ? 'Unavailable in this demo' : 'Available'}</span>${icon('globe')}<h3>Your request form</h3><p>${config.public_forms_available === false ? 'Use an inbound API connection or let your employees create requests in the workspace.' : 'Create a hosted form for customers, partners or other external requesters. Share its link from your website.'}</p></article><article><span class="channel-state live">Available</span>${icon('link')}<h3>Inbound API</h3><p>Send requests from a website, CRM or existing workflow with a dedicated write-only API token and duplicate protection.</p></article><article><span class="channel-state live">Available</span>${icon('human')}<h3>Your employees</h3><p>Team members create internal requests, choose a type and assign work to the right person in the workspace.</p></article><article><span class="channel-state">Requires integration</span>${icon('mail')}<h3>Email & messengers</h3><p>Connect your mail or messaging provider through an API adapter. Email and messenger requests can be imported through your business integration.</p></article></div></section><section class="landing-section intelligence-section" id="ai-workflow"><div><span class="eyebrow">ANSWERS WITH A SOURCE</span><h2>An agent that starts<br>with your knowledge.</h2><p>OpenAI language models investigate requests with read-only tools. Real embeddings power semantic retrieval across your current documents. The agent can search again, inspect a passage or hand the request to a person.</p><ul>${['Company-scoped search and versioned sources', 'Paragraph-level citations and a separate grounding check', 'Your templates and request-type instructions', 'Explicit handoff when evidence or access is missing'].map((x) => `<li>${icon('checkcircle')}${x}</li>`).join('')}</ul></div><div class="agent-illustration"><div class="agent-illustration-head">${icon('spark')}Your business agent<span>Human review required</span></div>${[
    ['Request', '“How do I claim my hotel expenses?”'],
    ['Search & read', 'Travel policy · current version'],
    ['Prepare', 'A clear answer, with cited evidence'],
    ['Verify & review', 'Model grounding check → your employee'],
  ]
    .map(
      ([a, b], i) =>
        `<div class="agent-flow-row"><b>${i + 1}</b><span><small>${a}</small>${b}</span>${icon(i === 3 ? 'human' : 'arrow')}</div>`,
    )
    .join(
      '',
    )}<p>Illustrative workflow · your actual rules determine the answer</p></div></section>${config.demo_release ? demoAccessSection() : `<section class="landing-section" id="pricing"><div class="section-intro"><span class="eyebrow">PREDICTABLE PLANS. CONTROLLED AI SPEND.</span><h2>Start small.<br>Grow with a clear budget.</h2><p>A workspace subscription for your team, with included AI credits. No automatic overage charges.</p></div>${planCards(false)}<p class="landing-footnote">14-day trial with 100 one-time AI credits and 3 team seats. AI credit use depends on tokens, document indexing and agent work. Paid credits renew with your billing period and do not roll over. Prices are in USD per workspace per month; applicable taxes may apply at checkout.</p></section>`}<section class="landing-section trust-section"><div><span class="eyebrow">CLEAR BOUNDARIES</span><h2>Your workspace.<br>Your people in control.</h2></div><div>${[
    [
      'Separate business data',
      'Your requests, documents and team access are scoped to your company.',
    ],
    [
      'Role-based access',
      'Owners and admins manage knowledge and access. Agents investigate and review requests.',
    ],
    [
      'Visible usage',
      'See your AI usage and remaining allowance. Paid model calls pause when the demo budget is used.',
    ],
    [
      'No silent actions',
      'Approving saves a reviewed result. Sending messages, issuing refunds or changing external records requires a separate integration.',
    ],
  ]
    .map(([t, d]) => `<article><h3>${icon('shield')}${t}</h3><p>${d}</p></article>`)
    .join(
      '',
    )}</div></section><section class="landing-faq landing-section"><div class="section-intro"><span class="eyebrow">A FEW PRACTICAL ANSWERS</span><h2>Know what you’re starting.</h2></div>${[
    [
      'Is this only for customer support?',
      'No. Configure request types for operations, sales, IT, people or customer teams. Each type can include instructions for the agent and be internal or available to a public form.',
    ],
    [
      'Where do requests come from?',
      config.public_forms_available === false
        ? 'From an inbound API connection or your employees. Connect your website, CRM, email or messaging tools through the API. Public forms are unavailable in this demo.'
        : 'From your hosted form, a scoped inbound API, or your employees. Email and messaging tools can send requests through an API integration.',
    ],
    [
      'What happens when the demo AI allowance runs out?',
      'Paid AI processing and document indexing pause at the account limit. Your team can still create, read and manually handle requests. Purchasing is coming soon. We’ll email you when it launches if you reach the limit or request a notification.',
    ],
    [
      'Can I use my existing procedures?',
      'Yes. Upload selectable-text PDF, DOCX, TXT, Markdown or JSON files. Publish new versions when your rules change. The agent uses the current indexed version.',
    ],
    [
      'Will AI answer people or approve requests automatically?',
      'No. The agent produces a proposal and evidence for your team. An employee reviews the result before taking the next step.',
    ],
  ]
    .map(([q, a]) => `<details><summary>${q}<span>+</span></summary><p>${a}</p></details>`)
    .join(
      '',
    )}<div class="landing-final-cta"><h2>Bring your next request<br>into a clearer process.</h2><button class="btn btn-primary" data-scrollto="signup">${config.registration_available === false ? 'Registration unavailable' : 'Create your workspace'} ${icon('arrow')}</button></div></section>`;
}
function demoAccessSection() {
  return `<section class="landing-section" id="demo-access"><div class="section-intro"><span class="eyebrow">DEMO VERSION · LIMITED EARLY ACCESS</span><h2>Try your real workflow.<br>With a clear AI allowance.</h2><p>SupportOps AI is in a limited demo release for ${Number(config.workspace_limit || 10)} business workspaces. Each account includes $${Number(config.demo_ai_budget_usd || 5).toFixed(2)} of one-time AI usage per account across all your businesses.</p></div><div class="section-grid"><section class="panel"><h3>Explore with your team</h3><p>Create requests, invite colleagues, upload your rules and review drafts. Only calls to paid AI models count toward your allowance, including embeddings and verification.</p></section><section class="panel"><h3>Purchasing is coming soon</h3><p>When your AI allowance is used, paid AI calls pause. Your workspace stays available for manual work. We’ll email you when purchasing becomes available.</p><button class="btn btn-primary" ${account ? 'data-action="notify-launch"' : 'data-scrollto="signup"'}>${account ? 'Email me at launch' : config.registration_available === false ? 'Registration unavailable' : 'Try the demo'} ${icon('arrow')}</button></section></div></section>`;
}
function demoBudgetBanner() {
  if (!config.demo_release || isDemo) return '';
  const b = state.billing || {};
  return `<div class="info-strip demo-release-strip" role="status">${icon(b.limit_reached ? 'warning' : 'info')}<div><strong>${b.limit_reached ? 'Demo AI allowance reached' : 'Demo version · purchasing coming soon'}</strong><p>${b.limit_reached ? esc(b.message) : `Your account includes $${Number(b.limit_usd || config.demo_ai_budget_usd || 5).toFixed(2)} of one-time AI usage. ${b.launch_email_opt_out ? 'You’ve opted out of launch emails. You can change this in AI usage.' : 'We’ll email you when purchasing launches if you reach the limit or ask to be notified.'}`}</p></div><a class="btn" href="#billing">View AI usage</a></div>`;
}
function demoUsagePage() {
  const b = state.billing || {},
    money = (n) =>
      Number(n || 0).toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 6,
      });
  return `${heading('Demo AI usage', 'A one-time allowance per account, shared across all your workspaces.')}<div class="metrics-row">${metric('AI allowance', money(b.limit_usd || config.demo_ai_budget_usd || 5), 'No monthly reset or expiry', 'grid')}${metric('Model usage', money(b.spent_usd), 'Embeddings, agent and verifier', 'spark')}${metric('Available now', money(b.remaining_usd), `${money(b.reserved_usd)} reserved by running work`, 'activity')}</div><section class="panel"><h2>${b.limit_reached ? 'Your demo AI allowance is used' : 'Your workspace is in demo'}</h2><p>${esc(b.message || 'Purchasing will be available soon.')}</p><div class="notice-card">${icon('info')}Only paid model operations use this allowance. Creating and reviewing requests, managing your team, editing templates and reading knowledge remain available. Costs are estimated from provider token usage and configured prices; unknown provider charges retain a reserve until reconciled.</div></section><section class="panel"><h2>Purchasing is coming soon</h2><p>${b.launch_email_requested ? `You’re on the launch notification list. We’ll email ${esc(account.user.email)} when purchasing becomes available.` : b.launch_email_opt_out ? 'You’ve opted out of launch emails. You can ask to be notified again at any time.' : 'Ask for an email when purchasing becomes available. You can also keep exploring the demo.'}</p>${b.launch_email_requested ? '<span class="badge badge-review">Launch email requested</span> <button class="btn" data-action="cancel-launch-notification">Cancel launch email</button>' : '<button class="btn btn-primary" data-action="notify-launch">Email me at launch</button>'}</section>`;
}
function planCards(inWorkspace = true) {
  return `<div class="pricing-grid">${state.plans
    .filter((p) => p.id !== 'pilot')
    .map(
      (p) =>
        `<article class="pricing-card ${p.id === 'growth' ? 'recommended' : ''}">${p.id === 'growth' ? '<span class="pricing-recommended">ROOM TO GROW</span>' : ''}<h3>${esc(p.name)}</h3><p>${{ starter: 'For a small team building its first shared workflow.', growth: 'For several teams with a steady flow of requests.', scale: 'For a growing operation with more knowledge and people.' }[p.id] || ''}</p><div class="plan-price">$${p.price_usd}<span>/ month</span></div><small>per business workspace</small><ul><li>${icon('check')}${p.credits.toLocaleString('en-US')} AI credits / period</li><li>${icon('check')}${p.seats} team seats</li><li>${icon('check')}${p.requests.toLocaleString('en-US')} requests / period</li><li>${icon('check')}${p.chunks.toLocaleString('en-US')} knowledge chunks</li><li>${icon('check')}Forms, API & internal requests</li><li>${icon('check')}Agent, citations & human review</li></ul>${inWorkspace ? `<button class="btn ${p.id === 'growth' ? 'btn-primary' : ''} auth-submit" data-checkout="${esc(p.id)}" ${!p.checkout_available || account?.organization?.role !== 'owner' ? 'disabled' : ''}>${p.checkout_available ? 'Choose ' + esc(p.name) : 'Purchasing unavailable'} ${icon('arrow')}</button>` : `<button class="btn ${p.id === 'growth' ? 'btn-primary' : ''} auth-submit" data-scrollto="signup">Start with a free trial ${icon('arrow')}</button>`}</article>`,
    )
    .join('')}</div>`;
}
function billingPage() {
  if (config.demo_release) return demoUsagePage();
  const b = state.billing || {},
    p = b.plan || {},
    used = b.used || {};
  return `${heading('Plan & usage', 'One subscription for your business. A clear limit on AI spend.', account.organization?.role === 'owner' && b.portal_enabled ? '<button class="btn" data-action="billing-portal">Manage subscription</button>' : '')}<div class="metrics-row">${metric('Current plan', esc(p.name || 'Trial'), esc(p.status || 'trial'), 'grid')}${metric('AI credits available', b.credits_remaining ?? 0, `${used.credits || 0} used in this period`, 'spark', true)}${metric('Team seats', p.seats || 3, 'Shared across your workspace', 'human')}${metric('Period ends', fmtDate(p.reset_at), p.plan === 'pilot' ? 'One-time trial balance' : 'Credits renew with your paid period', 'clock')}</div><section class="panel"><h2>How your AI balance works</h2><p>${esc(b.credit_policy || '')}</p><div class="notice-card">${icon('info')}A run temporarily reserves up to ${b.reservation_credits || 90} available credits to cover tool use and verification. Only the actual rounded usage is kept after a completed run. Long documents and multi-step investigations use more credits. 100 credits is not a promise of 100 replies.</div>${!b.checkout_enabled ? '<p class="form-hint">Purchasing is not available in this release. No payment is taken from this screen.</p>' : ''}</section>${planCards()}<p class="form-hint">Prices in USD per workspace per month. Applicable taxes may apply at checkout. Upgrades and cancellation are managed through Stripe. No automatic overage billing.</p>`;
}
function requestTypesPage() {
  return `${heading('Request types', 'Define the work your business handles, and how the agent should approach it.', canManage() ? '<button class="btn btn-primary" data-action="new-request-type">' + icon('plus') + 'New request type</button>' : '')}<div class="document-grid">${state.types.map((t) => `<button class="document-card" data-edit-type="${esc(t.id)}"><span class="document-icon">${icon('inbox')}</span><h3>${esc(t.name)}</h3><p>${esc(t.description || 'No description added.')}</p><div class="source-meta"><span>${esc(t.visibility)}</span><span class="badge ${t.active ? 'badge-review' : ''}">${t.active ? 'Active' : 'Disabled'}</span></div></button>`).join('') || '<section class="panel"><div class="empty"><strong>What kinds of work land on your team?</strong><p>Create types such as travel expenses, sales inquiries, vendor requests or IT access.</p></div></section>'}</div>`;
}
function sourcesPage() {
  if (!canManage())
    return `${heading('Request sources', 'Your administrators manage inbound connections.')}<section class="panel">Ask an owner or admin to connect a form or another system.</section>`;
  return `${heading('Request sources', 'Bring incoming work into this business’s shared queue.', '<button class="btn btn-primary" data-action="new-intake">' + icon('plus') + 'Connect a source</button>')}<div class="info-strip">${icon('info')}${config.public_forms_available === false ? 'Connect a website or business system through the inbound API. Public forms are unavailable in this demo.' : 'Hosted forms and scoped API connections bring requests into your workspace.'} Email and messenger tools can connect through an API integration.</div><div class="document-grid">${state.intakes.map((c) => `<section class="document-card"><span class="document-icon">${icon(c.kind === 'form' ? 'globe' : 'link')}</span><h3>${esc(c.name)}</h3><p>${c.kind === 'form' ? 'A public request form' : 'A write-only inbound API connection'}</p><div class="source-meta"><span class="badge ${c.active ? 'badge-review' : ''}">${c.active ? 'Active' : 'Revoked'}</span></div><label class="field source-url">${c.kind === 'form' ? 'Shareable form link' : 'Inbound endpoint'}<input readonly value="${esc(c.url)}"></label><div class="source-actions">${c.active ? `${c.kind === 'form' ? `<a class="btn" href="${esc(c.url)}" target="_blank" rel="noopener">Open form ${icon('external')}</a>` : ''}<button class="btn" data-revoke-intake="${esc(c.id)}">Revoke connection</button>` : ''}</div></section>`).join('') || `<section class="panel"><div class="empty">No connections yet. ${config.public_forms_available === false ? 'Connect your system through the inbound API.' : 'Create a public form or an inbound API connection.'}</div></section>`}</div><section class="panel"><h2>Connect your existing systems</h2><p>A website or CRM can send a request to its dedicated inbound endpoint with a Bearer token. Include an external_id to prevent duplicate imports. The credential can create requests only; it cannot read your queue or trigger AI.</p><p class="form-hint">Tokens are shown once. Revoke a connection if a token is lost. Public forms include a security check when available.</p></section>`;
}
function typeEditor(id) {
  const t = state.types.find((t) => t.id === id);
  modal(
    t ? 'Edit request type' : 'New request type',
    'A workflow for any kind of business request.',
    `<form id="request-type-form" data-id="${esc(id || '')}"><label class="field">Type name<input name="name" required minlength="2" maxlength="100" value="${esc(t?.name || '')}" placeholder="Travel expense request"></label><label class="field">Description<textarea name="description" maxlength="1000" rows="2" placeholder="What belongs in this queue?">${esc(t?.description || '')}</textarea></label><label class="field">Agent instructions<textarea name="instructions" maxlength="3000" rows="4" placeholder="Check the travel policy, explain the required documents and refer approval to Finance.">${esc(t?.instructions || '')}</textarea></label><div class="form-grid"><label class="field">Visibility<select name="visibility"><option value="internal" ${t?.visibility !== 'public' ? 'selected' : ''}>Internal — team only</option><option value="public" ${t?.visibility === 'public' ? 'selected' : ''}>Public — can be used on a form</option></select></label><label class="field">Status<select name="active"><option value="true" ${t?.active !== false ? 'selected' : ''}>Active</option><option value="false" ${t?.active === false ? 'selected' : ''}>Disabled</option></select></label></div><div class="notice-card">${icon('shield')}Instructions guide investigation and drafting. They cannot authorize external actions or sensitive automated decisions.</div><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn" type="button" data-action="close-modal">Close</button>${canManage() ? '<button class="btn btn-primary">Save type</button>' : ''}</div></form>`,
    true,
  );
}
async function businessAction(el) {
  if (el.dataset.scrollto) {
    if (!document.getElementById(el.dataset.scrollto)) {
      history.pushState(null, '', account ? '#home' : '#register');
      render();
    }
    document
      .getElementById(el.dataset.scrollto)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    return true;
  }
  if (el.dataset.editType) {
    typeEditor(el.dataset.editType);
    return true;
  }
  if (el.dataset.checkout) {
    const r = await request('/billing/checkout', {
      method: 'POST',
      body: { plan: el.dataset.checkout },
    });
    location.assign(r.url);
    return true;
  }
  if (el.dataset.reindex) {
    await request('/knowledge/' + el.dataset.reindex + '/reindex', { method: 'POST' });
    await reload();
    render();
    toast('Indexing queued. Refresh to check progress.');
    return true;
  }
  if (el.dataset.revokeIntake) {
    await request('/intakes/' + el.dataset.revokeIntake + '/revoke', { method: 'POST' });
    await reload();
    render();
    toast('Connection revoked');
    return true;
  }
  switch (el.dataset.action) {
    case 'cancel-launch-notification':
      await request('/demo/launch-notification', { method: 'DELETE' });
      await reload();
      render();
      toast('Launch notification cancelled.');
      return true;
    case 'export-account':
      download('supportops-account.json', await request('/account/export'));
      return true;
    case 'email-deliveries': {
      const jobs = await request('/auth/email-deliveries');
      modal(
        'Your account emails',
        'Delivery attempts are retained across server restarts.',
        `<div class="check-list">${jobs.map((j) => `<div class="check-item"><span><b>${esc(j.purpose === 'verify' ? 'Email verification' : 'Password recovery')}</b><br>${esc(j.status)} · ${j.attempts} attempt(s) · ${fmtDate(j.created_at)}${j.error === 'smtp_not_configured' ? '<br>Email delivery is temporarily unavailable. Contact support for help.' : ''}</span></div>`).join('') || '<p>No account emails have been requested.</p>'}</div>`,
        true,
      );
      return true;
    }
    case 'request-deletion':
      modal(
        'Request account deletion',
        'This records a request for operator review. It does not immediately erase shared company records.',
        '<form id="privacy-request"><label class="field">Current password<input name="password" type="password" required maxlength="128" autocomplete="current-password"></label><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn" type="button" data-action="close-modal">Cancel</button><button class="btn btn-primary">Record deletion request</button></div></form>',
      );
      return true;
    case 'operations-status': {
      const r = await request('/operations/status');
      const counts = (jobs) =>
        Object.entries(jobs)
          .map(([name, count]) => esc(name + ': ' + count))
          .join(' · ') || 'No jobs recorded';
      modal(
        'Background jobs',
        'Status for this workspace and your account.',
        `<div class="check-list"><div class="check-item"><span><b>Background worker</b><br>${r.worker.healthy ? 'Reporting a recent heartbeat' : 'No recent worker heartbeat. The operator should check the worker.'}</span></div><div class="check-item"><span><b>Knowledge indexing</b><br>${counts(r.knowledge_jobs)}</span></div><div class="check-item"><span><b>Your account emails</b><br>${counts(r.your_email_jobs)}</span></div><div class="check-item"><span><b>Unsettled AI reservations</b><br>${r.your_unsettled_ai_reservations}</span></div></div>`,
        true,
      );
      return true;
    }
    case 'notify-launch':
      await request('/demo/launch-notification', { method: 'POST' });
      await reload();
      render();
      toast('We’ll email you when purchasing launches.');
      return true;
    case 'export-workspace':
      download('supportops-workspace.json', await request('/workspace/export'));
      return true;
    case 'billing-portal': {
      const r = await request('/billing/portal', { method: 'POST' });
      location.assign(r.url);
      return true;
    }
    case 'new-request-type':
      typeEditor();
      return true;
    case 'verify-email':
      await request('/auth/verification-email', { method: 'POST' });
      toast(
        config.email_delivery_enabled
          ? 'Verification email queued. Check your delivery status if it does not arrive.'
          : 'Email delivery is temporarily unavailable. Your request is saved; contact support for help.',
      );
      return true;
    case 'new-intake':
      modal(
        'Connect a request source',
        'Choose how requests should enter your workspace.',
        `<form id="intake-form"><label class="field">Source name<input name="name" required minlength="2" maxlength="100" placeholder="Website requests"></label><label class="field">Channel<select name="kind"><option value="form" ${config.public_forms_available === false ? 'disabled' : ''}>Hosted public form — share a link</option><option value="api" ${config.public_forms_available === false ? 'selected' : ''}>Inbound API — connect your system</option></select></label><label class="field">Request type<select name="request_type_id"><option value="">General request</option>${state.types
          .filter((t) => t.active)
          .map((t) => `<option value="${esc(t.id)}">${esc(t.name)} · ${esc(t.visibility)}</option>`)
          .join(
            '',
          )}</select></label><p class="form-hint">A hosted form may use only an explicitly public request type. API connections can import internal types with a secret token.</p><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn btn-primary">Create connection</button></div></form>`,
      );
      return true;
    case 'route-request': {
      const t = state.detail;
      modal(
        'Route this request',
        'Choose a type and the employee responsible for reviewing it.',
        `<form id="routing-form"><label class="field">Request type<select name="request_type_id"><option value="">General request</option>${state.types
          .filter((x) => x.active)
          .map(
            (x) =>
              `<option value="${esc(x.id)}" ${x.id === t.request_type_id ? 'selected' : ''}>${esc(x.name)}</option>`,
          )
          .join(
            '',
          )}</select></label><label class="field">Assigned to<select name="assigned_to"><option value="">Unassigned</option>${state.people
          .filter((p) => canManage() || p.id === account.user.id)
          .map(
            (p) =>
              `<option value="${esc(p.id)}" ${p.id === t.assigned_to ? 'selected' : ''}>${esc(p.name)}</option>`,
          )
          .join(
            '',
          )}</select></label><div class="form-error" role="alert"></div><div class="modal-actions"><button class="btn btn-primary">Save routing</button></div></form>`,
      );
      return true;
    }
  }
  return false;
}
async function businessSubmit(form) {
  if (
    ![
      'request-type-form',
      'intake-form',
      'routing-form',
      'account-recovery',
      'privacy-request',
    ].includes(form.id)
  )
    return false;
  const button = form.querySelector('.btn-primary'),
    error = form.querySelector('.form-error');
  if (button.disabled) return true;
  button.disabled = true;
  if (error) error.textContent = '';
  try {
    const data = Object.fromEntries(new FormData(form));
    if (form.id === 'privacy-request') {
      const result = await request('/account/deletion-request', { method: 'POST', body: data });
      closeModal();
      toast(result.message);
      return true;
    }
    if (form.id === 'request-type-form') {
      data.active = data.active === 'true';
      await request('/request-types' + (form.dataset.id ? '/' + form.dataset.id : ''), {
        method: form.dataset.id ? 'PATCH' : 'POST',
        body: data,
      });
    }
    if (form.id === 'routing-form') {
      data.request_type_id = data.request_type_id || null;
      data.assigned_to = data.assigned_to || null;
      data.revision = state.detail.revision;
      await request('/tickets/' + state.selected + '/routing', { method: 'PATCH', body: data });
    }
    if (form.id === 'intake-form') {
      data.request_type_id = data.request_type_id || null;
      const connection = await request('/intakes', { method: 'POST', body: data });
      await reload();
      render();
      if (connection.token) {
        modal(
          'Your inbound API is ready',
          'Copy this write-only credential now. It will not be shown again.',
          `<label class="field">Endpoint<input readonly value="${esc(connection.url)}"></label><label class="field">Bearer token<input readonly value="${esc(connection.token)}"></label><pre class="code-block">${esc('curl -X POST ' + connection.url + ' \\\n  -H "Authorization: Bearer ' + connection.token + '" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"customer":"Emma Wilson","subject":"Vendor request","message":"Please review this procurement question.","external_id":"crm-123"}\'')}</pre><div class="modal-actions"><button class="btn" data-action="close-modal">I’ve saved the credential</button></div>`,
          true,
        );
      } else {
        closeModal();
        toast('Form created. Open it from Request sources.');
      }
      return true;
    }
    if (form.id === 'account-recovery') {
      const mode = form.dataset.mode;
      if (mode === 'forgot') {
        const r = await request('/auth/forgot-password', { method: 'POST', body: data });
        $('#recovery-result').textContent = r.message;
      } else {
        data.token = decodeURIComponent(location.hash.split('/').at(-1));
        const r = await request(mode === 'verify' ? '/auth/verify-email' : '/auth/reset-password', {
          method: 'POST',
          body: data,
        });
        $('#recovery-result').textContent = r.message;
        await refreshAccount();
      }
      button.hidden = true;
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
    if (button.isConnected) button.disabled = false;
  }
  return true;
}
function recoveryPage() {
  const mode = location.hash.startsWith('#verify/')
    ? 'verify'
    : location.hash.startsWith('#reset/')
      ? 'reset'
      : 'forgot';
  return `<main class="account-recovery"><a class="brand" href="#register"><span class="brand-mark">${icon('logo')}</span>SupportOps AI</a><section class="signup-card"><h1>${mode === 'verify' ? 'Verify your email' : mode === 'reset' ? 'Choose a new password' : 'Recover your account'}</h1><p>${mode === 'verify' ? 'Confirm this email address to activate AI processing and external request sources.' : mode === 'reset' ? 'Use at least 12 characters. Your existing sessions will be signed out.' : 'We’ll send a single-use recovery link if your account exists and email delivery is configured.'}</p><form id="account-recovery" data-mode="${mode}">${mode === 'forgot' ? '<label class="field">Work email<input name="email" type="email" required autocomplete="email"></label>' : mode === 'reset' ? '<label class="field">New password<input name="password" type="password" required minlength="12" maxlength="128" autocomplete="new-password"></label>' : ''}<div class="form-error" role="alert"></div><button class="btn btn-primary auth-submit">${mode === 'verify' ? 'Confirm email address' : mode === 'reset' ? 'Update password' : 'Send recovery link'}</button><p id="recovery-result" role="status"></p></form><a class="btn auth-submit" href="#login">Back to sign in</a></section></main>`;
}

function modal(title, subtitle, body, wide = false) {
  if (!$('#overlay').children.length) modalTrigger = document.activeElement;
  document.querySelector('#app').inert = true;
  document.body.classList.add('modal-open');
  $('#overlay').innerHTML =
    `<div class="modal-backdrop"><section class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="modal-head"><h2>${esc(title)}</h2><button class="icon-btn" data-action="close-modal" aria-label="Close">${icon('x')}</button></div><p>${esc(subtitle)}</p>${body}</section></div>`;
  (
    $('#overlay input:not([type="hidden"]), #overlay textarea, #overlay select') ||
    $('#overlay button')
  )?.focus({ preventScroll: true });
}
function closeModal() {
  $('#overlay').innerHTML = '';
  document.body.classList.remove('modal-open');
  document.querySelector('#app').inert = false;
  if (modalTrigger?.isConnected) modalTrigger.focus({ preventScroll: true });
}
function newTicket() {
  creationKey = crypto.randomUUID();
  modal(
    'New request',
    'Create a request for ' + businessName() + '.',
    `<form id="ticket-form"><div class="form-grid"><label class="field">Requester name<input name="customer" required minlength="2" maxlength="100" placeholder="Emma Wilson"></label><label class="field">Email<input name="email" type="email" placeholder="customer@example.test" maxlength="160"></label></div><div class="form-grid">${retailDemo() ? `<label class="field">Order number<input name="order_id" inputmode="numeric" pattern="[0-9]{4,12}" placeholder="1042"></label>` : '<input name="order_id" type="hidden" value="">'}<label class="field">Channel<select name="channel"><option value="internal">Internal request</option><option value="email">Email</option><option value="chat">Chat</option><option value="web">Web form</option><option value="api">API import</option></select></label></div><label class="field">Subject<input name="subject" required minlength="3" maxlength="180" placeholder="For example, a delayed delivery"></label><label class="field">Request details<textarea name="message" required minlength="10" maxlength="5000" placeholder="Describe the customer’s request…"></textarea></label><div class="form-grid"><label class="field">Request type<select name="request_type_id"><option value="">General request</option>${state.types
      .filter((t) => t.active)
      .map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`)
      .join(
        '',
      )}</select></label><label class="field">Assigned to<select name="assigned_to"><option value="">Unassigned</option>${state.people
      .filter((p) => canManage() || p.id === account.user.id)
      .map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`)
      .join(
        '',
      )}</select></label></div><label class="field">Priority<select name="priority"><option value="normal">Normal</option><option value="high">High</option></select></label>${retailDemo() ? `<div class="notice-card">${icon('info')}Sample orders: 1042–1049. For order 1042, use Emma Wilson.</div>` : `<div class="notice-card">${icon('book')}Replies use your current business policies. Live account or order details require a separate check in your business system.</div>`}<label class="field">Reply template<select name="template_id"><option value="">Automatic — matching category or default</option>${state.templates
      .filter((t) => t.active)
      .map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`)
      .join(
        '',
      )}</select></label><div class="modal-actions"><button class="btn" type="button" data-action="fill-example">Use an example</button><button class="btn btn-primary" type="submit">${icon('plus')}Create request</button></div></form>`,
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
      )}</div><div class="modal-actions">${canManage() && state.docs.some((x) => x.id === d.id) ? `<button class="btn" data-reindex="${esc(d.id)}" ${config.demo_release && state.billing?.limit_reached ? 'disabled' : ''}>Index / retry indexing</button><button class="btn" data-version="${esc(d.id)}">Publish new version</button>${!d.archived ? `<button class="btn" data-archive-policy="${esc(d.id)}">Archive policy</button>` : ''}` : ''}<button class="btn" data-action="close-modal">Close</button></div>`,
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
  const workspace = workspaceToken();
  const version = ++selectionVersion;
  state.selected = id || null;
  state.tab = workflow ? 'workflow' : 'draft';
  state.detail = null;
  state.detailLoading = !!id;
  state.detailError = false;
  render();
  if (!id) return;
  try {
    const detail = await request('/tickets/' + id);
    if (workspace !== workspaceToken() || version !== selectionVersion) return;
    state.detail = detail;
  } catch (e) {
    if (workspace !== workspaceToken() || version !== selectionVersion) return;
    state.detailError = true;
    fail(e);
  } finally {
    if (workspace === workspaceToken() && version === selectionVersion) {
      state.detailLoading = false;
      render();
    }
  }
}
async function updateQueue() {
  const rows = filteredTickets();
  if (!rows.some((t) => t.id === state.selected)) await selectTicket(rows[0]?.id);
  else {
    $('#ticket-items').innerHTML = ticketItems();
  }
}
async function analyze(fault = 'none') {
  if (state.busy || !state.detail) return;
  const workspace = workspaceToken();
  const id = state.selected;
  state.busy = id;
  render();
  try {
    const result = await request(`/tickets/${id}/analyze`, { method: 'POST', body: { fault } });
    if (workspace !== workspaceToken()) return;
    await reload();
    if (workspace !== workspaceToken()) return;
    if (state.selected === id) state.tab = 'draft';
    toast(
      result.result?.reason ? 'This ticket needs manual review' : 'Your draft is ready for review',
    );
  } catch (e) {
    if (workspace !== workspaceToken()) return;
    if (config.demo_release && e.status === 429) await reload();
    fail(e);
  } finally {
    if (workspace === workspaceToken() && state.busy === id) {
      state.busy = false;
      render();
    }
  }
}
async function review(decision, note = '') {
  const workspace = workspaceToken();
  const t = state.detail;
  if (!t || t.id !== state.selected) return;
  const text = state.drafts[t.id] ?? t.result?.draft ?? '';
  const button = $(`[data-action="${decision === 'approve' ? 'approve' : 'escalate'}"]`);
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try {
    await request(`/tickets/${t.id}/review`, {
      method: 'POST',
      body: { decision, text, note, revision: t.revision },
    });
    if (workspace !== workspaceToken()) return;
    closeModal();
    await reload();
    render();
    toast(
      decision === 'approve'
        ? 'Reply approved. Your decision has been saved.'
        : 'Request escalated to an agent',
    );
  } catch (e) {
    if (workspace !== workspaceToken()) return;
    if (e.status === 409) {
      await reload();
      render();
    }
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
  if (el.dataset.scrollto) e.preventDefault();
  if (el.dataset.ticket) {
    await selectTicket(el.dataset.ticket);
    return;
  }
  if (el.dataset.openTicket) {
    state.page = 'tickets';
    state.filter = 'all';
    state.query = '';
    history.replaceState(null, '', '#tickets');
    await selectTicket(el.dataset.openTicket, !!el.dataset.workflow);
    return;
  }
  if (el.dataset.filter) {
    state.filter = el.dataset.filter;
    await updateQueue();
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
    if (await businessAction(el)) return;
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
        await updateQueue();
        render();
        break;
      case 'retry-ticket':
        await selectTicket(state.selected);
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
        toast('Request ID copied');
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
          `<div class="modal-prose">Create requests, prepare replies, inspect sources, edit text and save review decisions.<br><br>This sample uses synthetic records stored in your browser. It demonstrates how requests, sources and review decisions work. Your business workspace stores its own content on the service and uses the configured AI models.</div>`,
        );
        break;
      case 'eval-info':
        modal(
          'About the sample evaluation',
          'How sample replies are checked.',
          '<div class="modal-prose">The sample report compares replies with expected outcomes for synthetic requests. Checks cover factual support, policy citations and cases that need a human decision. Sample results describe the sample workspace; they are not a quality guarantee for your business content.</div>',
        );
        break;
      case 'runbook':
        modal(
          'When processing needs attention',
          'Your request stays available for manual review.',
          '<div class="modal-prose">If a connected system is unavailable, wait for it to recover and retry the analysis. When a reply cannot be supported by current policies, review the sources, update the knowledge base or escalate the request. Contact support if processing continues to fail.</div>',
        );
        break;
      case 'reset-demo':
        modal(
          'Reset the demo',
          'All requests and review decisions created in this demo will be removed from this browser.',
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
    updateQueue().catch(fail);
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
  if (await businessSubmit(form)) return;
  if (await handlePlatformSubmit(form)) return;
  if (form.id === 'ticket-form') {
    const button = form.querySelector('[type="submit"]');
    if (button.disabled) return;
    button.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(form));
      values.order_id = values.order_id || null;
      values.template_id = values.template_id || null;
      values.request_type_id = values.request_type_id || null;
      values.assigned_to = values.assigned_to || null;
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
      toast('Request created');
    } catch (err) {
      fail(err);
      button.disabled = false;
    }
  }
  if (form.id === 'escalate-form') await review('escalate', new FormData(form).get('note'));
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeModal();
    return;
  }
  if ($('#overlay').children.length) {
    if (e.key === 'Tab') {
      const list = [
        ...$('#overlay').querySelectorAll('button,input,textarea,select,a[href]'),
      ].filter((x) => !x.disabled && x.type !== 'hidden' && x.getClientRects().length);
      if (e.shiftKey && document.activeElement === list[0]) {
        e.preventDefault();
        list.at(-1)?.focus({ preventScroll: true });
      } else if (!e.shiftKey && document.activeElement === list.at(-1)) {
        e.preventDefault();
        list[0]?.focus({ preventScroll: true });
      }
    }
    return;
  }
  if (!account || !$('.app-shell')) return;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    if (state.page !== 'tickets') {
      state.page = 'tickets';
      history.pushState(null, '', '#tickets');
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
});
window.addEventListener('hashchange', () => {
  if ($('#overlay').children.length) closeModal();
  if (location.hash === '#home') {
    render();
    return;
  }
  if (
    location.hash === '#forgot' ||
    location.hash.startsWith('#reset/') ||
    location.hash.startsWith('#verify/')
  ) {
    render();
    return;
  }
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
  if (!isDemo) state.plans = await request('/plans');
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
