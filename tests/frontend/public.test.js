import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';
import { workspace, flush, deferred } from './harness.js';

test('full registration shows availability notice while sign-in remains usable', async (t) => {
  const ui = await workspace(t, {
    hash: 'register',
    authenticated: false,
    config: { registration_available: false, captcha_site_key: 'synthetic-key' },
  });
  assert.match(
    ui.document.getElementById('signup').textContent,
    /Registration is currently unavailable/,
  );
  assert.equal(ui.document.getElementById('platform-auth'), null);
  assert.equal(ui.document.getElementById('signup-captcha'), null);
  await ui.route('login');
  assert.equal(ui.document.getElementById('platform-auth').dataset.mode, 'login');
});

test('last-slot race replaces a stale signup form with the closed notice', async (t) => {
  const ui = await workspace(t, {
    hash: 'register',
    authenticated: false,
    config: { registration_available: true },
    intercept: ({ path }) =>
      path === '/auth/register'
        ? {
            status: 503,
            body: { detail: 'Registration is currently unavailable. Please check back later.' },
          }
        : undefined,
  });
  const form = ui.document.getElementById('platform-auth');
  for (const [name, value] of Object.entries({
    name: 'Alex Morgan',
    business_name: 'Acme',
    email: 'alex@example.test',
    password: 'Synthetic password 123!',
  }))
    form.elements[name].value = value;
  form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  assert.equal(ui.document.getElementById('platform-auth'), null);
  assert.match(
    ui.document.getElementById('signup').textContent,
    /Registration is currently unavailable/,
  );
});

test('landing section links work from the sign-in page', async (t) => {
  const ui = await workspace(t, { hash: 'login', authenticated: false });
  assert.equal(ui.document.getElementById('how-it-works'), null);
  await ui.click('[data-scrollto="how-it-works"]');
  assert.equal(ui.window.location.hash, '#register');
  assert.equal(ui.window.lastScrolledId, 'how-it-works');
});

test('failed signup resets CAPTCHA and switching auth pages removes the previous widget', async (t) => {
  let renders = 0,
    resets = 0,
    removes = 0;
  const ui = await workspace(t, {
    hash: 'register',
    authenticated: false,
    config: { captcha_site_key: 'synthetic-site-key' },
    setupWindow: (w) => {
      w.turnstile = {
        render: () => renders++,
        reset: () => resets++,
        remove: () => removes++,
        getResponse: () => 'synthetic-captcha-token',
      };
    },
    intercept: ({ path }) =>
      path === '/auth/register'
        ? { status: 409, body: { detail: 'Email already registered' } }
        : undefined,
  });
  const form = ui.document.getElementById('platform-auth');
  for (const [name, value] of Object.entries({
    name: 'Alex Morgan',
    email: 'alex@example.test',
    business_name: 'Acme',
    password: 'Synthetic password 123!',
  }))
    form.elements[name].value = value;
  form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  assert.equal(resets, 1);
  assert.match(form.querySelector('.form-error').textContent, /already registered/);
  await ui.route('login');
  assert.equal(removes, 1);
  await ui.route('register');
  assert.equal(renders, 2);
});

test('public form retries reuse an idempotency key and omit the Turnstile hidden field', async (t) => {
  const dom = new JSDOM('<div id="intake-app"></div>', {
    url: 'http://localhost/forms/test-source',
    runScripts: 'outside-only',
  });
  t.after(() => dom.window.close());
  const w = dom.window,
    requests = [],
    first = deferred();
  w.fetch = async (url, options = {}) => {
    if (!options.method)
      return new Response(JSON.stringify({ title: 'Business questions', business_name: 'Acme' }));
    requests.push(JSON.parse(options.body));
    if (requests.length === 1) return first.promise;
    return new Response(JSON.stringify({ reference: 'SO-ONE-REQUEST' }), { status: 202 });
  };
  const module = new vm.SourceTextModule(
    await readFile(new URL('../../supportops/web/intake.js', import.meta.url), 'utf8'),
    { context: dom.getInternalVMContext() },
  );
  await module.link(() => {
    throw new Error('Unexpected import');
  });
  await module.evaluate();
  const form = w.document.getElementById('incoming');
  for (const [name, value] of Object.entries({
    customer: 'Emma Wilson',
    email: 'emma@example.test',
    subject: 'Cancellation question',
    message: 'Please explain your cancellation policy.',
  }))
    form.elements[name].value = value;
  const hidden = w.document.createElement('input');
  hidden.type = 'hidden';
  hidden.name = 'cf-turnstile-response';
  hidden.value = 'first-captcha';
  form.append(hidden);
  const submit = () =>
    form.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  submit();
  submit();
  await flush();
  assert.equal(requests.length, 1, 'double submission is ignored');
  first.resolve({
    json: () => {
      throw new TypeError('Response lost after acceptance');
    },
  });
  await flush();
  assert.match(w.document.querySelector('.form-error').textContent, /Response lost/);
  hidden.value = 'new-captcha-after-retry';
  submit();
  await flush();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].external_id, requests[1].external_id);
  assert.equal(requests[1]['cf-turnstile-response'], undefined);
  assert.match(w.document.getElementById('intake-app').textContent, /Request received/);
});
