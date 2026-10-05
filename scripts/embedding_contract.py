#!/usr/bin/env python3
"""Nightly embedding contract check.

Standalone script: stdlib + httpx only. No src/ or tests/ imports.

Required env vars:
  LITELLM_BASE_URL, LITELLM_API_KEY, LITELLM_EMBEDDING_MODEL, QDRANT_DENSE_DIMENSION
"""

import os
import re
import sys
import json
import time
import httpx


def _load_env():
    """Load .env if python-dotenv is available (never override existing env)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"::error::Missing required environment variable: {name}")
        sys.exit(1)
    return val


def _normalize_model(name: str) -> str:
    """Normalize model name: lowercase, last path segment after '/'."""
    return name.strip().lower().rsplit("/", 1)[-1]


def _parse_retry_delay(body_text: str, headers: httpx.Headers) -> int | None:
    """Parse retry delay from body text or Retry-After header."""
    # Check body for "retryDelay": "34s" pattern
    m = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', body_text)
    if m:
        return int(m.group(1))
    # Check Retry-After header (seconds)
    retry_after = headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return int(retry_after)
    return None


def classify(status_code: int, body_text: str) -> str:
    """Classify a failed embedding response.

    Returns: "per_minute" | "per_day" | "auth" | "not_found" | "unclassified_429" | "other"
    """
    if status_code in (401, 403):
        return "auth"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        body_lower = body_text.lower()
        # Check per_day FIRST on purpose
        if re.search(r"per[\s_-]*day", body_lower):
            return "per_day"
        if re.search(r"per[\s_-]*minute", body_lower) or "retrydelay" in body_lower:
            return "per_minute"
        return "unclassified_429"
    # Any other non-2xx
    return "other"


def run_check() -> None:
    _load_env()

    base_url = _require_env("LITELLM_BASE_URL")
    api_key = _require_env("LITELLM_API_KEY")
    model = _require_env("LITELLM_EMBEDDING_MODEL")
    expected_dim = int(_require_env("QDRANT_DENSE_DIMENSION"))

    url = base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": model, "input": "embedding contract check"}

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, json=payload, headers=headers)
        except Exception as exc:
            print(f"::error::Network error: {exc}")
            sys.exit(1)

        body_text = response.text

        if response.status_code == 200:
            # Validate response shape
            try:
                data = response.json()
            except Exception as exc:
                print(f"::error::Invalid JSON response: {exc}")
                sys.exit(1)

            try:
                vector = data["data"][0]["embedding"]
            except (KeyError, IndexError, TypeError) as exc:
                print(f"::error::Invalid response shape - missing data[0].embedding: {exc}")
                sys.exit(1)

            if not isinstance(vector, list) or not vector:
                print(f"::error::Embedding vector is not a non-empty list")
                sys.exit(1)

            if not all(isinstance(v, (int, float)) for v in vector):
                print(f"::error::Embedding vector contains non-numeric values")
                sys.exit(1)

            if len(vector) != expected_dim:
                print(f"::error::Dimension mismatch: got {len(vector)}, expected {expected_dim}")
                sys.exit(1)

            # Check model name in response
            resp_model = data.get("model")
            if resp_model:
                normalized_resp = _normalize_model(resp_model)
                normalized_cfg = _normalize_model(model)
                if normalized_resp != normalized_cfg:
                    print(f"::warning::Model name mismatch: response '{resp_model}' (normalized: '{normalized_resp}') vs configured '{model}' (normalized: '{normalized_cfg}')")

            print("OK: HTTP 200")
            print(f"OK: Response shape valid (data[0].embedding is list of {len(vector)} numbers)")
            print(f"OK: Dimension matches ({len(vector)} == {expected_dim})")
            if resp_model:
                print(f"OK: Model present in response: {resp_model}")
            return

        # Non-2xx response
        classification = classify(response.status_code, body_text)

        if classification == "per_minute":
            if attempt < max_attempts:
                delay = _parse_retry_delay(body_text, response.headers) or 20
                sleep_secs = min(delay, 60)
                print(f"::warning::Per-minute rate limit hit (attempt {attempt}/{max_attempts}); sleeping {sleep_secs}s and retrying")
                time.sleep(sleep_secs)
                continue
            else:
                # All attempts exhausted
                summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
                if summary_file:
                    with open(summary_file, "a", encoding="utf-8") as f:
                        f.write("\n::warning title=Embedding contract skipped::Per-minute rate limit persisted after 3 attempts; contract check skipped.\n")
                print("::warning title=Embedding contract skipped::Per-minute rate limit persisted after 3 attempts; contract check skipped.")
                sys.exit(0)

        # All other failure classes -> error and exit 1
        error_map = {
            "per_day": "Per-day quota exhausted",
            "auth": "Authentication failed",
            "not_found": "Endpoint not found",
            "unclassified_429": "Rate limit (unclassified)",
            "other": "HTTP error",
        }
        error_title = error_map.get(classification, "Embedding contract failed")
        snippet = body_text[:300].replace("\n", " ")
        print(f"::error title={error_title}::HTTP {response.status_code}: {snippet}")
        sys.exit(1)

    # Should not reach here
    sys.exit(1)


if __name__ == "__main__":
    run_check()