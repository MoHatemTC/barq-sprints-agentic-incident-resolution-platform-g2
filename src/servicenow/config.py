import os
from dotenv import load_dotenv

load_dotenv()

def _required(name):
    # Read  env vars, failing it is missing or blank
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# connection 
INSTANCE_URL = _required("SERVICENOW_INSTANCE_URL").rstrip("/")
CLIENT_ID = _required("SERVICENOW_OAUTH_CLIENT_ID")
CLIENT_SECRET = _required("SERVICENOW_OAUTH_CLIENT_SECRET")
USERNAME = _required("SERVICENOW_OAUTH_USERNAME")
PASSWORD = _required("SERVICENOW_OAUTH_PASSWORD")

TIMEOUT = int(os.getenv("SERVICENOW_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("SERVICENOW_MAX_RETRIES", "1"))

TOKEN_URL = f"{INSTANCE_URL}/oauth_token.do"
TABLE_API = f"{INSTANCE_URL}/api/now/table"

# scope and tables
SCOPE = "x_2215689_ai_inc_0"
INCIDENT_TABLE = "incident"
EXECUTION_LOG_TABLE = f"{SCOPE}_ai_execution_log"

# incident AI fields
AI_FIELDS = {
    "processing_state": f"{SCOPE}_u_ai_processing_state",   # Choice, default pending
    "classification":   f"{SCOPE}_u_ai_classification",     # String
    "confidence":       f"{SCOPE}_ai_confidence",           # Decimal
    "suggestion":       f"{SCOPE}_ai_suggestion",           # String
    "resolution":       f"{SCOPE}_ai_resolution",           # String
    "failure_reason":   f"{SCOPE}_ai_failure_reason",       # String
    "model_name":       f"{SCOPE}_ai_model_name",           # String
    "agent_version":    f"{SCOPE}_ai_agent_version",        # String
    "processing_start": f"{SCOPE}_ai_processing_start",     # Date,Time
    "processing_end":   f"{SCOPE}_ai_processing_end",       # Date,Time
    "human_review":     f"{SCOPE}_human_review_required",   # True/False
    "human_lock":       f"{SCOPE}_human_lock",              # True/False
    "ai_enabled":       f"{SCOPE}_ai_enabled",              # True/False, default true
    "retry_count":      f"{SCOPE}_ai_retry_count",          # Integer, default 0
    "max_retries":      f"{SCOPE}_ai_max_retries",          # Integer, default 3
    "retry_time_out":   f"{SCOPE}_ai_retry_time_out",       # Date,Time
}

# execution log columns
LOG_FIELDS = {
    "incident":     "incident",       # reference incident
    "execution_id": "execution_id",   # String
    "action":       "action",         # String
    "agent":        "agent",          # String
    "timestamp":    "timestamp",      # Time
    "status":       "status",         # Choice
    "result":       "result",         # String
    "error":        "error",          # String
}

# execution log status values
LOG_STARTED = "started"
LOG_SUCCEEDED = "succeeded"
LOG_FAILED = "failed"
LOG_BLOCKED = "blocked"
LOG_ABANDONED = "abandoned"

# journal field 
WORK_NOTES = "work_notes"