import test from 'node:test';
import assert from 'node:assert/strict';
import { workspace, ticket, deferred, flush } from './harness.js';

test('late request details cannot replace the latest selection', async (t) => {
  const slow = deferred();
  let delay = false;
  const ui = await workspace(t, {
    intercept: ({ path }) => (delay && path === '/tickets/A2' ? slow.promise : undefined),
  });
  delay = true;
  await ui.click('[data-ticket="A2"]');
  assert.equal(
    ui.document.querySelector('.ticket-title'),
    null,
    'old request is hidden while loading',
  );
  await ui.click('[data-ticket="A1"]');
  slow.resolve(ticket('A2'));
  await flush();
  assert.equal(ui.document.querySelector('.ticket-title').textContent, 'Request A1');
  assert.equal(ui.document.querySelector('.ticket-item.selected').dataset.ticket, 'A1');
});

test('failed selection does not leave another request available for review', async (t) => {
  const ui = await workspace(t, {
    intercept: ({ path }) =>
      path === '/tickets/A2' ? { status: 503, body: { detail: 'Try again later' } } : undefined,
  });
  await ui.click('[data-ticket="A2"]');
  assert.equal(ui.document.querySelector('.ticket-title'), null);
  assert.match(
    ui.document.querySelector('#ticket-detail').textContent,
    /could not|couldn’t|unable/i,
  );
});

test('search and filters keep details inside the visible queue', async (t) => {
  const ui = await workspace(t);
  const search = ui.document.querySelector('#ticket-search');
  search.value = 'Request A2';
  search.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  await flush();
  assert.equal(ui.document.querySelector('.ticket-title').textContent, 'Request A2');
  await ui.click('[data-filter="approved"]');
  assert.equal(ui.document.querySelector('.ticket-title'), null);
  assert.equal(ui.document.querySelectorAll('[data-ticket]').length, 0);
});

test('a delayed refresh from the previous business cannot overwrite the new workspace', async (t) => {
  const slow = deferred();
  let delay = false;
  const ui = await workspace(t, {
    hash: 'monitoring',
    intercept: ({ path, org }) =>
      delay && org === 'a' && path === '/tickets' ? slow.promise : undefined,
  });
  delay = true;
  await ui.click('[data-action="refresh"]');
  await ui.route('settings');
  await ui.click('[data-switch="b"]');
  slow.resolve([ticket('A1'), ticket('A2')]);
  await flush();
  await ui.click('[data-action="sort"]');
  assert.match(ui.document.querySelector('.workspace').textContent, /Business B/);
  assert.equal(ui.document.querySelector('.ticket-title').textContent, 'Request B1');
  assert.equal(ui.document.querySelectorAll('[data-ticket="A1"]').length, 0);
});

test('finishing an analysis after a workspace switch cannot affect the new workspace', async (t) => {
  const slow = deferred();
  const ui = await workspace(t, {
    intercept: ({ path }) => (path === '/tickets/A1/analyze' ? slow.promise : undefined),
  });
  await ui.click('[data-action="analyze"]');
  await ui.route('settings');
  await ui.click('[data-switch="b"]');
  slow.resolve(ticket('A1', { result: { reason: 'missing_sources' } }));
  await flush();
  assert.equal(ui.document.querySelector('.ticket-title').textContent, 'Request B1');
  assert.equal(ui.document.querySelector('.loading-line'), null);
  assert.ok(!ui.document.querySelector('#toasts').textContent.includes('manual review'));
});

test('knowledge search preserves keyboard focus and cursor while repainting', async (t) => {
  const ui = await workspace(t, { hash: 'knowledge' });
  const search = ui.document.querySelector('#doc-search');
  search.focus();
  search.value = 'no matching policy';
  search.setSelectionRange(4, 4);
  search.dispatchEvent(new ui.window.Event('input', { bubbles: true }));
  assert.equal(ui.document.activeElement.id, 'doc-search');
  assert.equal(ui.document.activeElement.selectionStart, 4);
});
