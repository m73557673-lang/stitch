import os
import tempfile
import unittest
import uuid
from pathlib import Path


TEST_DB = os.path.join(tempfile.gettempdir(), f"vitality-tests-{uuid.uuid4().hex}.sqlite3")
os.environ["INCIDENT_DB_PATH"] = TEST_DB
os.environ["ALERT_WEBHOOK_SECRET"] = "test-only-webhook-secret"

from fastapi.testclient import TestClient

from main import app


class IncidentApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        for suffix in ("", "-shm", "-wal"):
            try:
                os.remove(TEST_DB + suffix)
            except FileNotFoundError:
                pass

    def test_health_and_seeded_demo_records(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["mode"], "demo")
        incidents = self.client.get("/api/incidents").json()["items"]
        self.assertGreaterEqual(len(incidents), 3)
        self.assertTrue(all(item["is_synthetic"] for item in incidents))

    def test_investigation_and_chat_are_explicitly_demo_grounded(self):
        investigation = self.client.post("/api/incidents/INC-8492/investigate")
        self.assertEqual(investigation.status_code, 200)
        self.assertEqual(investigation.json()["mode"], "demo_heuristic")
        self.assertTrue(investigation.json()["source_is_synthetic"])
        self.assertIn("hypothesis", investigation.json()["root_cause_hypothesis"].lower())

        answer = self.client.post(
            "/api/chat",
            json={"incident_id": "INC-8492", "message": "What might be causing this?"},
        )
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["mode"], "demo_fallback")
        self.assertIn("synthetic", answer.json()["answer"].lower())

    def test_fix_requires_approval_and_never_changes_production(self):
        before_approval = self.client.post("/api/incidents/INC-8492/simulate-fix")
        self.assertEqual(before_approval.status_code, 409)

        approved = self.client.post(
            "/api/incidents/INC-8492/approve",
            json={"actor": "test operator", "note": "Test approval only"},
        )
        self.assertEqual(approved.status_code, 200)

        result = self.client.post("/api/incidents/INC-8492/simulate-fix")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["production_changed"])
        incident = self.client.get("/api/incidents/INC-8492").json()["incident"]
        self.assertEqual(incident["status"], "resolved")
        self.assertTrue(incident["is_synthetic"])

    def test_webhook_authentication_and_event_deduplication(self):
        body = {
            "event_id": "unit-test-event-1",
            "service": "Unit Test Checkout",
            "component": "payment-worker",
            "title": "Unit test elevated error rate",
            "severity": "high",
            "summary": "Test fixture alert; not a real service.",
            "measurements": {"error_rate": 3.2},
        }
        denied = self.client.post("/api/alerts/webhook", json=body)
        self.assertEqual(denied.status_code, 401)

        headers = {"X-Webhook-Token": "test-only-webhook-secret"}
        first = self.client.post("/api/alerts/webhook", json=body, headers=headers)
        repeated = self.client.post("/api/alerts/webhook", json=body, headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["deduplicated"])
        self.assertTrue(repeated.json()["deduplicated"])
        incident = self.client.get(
            f"/api/incidents/{first.json()['incident_id']}"
        ).json()["incident"]
        self.assertFalse(incident["is_synthetic"])
        self.assertEqual(incident["source"], "monitoring_webhook")
        self.assertEqual(
            self.client.post(
                f"/api/incidents/{first.json()['incident_id']}/simulate"
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                f"/api/incidents/{first.json()['incident_id']}/simulate-fix"
            ).status_code,
            409,
        )

    def test_report_contains_saved_evidence(self):
        response = self.client.post("/api/incidents/INC-8492/postmortem")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["report"]["evidence"])
        self.assertIn("synthetic", response.json()["report"]["limitations"].lower())

    def test_stitch_screens_and_settings_route_use_the_local_mascot(self):
        for path in (
            "/",
            "/incidents",
            "/investigation",
            "/fix-recommendations",
            "/reports",
            "/assistant",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("/assets/vitality-dachshund.png", response.text)
                self.assertIn('/assets/app.js', response.text)
                rendered_copy = response.text.lower().replace(
                    "/assets/vitality-dachshund.png", ""
                )
                self.assertNotIn("vitality", rendered_copy)

        settings = self.client.get("/settings")
        self.assertEqual(settings.status_code, 200)
        self.assertIn('data-app-view="settings"', settings.text)
        app_script = (
            Path(__file__).resolve().parent.parent / "static" / "assets" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertNotIn("Vitality", app_script)
        self.assertNotIn("VITALITY", app_script)

    def test_exported_demo_copy_does_not_claim_live_fix_or_monitoring(self):
        incidents = self.client.get("/incidents").text
        assistant = self.client.get("/assistant").text
        report = self.client.get("/reports").text
        self.assertIn("Synthetic incident scenario", incidents)
        self.assertNotIn("Root cause isolated to pool configuration", incidents)
        self.assertNotIn("I am continuously monitoring the payment gateway", incidents)
        self.assertNotIn("100% safe to restore", assistant)
        self.assertIn("SYNTHETIC REPORT PREVIEW", report)
        self.assertNotIn("Hot-reload config adjustment initiates", report)
        self.assertNotIn("Full recovery was achieved", report)
        self.assertNotIn("Safe pool scale rebalance applied", report)


if __name__ == "__main__":
    unittest.main()