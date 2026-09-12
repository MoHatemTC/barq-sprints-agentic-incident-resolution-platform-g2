# to test service now intgeration run it "pytest tests/test_integration.py -v"
import os
import pytest
from dotenv import load_dotenv

from src.servicenow import config
from src.servicenow.client import ServiceNowClient
from src.servicenow import exceptions as exc
load_dotenv()

SYS_ID = os.getenv("INCIDENT_SYS_ID")
pytestmark = pytest.mark.skipif(not SYS_ID, reason="INCIDENT_SYS_ID not set")


@pytest.fixture(scope="module")
def client():
    return ServiceNowClient()


def test_read_incident(client):
    inc = client.get_incident(SYS_ID)
    assert inc["sys_id"] == SYS_ID


def test_update_is_idempotent(client):
    # written twice, read back once to test write and idempotency together
    fields = {
        "classification": "network",
        "suggestion": "Restart the VPN",
        "confidence": 0.87,
        "model_name": "test-model",
    }
    client.update_incident(SYS_ID, fields)
    client.update_incident(SYS_ID, fields)
    back = client.get_incident(SYS_ID)
    assert back[config.AI_FIELDS["classification"]] == "network"
    assert float(back[config.AI_FIELDS["confidence"]]) == 0.87


def test_bad_input_is_rejected_before_the_request(client):
    with pytest.raises(ValueError):
        client.update_incident(SYS_ID, {"not_a_field": "x"})
    with pytest.raises(ValueError):
        client.add_work_note(SYS_ID, "   ")


def test_add_work_note(client):
    assert client.add_work_note(SYS_ID, "S1.5 integration test note") is not None


def test_execution_log_records_start_and_end(client):
    eid = client.new_execution_id()
    started = client.write_execution_log(SYS_ID, eid, "test", config.LOG_STARTED)
    done = client.write_execution_log(SYS_ID, eid, "test", config.LOG_SUCCEEDED,
                                      result="ok")
    assert started["execution_id"] == eid
    assert done is not None


def test_log_write_never_raises(client, monkeypatch):
    # test that audit failure must not break the calling path
    monkeypatch.setattr(config, "EXECUTION_LOG_TABLE", "x_does_not_exist")
    assert client.write_execution_log(SYS_ID, "x", "bad", config.LOG_FAILED) is None

def test_all_log_statuses_are_accepted(client):
    # every status in the taxonomy must actually store
    for status in sorted(config.VALID_LOG_STATUSES):
        row = client.write_execution_log(
            SYS_ID, client.new_execution_id(), "status_check", status,
            agent="ai_orchestrator_svc",
        )
        assert row is not None, f"{status} was rejected"
        assert row["status"] == status


def test_refused_field_write_is_detected(client):
    # human_lock is human-only: the ACL must refuse the service account
    with pytest.raises(exc.ServiceNowWriteNotAppliedError):
        client.update_incident(SYS_ID, {"human_lock": True})

def test_confidence_writes_and_reads_back(client):
    # Decimal field — was silently dropped under the previous identity
    client.update_incident(SYS_ID, {"confidence": 0.87})
    back = client.get_incident(SYS_ID)
    assert float(back[config.AI_FIELDS["confidence"]]) == 0.87