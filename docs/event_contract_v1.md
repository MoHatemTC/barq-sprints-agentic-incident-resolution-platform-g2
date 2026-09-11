# AI Incident Orchestrator: Event Contract v1.0

## 1. Overview
This document defines the minimal outbound event payload emitted by the ServiceNow Business Rule (S1.3) to the backend webhook. Per FR-04, this payload is strictly limited to identifiers. No actual incident data is transmitted to ensure platform ACLs remain the single source of truth for data access.

## 2. Payload Schema

| Field Name | Data Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `event_id` | String (UUID) | Yes | A unique identifier generated via `gs.generateGUID()` at emission. Serves as the idempotency key for the backend queue. |
| `sys_id` | String | Yes | The 32-character ServiceNow `sys_id` of the triggered incident. |
| `number` | String | Yes | The human-readable ServiceNow incident number (e.g., INC0010043). |
| `event_type` | String | Yes | Indicates the trigger source. Valid values are `"Insert"` or `"Update"`. |

## 3. Example Payload
```json
{
  "event_id": "5ac951ecc3978f10b9523342b40131be",
  "sys_id": "a9b95568c3978f10b9523342b401313f",
  "number": "INC0010047",
  "event_type": "Insert"
}
```

## 4. Contract Versioning & Change Policy

To ensure seamless integration between ServiceNow and backend consumers, changes to the event contract are governed by strict versioning rules:

### Backward-Compatible Changes
* **Definition:** Modifications that do not disrupt existing consumers parsing the payload.
* **Examples:** Adding new optional payload fields or expanding non-critical metadata.
* **Handling:** Consumers are expected to ignore unrecognized fields gracefully. Version numbers remain unchanged for purely additive, optional properties.

### Breaking Changes
* **Definition:** Modifications that alter the structure, data type, or presence of required identifiers.
* **Examples:** Renaming, dropping, or reformatting required fields (`event_id`, `sys_id`, `number`, `event_type`), or altering expected data types.
* **Handling:** Any breaking change requires a major version increment (e.g., transitioning from `v1.0` to `v2.0`) and coordinated deployment with backend consumers to prevent ingestion failures.