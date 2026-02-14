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
  "tools": {
    "web": { "search": { "apiKey": "", "maxResults": 5 } },
    "exec": { "timeout": 60 },
    "restrictToWorkspace": false
  }
}
EOF

mkdir -p /root/.nanobot/workspace

exec nanobot gateway --port "${PORT:-10000}"
