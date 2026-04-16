# Email Setup — Resend + voxsite.app

This is a one-time setup to get transactional emails sending from `noreply@voxsite.app`.
Once complete, VoxSite can send welcome emails, team invites, subscription
confirmations, and PDF reports.

## 1. Create a Resend account

1. Sign up at <https://resend.com> (free tier: 3 000 emails/month, 100/day).
2. On the dashboard, open **API Keys** and create a new key.
   - Name: `voxsite-production`
   - Permission: **Sending access**
   - Domain: leave blank for now, we'll attach it once verified.
3. Copy the key — it starts with `re_`. You'll paste it into Railway env vars below.

## 2. Verify the `voxsite.app` domain

In Resend → **Domains** → **Add Domain** → enter `voxsite.app`.

Resend will show a set of DNS records to add. There are typically four:

| Type  | Host / Name                        | Purpose  |
| ----- | ---------------------------------- | -------- |
| MX    | `send.voxsite.app`                 | Bounce handling |
| TXT   | `send.voxsite.app` (SPF)           | Authorises Resend to send as you |
| TXT   | `resend._domainkey.voxsite.app`    | DKIM signing key |
| TXT   | `_dmarc.voxsite.app` (DMARC)       | Policy against spoofing |

The exact values come from the Resend dashboard — **copy them from there, not from here**,
as DKIM keys are unique per account.

### Adding the records

Go to wherever `voxsite.app` DNS is managed (Vercel, Cloudflare, your registrar, etc.)
and add each record exactly as Resend shows. A few gotchas:

- **Don't include the root domain in the Host field.** If Resend says the name is
  `send.voxsite.app` and your DNS provider auto-appends `voxsite.app`, enter `send`.
- **TTL** can be `Auto` or `3600`. Doesn't matter much.
- **SPF conflict**: if you already have an SPF record for `voxsite.app` (e.g. for
  Google Workspace), merge the Resend directive into the existing record — don't
  add a second SPF TXT. Two SPF records invalidate each other.

Propagation is usually 5 minutes but can take up to an hour. Click **Verify DNS records**
in Resend after ~10 minutes. All four must show green before you can send.

## 3. Set environment variables on Railway

In the Railway dashboard for `snagflow-api`, set these vars:

```
RESEND_API_KEY    = re_xxxxxxxxxxxxx       # from step 1
EMAIL_FROM        = noreply@voxsite.app
EMAIL_FROM_NAME   = VoxSite
SUPPORT_EMAIL     = support@voxsite.app    # or your own address
APP_URL           = https://voxsite.app
```

Redeploy (Railway does this automatically on env-var change).

## 4. Verify end-to-end

Once deployed, hit the test endpoint from any logged-in client:

```bash
curl -X POST https://<your-api>/api/profiles/test-email \
  -H "Authorization: Bearer <your-supabase-jwt>"
```

Expected response:

```json
{
  "status": "sent",
  "to": "you@example.com",
  "message": "Test email sent to you@example.com. Check your inbox (and spam folder)."
}
```

You should receive a test email within a few seconds. If it lands in spam, that's
usually a DMARC policy issue — tighten the record to `p=none` for the first week,
then step up to `p=quarantine` once you've confirmed legitimate mail flows.

## 5. Developer mode (no Resend setup)

If `RESEND_API_KEY` is not set in the environment, `send_email()` will log the
email contents to stdout and return `True` without actually sending anything.
This lets contributors run the app locally without needing Resend credentials.
Log format:

```
INFO voxsite.email: [email dev-mode | RESEND_API_KEY not set] to=[...] | from=... | subject=...
```

## Troubleshooting

- **`502 Email send failed`** → check Railway logs for the stack trace. Usually
  an invalid/revoked API key or unverified domain.
- **Email sent, lands in spam** → wait 24h for DKIM trust to build up, then send
  again. Repeated spam-folder placement after a week means DMARC needs attention.
- **Works from `/test-email` but not from code** → confirm the calling code is
  `await`-ing `send_email()` — it's async.

## 6. Supabase Storage bucket for large reports (task 7)

The email-report endpoint (`POST /api/reports/{project_id}/email`) needs a
private Storage bucket for reports that exceed the attachment size threshold
(default 10 MB raw).

### Create the bucket

In Supabase → **Storage** → **New bucket**:

- **Name:** `report-exports`
- **Public:** **off** (must be private — signed URLs only)
- **File size limit:** 50 MB or higher
- **Allowed MIME types:** `application/pdf` (optional; not required)

### RLS / policies

The backend uploads via the service-role key, so no RLS policies are required
on the bucket for writes. Signed URLs handle reads.

### Optional: auto-delete old exports

Signed URLs expire after 7 days anyway, so older files are dead weight. You can
set up a scheduled job (Supabase Edge Function or a cron on Railway) to delete
objects older than 30 days. Or leave them — storage is cheap and it doubles as
a manual retrieval fallback.

### What happens if the bucket doesn't exist

For reports ≤ 10 MB: nothing breaks — they're attached directly to the email.

For reports > 10 MB: the API returns a 500 with the message *"Report is too
large to attach and could not be uploaded to storage."* and the frontend
surfaces it as a toast. The user can still use **Download PDF** and share the
file themselves.

## 7. Re-enabling Supabase email confirmation

Email confirmation was disabled during early development so it didn't block
sign-up flows. To turn it back on for production:

### Supabase dashboard steps

1. **Authentication → Providers → Email**
   - Toggle **Confirm email** to **on**.
   - (Optional) Set **Secure email change** to **on** so existing users re-confirm when they change their email.

2. **Authentication → URL Configuration**
   - **Site URL:** `https://voxsite.app`
   - **Redirect URLs:** add `https://voxsite.app/**` and `http://localhost:3000/**` (for local dev).

3. **Authentication → Email Templates → Confirm signup**
   - Customise the template so it looks like a VoxSite email, not the Supabase default. Minimum edits:
     - **Subject:** `Confirm your VoxSite account`
     - **Message:** keep the `{{ .ConfirmationURL }}` variable but re-word the body to match the VoxSite voice. Example:
       ```
       Welcome to VoxSite. Click the link below to confirm your email address and activate your account:

       {{ .ConfirmationURL }}

       If you didn't sign up, you can safely ignore this email.
       ```

### Backend behaviour

The app already handles both states correctly:

- **Signup** (`POST /api/auth/signup`) returns the same 200 response either way. When confirmation is on, Supabase sends its confirmation email automatically; we *also* send our own welcome email (task #4). The two serve different purposes and are fine to arrive together.

- **Login** (`POST /api/auth/login`) now distinguishes "unconfirmed email" from "wrong password". An unconfirmed user gets:
  > 401 — Please confirm your email before logging in. Check your inbox (and spam folder) for the confirmation link.

  A wrong-password attempt still gets the generic `Invalid credentials` (we don't leak whether the email exists).

### Existing users

If there are already users in `auth.users` with `email_confirmed_at IS NULL` when you flip confirmation on, they'll be locked out. Either:
- Manually confirm them via the Supabase dashboard (**Authentication → Users → Confirm email**), or
- Run this SQL in the SQL editor (only if you trust the existing users):
  ```sql
  UPDATE auth.users
  SET    email_confirmed_at = COALESCE(email_confirmed_at, now())
  WHERE  email_confirmed_at IS NULL;
  ```


## 8. Running the profiles email-sync migration

The subscription confirmation email (task #5) and team-invite email (task #6) look up recipient addresses from `public.profiles.email`. Until this migration runs, that column is either missing or empty, and those emails will silently no-op.

Apply once in **Supabase → SQL Editor → New query**, paste the contents of:
```
supabase-migrations/2026-04-16-profiles-email-sync.sql
```
…and run. It's idempotent — safe to re-run if you're not sure it's been applied. The migration:

1. Adds `email` + `created_at` + `updated_at` columns to `profiles` (if missing).
2. Installs a trigger on `auth.users` that mirrors email into `profiles` on insert and on every email change. The trigger runs as `SECURITY DEFINER` so it bypasses RLS.
3. Rewrites the RLS policies on `profiles`: users can read/update their own row, and can read profiles of teammates (same company).
4. Backfills emails for existing profile rows and creates profile rows for any `auth.users` entries that don't have one.

Verify afterwards with the queries in the comment at the bottom of the SQL file — both should return 0.
