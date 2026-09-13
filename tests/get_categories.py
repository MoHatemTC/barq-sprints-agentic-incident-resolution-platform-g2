import os, certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
from dotenv import load_dotenv
load_dotenv()
import httpx

INSTANCE_URL = os.getenv("SERVICENOW_INSTANCE_URL")
USERNAME = os.getenv("SERVICENOW_OAUTH_USERNAME")
PASSWORD = os.getenv("SERVICENOW_OAUTH_PASSWORD")
CLIENT_ID = os.getenv("SERVICENOW_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.getenv("SERVICENOW_OAUTH_CLIENT_SECRET")

token_resp = httpx.post(
    f"{INSTANCE_URL}/oauth_token.do",
    data={
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username": USERNAME,
        "password": PASSWORD,
    },
)
token = token_resp.json()["access_token"]

resp = httpx.get(
    f"{INSTANCE_URL}/api/now/table/kb_category",
    headers={"Authorization": f"Bearer {token}"},
    params={
        "sysparm_query": "kb_knowledge_base=ae9df291c39b4750b9523342b4013147",
        "sysparm_fields": "sys_id,label",
    },
)

mapping = {row["label"]: row["sys_id"] for row in resp.json()["result"]}
print(mapping)

import json
json.dump(mapping, open("data/kb_category_mapping.json", "w"), indent=2)
print("Saved to data/kb_category_mapping.json")