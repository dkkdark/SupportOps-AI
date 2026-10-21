export const isDemo = document.querySelector('meta[name="supportops-mode"]').content === 'demo';
export let config = {};
export let account = null;
let demo;
export async function init() {
  if (isDemo) {
    demo = (await import('./demo.js')).demo;
    await demo.init();
    config = { mode: 'demo', provider: 'baseline', version: '0.2.0', faults_enabled: true };
    account = {
      user: { name: 'Alex Morgan', email: 'alex@example.test' },
      organization: {
        id: 'offline',
        name: 'Northstar Goods',
        role: 'owner',
        is_demo: true,
        workflow_mode: 'retail_demo',
      },
      memberships: [],
    };
  } else {
    config = await request('/config');
    await refreshAccount();
  }
  return config;
}
export async function refreshAccount() {
  if (isDemo) return account;
  try {
    account = await request('/auth/me');
  } catch (e) {
    if (e.status !== 401) throw e;
    account = null;
  }
  return account;
}
export async function authenticate(path, body = {}) {
  await request(path, { method: 'POST', body });
  return refreshAccount();
}
export async function signOut() {
  await request('/auth/logout', { method: 'POST' });
  account = null;
}
export async function request(path, options = {}) {
  if (isDemo) return demo.request(path, options);
  const multipart = options.body instanceof FormData;
  const r = await fetch('/api' + path, {
    ...options,
    credentials: 'same-origin',
    headers: {
      ...(!multipart ? { 'Content-Type': 'application/json' } : {}),
      ...(account?.csrf_token ? { 'X-CSRF-Token': account.csrf_token } : {}),
      ...(account?.organization?.id ? { 'X-Workspace-ID': account.organization.id } : {}),
      ...options.headers,
    },
    body: options.body ? (multipart ? options.body : JSON.stringify(options.body)) : undefined,
  });
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    const detail = b.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((x) => `${x.loc?.slice(1).join('.') || 'Input'}: ${x.msg}`).join('; ')
          : 'The request could not be completed.';
    const e = new Error(message);
    e.status = r.status;
    throw e;
  }
  return r.json();
}
