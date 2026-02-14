#!/bin/sh
# Generate config from env vars at runtime (keeps secrets out of the image)
cat > /root/.nanobot/config.json <<EOF
{
  "agents": {
    "defaults": {
      "workspace": "/root/.nanobot/workspace",
      "model": "groq/meta-llama/llama-4-scout-17b-16e-instruct",
      "maxTokens": 8192,
      "temperature": 0.7,
      "maxToolIterations": 20,
      "memoryWindow": 50
    }
  },
  "providers": {
    "groq": {
      "apiKey": "${GROQ_API_KEY}",
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

mkdir -p /root/.nanobot/workspace

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
