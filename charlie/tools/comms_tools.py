"""Communication tools — email and calendar via Gmail/Google Calendar APIs."""

import datetime
import logging

from charlie.tools.tool_decorator import tool, RiskTier

logger = logging.getLogger("charlie.tools.comms")


def _get_gmail():
    """Lazy-load Gmail integration."""
    from charlie.integrations.gmail import GmailIntegration
    return GmailIntegration()


def _get_calendar():
    """Lazy-load Google Calendar integration."""
    from charlie.integrations.google_calendar import GoogleCalendarIntegration
    return GoogleCalendarIntegration()


# ── WIRED TO REAL INTEGRATIONS ──────────────────────────────────────────────


@tool(
    name="send_gmail",
    description="Send an email via Gmail API. Requires OAuth setup.",
    category="comms",
    risk_tier=RiskTier.TIER_1,
)
def send_gmail(to: str, subject: str, body: str) -> str:
    """Send an email via Gmail API."""
    try:
        gmail = _get_gmail()
        success = gmail.execute("send_email", to=to, subject=subject, body=body)
        if success:
            return f"Email sent to {to}: {subject}"
        return f"Failed to send email to {to}. Check Gmail OAuth setup."
    except Exception as e:
        logger.error(f"send_gmail_failed | {e}")
        return f"Gmail error: {e}"


@tool(
    name="get_gmail_messages",
    description="Get recent Gmail messages. Requires OAuth setup.",
    category="comms",
    risk_tier=RiskTier.TIER_0,
)
def get_gmail_messages(max_results: int = 10, query: str = "is:unread") -> str:
    """Get recent Gmail messages."""
    try:
        gmail = _get_gmail()
        messages = gmail.fetch(max_results=max_results, query=query)
        if not messages:
            return "No Gmail messages found."
        lines = [f"Gmail messages ({len(messages)}):"]
        for m in messages:
            lines.append(f"  - From: {m.get('from', '?')} | Subject: {m.get('subject', '?')}")
            lines.append(f"    Snippet: {m.get('snippet', '')[:100]}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"get_gmail_failed | {e}")
        return f"Gmail error: {e}"


@tool(
    name="get_calendar_events",
    description="Get upcoming Google Calendar events. Requires OAuth setup.",
    category="comms",
    risk_tier=RiskTier.TIER_0,
)
def get_calendar_events(max_results: int = 10, days_ahead: int = 7) -> str:
    """Get upcoming calendar events."""
    try:
        cal = _get_calendar()
        events = cal.fetch(max_results=max_results)
        if not events:
            return "No upcoming calendar events."
        lines = [f"Calendar events ({len(events)}):"]
        for e in events:
            lines.append(f"  - {e.get('summary', '?')} at {e.get('start', '?')}")
            if e.get('location'):
                lines.append(f"    Location: {e['location']}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"get_calendar_failed | {e}")
        return f"Calendar error: {e}"


@tool(
    name="create_event",
    description="Create a Google Calendar event. Requires OAuth setup.",
    category="comms",
    risk_tier=RiskTier.TIER_1,
)
def create_event(title: str, start_time: str, duration_minutes: int = 30, description: str = "") -> str:
    """Create a calendar event. start_time should be ISO 8601 format."""
    try:
        cal = _get_calendar()
        # Calculate end_time from start_time + duration
        start_dt = datetime.datetime.fromisoformat(start_time)
        end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
        end_time = end_dt.isoformat()

        success = cal.execute(
            "create_event",
            summary=title,
            start_time=start_time,
            end_time=end_time,
            description=description,
        )
        if success:
            return f"Event created: {title} at {start_time} ({duration_minutes}min)"
        return "Failed to create event. Check Calendar OAuth setup."
    except Exception as e:
        logger.error(f"create_event_failed | {e}")
        return f"Calendar error: {e}"

