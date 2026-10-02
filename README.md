# Incident Commander

A working prototype built from the supplied Stitch exports. The application keeps
the original pink-and-white screens and uses the bundled dachshund illustration
in assistant avatars.

## Run on Replit

The project workflow runs:

```sh
uv run uvicorn main:app --host 0.0.0.0 --port 5000
```

Open the preview. FastAPI's interactive API documentation is available at
`/docs`; the basic readiness check is `/api/health`.

## Demo mode and safety

The initial incidents, telemetry, evidence, and charts are synthetic fixtures
clearly marked as demo data. This prototype does not perform health checks against
a website, does not connect to Prometheus or Alertmanager, and does not execute
production changes. The only simulated recovery changes records and metrics for a
synthetic incident in the local SQLite database.

Incident investigations and assistant answers use the evidence currently stored
for the selected incident. When an AI provider is not configured, the app returns
a deterministic, evidence-grounded demo response and labels it as a fallback.
Demo hypotheses are not presented as confirmed root causes.

The UI has no user login, and the webhook token is not an authorization system
for dashboard users. Do not connect real or confidential incident data until
user authentication, role-based access, deployment security, retention rules,
and the target monitoring environment have been reviewed.

## Data and API

The SQLite database is created automatically at `data/incidents.sqlite3`.
Set `INCIDENT_DB_PATH` to use a different location.

Available endpoints:

- `GET /api/health`
- `GET /api/dashboard/summary`
- `GET /api/services`
- `GET /api/incidents` and `GET /api/incidents/{incident_id}`
- `GET /api/incidents/{incident_id}/evidence`
- `GET /api/incidents/{incident_id}/recommendations`
- `GET /api/incidents/{incident_id}/metrics`
- `POST /api/incidents/{incident_id}/investigate`
- `POST /api/incidents/{incident_id}/approve`
- `POST /api/incidents/{incident_id}/simulate`
- `POST /api/incidents/{incident_id}/simulate-fix`
- `POST /api/incidents/{incident_id}/postmortem`
- `GET /api/incidents/{incident_id}/postmortem`
- `POST /api/chat`
- `POST /api/alerts/webhook`

### Alert webhook

Add `ALERT_WEBHOOK_SECRET` using Replit Secrets. The webhook is disabled when
the secret is absent. The sender must provide it in the `X-Webhook-Token` header.
Example JSON body:

```json
{
  "event_id": "monitor-event-123",
  "service": "Checkout API",
  "component": "payment-gateway",
  "title": "Elevated checkout error rate",
  "severity": "critical",
  "summary": "The monitoring provider reported an elevated error rate.",
  "measurements": {
    "error_rate_percent": 4.8,
    "p95_latency_ms": 2450
  }
}
```

The app groups matching active alerts and deduplicates repeated `event_id` values.
Webhook incidents are stored separately from synthetic fixtures and are labeled
as webhook-reported. Receiving an alert does not independently confirm its
underlying cause.

## Optional AI providers

Provider calls are server-side. Each role can use its own provider, model, and API
base URL. Supported adapters are OpenAI-compatible chat completions and Anthropic
Messages. Add the following names as Replit Secrets after selecting a compatible
provider and model:

- Investigation: `AI_AGENT_1_API_KEY`, `AI_AGENT_1_PROVIDER`,
  `AI_AGENT_1_MODEL`, `AI_AGENT_1_BASE_URL`
- Root cause: `AI_AGENT_2_API_KEY`, `AI_AGENT_2_PROVIDER`,
  `AI_AGENT_2_MODEL`, `AI_AGENT_2_BASE_URL`
- Chatbot: `CHATBOT_API_KEY`, `CHATBOT_PROVIDER`, `CHATBOT_MODEL`,
  `CHATBOT_BASE_URL`

For an OpenAI-compatible endpoint, use provider `openai_compatible` (or `openai`)
and a base URL ending at its API root, usually `/v1`. For Anthropic use provider
`anthropic` and its API root. Never put secret values in source files, logs, or
chat. If a provider is missing, that action uses a labeled, evidence-grounded demo
fallback. If a configured provider fails, that AI action shows an error while the
rest of the app remains available.

## Tests

Run the API tests with:

```sh
uv run python -m unittest discover -s tests -v
```

## Current integration limits

- Live URL health checks are not configured or implemented. This avoids accepting
  arbitrary target URLs that could expose internal network services.
- Prometheus and Alertmanager are not connected. Use the authenticated webhook
  adapter for an initial authorized monitoring source.
- Human approvals are recorded for the demo flow, but the dashboard itself has no
  user authentication. Do not expose real-data approval actions until access
  controls and audit ownership are in place.
- Generated incident reports are JSON; no PDF export service is connected.