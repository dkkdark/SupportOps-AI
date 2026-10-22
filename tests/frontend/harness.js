import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';

export const flush = () => new Promise((resolve) => setTimeout(resolve, 15));
export function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
export function ticket(id, extra = {}) {
  return {
    id,
    customer: 'Emma Wilson',
    subject: 'Request ' + id,
    message: 'Please explain your cancellation policy.',
    channel: 'internal',
    priority: 'normal',
    status: 'new',
    category: 'other',
    created_at: '2026-10-22T10:00:00Z',
    revision: 0,
    reviews: [],
    ...extra,
  };
}
export async function workspace(
  t,
  { hash = 'tickets', intercept, authenticated = true, config = {}, setupWindow } = {},
) {
  const dom = new JSDOM(
    '<meta name="supportops-mode" content="api"><div id="app"></div><div id="overlay"></div><div id="toasts"></div>',
    { url: 'http://localhost/#' + hash, runScripts: 'outside-only', pretendToBeVisual: true },
  );
  t.after(() => dom.window.close());
  const w = dom.window;
  setupWindow?.(w);
  w.HTMLElement.prototype.scrollIntoView = function () {
    w.lastScrolledId = this.id;
  };
  w.scrollTo = ({ left, top }) => {
    w.scrollX = left;
    w.scrollY = top;
  };
  w.HTMLElement.prototype.getClientRects = function () {
    return this.hidden || this.type === 'hidden' ? [] : [{}];
  };
  const organizations = ['a', 'b'].map((id) => ({
    id,
    name: 'Business ' + id.toUpperCase(),
    industry: 'Consulting',
    role: 'owner',
    is_demo: false,
  }));
  let organization = 'a';
  const requests = [];
  const records = { a: [ticket('A1'), ticket('A2')], b: [ticket('B1')] };
  const account = () => ({
    user: { id: 'user', name: 'Alex Morgan', email: 'alex@example.test', email_verified: true },
    csrf_token: 'synthetic-csrf',
    organization: organizations.find((x) => x.id === organization),
    memberships: organizations,
  });
  w.fetch = async (url, options = {}) => {
    const path = url.slice(4),
      org = options.headers?.['X-Workspace-ID'] || organization;
    const body = options.body && JSON.parse(options.body);
    requests.push({ path, org, method: options.method || 'GET', body });
    const intercepted = await intercept?.({ path, org, options, body });
    if (intercepted !== undefined)
      return new Response(JSON.stringify(intercepted.body ?? intercepted), {
        status: typeof intercepted.status === 'number' ? intercepted.status : 200,
      });
    let result;
    if (path === '/config')
      result = { provider: 'baseline', demo_release: true, demo_ai_budget_usd: 5, ...config };
    else if (path === '/auth/me') {
      if (!authenticated) return new Response('{"detail":"Sign in required"}', { status: 401 });
      result = account();
    } else if (path === '/auth/switch') {
      organization = body.organization_id;
      result = {};
    } else if (path === '/tickets') result = records[org];
    else if (path.startsWith('/tickets/'))
      result = records[org].find((x) => x.id === path.split('/')[2]);
    else if (path === '/metrics') result = { total: records[org].length };
    else if (path === '/evaluation') result = { status: 'not_run' };
    else if (path === '/billing') result = {};
    else result = [];
    return new Response(JSON.stringify(result ?? {}), { status: 200 });
  };
  const context = dom.getInternalVMContext();
  const modules = new Map();
  async function load(name) {
    if (!modules.has(name)) {
      const source = await readFile(
        new URL('../../supportops/web/' + name, import.meta.url),
        'utf8',
      );
      modules.set(name, new vm.SourceTextModule(source, { context, identifier: name }));
    }
    return modules.get(name);
  }
  const app = await load('app.js');
  await app.link((specifier) => load(specifier.replace('./', '')));
  await app.evaluate();
  const document = w.document;
  const click = async (selector) => {
    const el = document.querySelector(selector);
    if (!el) throw new Error('Missing control: ' + selector);
    el.click();
    await flush();
  };
  return {
    window: w,
    document,
    requests,
    records,
    click,
    route: async (page) => {
      w.location.hash = page;
      await flush();
    },
  };
}
