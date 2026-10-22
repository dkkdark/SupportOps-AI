import test from 'node:test';
import assert from 'node:assert/strict';
import { workspace } from './harness.js';

test('navigation preserves sidebar, page and queue scroll independently', async (t) => {
  const ui = await workspace(t);
  ui.document.querySelector('.sidebar nav').scrollTop = 200;
  ui.document.querySelector('.ticket-list').scrollTop = 425;
  ui.window.scrollY = 310;
  await ui.route('settings');
  assert.equal(ui.document.querySelector('.sidebar nav').scrollTop, 200);
  assert.equal(ui.window.scrollY, 0);
  ui.window.scrollY = 640;
  await ui.route('tickets');
  assert.equal(ui.window.scrollY, 310);
  assert.equal(ui.document.querySelector('.ticket-list').scrollTop, 425);
  await ui.click('[data-action="sort"]');
  assert.equal(ui.document.querySelector('.ticket-list').scrollTop, 425);
  await ui.route('settings');
  assert.equal(ui.window.scrollY, 640);
});

test('modal keeps focus and keyboard shortcuts out of the inert background', async (t) => {
  const ui = await workspace(t, { hash: 'settings' });
  ui.window.scrollY = 600;
  await ui.click('[data-action="new-workspace"]');
  assert.equal(ui.document.querySelector('#app').inert, true);
  assert.ok(ui.document.body.classList.contains('modal-open'));
  assert.equal(ui.document.activeElement.name, 'name');
  ui.document.dispatchEvent(
    new ui.window.KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }),
  );
  assert.equal(ui.window.location.hash, '#settings');
  assert.ok(ui.document.querySelector('.modal').contains(ui.document.activeElement));
  const last = ui.document.querySelector('.modal .btn-primary');
  last.focus();
  ui.document.dispatchEvent(
    new ui.window.KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
  );
  assert.equal(ui.document.activeElement.dataset.action, 'close-modal');
  ui.document.dispatchEvent(
    new ui.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
  );
  assert.equal(ui.document.querySelector('#overlay').children.length, 0);
  assert.equal(ui.document.querySelector('#app').inert, false);
  assert.ok(!ui.document.body.classList.contains('modal-open'));
  assert.equal(ui.window.scrollY, 600);
});

test('search shortcut updates the route so reloading keeps Requests open', async (t) => {
  const ui = await workspace(t, { hash: 'settings' });
  ui.document.dispatchEvent(
    new ui.window.KeyboardEvent('keydown', {
      key: 'k',
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    }),
  );
  assert.equal(ui.window.location.hash, '#tickets');
  assert.equal(ui.document.activeElement.id, 'ticket-search');
});
