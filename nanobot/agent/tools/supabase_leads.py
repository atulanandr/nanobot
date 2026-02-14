"""Supabase leads report tool — fetches and analyzes leads in one step."""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from nanobot.agent.tools.base import Tool


class SupabaseLeadsReportTool(Tool):
    """Fetch leads from Supabase and generate a summary report."""

    name = "leads_report"
    description = (
        "Generate a leads report from the Supabase database. "
        "Shows: new leads added today, stale leads (not updated in 7 days), "
        "and hot leads with status containing 'site visit'. "
        "Requires supabase_url and supabase_key stored in memory, "
        "or passed as parameters."
    )
    parameters = {
        "type": "object",
        "properties": {
            "supabase_url": {
                "type": "string",
                "description": "Supabase project URL, e.g. https://xxx.supabase.co"
            },
            "supabase_key": {
                "type": "string",
                "description": "Supabase anon/service key (JWT)"
            },
            "days_stale": {
                "type": "string",
                "description": "Number of days without update to consider stale. Default: '7'"
            }
        },
        "required": ["supabase_url", "supabase_key"]
    }

    async def execute(
        self,
        supabase_url: str,
        supabase_key: str,
        days_stale: str | int = "7",
        **kwargs: Any,
    ) -> str:
        if isinstance(days_stale, str):
            days_stale = int(days_stale) if days_stale.strip().isdigit() else 7

        url = f"{supabase_url.rstrip('/')}/rest/v1/leads?select=*"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                leads = r.json()
        except Exception as e:
            return f"Error fetching leads: {e}"

        if not leads:
            return "No leads found in the database."

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        stale_cutoff = now - timedelta(days=days_stale)

        new_today = []
        stale = []
        hot_site_visit = []

        for lead in leads:
            name = lead.get("lead_name", "Unknown")
            phone = lead.get("mobile_number", "N/A")
            project = lead.get("project", "N/A")
            status = (lead.get("status") or "").lower()
            priority = lead.get("priority", "N/A")
            bucket = lead.get("lead_bucket", "N/A")
            source = lead.get("lead_source", "N/A")
            notes = lead.get("notes", "")

            created_at = _parse_dt(lead.get("created_at"))
            updated_at = _parse_dt(lead.get("updated_at"))

            summary = f"• {name} | {phone} | {project} | {source} | {bucket} | {priority}"
            if notes:
                summary += f" | {notes[:60]}"

            # New leads added today
            if created_at and created_at >= today_start:
                new_today.append(summary)

            # Stale leads — not updated in N days
            if updated_at and updated_at < stale_cutoff:
                days_ago = (now - updated_at).days
                stale.append(f"{summary} (last updated {days_ago}d ago)")

            # Hot leads with site visit status
            if "site visit" in status:
                sv_date = lead.get("site_visit_date", "N/A")
                hot_site_visit.append(f"{summary} | site visit date: {sv_date}")

        # Build report
        report = [f"📊 *Leads Report — {now.strftime('%Y-%m-%d')}*"]
        report.append(f"Total leads in database: {len(leads)}\n")

        report.append(f"*🆕 New Leads Added Today ({len(new_today)}):*")
        if new_today:
            report.extend(new_today)
        else:
            report.append("  None today.")

        report.append(f"\n*⏰ Stale Leads — Not Updated in {days_stale}+ Days ({len(stale)}):*")
        if stale:
            report.extend(stale[:20])  # Limit to 20
            if len(stale) > 20:
                report.append(f"  ...and {len(stale) - 20} more")
        else:
            report.append("  None — all leads are up to date!")

        report.append(f"\n*🔥 Hot Leads — Site Visit Status ({len(hot_site_visit)}):*")
        if hot_site_visit:
            report.extend(hot_site_visit)
        else:
            report.append("  None with site visit status.")

        return "\n".join(report)


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
