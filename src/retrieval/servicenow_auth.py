"""OAuth password-grant token cache for ServiceNow Table API calls."""

import time
import httpx

from ..config import SERVICENOW


class ServiceNowAuthError(RuntimeError):
    pass


class ServiceNowOAuthClient:
    """Fetch and refresh access tokens before they expire."""

    def __init__(self):
        self._access_token = None
        self._expires_at = 0

    def _fetch_token(self):
        if not all([SERVICENOW.instance_url, SERVICENOW.oauth_client_id,
                    SERVICENOW.oauth_client_secret, SERVICENOW.oauth_username,
                    SERVICENOW.oauth_password]):
            raise ServiceNowAuthError(
                "Missing one or more required ServiceNow OAuth env vars: "
                "SERVICENOW_INSTANCE_URL, SERVICENOW_OAUTH_CLIENT_ID, "
                "SERVICENOW_OAUTH_CLIENT_SECRET, SERVICENOW_OAUTH_USERNAME, "
                "SERVICENOW_OAUTH_PASSWORD. Check .env."
            )

        url = f"{SERVICENOW.instance_url}/oauth_token.do"
        resp = httpx.post(
            url,
            data={
                "grant_type": "password",
                "client_id": SERVICENOW.oauth_client_id,
                "client_secret": SERVICENOW.oauth_client_secret,
                "username": SERVICENOW.oauth_username,
                "password": SERVICENOW.oauth_password,
                "scope": SERVICENOW.oauth_scope,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        # Avoid a token expiring during a request.
        self._expires_at = time.time() + int(data.get("expires_in", 1800)) - 60

    def get_token(self) -> str:
        if not self._access_token or time.time() >= self._expires_at:
            self._fetch_token()
        return self._access_token

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_token()}"}
