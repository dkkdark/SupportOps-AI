import test from 'node:test';
import assert from 'node:assert/strict';
import { workspace, ticket, deferred, flush } from './harness.js';

function capturePoll() {
  let tick;
  return {
    setupWindow(window) {
      window.setInterval = (callback, delay) => {
        assert.equal(delay, 15000);
        tick = callback;
        return 1;
      };
    },
    async run() {
      tick();
      await flush();
    },
  };
}

test('incoming requests appear without navigation, preserve selection and scroll, and make only read calls', async (t) => {
  const poll = capturePoll();
  const ui = await workspace(t, poll);
  ui.document.querySelector('.ticket-list').scrollTop = 250;
  ui.window.scrollY = 180;
  ui.records.a.unshift(ticket('MAIL1', { channel: 'email' }));
  const start = ui.requests.length;
  await poll.run();
  assert.ok(ui.document.querySelector('[data-ticket="MAIL1"]'));
  assert.equal(ui.document.querySelector('.ticket-item.selected').dataset.ticket, 'A1');
  assert.equal(ui.document.querySelector('.ticket-list').scrollTop, 250);
  assert.equal(ui.window.scrollY, 180);
  const calls = ui.requests.slice(start);
  assert.ok(calls.length > 0);
  assert.ok(calls.every((r) => r.method === 'GET'));
  assert.ok(
    calls.every((r) =>
      [
        '/tickets',
        '/metrics',
        '/integrations/gmail',
        '/tickets/A1',
        '/reply-settings',
        '/billing',
      ].includes(r.path),
    ),
  );
});

test('Gmail status and imported count update automatically on Request sources', async (t) => {
  const poll = capturePoll();
  let imported = 0;
  const ui = await workspace(t, {
    ...poll,
    hash: 'sources',
    intercept: ({ path }) =>
      path === '/integrations/gmail'
        ? {
            available: true,
            connection: {
              mailbox: 'support@example.test',
              label_name: 'INBOX',
              status: 'active',
              imported_count: imported,
            },
          }
        : undefined,
  });
  assert.match(ui.document.querySelector('.gmail-source').textContent, /0 imported/);
  imported = 1;
  await poll.run();
  assert.match(ui.document.querySelector('.gmail-source').textContent, /1 imported/);
});

test('background refresh waits while writing a reply and preserves the unsaved draft afterwards', async (t) => {
  const poll = capturePoll();
  const review = ticket('A1', {
    status: 'review',
    result: { draft: 'Original reply.', sources: [], checks: [] },
  });
  const ui = await workspace(t, {
    ...poll,
    intercept: ({ path }) => (path === '/tickets/A1' ? review : undefined),
  });
  const draft = ui.document.getElementById('draft-text');
  draft.focus();
  draft.value = 'My carefully edited reply.';
  draft.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  ui.records.a.unshift(ticket('MAIL2'));
  const start = ui.requests.length;
  await poll.run();
  assert.equal(ui.requests.length, start);
  assert.equal(ui.document.getElementById('draft-text'), draft);
  draft.blur();
  await poll.run();
  assert.ok(ui.document.querySelector('[data-ticket="MAIL2"]'));
  assert.equal(ui.document.getElementById('draft-text').value, 'My carefully edited reply.');
});

test('hidden tabs and open forms do not poll or replace in-progress input', async (t) => {
  const poll = capturePoll();
  let visibility = 'visible';
  const ui = await workspace(t, {
    setupWindow(window) {
      poll.setupWindow(window);
      Object.defineProperty(window.document, 'visibilityState', { get: () => visibility });
    },
  });
  visibility = 'hidden';
  let start = ui.requests.length;
  await poll.run();
  assert.equal(ui.requests.length, start);
  visibility = 'visible';
  await ui.click('[data-action="new"]');
  const form = ui.document.getElementById('ticket-form');
  form.elements.subject.value = 'Work in progress';
  start = ui.requests.length;
  await poll.run();
  assert.equal(ui.requests.length, start);
  assert.equal(ui.document.getElementById('ticket-form'), form);
  assert.equal(form.elements.subject.value, 'Work in progress');
});

test('a refresh finishing after typing starts leaves the editor untouched and retries later', async (t) => {
  const poll = capturePoll();
  const slow = deferred();
  let delay = false;
  const review = ticket('A1', {
    status: 'review',
    result: { draft: 'Original reply.', sources: [], checks: [] },
  });
  const ui = await workspace(t, {
    ...poll,
    intercept: ({ path }) =>
      delay && path === '/tickets' ? slow.promise : path === '/tickets/A1' ? review : undefined,
  });
  delay = true;
  await poll.run();
  const draft = ui.document.getElementById('draft-text');
  draft.focus();
  draft.value = 'Typing while the request was pending.';
  draft.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  ui.records.a.push(ticket('MAIL3'));
  delay = false;
  slow.resolve(ui.records.a);
  await flush();
  assert.equal(ui.document.getElementById('draft-text'), draft);
  assert.equal(ui.document.querySelector('[data-ticket="MAIL3"]'), null);
  draft.blur();
  await poll.run();
  assert.ok(ui.document.querySelector('[data-ticket="MAIL3"]'));
  assert.equal(
    ui.document.getElementById('draft-text').value,
    'Typing while the request was pending.',
  );
});

test('a transient polling failure retries without hiding the queue or interrupting work', async (t) => {
  const poll = capturePoll();
  let unavailable = false;
  const ui = await workspace(t, {
    ...poll,
    intercept: ({ path }) =>
      unavailable && path === '/tickets'
        ? { status: 503, body: { detail: 'Temporarily unavailable' } }
        : undefined,
  });
  unavailable = true;
  await poll.run();
  assert.ok(ui.document.querySelector('[data-ticket="A1"]'));
  assert.equal(ui.document.querySelector('.toast.error'), null);
  unavailable = false;
  ui.records.a.push(ticket('MAIL4'));
  await poll.run();
  assert.ok(ui.document.querySelector('[data-ticket="MAIL4"]'));
});

test('a delayed incoming refresh cannot show another workspace data after switching', async (t) => {
  const poll = capturePoll();
  const slow = deferred();
  let delay = false;
  const ui = await workspace(t, {
    ...poll,
    intercept: ({ path, org }) =>
      delay && org === 'a' && path === '/tickets' ? slow.promise : undefined,
  });
  delay = true;
  await poll.run();
  await ui.route('settings');
  await ui.click('[data-switch="b"]');
  slow.resolve([ticket('PRIVATE-A')]);
  await flush();
  assert.match(ui.document.querySelector('.workspace').textContent, /Business B/);
  assert.equal(ui.document.querySelector('[data-ticket="PRIVATE-A"]'), null);
  assert.ok(ui.document.querySelector('[data-ticket="B1"]'));
});
