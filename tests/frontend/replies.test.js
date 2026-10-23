import test from 'node:test';
import assert from 'node:assert/strict';
import { workspace, ticket, deferred, flush } from './harness.js';

test('Requests refresh queues Gmail, reloads API requests and never starts AI', async (t) => {
  const ui = await workspace(t, {
    intercept: ({ path }) =>
      path === '/tickets/refresh-inbox' ? { gmail_queued: true } : undefined,
  });
  ui.records.a.push(ticket('API-NEW', { channel: 'api' }));
  await ui.click('[data-action="refresh-inbox"]');
  assert.ok(ui.document.querySelector('[data-ticket="API-NEW"]'));
  const mutations = ui.requests.filter((r) => r.method !== 'GET');
  assert.deepEqual(
    mutations.map((r) => r.path),
    ['/tickets/refresh-inbox'],
  );
  assert.match(ui.document.getElementById('toasts').textContent, /Gmail sync queued/);
});

test('bulk preparation covers all new requests and submits the chosen mode only once', async (t) => {
  const pending = deferred();
  const ui = await workspace(t, {
    intercept: ({ path }) =>
      path === '/reply-settings'
        ? { mode: 'human', sender_ready: true, can_configure: true }
        : path === '/tickets/prepare-all'
          ? pending.promise
          : undefined,
  });
  await ui.click('[data-action="prepare-all"]');
  assert.match(ui.document.querySelector('.modal').textContent, /2 new requests/);
  const form = ui.document.getElementById('prepare-all-form');
  form.elements.mode.value = 'automatic';
  const submit = () =>
    form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  submit();
  submit();
  await flush();
  const calls = ui.requests.filter((r) => r.path === '/tickets/prepare-all');
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].body, { mode: 'automatic' });
  ui.records.a.forEach((r) => {
    r.status = 'queued';
  });
  pending.resolve({ queued: 2, mode: 'automatic' });
  await flush();
  assert.equal(ui.document.querySelector('.modal'), null);
  assert.equal(ui.document.querySelector('[data-action="prepare-all"]').disabled, true);
  assert.equal(ui.document.querySelector('[data-action="analyze"]'), null);
});

test('automatic sending is unavailable until Gmail send permission is granted', async (t) => {
  const ui = await workspace(t, {
    intercept: ({ path }) =>
      path === '/reply-settings' ? { mode: 'human', sender_ready: false } : undefined,
  });
  await ui.click('[data-action="prepare-all"]');
  const form = ui.document.getElementById('prepare-all-form');
  assert.equal(form.elements.mode.value, 'human');
  assert.equal(form.querySelector('option[value="automatic"]').disabled, true);
  assert.match(form.textContent, /reconnect Gmail/i);
});

test('an administrator can switch an automatic workspace back to human review', async (t) => {
  let mode = 'automatic';
  const ui = await workspace(t, {
    intercept: ({ path, options, body }) => {
      if (path !== '/reply-settings') return;
      if (options.method === 'PUT') mode = body.mode;
      return { mode, sender_ready: true, can_configure: true };
    },
  });
  await ui.click('[data-action="reply-settings"]');
  const form = ui.document.getElementById('reply-settings-form');
  form.elements.mode.value = 'human';
  form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  assert.equal(mode, 'human');
  assert.match(ui.document.querySelector('.reply-policy-strip').textContent, /Human review/);
  assert.deepEqual(ui.requests.find((r) => r.method === 'PUT').body, { mode: 'human' });
});

test('a human can write and approve an agent-queue reply for email delivery', async (t) => {
  const record = ticket('A1', {
    status: 'manual',
    reply_email: 'emma@example.test',
    result: { reason: 'missing_sources', draft: null },
  });
  const ui = await workspace(t, {
    intercept: ({ path, options, body }) => {
      if (path === '/reply-settings') return { mode: 'human', sender_ready: true };
      if (path === '/tickets') return [record];
      if (path === '/tickets/A1') return record;
      if (path === '/tickets/A1/review' && options.method === 'POST') {
        record.status = 'sending';
        record.delivery = { status: 'pending', recipient: 'emma@example.test' };
        record.reviews.push({ decision: 'approve', text: body.text });
        return record;
      }
    },
  });
  await ui.click('[data-filter="manual"]');
  await ui.click('[data-action="agent-reply"]');
  const form = ui.document.getElementById('agent-reply-form');
  form.elements.text.value = 'I checked your request and here is my response.';
  form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  const call = ui.requests.find((r) => r.path === '/tickets/A1/review');
  assert.equal(call.body.manual, true);
  assert.equal(call.body.send, true);
  assert.equal(call.body.revision, 0);
  await ui.click('[data-filter="all"]');
  assert.match(
    ui.document.querySelector('.completed-box').textContent,
    /queued for email delivery/,
  );
});

test('Gmail send permission is a separate explicit checkbox', async (t) => {
  const ui = await workspace(t, {
    hash: 'sources',
    intercept: ({ path }) =>
      path === '/integrations/gmail'
        ? { available: true }
        : path === '/integrations/gmail/connect'
          ? { url: 'http://localhost/#sources' }
          : undefined,
  });
  await ui.click('[data-action="gmail-connect"]');
  const form = ui.document.getElementById('gmail-connect-form');
  assert.equal(form.elements.enable_sending.checked, false);
  form.elements.authorized.checked = true;
  form.elements.enable_sending.checked = true;
  form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  assert.equal(
    ui.requests.find((r) => r.path === '/integrations/gmail/connect').body.enable_sending,
    true,
  );
});
