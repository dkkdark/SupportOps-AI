const root = document.querySelector('#intake-app');
const esc = (x) =>
  String(x ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
const slug = location.pathname.split('/').filter(Boolean).at(-1);
let widget;
try {
  const r = await fetch('/api/forms/' + encodeURIComponent(slug), { credentials: 'omit' });
  const config = await r.json();
  if (!r.ok) throw new Error(config.detail || 'This form is unavailable.');
  document.title = config.title + ' · ' + config.business_name;
  root.innerHTML = `<a class="intake-brand" href="/">SupportOps <span>AI</span></a><span class="eyebrow">${esc(config.business_name)}</span><h1>${esc(config.title)}</h1><p>Your request goes directly to the team at ${esc(config.business_name)}. They will review it and decide the next steps.</p><form id="incoming"><label class="field">Your name<input name="customer" required minlength="2" maxlength="100" autocomplete="name"></label><label class="field">Email<input name="email" type="email" required maxlength="160" autocomplete="email"></label><label class="field">Subject<input name="subject" required minlength="3" maxlength="180"></label><label class="field">Request details<textarea name="message" required minlength="10" maxlength="5000" rows="6" placeholder="What would you like the team to help with?"></textarea></label><label class="honeypot" aria-hidden="true">Website<input name="website" tabindex="-1" autocomplete="off"></label><div id="spam-check"></div><p class="form-hint">Include only the information needed to handle your request. This form does not provide an automatic decision or response.</p><div class="form-error" role="alert"></div><button class="btn btn-primary auth-submit" type="submit">Submit request →</button></form><footer class="intake-footer">Sent to ${esc(config.business_name)} · Powered by SupportOps</footer>`;
  if (config.captcha_site_key) {
    const script = document.createElement('script');
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    script.onload = () => {
      widget = window.turnstile.render('#spam-check', { sitekey: config.captcha_site_key });
    };
    document.head.appendChild(script);
  }
  document.querySelector('#incoming').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target,
      button = form.querySelector('button'),
      error = form.querySelector('.form-error');
    button.disabled = true;
    error.textContent = '';
    try {
      const data = Object.fromEntries(new FormData(form));
      data.captcha_token = widget !== undefined ? window.turnstile.getResponse(widget) : '';
      const response = await fetch('/api/forms/' + encodeURIComponent(slug), {
        method: 'POST',
        credentials: 'omit',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      const result = await response.json();
      if (!response.ok)
        throw new Error(
          typeof result.detail === 'string' ? result.detail : 'Check the form and try again.',
        );
      root.innerHTML = `<a class="intake-brand" href="/">SupportOps AI</a><div class="receipt-mark">✓</div><h1>Request received</h1><p>Your request is in ${esc(config.business_name)}’s workspace. Keep this reference for your records.</p><code class="receipt-code">${esc(result.reference)}</code><p class="form-hint">The team will review the request. No automatic reply or decision has been issued.</p><a class="btn auth-submit" href="${esc(location.pathname)}">Submit another request</a>`;
    } catch (err) {
      error.textContent = err.message;
      button.disabled = false;
      if (widget !== undefined) window.turnstile.reset(widget);
    }
  });
} catch (e) {
  root.innerHTML = `<h1>This form is unavailable</h1><p>${esc(e.message)}</p><a class="btn" href="/">Back to SupportOps</a>`;
}
