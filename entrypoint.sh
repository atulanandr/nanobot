#!/bin/sh
# Generate config from env vars at runtime (keeps secrets out of the image)
cat > /root/.nanobot/config.json <<EOF
{
  "agents": {
    "defaults": {
      "workspace": "/root/.nanobot/workspace",
      "model": "arcee-ai/trinity-large-preview:free",
      "maxTokens": 8192,
      "temperature": 0.7,
      "maxToolIterations": 20,
      "memoryWindow": 50
    }
  },
  "providers": {
    "openrouter": {
      "apiKey": "${OPENROUTER_API_KEY}",
      "apiBase": null,
      "extraHeaders": null
    }
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": ${PORT:-10000}
  },
  "channels": {
    "slack": {
      "enabled": true,
      "mode": "socket",
      "botToken": "${SLACK_BOT_TOKEN}",
      "appToken": "${SLACK_APP_TOKEN}",
      "dm": {
        "enabled": true,
        "policy": "open",
        "allowFrom": []
      }
    }
  },
  "tools": {
    "web": { "search": { "apiKey": "", "maxResults": 5 } },
    "exec": { "timeout": 60 },
    "restrictToWorkspace": false
  }
}
EOF

mkdir -p /root/.nanobot/workspace/memory

# Export Supabase credentials for the leads_report tool
export SUPABASE_URL="${SUPABASE_URL}"
export SUPABASE_KEY="${SUPABASE_KEY}"

# Seed MEMORY.md with persistent knowledge (always overwrite on deploy to pick up changes)
cat > /root/.nanobot/workspace/memory/MEMORY.md <<'MEMEOF'
# Nanobot Persistent Memory

## ⚠️ GROUNDING RULES (MANDATORY)
- NEVER make up lead data. Every fact about a lead MUST come from a tool call (lead_lookup or leads_report).
- When asked about a specific lead or phone number → use `lead_lookup` tool FIRST, then respond based on its output.
- When asked for a report → use `leads_report` tool, then present the output. Do not add fabricated analysis.
- If a tool returns no data or an error, say so honestly. NEVER fill gaps with invented information.
- Do not guess project names, phone numbers, statuses, notes, dates, or priorities.
- Do not say "9 days ago" unless the tool output confirms that number.

## Supabase Database
- Project: TownPark Real Estate CRM
- Tables: `leads`, `lead_notes`
- Credentials are in SUPABASE_URL and SUPABASE_KEY env vars (auto-loaded by leads_report tool)
- RLS is enabled with public SELECT policy on leads table

## Leads Report Format
When asked for a leads report, ALWAYS use the `leads_report` tool (no parameters needed).
The tool automatically:
- Reads Supabase credentials from environment variables
- Fetches all leads and lead_notes
- Generates a formatted report with these sections:
  1. 📊 Header with total count and project breakdown
  2. 🆕 New Leads — added in last 10 days
  3. 🏠 Site Visit — Scheduled/Confirmed/Done
  4. 🔥 Hot Leads — priority=hot, needs attention
  5. ⏰ Stale Leads — no update in 7+ days (excludes Lost/Junk)

DO NOT pass supabase_url or supabase_key to the tool — they are read from env vars.

## Lead Lookup
When asked about a specific lead by name or phone number, use the `lead_lookup` tool.
- Pass the name or phone number as the `query` parameter
- Returns full details: status, project, notes, dates, priority
- NEVER fabricate or guess lead data — always use this tool to get real data

## Cron Jobs
- Daily leads report: cron expression `0 9 * * *` (9:00 AM UTC daily)
- Cron job message should be: "Generate the daily leads report using the leads_report tool and share it."
- Deliver to Slack channel: C0AELBZHDNK
- Do NOT create duplicate cron jobs. Check existing jobs first with `cron list`.

## User Preferences
- User: Atul
- Channel: Slack
- Slack report channel: C0AELBZHDNK
- Prefers concise, actionable reports
- Wants to see notes and follow-up dates per lead
- Does not want Lost/Junk leads in stale section
MEMEOF
echo "MEMORY.md seeded."

# Fetch leads index from Supabase and append to MEMORY.md
if [ -n "$SUPABASE_URL" ] && [ -n "$SUPABASE_KEY" ]; then
python3 <<'PYEOF'
import json, os, sys
from urllib.request import Request, urlopen
from datetime import datetime, timezone, timedelta

url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/leads?select=lead_name,mobile_number,project,status,priority,lead_bucket,updated_at,created_at&order=created_at.desc"
key = os.environ["SUPABASE_KEY"]
req = Request(url, headers={"apikey": key, "Authorization": f"Bearer {key}"})
try:
    resp = urlopen(req, timeout=15)
    leads = json.loads(resp.read())
except Exception as e:
    print(f"Failed to fetch leads index: {e}", file=sys.stderr)
    sys.exit(0)

if not leads:
    sys.exit(0)

now = datetime.now(timezone.utc)
lines = ["\n## Leads Index (auto-refreshed on deploy)",
         f"Total: {len(leads)} leads | Updated: {now.strftime('%b %d, %Y %H:%M UTC')}",
         "Use this index for quick reference. For full details, use lead_lookup tool.",
         "",
         "| Name | Phone | Project | Status | Priority | Updated |",
         "|------|-------|---------|--------|----------|---------|"]

for l in leads:
    name = l.get("lead_name", "?")
    phone = l.get("mobile_number", "?")
    proj = (l.get("project") or "?")[:20]
    status = (l.get("status") or "?")[:20]
    priority = l.get("priority") or "-"
    upd = l.get("updated_at", "")
    if upd:
        try:
            dt = datetime.fromisoformat(upd.replace("Z", "+00:00"))
            days = (now - dt).days
            upd = f"{days}d ago"
        except: pass
    lines.append(f"| {name} | {phone} | {proj} | {status} | {priority} | {upd} |")

with open("/root/.nanobot/workspace/memory/MEMORY.md", "a") as f:
    f.write("\n".join(lines) + "\n")
print(f"Leads index appended: {len(leads)} leads")
PYEOF
fi

# Start a minimal HTTP health check server in the background
# (Render requires an open port to detect the service)
python3 -c "
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, threading

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'status': 'ok', 'service': 'nanobot'}).encode())
    def log_message(self, *args): pass

server = HTTPServer(('0.0.0.0', ${PORT:-10000}), Handler)
server.serve_forever()
" &

exec nanobot gateway
