# Gmail setup for SupportOps AI

Sender and support address: **info.xenonimus@gmail.com**.

## Implemented support

- Gmail SMTP, sender identity, Reply-To and operator contact are configurable in the ignored local environment file or server secrets. Credential values are managed by the operator.
- Gmail uses `smtp.gmail.com:587` with STARTTLS and certificate verification. Port 465 is supported with `SMTP_SSL=true` and `SMTP_STARTTLS=false`.
- Missing SMTP credentials, incompatible TLS settings or an insecure production public URL fail readiness checks.
- Verification and recovery emails use single-use links. A failed resend does not invalidate an earlier link. Using a link revokes other links for the same purpose; password recovery also ends user sessions.
- SMTP failures are recorded as technical codes without passwords, message content, recipients or full provider responses.
- Owners/admins can use `GET /api/email/status`; local diagnostics are also available. Neither sends a test email by default.
- Demo mode, the $5 allowance and purchasing-launch notification records remain enabled.

SMTP sends application account emails. Reading this Gmail inbox and converting incoming messages into requests is not implemented. Requests can arrive through public forms, the inbound API or internal creation.

## Gmail account setup

1. Sign in to **info.xenonimus@gmail.com** and enable two-step verification.
2. Open [App Passwords](https://myaccount.google.com/apppasswords) and create a dedicated password for SupportOps AI. Account settings may make this unavailable. The current SMTP adapter requires an App Password; do not use the regular Google password. If App Passwords are unavailable, a separate OAuth integration or another SMTP provider is needed. See [Google's App Password documentation](https://support.google.com/mail/answer/185833?hl=en).
3. Add the password privately in a local editor:

```dotenv
SMTP_PASSWORD=your_app_password_without_separator_spaces
```

Keep the password out of chat, shell command arguments and Git. On a server, use secret storage such as Secrets Manager; Terraform receives the secret ARN only.

4. With `.venv` activated, check configuration:

```bash
python -m scripts.check_email
```

Before the password is configured, expect `ready: false` and `missing: ["SMTP_PASSWORD"]`. After configuration, `ready: true` confirms completeness, not proven delivery.

5. Check connectivity and authentication:

```bash
python -m scripts.check_email --connect
```

This performs TLS/LOGIN/NOOP and **does not send an email**. `smtp_connected` confirms the connection check. For `smtp_authentication_failed`, verify the account and App Password. Output excludes passwords and full server responses.

6. Restart the API and worker after changing environment settings. For a public launch, set `APP_ENV=production` and an HTTPS domain in `PUBLIC_BASE_URL`; email links must open for recipients rather than point to localhost.
7. Register a test account with an inbox you control. Check delivery, the spam folder and the verification link, then test password recovery. Completing these links is a separate user check beyond SMTP diagnostics.

SMTP settings follow the [Gmail documentation](https://support.google.com/mail/answer/7104828?hl=en). Google determines sending limits and App Password availability; this setup is not a bulk-mail service.

## Future purchasing announcements

Accounts that exhaust their allowance or choose `Email me at launch` are recorded in `demo_accounts`. `scripts.demo_waitlist` exports verified pending recipients without sending anything. Bulk launch announcements are not enabled, and delivery timestamps must not be set in advance.

Verification and recovery requests are persisted in a durable queue and delivered by the worker with retries. Users can request a new email after retries are exhausted. See [backend operations](backend-operations.md) for queue states and operator commands.
