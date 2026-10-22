# UI verification — October 22, 2026

Verification used an isolated local SQLite database with the synthetic businesses Acme QA Services and Second QA Business. No test records were written to the working database. `.env` was not opened; tests and the QA server explicitly disable dotenv loading. Paid models and SMTP were disabled in QA. No publication or Git push was performed.

## Fixes

| Scenario | Previous behavior | Corrected behavior |
| --- | --- | --- |
| Navigating Workspace sections | Recreated the sidebar with its scroll position at the top | Preserves sidebar scroll |
| Returning to a long page | Lost the reading position | Remembers each page's position within the session |
| Refreshing the queue or selecting a request | Reset queue scroll | Restores independent scroll regions |
| Opening a modal | Allowed background scrolling; focus could move the page | Locks the background and focuses without scrolling |
| ⌘K / Ctrl+K inside a modal | Moved focus into the inert background | Global shortcuts do not interfere with modal forms |
| Opening search with a shortcut | Displayed Requests with the previous URL | Keeps the URL and visible section consistent |
| Selecting requests rapidly | A late response could replace the latest selection | Accepts details only for the current selection |
| Failing to load a request | Left the previous request's actions available | Hides the previous card and provides error/retry controls |
| Searching and filtering | Kept an excluded request open on the right | Selects a matching request or shows an empty state |
| Switching businesses during refresh or analysis | Old responses could change the new workspace | Guards responses by workspace/version and ignores stale errors |
| Viewing another request during analysis | A shared spinner implied the other request was processing | Associates progress with one request and disables another run until completion |
| Repeated clicks or submissions | Could start overlapping switches or duplicate submissions | Blocks the action while it is pending |
| Public forms with Turnstile | Sent the unsupported hidden `cf-turnstile-response` field to a strict API schema | Sends the supported `captcha_token` field only |
| Losing a public form's response | Retrying could create a second request | Reuses `external_id` for unchanged content; new content gets a new ID |
| Failing registration | Left the CAPTCHA consumed | Resets CAPTCHA and removes the previous widget when changing forms |
| Landing-section links on sign-in | Target sections were absent and clicks did nothing | Opens the landing page before scrolling to the requested section |
| Updating frontend files | Stable asset names could retain stale cached code | HTML/assets use `Cache-Control: no-cache`; API responses retain `no-store` |
| Narrow sidebar | Sign-out control extended beyond the panel | Stacks avatar and sign-out control inside the panel |

## Results

- **84 backend tests passed**, including real PostgreSQL 17 / pgvector and Qdrant 1.19.0 integrations. A separate SQL test database and Qdrant test collections were used.
- **16 frontend regression tests passed**. They execute the actual application modules in JSDOM with controlled API delays/failures. These are DOM tests; browser checks separately verify layout and scrolling.
- JavaScript syntax and Prettier checks passed. The browser demo was rebuilt with the same fixes.
- Browser checks covered 1280×720 and 390×844 screens, request creation, filters, search, business switching, modal forms, public submissions and their appearance in the queue.
- Settings restored its **720 px** position after navigating to Knowledge base and back. Opening AI usage preserved the sidebar near **200 px** instead of resetting it to zero.
- Mobile page width matched the available **375 px** within a 390 px viewport with a system scrollbar. The sign-out control occupied **x=21…51** inside a sidebar spanning **x=0…72**.
- The checked QA tabs had no console errors or warnings.

## Reproduce

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run check:format
python -m pytest
```

For integration tests, point `TEST_DATABASE_URL` at a separate PostgreSQL test database and `TEST_QDRANT_URL` at an available Qdrant server. Corresponding integration tests are skipped without these settings. Ordinary tests do not read the operator's `.env` or call real models. Do not use a working business database as a test database.

Local screenshots are saved in `var/qa/ui-workspace-scroll.png`, `var/qa/ui-search-selection.png`, `var/qa/ui-mobile-navigation.png` and `var/qa/ui-public-form-receipt.png`. `var/` is excluded from Git.

These checks do not verify the OVH server that was awaiting activation. QA did not call real OpenAI, SMTP or Turnstile services; their integration contracts used controlled substitutes.
