from types import SimpleNamespace

from src.agent.cost_tracking import (
    consume_cost_fields,
    extract_token_usage,
    get_accumulator,
    pop_accumulator,
    snapshot_cost,
)
from src.observability.tracing import _extract_execution_ids


class _Dummy:
    pass


def test_extract_token_usage_from_message_usage_metadata():
    message = SimpleNamespace(
        usage_metadata={"input_tokens": 120, "output_tokens": 40},
        response_metadata={},
    )
    gen = SimpleNamespace(message=message, generation_info=None)
    response = SimpleNamespace(llm_output=None, generations=[[gen]])
    assert extract_token_usage(response) == (120, 40)


def test_extract_token_usage_from_openai_token_usage():
    response = SimpleNamespace(
        llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 3}},
        generations=[],
    )
    assert extract_token_usage(response) == (10, 3)


def test_snapshot_and_consume_round_trip():
    eid = "exec-cost-test"
    pop_accumulator(eid)
    acc = get_accumulator(eid)
    acc.add(1000, 200)
    snap = snapshot_cost(eid)
    assert snap["total_tokens_in"] == 1000
    assert snap["total_tokens_out"] == 200
    assert snap["estimated_cost_usd"] > 0
    fields = consume_cost_fields(eid)
    assert fields["total_tokens_in"] == 1000
    assert pop_accumulator(eid) is None


def test_extract_execution_ids_from_bound_method_kwargs():
    exec_id, inc_num = _extract_execution_ids(
        (_Dummy(), {"number": "INC001", "sys_id": "abc"}),
        {"execution_id": "eid-1", "incident_number": "INC001"},
    )
    assert exec_id == "eid-1"
    assert inc_num == "INC001"


def test_extract_execution_ids_does_not_use_incident_dict_as_id():
    exec_id, inc_num = _extract_execution_ids(
        (_Dummy(), {"number": "INC009", "short_description": "vpn"}),
        {},
    )
    assert exec_id is None
    assert inc_num == "INC009"


def test_attach_root_execution_cost_sets_usage_on_span():
    from src.observability.tracing import _attach_root_execution_cost

    eid = "exec-root-cost"
    pop_accumulator(eid)
    get_accumulator(eid).add(800, 200)

    updates = []

    class _Span:
        def update(self, **kwargs):
            updates.append(kwargs)

    _attach_root_execution_cost(_Span(), client=None, exec_id=eid)
    assert updates
    payload = updates[0]
    assert payload["usage_details"]["input"] == 800
    assert payload["usage_details"]["output"] == 200
    assert payload["cost_details"]["total"] > 0
    pop_accumulator(eid)
