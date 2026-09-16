import time
import requests

from . import config
from .exceptions import ServiceNowAuthError

# how early to treat a token as stale, in seconds
_EXPIRY_MARGIN = 60


class TokenManager:
    # Fetches & caches and refreshes the OAuth access token

    def __init__(self):
        self._token = None
        self._expires_at = 0.0

    def _fetch(self):
        # Request a new access token with the password grant
        response = requests.post(
            config.TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": config.CLIENT_ID,
                "client_secret": config.CLIENT_SECRET,
                "username": config.USERNAME,
                "password": config.PASSWORD,
            },
            timeout=config.TIMEOUT,
        )

        if response.status_code != 200:
            raise ServiceNowAuthError(
                response.status_code,
                "OAuth token request failed",
            )

        payload = response.json()
        self._token = payload["access_token"]
        self._expires_at = time.time() + payload.get("expires_in", 1800)

    def _is_stale(self):
        return (
            self._token is None
            or time.time() >= self._expires_at - _EXPIRY_MARGIN
        )

    def get_token(self):
        # Return a valid token, fetching one if needed
        if self._is_stale():
            self._fetch()
        return self._token

    def invalidate(self):
        # Drop the cached token so the next call fetches a fresh one
        self._token = None
        self._expires_at = 0.0

    def headers(self):
        return {
            "Authorization": f"Bearer {self.get_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }