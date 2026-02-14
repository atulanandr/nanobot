"""Supabase leads report tool — fetches and analyzes leads in one step."""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from nanobot.agent.tools.base import Tool


class SupabaseLeadsReportTool(Tool):
    """Fetch leads from Supabase and generate a summary report."""

    name = "leads_report"
    description = (
        "Generate a leads report from the Supabase database. "
        "Shows: new leads (last 10 days), stale leads (not updated in 7 days), "
        "site visit scheduled/confirmed leads, and hot leads. "
        "Uses notes and updated_at to understand latest status. "
        "Credentials are loaded from environment variables automatically — no need to pass them."
    )
    parameters = {
        "type": "object",
        "properties": {
            "days_new": {
                "type": "string",
                "description": "Days to look back for new leads. Default: '10'"
            },
            "days_stale": {
                "type": "string",
                "description": "Days without update to consider stale. Default: '7'"
            }
        },
        "required": []
    }

    async def execute(
        self,
        days_new: str | int = "10",
        days_stale: str | int = "7",
        **kwargs: Any,
    ) -> str:
        # Read credentials from env vars (set in entrypoint.sh)
        supabase_url = kwargs.get("supabase_url") or os.environ.get("SUPABASE_URL", "")
        supabase_key = kwargs.get("supabase_key") or os.environ.get("SUPABASE_KEY", "")
        if not supabase_url or not supabase_key:
            return "Error: SUPABASE_URL and SUPABASE_KEY environment variables are not set."

        if isinstance(days_new, str):
            days_new = int(days_new) if days_new.strip().isdigit() else 10
        if isinstance(days_stale, str):
            days_stale = int(days_stale) if days_stale.strip().isdigit() else 7

        url = f"{supabase_url.rstrip('/')}/rest/v1/leads?select=*"
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }

        # Also fetch lead_notes for recent activity
        notes_url = f"{supabase_url.rstrip('/')}/rest/v1/lead_notes?select=*&order=created_at.desc&limit=100"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                leads = r.json()

                # Try to fetch notes (non-critical)
                lead_notes = {}
                try:
                    rn = await client.get(notes_url, headers=headers)
                    if rn.status_code == 200:
                        for note in rn.json():
                            lid = note.get("lead_id")
                            if lid and lid not in lead_notes:
                                lead_notes[lid] = note.get("content", "")
                except Exception:
                    pass
        except Exception as e:
            return f"Error fetching leads: {e}"

        if not leads:
            return "No leads found in the database."

        now = datetime.now(timezone.utc)
        new_cutoff = now - timedelta(days=days_new)
        stale_cutoff = now - timedelta(days=days_stale)

        new_leads = []
        stale_leads = []
        site_visit_leads = []
        hot_leads = []

        # Group by project
        by_project: dict[str, int] = {}

        for lead in leads:
            name = lead.get("lead_name", "Unknown")
            phone = lead.get("mobile_number", "N/A")
            project = lead.get("project", "N/A")
            status = (lead.get("status") or "").strip()
            status_lower = status.lower()
            priority = lead.get("priority", "N/A")
            bucket = lead.get("lead_bucket", "N/A")
            source = lead.get("lead_source", "N/A")
            notes = (lead.get("notes") or "").strip()
            lead_id = lead.get("id", "")
            sv_date = lead.get("site_visit_date")
            next_fu = lead.get("next_follow_up")

            # Check for recent notes
            recent_note = lead_notes.get(lead_id, "")

            created_at = _parse_dt(lead.get("created_at"))
            updated_at = _parse_dt(lead.get("updated_at"))

            by_project[project] = by_project.get(project, 0) + 1

            # Format lead line
            line = f"  • *{name}* | {phone} | {project}"
            if source:
                line += f" | {source}"
            if status:
                line += f" | _{status}_"
            if notes:
                line += f"\n    📝 {notes[:80]}"
            if recent_note and recent_note != notes:
                line += f"\n    💬 Latest note: {recent_note[:80]}"
            if next_fu:
                fu_dt = _parse_dt(next_fu)
                if fu_dt:
                    line += f"\n    📅 Next follow-up: {fu_dt.strftime('%b %d, %Y')}"

            # New leads (last N days)
            if created_at and created_at >= new_cutoff:
                days_ago = (now - created_at).days
                new_leads.append((days_ago, f"{line}\n    🕐 Added {days_ago}d ago"))

            # Stale leads — not updated in N days, exclude Lost/Junk
            if updated_at and updated_at < stale_cutoff and bucket not in ("Lost/Junk",):
                days_ago = (now - updated_at).days
                stale_leads.append((days_ago, f"{line}\n    ⚠️ Last updated {days_ago}d ago"))

            # Site visit scheduled/confirmed/done
            if any(kw in status_lower for kw in ("site visit", "site_visit")):
                sv_info = ""
                if sv_date:
                    sv_dt = _parse_dt(sv_date)
                    sv_info = f" on {sv_dt.strftime('%b %d, %Y')}" if sv_dt else f" on {sv_date}"
                site_visit_leads.append(f"{line}\n    🏠 Site visit{sv_info}")

            # Hot leads (by priority)
            if priority and priority.lower() == "hot" and "site visit" not in status_lower:
                hot_leads.append(line)

        # Sort
        new_leads.sort(key=lambda x: x[0])
        stale_leads.sort(key=lambda x: -x[0])

        # Build report
        report = []
        report.append(f"📊 *Daily Leads Report — {now.strftime('%b %d, %Y')}*")
        report.append(f"━━━━━━━━━━━━━━━━━━━━━━")
        report.append(f"Total leads: *{len(leads)}*")

        # Project breakdown
        proj_parts = [f"{p}: {c}" for p, c in sorted(by_project.items(), key=lambda x: -x[1])]
        report.append(f"By project: {' | '.join(proj_parts)}")
        report.append("")

        # New leads
        report.append(f"🆕 *New Leads — Last {days_new} Days ({len(new_leads)})*")
        if new_leads:
            for _, line in new_leads:
                report.append(line)
        else:
            report.append("  No new leads in this period.")
        report.append("")

        # Site visit section
        report.append(f"🏠 *Site Visit — Scheduled/Confirmed/Done ({len(site_visit_leads)})*")
        if site_visit_leads:
            for line in site_visit_leads:
                report.append(line)
        else:
            report.append("  No leads with site visit status.")
        report.append("")

        # Hot leads (non-site-visit)
        report.append(f"🔥 *Hot Leads — Needs Attention ({len(hot_leads)})*")
        if hot_leads:
            for line in hot_leads:
                report.append(line)
        else:
            report.append("  No additional hot leads.")
        report.append("")

        # Stale leads
        report.append(f"⏰ *Stale Leads — No Update in {days_stale}+ Days ({len(stale_leads)})*")
        if stale_leads:
            for _, line in stale_leads[:15]:
                report.append(line)
            if len(stale_leads) > 15:
                report.append(f"  ...and {len(stale_leads) - 15} more stale leads")
        else:
            report.append("  All leads are up to date! ✅")

        return "\n".join(report)


class LeadLookupTool(Tool):
    """Look up a specific lead by name, phone number, or other field."""

    name = "lead_lookup"
    description = (
        "Look up a specific lead from the Supabase database by name or phone number. "
        "Returns full details including notes, status, project, dates. "
        "Credentials are loaded from environment variables automatically. "
        "IMPORTANT: You MUST call this tool before answering any question about a specific lead. "
        "Never guess or fabricate lead information."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Name or phone number to search for"
            }
        },
        "required": ["query"]
    }

    async def execute(self, query: str, **kwargs: Any) -> str:
        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_key = os.environ.get("SUPABASE_KEY", "")
        if not supabase_url or not supabase_key:
            return "Error: SUPABASE_URL and SUPABASE_KEY environment variables are not set."

        query = query.strip()
        headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        }

        # Fetch all leads and filter locally (simpler than complex PostgREST OR queries)
        url = f"{supabase_url.rstrip('/')}/rest/v1/leads?select=*"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                leads = r.json()

                # Also fetch notes
                notes_url = f"{supabase_url.rstrip('/')}/rest/v1/lead_notes?select=*&order=created_at.desc&limit=200"
                lead_notes: dict[str, list[str]] = {}
                try:
                    rn = await client.get(notes_url, headers=headers)
                    if rn.status_code == 200:
                        for note in rn.json():
                            lid = note.get("lead_id")
                            if lid:
                                lead_notes.setdefault(lid, []).append(note.get("content", ""))
                except Exception:
                    pass
        except Exception as e:
            return f"Error fetching leads: {e}"

        # Search by name or phone (case-insensitive, partial match)
        q_lower = query.lower().replace("+91", "").replace(" ", "").replace("-", "")
        matches = []
        for lead in leads:
            name = (lead.get("lead_name") or "").lower()
            phone = (lead.get("mobile_number") or "").replace("+91", "").replace(" ", "").replace("-", "")
            if q_lower in name or q_lower in phone:
                matches.append(lead)

        if not matches:
            return f"No leads found matching '{query}'."

        now = datetime.now(timezone.utc)
        results = []
        for lead in matches:
            lead_id = lead.get("id", "")
            created = _parse_dt(lead.get("created_at"))
            updated = _parse_dt(lead.get("updated_at"))
            created_ago = f"{(now - created).days}d ago" if created else "unknown"
            updated_ago = f"{(now - updated).days}d ago" if updated else "unknown"

            lines = [
                f"*{lead.get('lead_name', 'Unknown')}*",
                f"  Phone: {lead.get('mobile_number', 'N/A')}",
                f"  Project: {lead.get('project', 'N/A')}",
                f"  Status: {lead.get('status', 'N/A')}",
                f"  Priority: {lead.get('priority', 'N/A')}",
                f"  Bucket: {lead.get('lead_bucket', 'N/A')}",
                f"  Source: {lead.get('lead_source', 'N/A')}",
                f"  Created: {created.strftime('%b %d, %Y') if created else 'N/A'} ({created_ago})",
                f"  Last Updated: {updated.strftime('%b %d, %Y') if updated else 'N/A'} ({updated_ago})",
            ]
            if lead.get("site_visit_date"):
                sv = _parse_dt(lead["site_visit_date"])
                lines.append(f"  Site Visit Date: {sv.strftime('%b %d, %Y') if sv else lead['site_visit_date']}")
            if lead.get("next_follow_up"):
                fu = _parse_dt(lead["next_follow_up"])
                lines.append(f"  Next Follow-up: {fu.strftime('%b %d, %Y') if fu else lead['next_follow_up']}")
            if lead.get("notes"):
                lines.append(f"  Notes: {lead['notes']}")
            # Show all recent notes from lead_notes table
            notes = lead_notes.get(lead_id, [])
            if notes:
                lines.append(f"  Recent Notes ({len(notes)}):")
                for n in notes[:5]:
                    lines.append(f"    • {n[:120]}")
            results.append("\n".join(lines))

        header = f"Found {len(matches)} lead(s) matching '{query}':\n"
        return header + "\n\n".join(results)


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
