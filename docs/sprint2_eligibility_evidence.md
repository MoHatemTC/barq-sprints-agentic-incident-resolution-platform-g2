# Eligibility & Outbound Emission Evidence

## 1. Payload Minimalism & Successful Emission
**Requirement:** Demonstrate payload minimalism via captured traffic to a request-inspection endpoint.
* **Evidence:** ![Webhook.site Evidence](images/webhook-site%20evidence.png)
* **Notes:** The payload successfully transmits only the four required identifiers (`event_id`, `sys_id`, `number`, `event_type`). Authenticated via OAuth integration user.

## 2. Suppression Conditions
**Requirement:** Provide distinct recorded proof for each of the six suppression conditions via system logs.

### 2.1 Condition: Inactive
* **Trigger:** Set incident to `active = false`.
* **Evidence:** ![Inactive Evidence](images/inactive%20evidence.png)

### 2.2 Condition: Not AI-Enabled
* **Trigger:** Uncheck the AI-enabled boolean.
* **Evidence:** ![Not AI-Enabled Evidence](images/ai-enabled%20evidence.png)

### 2.3 Condition: Already Processed
* **Trigger:** Set `x_2215689_ai_inc_0_u_ai_processing_state` to `complete`.
* **Evidence:** ![Already Processed Evidence](images/incident-processed%20evidence.png)

### 2.4 Condition: Unsupported Category
* **Trigger:** Set incident category to 'Inquiry'.
* **Evidence:** ![Unsupported Category Evidence](images/category%20evidence.png)

### 2.5 Condition: Already Running
* **Trigger:** Set `x_2215689_ai_inc_0_u_ai_processing_state` to `in_progress`.
* **Evidence:** ![Already Running Evidence](images/incident_inprogress%20evidence.png)

### 2.6 Condition: Human-Locked
* **Trigger:** Check the `human_lock` boolean.
* **Evidence:** ![Human-Locked Evidence](images/human-lock%20evidence.png)

## 3. Relevant vs. Irrelevant Updates
**Requirement:** Define field-level criteria for relevant updates so unrelated incident modifications do not trigger emissions.
* **Relevant Update Evidence:** ![Relevant Update Evidence](images/relevant-update%20evidence.png)
* **Irrelevant Update Evidence:** ![Irrelevant Update Evidence](images/irrelevant-update%20evidence.png)

## 4. Save Performance Benchmarks
**Requirement:** Measure incident save performance before and after rule implementation to show no material impact.
* **Baseline (Rule Disabled):** 823 ms (Server processing time)
* **Implemented (Rule Enabled):** 783 ms (Server processing time)
* **Impact Analysis:** The execution of the Business Rule logic adds zero blocking latency to the server transaction. The `executeAsync()` method successfully offloads the RESTMessageV2 call, keeping server-side processing stable and completely unaffected by the outbound event emission.