"""
Minimal OAuth token client for ServiceNow's Table API.

Uses the OAuth password grant against the integration identity created in
S1.2 (Mostafa). No admin credentials are used or accepted here -- this
client only works with an OAuth client id/secret + service account
username/password, per FR-06.
"""

import time
import httpx

from ..config import SERVICENOW


class ServiceNowAuthError(RuntimeError):
    pass


class ServiceNowOAuthClient:
    """
    Fetches and caches an OAuth access token, refreshing automatically
    when it's close to expiry. A long-running publish job (many articles)
    should never fail mid-run just because the token aged out.
    """

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
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        # refresh 60s before actual expiry to avoid edge-of-window failures
        self._expires_at = time.time() + int(data.get("expires_in", 1800)) - 60

    def get_token(self) -> str:
        if not self._access_token or time.time() >= self._expires_at:
            self._fetch_token()
        return self._access_token

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_token()}"}