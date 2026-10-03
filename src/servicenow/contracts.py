"""The ServiceNow response shapes this codebase depends on.

ServiceNowClient checks every response against these before using it, so an
upstream format change fails loudly here with what changed, instead of as a
KeyError deep in the dashboard or the agent. The same checks run against the
live instance nightly (tests/test_servicenow_contract_live.py) and against
recorded real responses in CI (tests/fixtures/servicenow/).
"""

from .exceptions import ServiceNowContractError


def _fail(what, problem):
    raise ServiceNowContractError(200, f"{what}: {problem}")


def result_list(body, what):
    """Table API list: {"result": [ {...}, ... ]}."""
    result = (body or {}).get("result") if isinstance(body, dict) else None
    if not isinstance(result, list):
        _fail(what, f"'result' is {type(result).__name__}, expected a list")
    for i, row in enumerate(result):
        if not isinstance(row, dict):
            _fail(what, f"row {i} is {type(row).__name__}, expected an object")
    return result


def result_record(body, what, required=()):
    """Table API single record: {"result": {...}} with the required fields set."""
    result = (body or {}).get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        _fail(what, f"'result' is {type(result).__name__}, expected an object")
    missing = [f for f in required if not result.get(f)]
    if missing:
        _fail(what, f"missing {', '.join(missing)}")
    return result


def total_count(headers, what):
    """X-Total-Count header: a non-negative integer, or absent (None)."""
    raw = headers.get("X-Total-Count")
    if raw is None:
        return None
    try:
        total = int(raw)
    except (TypeError, ValueError):
        _fail(what, f"X-Total-Count is {raw!r}, expected an integer")
    if total < 0:
        _fail(what, f"X-Total-Count is {total}, expected >= 0")
    return total


def display_value_rows(rows, fields, what):
    """sysparm_display_value=all: every requested field is {"value", "display_value"}.

    A field the integration user cannot read (ACL) is left out by ServiceNow, so
    a missing field is reported, not silently shown as empty.
    """
    for i, row in enumerate(rows):
        missing = [f for f in fields if f not in row]
        if missing:
            _fail(what, f"row {i} is missing {', '.join(missing)} (field renamed or ACL)")
        for f in fields:
            cell = row[f]
            if not (isinstance(cell, dict) and "value" in cell and "display_value" in cell):
                _fail(what, f"row {i} field {f} is not {{value, display_value}}")
    return rows


def ui_meta_choices(body, element, what):
    """UI meta API: {"result": {"columns": {element: {"choices": [{label, value}]}}}}."""
    result = (body or {}).get("result") if isinstance(body, dict) else None
    columns = result.get("columns") if isinstance(result, dict) else None
    if not isinstance(columns, dict):
        _fail(what, "no 'columns' in the UI meta response")
    column = columns.get(element)
    if not isinstance(column, dict):
        _fail(what, f"no column '{element}'")
    choices = column.get("choices")
    if not isinstance(choices, list):
        _fail(what, f"column '{element}' has no choice list")
    for i, choice in enumerate(choices):
        if not (isinstance(choice, dict) and "value" in choice and "label" in choice):
            _fail(what, f"choice {i} is not {{label, value}}")
    return choices
