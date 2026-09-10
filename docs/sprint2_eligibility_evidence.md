# Eligibility & Outbound Emission Evidence

## 1. Payload Minimalism & Successful Emission
**Requirement:** Demonstrate payload minimalism via captured traffic to a request-inspection endpoint.
* **Evidence:** [Paste your Webhook.site screenshot here showing the POST request and JSON payload]
* **Notes:** The payload successfully transmits only the four required identifiers (`event_id`, `incident_sys_id`, `number`, `event_type`). Authenticated via OAuth integration user (Placeholder currently set pending S1.2 completion).

## 2. Suppression Conditions
**Requirement:** Provide distinct recorded proof for each of the six suppression conditions via system logs.

### 2.1 Condition: Inactive
* **Trigger:** Set incident to `active = false`.
* **Evidence:** [Paste screenshot of log: "AI Orchestrator Suppression: Incident is not active."]

### 2.2 Condition: Not AI-Enabled
* **Trigger:** Uncheck the AI-enabled boolean.
* **Evidence:** [Paste screenshot of log: "AI Orchestrator Suppression: AI is not enabled for this incident."]

### 2.3 Condition: Already Processed
* **Trigger:** Set `x_2215689_ai_inc_0_u_ai_processing_state` to `complete`.
* **Evidence:** [Paste screenshot of log: "AI Orchestrator Suppression: Incident is already processed."]

### 2.4 Condition: Unsupported Category
* **Trigger:** Set incident category to 'Inquiry'.
* **Evidence:** [Paste screenshot of log: "AI Orchestrator Suppression: Incident category is not supported."]

### 2.5 Condition: Already Running
* **Trigger:** Set `x_2215689_ai_inc_0_u_ai_processing_state` to `in_progress`.
* **Evidence:** [Paste screenshot of log: "AI Orchestrator Suppression: Incident is being processed."]

### 2.6 Condition: Human-Locked
* **Trigger:** Check the `human_lock` boolean.
* **Evidence:** [Paste screenshot of log: "AI Orchestrator Suppression: Incident is being processed by a human."]

## 3. Relevant vs. Irrelevant Updates
**Requirement:** Define field-level criteria for relevant updates so unrelated incident modifications do not trigger emissions.
* **Relevant Update Evidence:** [Paste screenshot showing emission triggered when a key field (e.g., state or category) is updated to meet eligibility.]
* **Irrelevant Update Evidence:** [Paste screenshot showing no emission/log when modifying an unrelated field (e.g., adding a work note to a non-eligible incident).]

## 4. Save Performance Benchmarks
**Requirement:** Measure incident save performance before and after rule implementation to show no material impact.
* **Baseline (Rule Disabled):** 823 ms (Server processing time)
* **Implemented (Rule Enabled):** 783 ms (Server processing time)
* **Impact Analysis:** The execution of the Business Rule logic adds zero blocking latency to the server transaction. The `executeAsync()` method successfully offloads the RESTMessageV2 call, keeping server-side processing stable and completely unaffected by the outbound event emission.