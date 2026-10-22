// Keep independent scroll regions stable when the SPA replaces its markup.
export function createViewState(window, document) {
  const positions = new Map();
  let current = null;
  return {
    render(key, draw) {
      const active = document.activeElement;
      const focus = current === key && active?.id ? active.id : null;
      const selection =
        focus && typeof active.selectionStart === 'number'
          ? [active.selectionStart, active.selectionEnd]
          : null;
      if (current) positions.set(current, [window.scrollX, window.scrollY]);
      for (const el of document.querySelectorAll('[data-scroll-key]'))
        positions.set('region:' + el.dataset.scrollKey, [el.scrollLeft, el.scrollTop]);
      draw();
      current = key;
      for (const el of document.querySelectorAll('[data-scroll-key]')) {
        const [left, top] = positions.get('region:' + el.dataset.scrollKey) || [0, 0];
        el.scrollLeft = left;
        el.scrollTop = top;
      }
      const [left, top] = positions.get(key) || [0, 0];
      window.scrollTo({ left, top, behavior: 'instant' });
      const replacement = focus && document.getElementById(focus);
      if (replacement && !document.getElementById('app').inert) {
        replacement.focus({ preventScroll: true });
        if (selection) replacement.setSelectionRange(...selection);
      }
      // Bound memory in long sessions with many requests.
      while (positions.size > 150) positions.delete(positions.keys().next().value);
    },
    clear() {
      positions.clear();
      current = null;
    },
  };
}
