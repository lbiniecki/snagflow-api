"""
Email service — Resend wrapper.

Provides:
  - `send_email()`: the one function every caller uses. Best-effort: logs and
    returns False on failure rather than raising. That way a transient Resend
    outage doesn't break a team invite, subscription confirmation, etc.
  - `render_email()`: wraps body HTML in a branded VoxSite shell (orange
    header, footer with support link). Keeps every email visually consistent.

Configuration comes from settings (see app/config.py):
  - RESEND_API_KEY   — unset = dev mode (logs to stdout, never calls Resend)
  - EMAIL_FROM       — e.g. "noreply@voxsite.app"
  - EMAIL_FROM_NAME  — e.g. "VoxSite"
  - APP_URL          — used to construct action links in emails
  - SUPPORT_EMAIL    — shown in email footers
"""
from __future__ import annotations

import html as _html
import logging
import re
from typing import Optional

from app.config import settings

logger = logging.getLogger("voxsite.email")

# Import resend lazily — we want the module to be importable even if the
# package isn't installed yet (e.g. in a fresh clone before `pip install`).
try:
    import resend  # type: ignore
    _RESEND_AVAILABLE = True
except ImportError:
    resend = None  # type: ignore
    _RESEND_AVAILABLE = False


# ─── Public API ─────────────────────────────────────────────────

async def send_email(
    to: str | list[str],
    subject: str,
    html: str,
    text: Optional[str] = None,
    reply_to: Optional[str] = None,
    tags: Optional[list[dict]] = None,
    attachments: Optional[list[dict]] = None,
) -> bool:
    """
    Send a transactional email via Resend.

    Returns True on success, False on any failure. Never raises — callers
    should treat email sending as best-effort and not fail the main request
    if it returns False.

    Args:
      to:          recipient address, or a list of addresses
      subject:     subject line
      html:        full HTML body (use `render_email()` to wrap body in the shell)
      text:        plaintext alternative (auto-derived from html if omitted)
      reply_to:    optional reply-to address (falls back to EMAIL_FROM)
      tags:        Resend tag dicts, e.g. [{"name": "category", "value": "invite"}]
      attachments: Resend attachment dicts, e.g.
                   [{"filename": "report.pdf", "content": "<base64-encoded bytes>"}]
                   Resend caps total email size (html + attachments, after
                   Base64) at 40 MB. Callers handle their own size budgeting.
    """
    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        logger.warning("send_email called with no recipients; skipping")
        return False

    # Auto-derive plaintext if caller didn't provide one
    text_body = text or _html_to_text(html)

    from_header = _format_from()

    # ── Dev mode: no API key, just log ─────────────────────────
    api_key = settings.RESEND_API_KEY
    if not api_key or not _RESEND_AVAILABLE:
        reason = "RESEND_API_KEY not set" if not api_key else "resend package not installed"
        attach_note = f" | {len(attachments)} attachment(s)" if attachments else ""
        logger.info(
            "[email dev-mode | %s] to=%s | from=%s | subject=%s%s",
            reason, recipients, from_header, subject, attach_note,
        )
        logger.debug("[email dev-mode] text body:\n%s", text_body)
        return True  # pretend-success so downstream code doesn't error

    # ── Real send ──────────────────────────────────────────────
    params: dict = {
        "from": from_header,
        "to": recipients,
        "subject": subject,
        "html": html,
        "text": text_body,
    }
    if reply_to:
        params["reply_to"] = reply_to
    if tags:
        params["tags"] = tags
    if attachments:
        params["attachments"] = attachments

    try:
        resend.api_key = api_key
        result = await resend.Emails.send_async(params)
        msg_id = (result or {}).get("id") if isinstance(result, dict) else getattr(result, "id", None)
        logger.info("email sent: to=%s subject=%r id=%s", recipients, subject, msg_id)
        return True
    except Exception as exc:
        # Best-effort: log with full context, don't raise
        logger.error(
            "email send failed: to=%s subject=%r error=%s",
            recipients, subject, exc,
            exc_info=True,
        )
        return False


# ─── Template helpers ───────────────────────────────────────────

def render_email(
    title: str,
    body_html: str,
    *,
    preheader: Optional[str] = None,
    cta_label: Optional[str] = None,
    cta_url: Optional[str] = None,
) -> str:
    """
    Wrap body HTML in the VoxSite email shell.

    Args:
      title:      large heading at the top of the content card
      body_html:  HTML for the main content (already-safe fragment, not escaped)
      preheader:  short plaintext preview shown by email clients in the inbox.
                  Defaults to the title if omitted.
      cta_label:  optional button text (renders a primary-coloured button)
      cta_url:    optional button destination
    """
    preheader = preheader or title
    brand_orange = "#FF6B35"
    dark = "#1A2638"
    text_light = "#6B7280"
    border = "#E5E7EB"
    support_email = settings.SUPPORT_EMAIL
    app_url = settings.APP_URL

    cta_html = ""
    if cta_label and cta_url:
        cta_html = f"""
        <tr>
          <td align="center" style="padding: 24px 0 8px 0; text-align: center;">
            <a href="{_html.escape(cta_url, quote=True)}"
               style="display: inline-block; background: {brand_orange}; color: #ffffff;
                      text-decoration: none; font-weight: 600; font-size: 15px;
                      padding: 12px 28px; border-radius: 8px;
                      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
              {_html.escape(cta_label)}
            </a>
          </td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_html.escape(title)}</title>
</head>
<body style="margin: 0; padding: 0; background: #F5F4F2;
             font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
             color: {dark}; line-height: 1.5;">
  <!-- Preheader (hidden in body, shown in inbox preview) -->
  <div style="display: none; max-height: 0; overflow: hidden;">
    {_html.escape(preheader)}
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background: #F5F4F2; padding: 24px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
               style="max-width: 600px; width: 100%; background: #ffffff;
                      border-radius: 12px; overflow: hidden;
                      box-shadow: 0 1px 3px rgba(0,0,0,0.06);">
          <!-- Header -->
          <tr>
            <td style="background: {dark}; padding: 20px 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="font-size: 18px; font-weight: 700; color: #ffffff; letter-spacing: 0.3px;">
                    <span style="color: {brand_orange};">Vox</span>Site
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Title -->
          <tr>
            <td style="padding: 32px 32px 0 32px;">
              <h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 700; color: {dark};">
                {_html.escape(title)}
              </h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding: 0 32px 8px 32px; font-size: 15px; color: {dark};">
              {body_html}
            </td>
          </tr>

          <!-- CTA -->
          {cta_html}

          <!-- Footer -->
          <tr>
            <td style="padding: 32px; border-top: 1px solid {border};
                       font-size: 12px; color: {text_light}; line-height: 1.6;">
              Questions? Reply to this email or contact
              <a href="mailto:{_html.escape(support_email, quote=True)}"
                 style="color: {brand_orange}; text-decoration: none;">
                {_html.escape(support_email)}
              </a>.
              <br>
              <a href="{_html.escape(app_url, quote=True)}"
                 style="color: {text_light}; text-decoration: underline;">
                voxsite.app
              </a>
              · Construction snagging, simplified.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ─── Internal helpers ───────────────────────────────────────────

def _format_from() -> str:
    """Build the 'From' header value from settings."""
    name = (settings.EMAIL_FROM_NAME or "").strip()
    addr = (settings.EMAIL_FROM or "").strip()
    if name and addr:
        return f"{name} <{addr}>"
    return addr or "noreply@voxsite.app"


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINE_RE = re.compile(r"\n\s*\n\s*\n+")


def _html_to_text(html: str) -> str:
    """
    Rough HTML-to-plaintext conversion for the text/plain alternative.
    Not perfect but good enough for deliverability — Gmail & co mostly want
    *some* plaintext present, they don't grade the prose.
    """
    # Preserve paragraph and br breaks as newlines
    t = re.sub(r"(?i)<br\s*/?>", "\n", html)
    t = re.sub(r"(?i)</(p|div|tr|h[1-6]|li)>", "\n", t)
    # Strip remaining tags
    t = _TAG_RE.sub("", t)
    # Unescape entities
    t = _html.unescape(t)
    # Collapse whitespace
    t = _WS_RE.sub(" ", t)
    t = _BLANKLINE_RE.sub("\n\n", t)
    return t.strip()
