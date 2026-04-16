"""
Specific transactional email templates.

One function per email type. Each builds the body, wraps it with render_email(),
and sends via send_email(). All return bool — callers treat as best-effort.

Tasks 4, 5, and 7 will add more functions here.
"""
from __future__ import annotations

import base64
import html as _html
from typing import Optional

from app.config import settings
from app.services.email_service import send_email, render_email


async def send_team_invite_email(
    *,
    to_email: str,
    company_name: str,
    inviter_name: str,
    inviter_email: str,
    is_new_user: bool,
    invite_token: Optional[str] = None,
) -> bool:
    """
    Notifies someone they've been invited to a VoxSite team.

    Two variants:
      - is_new_user=True  → recipient doesn't have a VoxSite account yet.
                            Email prompts them to sign up, then the existing
                            email-match auto-join (see /api/companies/join)
                            adds them to the team on first login.
      - is_new_user=False → recipient already has an account and has been
                            added directly to company_members. Email is a
                            courtesy notification — CTA opens the app.
    """
    # Who invited you — prefer a real name, fall back to the email
    inviter_display = inviter_name.strip() if inviter_name else ""
    if not inviter_display:
        inviter_display = inviter_email

    company_safe = _html.escape(company_name)
    inviter_safe = _html.escape(inviter_display)

    if is_new_user:
        # Include the token + email in the signup URL so the frontend can,
        # later, pre-fill the email field and show "Joining {Company}".
        # Not required for the join to work — the /join endpoint matches
        # by email regardless of the token — but it enables a better UX.
        params = []
        if invite_token:
            params.append(f"invite={_html.escape(invite_token, quote=True)}")
        params.append(f"email={_html.escape(to_email, quote=True)}")
        cta_url = f"{settings.APP_URL}/?{'&'.join(params)}"
        cta_label = "Create your VoxSite account"

        title = f"You've been invited to join {company_name} on VoxSite"
        preheader = f"{inviter_display} invited you to collaborate on {company_name}."
        body = f"""
          <p>Hi,</p>
          <p><strong>{inviter_safe}</strong> has invited you to join the
             <strong>{company_safe}</strong> team on VoxSite — a mobile-first
             construction snagging tool.</p>
          <p>To accept, create a free VoxSite account with
             <strong>{_html.escape(to_email)}</strong>. You'll be added to the
             team automatically when you sign in for the first time.</p>
          <p style="color: #6B7280; font-size: 13px;">
             Note: this invite is tied to your email address. If you sign up
             with a different address, you'll need a new invite.
          </p>
        """
    else:
        cta_url = settings.APP_URL
        cta_label = f"Open {company_name} in VoxSite"

        title = f"You've been added to {company_name}"
        preheader = f"{inviter_display} added you to {company_name} on VoxSite."
        body = f"""
          <p>Hi,</p>
          <p><strong>{inviter_safe}</strong> has added you to the
             <strong>{company_safe}</strong> team on VoxSite.</p>
          <p>You can now view and contribute to {company_safe}'s projects, site
             visits, and snag reports. Open VoxSite to get started.</p>
        """

    html = render_email(
        title=title,
        preheader=preheader,
        body_html=body,
        cta_label=cta_label,
        cta_url=cta_url,
    )

    return await send_email(
        to=to_email,
        subject=title,
        html=html,
        reply_to=inviter_email,  # replying goes to the inviter, not noreply@
        tags=[
            {"name": "category", "value": "team_invite"},
            {"name": "is_new_user", "value": "true" if is_new_user else "false"},
        ],
    )


async def send_report_email(
    *,
    to: list[str],
    project_name: str,
    visit_no: str,
    sender_name: str,
    sender_email: str,
    summary: dict,
    pdf_bytes: Optional[bytes] = None,
    pdf_filename: Optional[str] = None,
    download_url: Optional[str] = None,
    download_size_mb: Optional[float] = None,
    download_expires_days: int = 7,
    custom_message: Optional[str] = None,
) -> bool:
    """
    Send a site-visit PDF report to one or more recipients.

    Size-based delivery mode:
      - If pdf_bytes is given → attach the PDF to the email (Resend has a 40MB
        total cap, so callers should only pass bytes when the PDF is small
        enough, typically <10 MB raw).
      - If download_url is given → embed a big "Download report" button that
        links to a short-lived signed URL.

    Args:
      to:                 recipient addresses (at least one)
      project_name:       project display name (used in subject + body)
      visit_no:           visit number string (e.g. "3")
      sender_name:        name of the user sending — shown in signature
      sender_email:       reply-to, so replies go to the sender not noreply@
      summary:            dict with keys: total, open, closed, high_priority
      pdf_bytes:          raw PDF bytes for attachment mode
      pdf_filename:       filename for the attachment (required if pdf_bytes given)
      download_url:       signed URL for link mode
      download_size_mb:   PDF size for display next to the link
      download_expires_days: shown in the UI as "expires in X days"
      custom_message:     optional user-written note to include above the summary
    """
    if not to:
        return False

    # Subject & title
    subject = f"Site Visit Report — {project_name} (Visit {visit_no})"
    title = f"Site Visit Report: {project_name}"
    preheader = f"Visit {visit_no} · {summary.get('total', 0)} items"

    # Sender display
    sender_display = (sender_name or "").strip() or sender_email

    # Summary box
    total = int(summary.get("total", 0))
    open_count = int(summary.get("open", 0))
    closed = int(summary.get("closed", 0))
    high = int(summary.get("high_priority", 0))

    summary_html = f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background: #F5F4F2; border-radius: 8px; margin: 16px 0;">
        <tr>
          <td style="padding: 16px 20px; font-size: 14px; color: #1A2638;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="padding: 4px 0;">
                  <strong>Total items:</strong> {total}
                  &nbsp;·&nbsp;
                  <span style="color: #EF4444;">Open: {open_count}</span>
                  &nbsp;·&nbsp;
                  <span style="color: #22C55E;">Closed: {closed}</span>
                </td>
              </tr>
              {f'<tr><td style="padding: 4px 0; color: #DC2626;"><strong>High priority open:</strong> {high}</td></tr>' if high > 0 else ''}
            </table>
          </td>
        </tr>
      </table>
    """

    # Optional custom message from sender
    custom_msg_html = ""
    if custom_message and custom_message.strip():
        msg_safe = _html.escape(custom_message.strip()).replace("\n", "<br>")
        custom_msg_html = f"""
          <div style="border-left: 3px solid #FF6B35; padding: 4px 0 4px 14px;
                      margin: 16px 0; color: #374151; font-size: 14px;">
            {msg_safe}
          </div>
        """

    # Body depends on delivery mode
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    attachments: Optional[list[dict]] = None
    delivery_note = ""

    if pdf_bytes is not None:
        # ── Attachment mode ────────────────────────────────────
        if not pdf_filename:
            pdf_filename = f"{project_name}-visit-{visit_no}.pdf"
        encoded = base64.b64encode(pdf_bytes).decode("ascii")
        attachments = [{
            "filename": pdf_filename,
            "content": encoded,
        }]
        delivery_note = f"""
          <p style="font-size: 13px; color: #6B7280;">
            The full PDF is attached to this email ({_format_size(len(pdf_bytes))}).
          </p>
        """

    elif download_url:
        # ── Link mode ──────────────────────────────────────────
        cta_label = "Download report"
        cta_url = download_url
        size_str = f" ({download_size_mb:.1f} MB)" if download_size_mb else ""
        delivery_note = f"""
          <p style="font-size: 13px; color: #6B7280;">
            The PDF{size_str} is too large to attach to email. Use the button
            below to download it — the link expires in {download_expires_days} days.
          </p>
        """

    else:
        # Caller gave us neither — fall through with nothing but the summary
        delivery_note = """
          <p style="font-size: 13px; color: #DC2626;">
            (Report file could not be attached or uploaded — please contact the sender.)
          </p>
        """

    body = f"""
      <p>Hi,</p>
      <p><strong>{_html.escape(sender_display)}</strong> has shared a VoxSite site
         visit report for <strong>{_html.escape(project_name)}</strong>
         (Visit {_html.escape(str(visit_no))}).</p>
      {custom_msg_html}
      {summary_html}
      {delivery_note}
      <p style="color: #6B7280; font-size: 13px; margin-top: 24px;">
        Sent by {_html.escape(sender_display)} &lt;{_html.escape(sender_email)}&gt; via VoxSite.
      </p>
    """

    html = render_email(
        title=title,
        preheader=preheader,
        body_html=body,
        cta_label=cta_label,
        cta_url=cta_url,
    )

    # Build kwargs — only include attachments if present (resend SDK ignores
    # attachments=None but we'll just leave the key out entirely).
    send_kwargs: dict = {
        "to": to,
        "subject": subject,
        "html": html,
        "reply_to": sender_email,
        "tags": [
            {"name": "category", "value": "report"},
            {"name": "mode", "value": "attachment" if attachments else "link"},
        ],
    }
    if attachments is not None:
        send_kwargs["attachments"] = attachments

    return await send_email(**send_kwargs)


def _format_size(num_bytes: int) -> str:
    """Format a byte count as e.g. '3.2 MB' or '412 KB'."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


async def send_welcome_email(
    *,
    to_email: str,
    first_name: Optional[str] = None,
) -> bool:
    """
    Welcome email fired from POST /api/auth/signup.

    Keeps it short and actionable: welcome + the three things a new user
    should do next. Works whether or not Supabase email confirmation is on
    — it doesn't mention confirmation, so it reads fine in both states.
    """
    greeting = f"Hi {_html.escape(first_name.strip())}," if first_name and first_name.strip() else "Hi,"
    title = "Welcome to VoxSite"
    preheader = "Get started with your first project and site visit."
    body = f"""
      <p>{greeting}</p>
      <p>Welcome to VoxSite — thanks for signing up. You're now set up on the
         <strong>Free plan</strong> with room for 1 user, 2 projects, and 20
         snags per month.</p>
      <p><strong>Three things to do in the first five minutes:</strong></p>
      <ol style="padding-left: 20px; line-height: 1.7;">
        <li>Create your first project (the site you're inspecting).</li>
        <li>Start a site visit and capture snags — tap the mic to dictate,
            snap up to 4 photos per item.</li>
        <li>Generate a PDF report when you're done.</li>
      </ol>
      <p style="color: #6B7280; font-size: 13px;">
         Need more seats, projects, or want your company logo on reports?
         You can upgrade anytime from the Pricing screen.
      </p>
    """

    html = render_email(
        title=title,
        preheader=preheader,
        body_html=body,
        cta_label="Open VoxSite",
        cta_url=settings.APP_URL,
    )

    return await send_email(
        to=to_email,
        subject="Welcome to VoxSite",
        html=html,
        reply_to=settings.SUPPORT_EMAIL,
        tags=[{"name": "category", "value": "welcome"}],
    )


async def send_subscription_confirmation_email(
    *,
    to_email: str,
    first_name: Optional[str] = None,
    plan_name: str,
    plan_slug: str,
    is_upgrade: bool = True,
    portal_url: Optional[str] = None,
) -> bool:
    """
    Sent after a successful Stripe subscription change (new subscription or
    plan change). Not sent on renewals that don't change the plan.

    Args:
      plan_name:  human name, e.g. "Team"
      plan_slug:  internal slug, used to look up what the plan unlocks
      is_upgrade: affects wording. True for upgrades/new subs, False for
                  downgrades.
      portal_url: direct link to Stripe customer portal (optional). If given,
                  a secondary "Manage subscription" link is rendered in the
                  footer of the body.
    """
    greeting = f"Hi {_html.escape(first_name.strip())}," if first_name and first_name.strip() else "Hi,"

    # What the plan unlocks — sourced from the same plan_limits shape used
    # on the pricing screen, kept here as short marketing copy.
    perks_by_plan = {
        "starter": [
            "Up to 3 team members",
            "5 projects, 100 snags/month",
            "Company logo on PDF reports",
            "No VoxSite watermark",
        ],
        "team": [
            "Up to 10 team members",
            "15 projects, 500 snags/month",
            "Email PDF reports to clients",
            "Company logo on PDF reports",
        ],
        "pro": [
            "Up to 25 team members",
            "Unlimited projects & snags",
            "Email PDF reports to clients",
            "Everything in Team",
        ],
        "business": [
            "Up to 50 team members",
            "Unlimited projects & snags",
            "Priority support",
            "Everything in Pro",
        ],
        "enterprise": [
            "Unlimited team members",
            "Unlimited everything",
            "Dedicated support",
        ],
    }
    perks = perks_by_plan.get(plan_slug.lower(), [])
    perks_html = ""
    if perks:
        items = "\n".join(f"<li>{_html.escape(p)}</li>" for p in perks)
        perks_html = f"""
          <p><strong>What you've unlocked:</strong></p>
          <ul style="padding-left: 20px; line-height: 1.7;">
            {items}
          </ul>
        """

    if is_upgrade:
        title = f"Your VoxSite plan is now {plan_name}"
        preheader = f"Subscription confirmed — you're on the {plan_name} plan."
        intro = f"Your subscription is live — you're now on the <strong>{_html.escape(plan_name)}</strong> plan."
    else:
        title = f"Your VoxSite plan has changed to {plan_name}"
        preheader = f"Plan changed to {plan_name}."
        intro = f"Your subscription has been updated — you're now on the <strong>{_html.escape(plan_name)}</strong> plan."

    manage_link_html = ""
    if portal_url:
        manage_link_html = f"""
          <p style="color: #6B7280; font-size: 13px; margin-top: 18px;">
            Need to change your plan or update billing info?
            <a href="{_html.escape(portal_url, quote=True)}"
               style="color: #FF6B35; text-decoration: none;">Manage subscription</a>.
          </p>
        """

    body = f"""
      <p>{greeting}</p>
      <p>{intro}</p>
      {perks_html}
      <p style="color: #6B7280; font-size: 13px;">
         Your receipt and invoice have been sent separately by Stripe.
      </p>
      {manage_link_html}
    """

    html = render_email(
        title=title,
        preheader=preheader,
        body_html=body,
        cta_label="Open VoxSite",
        cta_url=settings.APP_URL,
    )

    return await send_email(
        to=to_email,
        subject=title,
        html=html,
        reply_to=settings.SUPPORT_EMAIL,
        tags=[
            {"name": "category", "value": "subscription"},
            {"name": "plan", "value": plan_slug.lower()},
        ],
    )
