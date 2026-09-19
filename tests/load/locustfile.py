import json
import uuid
from locust import HttpUser, task, between

AUTH_TOKEN = "local-dev-test-token-123"  # match your local .env WEBHOOK_AUTH_TOKEN

class WebhookUser(HttpUser):
    wait_time = between(0.1, 0.5)  # simulates service-desk-scale arrival pace, not zero-delay hammering

    @task
    def send_incident_webhook(self):
        payload = {
            "event_id": f"evt_{uuid.uuid4()}",  # unique every call — duplicates would short-circuit before Redis, skewing latency
            "sys_id": f"sys_{uuid.uuid4().hex[:32]}",
            "number": f"INC{uuid.uuid4().int % 1000000:06d}",
            "event_type": "incident.created",
            "contract_version": "v1",
        }
        self.client.post(
            "/webhook",
            json=payload,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            name="/webhook",
        )