# Incident Commander

## Run

The Replit web workflow starts the FastAPI app on `0.0.0.0:5000` with:

```sh
uv run uvicorn main:app --host 0.0.0.0 --port 5000
```

The browser preview serves the Stitch-export UI. `/docs` provides the API
documentation and `/api/health` shows readiness without revealing credentials.

## Important safety boundaries

- The default incidents, evidence, service states, and metrics are synthetic.
- No target URL health checks or Prometheus/Alertmanager connection is enabled.
- A configured webhook requires `ALERT_WEBHOOK_SECRET` and the
  `X-Webhook-Token` header.
- A simulated fix can only modify the synthetic demo incident and requires a
  separately recorded human approval.
- Do not use real incident data in this prototype until authentication,
  authorization, retention, and deployment security are implemented.
- Store AI and webhook credentials in Replit Secrets, never in code or chat.

See `README.md` for endpoint payloads, provider configuration, and tests.