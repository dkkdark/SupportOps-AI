import test from 'node:test';
import assert from 'node:assert/strict';
import { workspace, flush } from './harness.js';

const source = {
  id: 'gmail-1',
  mailbox: 'support@example.test',
  label_name: 'Support',
  status: 'active',
  phase: 'history',
  imported_count: 12,
  pending: 0,
};

test('Gmail settings show actual status and submit explicit consent through the protected API', async (t) => {
  const ui = await workspace(t, {
    hash: 'sources',
    intercept: ({ path }) =>
      path === '/integrations/gmail'
        ? { available: true, connection: source }
        : path === '/integrations/gmail/connect'
          ? { url: 'http://localhost/#sources' }
          : undefined,
  });
  assert.match(ui.document.querySelector('.gmail-source').textContent, /support@example.test/);
  assert.match(ui.document.querySelector('.gmail-source').textContent, /12 imported/);
  await ui.click('[data-action="gmail-connect"]');
  const form = ui.document.getElementById('gmail-connect-form');
  assert.equal(form.elements.authorized.required, true);
  assert.equal(form.elements.lookback_days.value, '0');
  form.elements.authorized.checked = true;
  form.elements.lookback_days.value = '7';
  form.dispatchEvent(new ui.window.Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  const submitted = ui.requests.find((request) => request.path === '/integrations/gmail/connect');
  assert.deepEqual(submitted.body, {
    label_name: 'Support',
    lookback_days: 7,
    request_type_id: null,
    authorized: true,
    enable_sending: false,
  });
});

test('disconnect requires confirmation and refreshes the mailbox state', async (t) => {
  let disconnected = false;
  const ui = await workspace(t, {
    hash: 'sources',
    intercept: ({ path }) => {
      if (path === '/integrations/gmail')
        return {
          available: true,
          connection: { ...source, status: disconnected ? 'disconnected' : 'active' },
        };
      if (path === '/integrations/gmail/disconnect') {
        disconnected = true;
        return { ok: true };
      }
    },
  });
  await ui.click('[data-action="gmail-disconnect"]');
  assert.equal(disconnected, false);
  await ui.click('[data-action="gmail-disconnect-confirm"]');
  assert.equal(disconnected, true);
  assert.equal(ui.document.querySelector('[data-action="gmail-sync"]'), null);
  assert.match(ui.document.querySelector('.gmail-source').textContent, /Disconnected/);
});

test('unconfigured Gmail displays availability and prevents a broken authorization flow', async (t) => {
  const ui = await workspace(t, {
    hash: 'sources',
    intercept: ({ path }) =>
      path === '/integrations/gmail' ? { available: false, connection: null } : undefined,
  });
  assert.equal(ui.document.querySelector('[data-action="gmail-connect"]').disabled, true);
  assert.match(ui.document.querySelector('.gmail-source').textContent, /temporarily unavailable/);
});
