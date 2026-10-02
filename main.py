from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DB_PATH = Path(os.getenv("INCIDENT_DB_PATH", str(ROOT / "data" / "incidents.sqlite3")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("vitality_incident_commander")

AI_ROLES = {
    "investigation": "AI_AGENT_1",
    "root_cause": "AI_AGENT_2",
    "chatbot": "CHATBOT",
}
SCREEN_FILES = {
    "dashboard": "dashboard.html",
    "incidents": "incidents.html",
    "investigation": "investigation.html",
    "fix-recommendations": "fix.html",
    "reports": "reports.html",
    "assistant": "assistant.html",
}


class AlertPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str | None = Field(default=None, min_length=1, max_length=160)
    service: str = Field(min_length=1, max_length=120)
    component: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    severity: Literal["critical", "high", "medium", "low"]
    occurred_at: datetime | None = None
    summary: str = Field(default="", max_length=2000)
    measurements: dict[str, str | int | float | bool] = Field(
        default_factory=dict, max_length=24
    )


class ApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="dashboard_user", min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)


class ChatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    incident_id: str | None = Field(default=None, max_length=80)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def open_db():
    connection = sqlite3.connect(DB_PATH, timeout=8)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 8000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def add_audit(
    connection: sqlite3.Connection,
    incident_id: str,
    action: str,
    details: dict[str, Any],
    actor: str = "system",
) -> None:
    connection.execute(
        """INSERT INTO audit_events (incident_id, action, details_json, actor, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (incident_id, action, json.dumps(details), actor, utc_now()),
    )


def init_db() -> None:
    with open_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS services (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                state TEXT NOT NULL,
                is_synthetic INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                title TEXT NOT NULL,
                service TEXT NOT NULL,
                component TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                is_synthetic INTEGER NOT NULL,
                summary TEXT NOT NULL,
                affected_users INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_incidents_status_created
                ON incidents(status, created_at DESC);
            CREATE TABLE IF NOT EXISTS alerts (
                event_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(id),
                payload_json TEXT NOT NULL,
                received_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL REFERENCES incidents(id),
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                source TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL REFERENCES incidents(id),
                title TEXT NOT NULL,
                details TEXT NOT NULL,
                risk TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL REFERENCES incidents(id),
                name TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT NOT NULL,
                source TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS investigations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL REFERENCES incidents(id),
                mode TEXT NOT NULL,
                summary TEXT NOT NULL,
                root_cause TEXT NOT NULL,
                evidence_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT REFERENCES incidents(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL REFERENCES incidents(id),
                content_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT NOT NULL REFERENCES incidents(id),
                action TEXT NOT NULL,
                details_json TEXT NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        if connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]:
            return

        now = datetime.now(timezone.utc)
        stamp = lambda minutes: (now - timedelta(minutes=minutes)).isoformat(
            timespec="seconds"
        )
        services = [
            ("checkout-payment-gateway", "Checkout & Payment Gateway", "degraded"),
            ("inventory-search", "Inventory Search Cache", "warning"),
            ("email-notifications", "Email Notification Dispatch", "healthy"),
            ("database", "Checkout Database Pool", "degraded"),
        ]
        connection.executemany(
            "INSERT INTO services (id, name, state, is_synthetic, updated_at) VALUES (?, ?, ?, 1, ?)",
            [(service_id, name, state, stamp(2)) for service_id, name, state in services],
        )
        demo_incidents = [
            (
                "INC-8492",
                "demo-checkout-pool-saturation",
                "Online Checkout Slowdown & Payment Timeouts",
                "Checkout & Payment Gateway",
                "Checkout API",
                "critical",
                "active",
                "synthetic_demo",
                "Synthetic demo incident: checkout latency rose after a sample connection-pool limit was reduced. This is not a live monitor alert.",
                340,
                stamp(18),
            ),
            (
                "INC-8919",
                "demo-inventory-cache-latency",
                "Inventory Search Cache Latency",
                "Inventory Search Cache",
                "Redis cache cluster",
                "medium",
                "resolved",
                "synthetic_demo",
                "Synthetic demo incident: a sample cache latency warning recovered after a simulated cache refresh.",
                0,
                stamp(1450),
            ),
            (
                "INC-8914",
                "demo-email-backlog",
                "Email Notification Dispatch Backlog",
                "Email Notification Dispatch",
                "Notification worker",
                "low",
                "resolved",
                "synthetic_demo",
                "Synthetic demo incident: a sample notification backlog drained without customer data loss.",
                0,
                stamp(4200),
            ),
        ]
        connection.executemany(
            """INSERT INTO incidents
               (id, fingerprint, title, service, component, severity, status, source,
                is_synthetic, summary, affected_users, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
            [(*row[:10], row[10], row[10]) for row in demo_incidents],
        )

        connection.executemany(
            """INSERT INTO evidence
               (incident_id, kind, title, description, source, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    "INC-8492",
                    "configuration",
                    "Sample connection-pool setting changed",
                    "Synthetic release sample shows max_connections changed from 100 to 10.",
                    "synthetic demo fixture",
                    stamp(18),
                ),
                (
                    "INC-8492",
                    "metric",
                    "Checkout latency and error rate increased",
                    "Synthetic sample: p95 latency 2,450 ms (baseline 140 ms); error rate 4.8% (baseline 0.01%).",
                    "synthetic demo metrics",
                    stamp(17),
                ),
                (
                    "INC-8492",
                    "metric",
                    "Database pool reached its sample limit",
                    "Synthetic sample: 10 of 10 pool connections were active when checkout errors began.",
                    "synthetic demo metrics",
                    stamp(16),
                ),
                (
                    "INC-8919",
                    "recovery",
                    "Synthetic cache latency returned to normal",
                    "Sample p95 cache latency fell below the demo warning threshold.",
                    "synthetic demo fixture",
                    stamp(1400),
                ),
                (
                    "INC-8914",
                    "recovery",
                    "Synthetic notification backlog drained",
                    "Sample dispatch queue returned to its demo baseline.",
                    "synthetic demo fixture",
                    stamp(4100),
                ),
            ],
        )
        connection.execute(
            """INSERT INTO recommendations
               (incident_id, title, details, risk, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "INC-8492",
                "Restore the sample database connection pool to 100",
                "Revert the synthetic max_connections change from 10 to 100. The demo only applies this change to simulated metrics; it will not contact a real database.",
                "low — simulation only",
                "recommended",
                stamp(15),
            ),
        )
        initial_metrics = [
            ("p95_latency", 140, "ms", "synthetic baseline"),
            ("p95_latency", 2450, "ms", "synthetic incident sample"),
            ("error_rate", 0.01, "%", "synthetic baseline"),
            ("error_rate", 4.8, "%", "synthetic incident sample"),
            ("pool_active", 10, "connections", "synthetic incident sample"),
            ("pool_capacity", 10, "connections", "synthetic incident sample"),
        ]
        connection.executemany(
            """INSERT INTO metrics (incident_id, name, value, unit, source, recorded_at)
               VALUES ('INC-8492', ?, ?, ?, ?, ?)""",
            [(name, value, unit, source, stamp(18 if "baseline" not in source else 19))
             for name, value, unit, source in initial_metrics],
        )


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def require_incident(connection: sqlite3.Connection, incident_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return row


def ai_configuration(role: str) -> dict[str, str] | None:
    prefix = AI_ROLES[role]
    api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
    provider = os.getenv(f"{prefix}_PROVIDER", "").strip().lower()
    model = os.getenv(f"{prefix}_MODEL", "").strip()
    base_url = os.getenv(f"{prefix}_BASE_URL", "").strip()
    if not all((api_key, provider, model, base_url)):
        return None
    return {
        "api_key": api_key,
        "provider": provider,
        "model": model,
        "base_url": base_url.rstrip("/"),
    }


def ai_status() -> dict[str, bool]:
    return {role: ai_configuration(role) is not None for role in AI_ROLES}


class AIProviderError(Exception):
    pass


def call_ai(role: str, system_prompt: str, user_prompt: str) -> str | None:
    config = ai_configuration(role)
    if config is None:
        return None
    provider = config["provider"]
    headers = {"Content-Type": "application/json"}
    if provider in {"openai", "openai_compatible"}:
        endpoint = config["base_url"]
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        headers["Authorization"] = f"Bearer {config['api_key']}"
        body = {
            "model": config["model"],
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
    elif provider == "anthropic":
        endpoint = config["base_url"]
        if not endpoint.endswith("/messages"):
            endpoint += "/messages"
        headers["x-api-key"] = config["api_key"]
        headers["anthropic-version"] = "2023-06-01"
        body = {
            "model": config["model"],
            "max_tokens": 900,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
    else:
        raise AIProviderError(
            "Unsupported provider. Use openai, openai_compatible, or anthropic."
        )

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as error:
        logger.warning("AI provider returned HTTP %s for role %s", error.code, role)
        raise AIProviderError(
            f"The configured {role} provider returned HTTP {error.code}."
        ) from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        logger.warning("AI provider request failed for role %s", role)
        raise AIProviderError(
            f"The configured {role} provider could not be reached or returned invalid data."
        ) from None

    if provider == "anthropic":
        text = "\n".join(
            item.get("text", "")
            for item in result.get("content", [])
            if item.get("type") == "text"
        )
    else:
        text = (
            result.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
    if not text:
        raise AIProviderError(f"The configured {role} provider returned no answer.")
    return str(text)[:12000]


def incident_evidence(connection: sqlite3.Connection, incident_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM evidence WHERE incident_id = ? ORDER BY occurred_at DESC, id DESC",
        (incident_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def heuristic_investigation(incident: sqlite3.Row, evidence: list[dict[str, Any]]) -> tuple[str, str]:
    evidence_lines = "\n".join(
        f"- {item['title']}: {item['description']}" for item in evidence
    ) or "- No evidence has been recorded."
    summary = (
        "Local evidence summary — no configured investigation model was called. "
        f"This { 'synthetic demo' if incident['is_synthetic'] else 'webhook-reported' } "
        f"incident affects {incident['service']}. "
        f"Available evidence:\n{evidence_lines}"
    )
    if incident["is_synthetic"]:
        cause = (
            "Demo hypothesis, not a confirmed live root cause: the supplied synthetic "
            "evidence links the sample database pool limit (10 connections) with the "
            "checkout latency spike. Validate against an authorized live telemetry source "
            "before treating this as a real diagnosis."
        )
    else:
        cause = (
            "No AI root-cause model is configured. The webhook payload confirms an alert "
            "was received, but it does not independently confirm why the service failed."
        )
    return summary, cause


def fetch_incident(connection: sqlite3.Connection, incident_id: str) -> sqlite3.Row:
    return require_incident(connection, incident_id)


@contextmanager
def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Vitality AI Incident Commander",
    description="Evidence-grounded incident demo with approval-gated simulated recovery.",
    version="0.1.0",
)
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/health")
def health():
    with open_db() as connection:
        connection.execute("SELECT 1").fetchone()
    return {
        "ok": True,
        "mode": "demo",
        "database": "ready",
        "alert_webhook_configured": bool(
            os.getenv("ALERT_WEBHOOK_SECRET", "").strip()
        ),
        "ai": ai_status(),
        "live_health_checks_configured": False,
    }


@app.get("/api/services")
def services():
    with open_db() as connection:
        rows = connection.execute(
            "SELECT * FROM services ORDER BY name"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/api/dashboard/summary")
def dashboard_summary():
    with open_db() as connection:
        incidents = connection.execute(
            "SELECT * FROM incidents ORDER BY created_at DESC LIMIT 8"
        ).fetchall()
        services = connection.execute(
            "SELECT * FROM services ORDER BY name"
        ).fetchall()
        active = connection.execute(
            "SELECT COUNT(*) FROM incidents WHERE status NOT IN ('resolved', 'failed')"
        ).fetchone()[0]
        critical = connection.execute(
            "SELECT COUNT(*) FROM incidents WHERE severity = 'critical' AND status NOT IN ('resolved', 'failed')"
        ).fetchone()[0]
        resolved = connection.execute(
            "SELECT COUNT(*) FROM incidents WHERE status = 'resolved'"
        ).fetchone()[0]
    return {
        "mode": "demo",
        "active_incidents": active,
        "critical_incidents": critical,
        "resolved_incidents": resolved,
        "services": [dict(row) for row in services],
        "recent_incidents": [dict(row) for row in incidents],
        "ai": ai_status(),
    }


@app.get("/api/incidents")
def list_incidents(status: str | None = None, limit: int = 50):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100.")
    query = "SELECT * FROM incidents"
    values: list[Any] = []
    if status:
        query += " WHERE status = ?"
        values.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    values.append(limit)
    with open_db() as connection:
        rows = connection.execute(query, values).fetchall()
    return {"items": [dict(row) for row in rows], "mode": "demo"}


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    with open_db() as connection:
        incident = row_dict(require_incident(connection, incident_id))
        evidence = incident_evidence(connection, incident_id)
        recommendation = connection.execute(
            "SELECT * FROM recommendations WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
    return {
        "incident": incident,
        "evidence": evidence,
        "recommendation": row_dict(recommendation),
    }


@app.get("/api/incidents/{incident_id}/evidence")
def get_evidence(incident_id: str):
    with open_db() as connection:
        require_incident(connection, incident_id)
        return {"items": incident_evidence(connection, incident_id)}


@app.get("/api/incidents/{incident_id}/recommendations")
def get_recommendations(incident_id: str):
    with open_db() as connection:
        require_incident(connection, incident_id)
        rows = connection.execute(
            "SELECT * FROM recommendations WHERE incident_id = ? ORDER BY id DESC",
            (incident_id,),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/api/incidents/{incident_id}/metrics")
def get_metrics(incident_id: str):
    with open_db() as connection:
        require_incident(connection, incident_id)
        rows = connection.execute(
            "SELECT * FROM metrics WHERE incident_id = ? ORDER BY recorded_at, id",
            (incident_id,),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/incidents/{incident_id}/investigate")
def investigate(incident_id: str):
    with open_db() as connection:
        incident = fetch_incident(connection, incident_id)
        evidence = incident_evidence(connection, incident_id)
        compact = {
            "id": incident["id"],
            "title": incident["title"],
            "service": incident["service"],
            "severity": incident["severity"],
            "status": incident["status"],
            "source": incident["source"],
            "is_synthetic": bool(incident["is_synthetic"]),
            "summary": incident["summary"],
            "evidence": evidence,
        }

    context = json.dumps(compact, ensure_ascii=False)
    try:
        summary = call_ai(
            "investigation",
            "Summarize only the provided incident evidence. Distinguish observations from hypotheses. Never claim synthetic data is live. State what evidence is missing.",
            context,
        )
        root_cause = call_ai(
            "root_cause",
            "Analyze only the provided incident record and evidence. Return a likely cause as a hypothesis, cite supporting evidence by its title, and list validation steps. Do not recommend executing production changes.",
            context,
        )
    except AIProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None

    if summary is None:
        summary, heuristic_cause = heuristic_investigation(incident, evidence)
    else:
        _, heuristic_cause = heuristic_investigation(incident, evidence)
    if root_cause is None:
        root_cause = heuristic_cause
    mode = "configured_ai" if any((ai_status()["investigation"], ai_status()["root_cause"])) else "demo_heuristic"
    with open_db() as connection:
        connection.execute(
            """INSERT INTO investigations
               (incident_id, mode, summary, root_cause, evidence_ids_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                incident_id,
                mode,
                summary,
                root_cause,
                json.dumps([item["id"] for item in evidence]),
                utc_now(),
            ),
        )
        if incident["status"] == "active":
            connection.execute(
                "UPDATE incidents SET status = 'investigating', updated_at = ? WHERE id = ?",
                (utc_now(), incident_id),
            )
    return {
        "incident_id": incident_id,
        "mode": mode,
        "source_is_synthetic": bool(incident["is_synthetic"]),
        "summary": summary,
        "root_cause_hypothesis": root_cause,
        "evidence": evidence,
        "validation_required": True,
    }


@app.post("/api/alerts/webhook")
async def receive_alert(request: Request):
    secret = os.getenv("ALERT_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Alert ingestion is disabled until ALERT_WEBHOOK_SECRET is configured.",
        )
    supplied = request.headers.get("X-Webhook-Token", "")
    if not supplied or not hmac.compare_digest(supplied, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook authorization.")
    raw = await request.body()
    if len(raw) > 64_000:
        raise HTTPException(status_code=413, detail="Alert payload is too large.")
    try:
        payload = AlertPayload.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Alert payload failed validation.") from None

    event_id = payload.event_id or str(uuid.uuid4())
    occurred_at = payload.occurred_at
    event_time = (
        occurred_at.astimezone(timezone.utc).isoformat(timespec="seconds")
        if occurred_at and occurred_at.tzinfo
        else (occurred_at.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
              if occurred_at else utc_now())
    )
    fingerprint = hashlib.sha256(
        "|".join(
            (
                payload.service.casefold().strip(),
                payload.component.casefold().strip(),
                payload.title.casefold().strip(),
                payload.severity,
            )
        ).encode("utf-8")
    ).hexdigest()
    safe_payload = payload.model_dump(mode="json")
    safe_payload["event_id"] = event_id

    with open_db() as connection:
        duplicate_event = connection.execute(
            "SELECT incident_id FROM alerts WHERE event_id = ?", (event_id,)
        ).fetchone()
        if duplicate_event:
            return {
                "incident_id": duplicate_event["incident_id"],
                "deduplicated": True,
                "event_id": event_id,
            }
        existing = connection.execute(
            """SELECT * FROM incidents
               WHERE fingerprint = ? AND status NOT IN ('resolved', 'failed')
               ORDER BY created_at DESC LIMIT 1""",
            (fingerprint,),
        ).fetchone()
        summary = payload.summary or "Alert received from an authenticated monitoring webhook."
        if existing:
            incident_id = existing["id"]
            connection.execute(
                "UPDATE incidents SET updated_at = ?, summary = ? WHERE id = ?",
                (event_time, summary, incident_id),
            )
        else:
            incident_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
            connection.execute(
                """INSERT INTO incidents
                   (id, fingerprint, title, service, component, severity, status, source,
                    is_synthetic, summary, affected_users, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', 'monitoring_webhook', 0, ?, NULL, ?, ?)""",
                (
                    incident_id,
                    fingerprint,
                    payload.title.strip(),
                    payload.service.strip(),
                    payload.component.strip(),
                    payload.severity,
                    summary.strip(),
                    event_time,
                    event_time,
                ),
            )
        connection.execute(
            "INSERT INTO alerts (event_id, incident_id, payload_json, received_at) VALUES (?, ?, ?, ?)",
            (event_id, incident_id, json.dumps(safe_payload), utc_now()),
        )
        connection.execute(
            """INSERT INTO evidence (incident_id, kind, title, description, source, occurred_at)
               VALUES (?, 'alert', ?, ?, 'authenticated monitoring webhook', ?)""",
            (
                incident_id,
                payload.title.strip(),
                summary.strip(),
                event_time,
            ),
        )
        service_id = re.sub(r"[^a-z0-9]+", "-", payload.service.lower()).strip("-")[:100]
        connection.execute(
            """INSERT INTO services (id, name, state, is_synthetic, updated_at)
               VALUES (?, ?, ?, 0, ?)
               ON CONFLICT(id) DO UPDATE SET state = excluded.state,
                   is_synthetic = 0, updated_at = excluded.updated_at""",
            (
                service_id or incident_id.lower(),
                payload.service.strip(),
                "critical" if payload.severity in {"critical", "high"} else "warning",
                event_time,
            ),
        )
        for key, value in payload.measurements.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            connection.execute(
                """INSERT INTO metrics
                   (incident_id, name, value, unit, source, recorded_at)
                   VALUES (?, ?, ?, '', 'authenticated monitoring webhook', ?)""",
                (incident_id, key[:100], float(value), event_time),
            )
        add_audit(
            connection,
            incident_id,
            "alert_received",
            {"event_id": event_id, "source": "authenticated monitoring webhook"},
        )
    return {"incident_id": incident_id, "deduplicated": False, "event_id": event_id}


@app.post("/api/incidents/{incident_id}/approve")
def approve_recommendation(incident_id: str, payload: ApprovalPayload):
    with open_db() as connection:
        incident = require_incident(connection, incident_id)
        recommendation = connection.execute(
            "SELECT * FROM recommendations WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
        if recommendation is None:
            raise HTTPException(status_code=409, detail="There is no recommendation to approve.")
        if incident["status"] in {"resolved", "failed"}:
            raise HTTPException(status_code=409, detail="This incident is already closed.")
        connection.execute(
            "UPDATE recommendations SET status = 'approved' WHERE id = ?",
            (recommendation["id"],),
        )
        connection.execute(
            "UPDATE incidents SET status = 'approved', updated_at = ? WHERE id = ?",
            (utc_now(), incident_id),
        )
        add_audit(
            connection,
            incident_id,
            "human_approval_recorded",
            {"recommendation_id": recommendation["id"], "note": payload.note},
            payload.actor,
        )
    return {
        "incident_id": incident_id,
        "status": "approved",
        "message": "Approval recorded. This does not execute any production action.",
    }


@app.post("/api/incidents/{incident_id}/simulate")
def preview_simulation(incident_id: str):
    with open_db() as connection:
        incident = require_incident(connection, incident_id)
        if not incident["is_synthetic"]:
            raise HTTPException(
                status_code=409,
                detail="Simulation previews are only available for synthetic demo incidents.",
            )
        add_audit(
            connection,
            incident_id,
            "safe_simulation_preview",
            {"changed_production": False},
        )
    return {
        "incident_id": incident_id,
        "mode": "synthetic_sandbox",
        "production_changed": False,
        "before": {"p95_latency_ms": 2450, "error_rate_percent": 4.8},
        "after": {"p95_latency_ms": 155, "error_rate_percent": 0.02},
        "message": "Synthetic preview only. No service, deployment, or database was changed.",
    }


@app.post("/api/incidents/{incident_id}/simulate-fix")
def simulate_fix(incident_id: str):
    with open_db() as connection:
        incident = require_incident(connection, incident_id)
        if not incident["is_synthetic"]:
            raise HTTPException(
                status_code=409,
                detail="A simulated fix cannot be run against an incident from a real webhook.",
            )
        recommendation = connection.execute(
            "SELECT * FROM recommendations WHERE incident_id = ? ORDER BY id DESC LIMIT 1",
            (incident_id,),
        ).fetchone()
        if recommendation is None or recommendation["status"] != "approved":
            raise HTTPException(
                status_code=409,
                detail="A human must approve the recommendation before the simulated fix.",
            )
        now = utc_now()
        connection.execute(
            "UPDATE incidents SET status = 'resolved', updated_at = ? WHERE id = ?",
            (now, incident_id),
        )
        connection.execute(
            "UPDATE recommendations SET status = 'simulated' WHERE id = ?",
            (recommendation["id"],),
        )
        connection.executemany(
            """INSERT INTO metrics (incident_id, name, value, unit, source, recorded_at)
               VALUES (?, ?, ?, ?, 'synthetic simulated recovery', ?)""",
            [
                (incident_id, "p95_latency", 155, "ms", now),
                (incident_id, "error_rate", 0.02, "%", now),
                (incident_id, "pool_capacity", 100, "connections", now),
            ],
        )
        connection.execute(
            "UPDATE services SET state = 'healthy', updated_at = ? WHERE name = ?",
            (now, incident["service"]),
        )
        connection.execute(
            "UPDATE services SET state = 'healthy', updated_at = ? WHERE name = 'Checkout Database Pool'",
            (now,),
        )
        add_audit(
            connection,
            incident_id,
            "synthetic_fix_simulated",
            {"production_changed": False, "result": "recovered"},
        )
    return {
        "incident_id": incident_id,
        "status": "resolved",
        "mode": "synthetic_sandbox",
        "production_changed": False,
        "metrics": {"p95_latency_ms": 155, "error_rate_percent": 0.02},
        "message": "The sample incident was marked recovered in the local demo database only.",
    }


@app.post("/api/incidents/{incident_id}/postmortem")
def create_postmortem(incident_id: str):
    with open_db() as connection:
        incident = require_incident(connection, incident_id)
        evidence = incident_evidence(connection, incident_id)
        report = {
            "incident": {
                key: incident[key]
                for key in (
                    "id", "title", "service", "component", "severity", "status",
                    "source", "is_synthetic", "summary", "created_at", "updated_at",
                )
            },
            "evidence": evidence,
            "timeline": [
                {
                    "time": item["occurred_at"],
                    "event": item["title"],
                    "source": item["source"],
                }
                for item in evidence
            ],
            "limitations": (
                "This report contains only recorded evidence. The incident is synthetic demo data."
                if incident["is_synthetic"]
                else "This report contains only recorded webhook evidence; no live health check was performed."
            ),
        }
        created_at = utc_now()
        cursor = connection.execute(
            "INSERT INTO reports (incident_id, content_json, created_at) VALUES (?, ?, ?)",
            (incident_id, json.dumps(report), created_at),
        )
        add_audit(connection, incident_id, "postmortem_generated", {"report_id": cursor.lastrowid})
    return {"id": cursor.lastrowid, "created_at": created_at, "report": report}


@app.get("/api/incidents/{incident_id}/postmortem")
def get_postmortems(incident_id: str):
    with open_db() as connection:
        require_incident(connection, incident_id)
        rows = connection.execute(
            "SELECT * FROM reports WHERE incident_id = ? ORDER BY created_at DESC",
            (incident_id,),
        ).fetchall()
    return {
        "items": [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "report": json.loads(row["content_json"]),
            }
            for row in rows
        ]
    }


@app.post("/api/chat")
def chat(payload: ChatPayload):
    with open_db() as connection:
        if payload.incident_id:
            incident = require_incident(connection, payload.incident_id)
        else:
            incident = connection.execute(
                "SELECT * FROM incidents WHERE status NOT IN ('resolved', 'failed') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        evidence = incident_evidence(connection, incident["id"]) if incident else []
        history = (
            connection.execute(
                """SELECT role, content FROM chat_messages
                   WHERE incident_id = ? ORDER BY id DESC LIMIT 8""",
                (incident["id"],),
            ).fetchall()
            if incident
            else []
        )

    if incident is None:
        answer = (
            "I don’t have an active incident record to refer to yet. This demo contains sample "
            "incidents, and it is not checking a live website. Connect an authenticated monitoring "
            "webhook to add real alerts."
        )
        mode = "demo_fallback"
        selected_id = None
    else:
        snapshot = {
            "incident": {
                key: incident[key]
                for key in (
                    "id", "title", "service", "component", "severity", "status",
                    "source", "is_synthetic", "summary",
                )
            },
            "evidence": evidence,
            "recent_conversation": [dict(row) for row in reversed(history)],
            "user_question": payload.message.strip(),
        }
        try:
            answer = call_ai(
                "chatbot",
                "Answer in clear beginner-friendly English using only the supplied incident record and evidence. Explicitly distinguish synthetic/demo data and hypotheses from confirmed webhook observations. Say when the evidence does not answer the question. Never execute or claim to execute a fix.",
                json.dumps(snapshot, ensure_ascii=False),
            )
        except AIProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from None
        mode = "configured_ai" if answer is not None else "demo_fallback"
        selected_id = incident["id"]
        if answer is None:
            if incident["is_synthetic"]:
                answer = (
                    f"This is the synthetic demo incident {incident['id']}: {incident['title']} "
                    f"for {incident['service']}. The sample record is marked {incident['severity']} "
                    f"and {incident['status']}. Its stored demo evidence includes: "
                    + ("; ".join(item["title"] for item in evidence) or "no recorded evidence")
                    + ". The sample suggests a possible connection-pool limit issue, but that is "
                    "only a demo hypothesis—not a confirmed live root cause. No fix has been run. "
                    "The application is not currently monitoring a live website."
                )
            else:
                answer = (
                    f"The monitoring webhook reported {incident['title']} for {incident['service']} "
                    f"({incident['severity']}). Recorded evidence: "
                    + ("; ".join(item["title"] for item in evidence) or "none yet")
                    + ". This confirms the alert was received, but does not by itself confirm a root cause. "
                    "No action has been executed."
                )

    if selected_id:
        with open_db() as connection:
            connection.executemany(
                """INSERT INTO chat_messages (incident_id, role, content, mode, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (selected_id, "user", payload.message.strip(), mode, utc_now()),
                    (selected_id, "assistant", answer, mode, utc_now()),
                ],
            )
    return {
        "incident_id": selected_id,
        "mode": mode,
        "answer": answer,
        "sources": [item["title"] for item in evidence],
    }


def render_screen(view: str, data_view: str | None = None) -> HTMLResponse:
    screen_file = SCREEN_FILES[view]
    html = (STATIC_DIR / "stitch" / screen_file).read_text(encoding="utf-8")

    def use_local_dog(match: re.Match[str]) -> str:
        tag = match.group(0)
        alt = re.search(r'\balt="([^"]*)"', tag, re.IGNORECASE)
        if not alt or not re.search(r"dog|mascot|logo|profile", alt.group(1), re.I):
            return tag
        if re.search(r'\bsrc="[^"]*"', tag, re.IGNORECASE):
            return re.sub(
                r'\bsrc="[^"]*"',
                'src="/assets/vitality-dachshund.png"',
                tag,
                count=1,
                flags=re.IGNORECASE,
            )
        return tag

    html = re.sub(r"<img\b[^>]*>", use_local_dog, html, flags=re.IGNORECASE)
    for original, replacement in (
        ("Vitality Core Systems Monitored", "Synthetic Samples · No Live Monitor"),
        ("All Core Systems Monitored", "Synthetic Demo Incident Data"),
        ("Live Safe Simulation Mode", "Safe Simulation · Synthetic Data"),
        ("All Core Systems Monitored • Live Safe Simulation Mode", "Synthetic demo data • Safe simulation only"),
        ("LIVE SURVEILLANCE ACTIVE", "SYNTHETIC DEMO DATA"),
        ("All systems are being sniffed 24/7!", "This screen shows sample metrics; no live target is being monitored."),
        ("Real-time AI monitoring and plain-language incident diagnostics.", "Sample incident data and evidence-grounded diagnostics."),
        ("Stanley &amp; Vitality AI are monitoring checkout pipelines with automated protection.", "Synthetic incident scenario for illustration only; no checkout pipeline is connected."),
        ("Root cause isolated to pool configuration. Human verification needed.", "Synthetic demo hypothesis: pool configuration may be related. Validate against authorized evidence; no root cause is confirmed."),
        ("AI pinpointed the database connection pool cap rollback and synthesized a", "Synthetic demo evidence suggests a possible database connection-pool issue. No live fix was created; review the simulation proposal below."),
        ("zero-downtime hotfix", "non-production simulation proposal"),
        ("awaiting your 1-click authorization.", "awaiting review for a synthetic-only simulation."),
        ("The AI has verified the patch in isolation with zero dependencies broken.", "No patch was tested against a live system; this prototype has no connected sandbox."),
        ("3 incident responders monitoring realtime telemetry", "Synthetic demo responder activity; no live telemetry is connected."),
        ("Stanley's Real-time Check", "Synthetic Demo Evidence Check"),
        ("Hi Sarah! I am continuously monitoring the payment gateway. What would you like to clarify?", "Hi Sarah! I can answer questions about this synthetic incident example."),
        ("Last AI diagnostic sync:", "Last sample timestamp:"),
        ("AI Confidence Score: 98%", "Demo hypothesis · validation required"),
        ("High Certainty Root Cause", "Synthetic Root-Cause Hypothesis"),
        ("Vitality AI Detective sniffed out the root cause!", "Synthetic evidence suggests a possible cause; live validation is required."),
        ("Autonomous Diagnosis", "Evidence-Based Demo Review"),
        ("What the AI Found (Plain English Root Cause)", "Evidence Review (Synthetic Demo Hypothesis)"),
        ("Safe Simulation Verified", "Synthetic Simulation Preview · Not Run"),
        ("98% Certain", "Demo hypothesis · needs validation"),
        ("Supporting Evidence Verified (3 Independent Sources):", "Synthetic Demo Evidence (3 Fixture Records):"),
        ("config/production/database.yaml", "synthetic demo configuration record"),
        ("— accidentally merged into the live production branch instead of the test staging environment.", "— this release description is fabricated for the demo."),
        ("How it was successfully fixed:", "Example recovery simulation outcome:"),
        ("Restored connection pool limit to 120. Zero downtime required.", "No service was changed; this screen shows synthetic example metrics only."),
        ("Simulation Pass: 100% Validated", "Synthetic sample replay · no service validation performed"),
        ("99.99% for 72h+", "No live success-rate data"),
        ("99.4% Match", "Synthetic demo estimate"),
        ("99.4%", "Synthetic estimate"),
        ("99% Historical Success Rate", "Unvalidated synthetic estimate · not historical performance"),
        ("Real-time SRE Copilot", "Synthetic Demo SRE Assistant"),
        ("Live Prod", "Demo Snapshot"),
        ("Simulation successfully executed!", "A synthetic recovery preview is available; no external action was executed."),
        ("Autonomous Copilot Online", "Assistant available · synthetic incident data"),
        ("Yes! It is 100% safe to restore.", "Production restoration is not enabled; only a synthetic simulation is available."),
        ("Powered by Vitality SRE LLM v4.2 • Autonomous Safety Guard rails active", "Optional AI provider · no production controls configured"),
        ("Zero Unapproved Production Writes", "No production writes are available in this demo"),
        ("Every remediation proposal is tested in sandbox environments first and requires two-factor signoff before applying to production.", "No sandbox cluster or production connection is configured. Simulated actions update only synthetic sample records."),
        ("Verified Hash #88b1", "Synthetic demo audit reference"),
        ("Autonomous canary sandbox ready", "Synthetic simulation preview only"),
        ("100% SLA Target", "Synthetic demo SLA example"),
        ("Live telemetry stream with automated AI root cause hypotheses", "Synthetic sample telemetry and demo root-cause hypotheses"),
        ("Vitality AI Root Cause Finding (94.2% Confidence)", "Synthetic demo hypothesis · validation required"),
        ("Safe Simulation verified in Canary staging", "Synthetic simulation preview available; no canary is connected"),
        ("RESOLVED (Successfully Recovered)", "SYNTHETIC REPORT PREVIEW · NO LIVE RECOVERY"),
        ("Audited &amp; Verified by Vitality AI Detective", "Synthetic demo report · not independently audited"),
        ("Autonomous verification pass executed at 14:32 UTC", "Synthetic report fixture · no live verification was run"),
        ("Autonomous AI Commander", "Synthetic Demo Assistant"),
        ("detected the anomaly in 2 minutes, pinpointed root cause in 3 minutes, and prepared a safe fix reviewed and approved by human operator", "describes a fabricated demo timeline; no detection, approval, or fix occurred"),
        ("Full recovery was achieved at 14:27 UTC with zero data loss.", "Synthetic example only; no live recovery or transaction data was observed."),
        ("Safe pool scale rebalance applied", "Synthetic recovery preview · not applied"),
        ("Zero customer orders lost", "No real customer data is present in this demo"),
        ("Fully Restored", "Synthetic sample baseline"),
        ("Autonomous Investigation &amp; Audit Trail", "Synthetic Investigation Example"),
        ("helm/checkout-service/values.production.yaml", "synthetic release fixture · not a production file"),
        ("100% successful payments", "Synthetic example payment metric"),
        ("Per Vitality Safety Directive §4, autonomous remediation in production finance tier requires explicit human authorization.", "No autonomous production actions are available in this demo."),
        ("Step 4: Canary Rollout &amp; Hot-Reload Verified", "Step 4: Synthetic canary preview · not performed"),
        ("Autonomous Detection", "Synthetic Detection Example"),
        ("Autonomous Incident Commander identifies abnormal connection pool starvation. Latency peaks at 2,450ms. AI triggers incident INC-8492 and pages Sarah Jenkins.", "Synthetic example only: a sample connection-pool limit coincides with elevated latency. No live detection, paging, or remediation occurs."),
        ("14:21 UTC — Human Gate Verified &amp; Remediation Dispatched", "Synthetic simulation preview · no action dispatched"),
        ("Sarah Jenkins reviews automated sandbox simulation results and taps 1-Click Approve. Hot-reload config adjustment initiates across 16 micro-service pods.", "Synthetic example only: no operator approval was collected and no change was dispatched to any service."),
        ("14:27 UTC — Complete Recovery Confirmed", "Synthetic example timeline · not a real recovery"),
        ("Latency drops back to baseline 155ms. Zero payment transactions dropped. AI Detective completes root cause documentation pass and closes INC-8492.", "Synthetic simulated metrics return to the example baseline. No real payment data was accessed or changed."),
        ("Resolved (Successfully Recovered)", "Synthetic report preview"),
        ("Download PDF Report", "Download JSON Report"),
    ):
        html = html.replace(original, replacement)
    html = re.sub(
        r"Full recovery was achieved at\s*<strong[^>]*>14:27 UTC</strong>\s*with zero data loss\.",
        "Synthetic example only; no live recovery or transaction data was observed.",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r"<body\b",
        f'<body data-app-view="{data_view or view}"',
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    html = html.replace(
        "</head>",
        '<link rel="stylesheet" href="/assets/app.css"></head>',
        1,
    )
    html = html.replace(
        "</body>",
        '<script src="/assets/app.js" defer></script></body>',
        1,
    )
    return HTMLResponse(html)


@app.get("/", response_class=HTMLResponse)
def home():
    return render_screen("dashboard")


@app.get("/favicon.ico")
def favicon():
    return HTMLResponse(content="", status_code=204)


@app.get("/{view}", response_class=HTMLResponse)
def page(view: str):
    aliases = {
        "dashboard": "dashboard",
        "dashboard-and-system-health": "dashboard",
        "incidents": "incidents",
        "active-incidents": "incidents",
        "incident-details": "incidents",
        "ai-investigation": "investigation",
        "investigation": "investigation",
        "evidence-logs": "investigation",
        "evidence-and-logs": "investigation",
        "evidence": "investigation",
        "fix-recommendations": "fix-recommendations",
        "safe-simulation": "fix-recommendations",
        "simulation": "fix-recommendations",
        "incident-reports": "reports",
        "incident-report": "reports",
        "reports": "reports",
        "ai-chatbot": "assistant",
        "chatbot": "assistant",
        "assistant": "assistant",
        "settings": "dashboard",
    }
    selected = aliases.get(view)
    if selected is None:
        raise HTTPException(status_code=404, detail="Page not found.")
    return render_screen(selected, data_view="settings" if view == "settings" else selected)