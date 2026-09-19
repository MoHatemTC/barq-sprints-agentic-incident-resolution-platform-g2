# Sprint 3 (S2.5): Graph Design & Explicit Edge Conditions

## Explicit Edge Conditions
The state machine implements a strict sequential flow with one major conditional fork:
- \determine_risk\ -> \etrieve\: Taken if the incident is evaluated as normal/low risk.
- \determine_risk\ -> \ct\: Taken if the incident is evaluated as high risk.

## Written Justification: Risk Before Retrieval
FR-13 explicitly demands determining risk *before* retrieval.
**Justification:**
- **Cost:** Retrieving evidence requires embedding the incident description, performing dense and sparse vector searches, and ultimately generating a diagnosis based on that context. If an incident is flagged as high-risk, we short-circuit the execution immediately. This bypasses the retrieval and generation phases entirely, saving significant LLM API costs and database query overhead.
- **Safety:** High-risk incidents (e.g., "wipe the database", "restart production cluster") pose a substantial threat if mishandled. By short-circuiting to a human path *before* retrieving context, we prevent the LLM from inadvertently synthesizing dangerous resolution steps based on retrieved manuals that were not meant for automated execution. It creates a strict safety boundary against prompt injection or hallucinated resolutions.
- **Independence:** The risk determination logic must remain independent and uninfluenced by the retrieved knowledge base. If retrieval happens first, the LLM might be biased by the retrieved articles (which could describe a complex but safe procedure) and mistakenly classify a truly high-risk incident as low-risk.

## Langfuse Trace Evidence
The agent's observability integration ensures that all node executions are captured as correlated spans under a single overarching trace per execution. 
- **Root Trace:** A single Langfuse trace is emitted per incident execution, keyed explicitly to the `execution_id` and `incident_number`.
- **Correlated Node Spans:** Individual LangGraph nodes (`load`, `validate`, `classify`, `determine_risk`, `retrieve`, etc.) emit child spans (`@trace_node`) under this root trace. 
- **Evidence:** This hierarchical structure captures latency, outcome, and errors per node without crashing the worker on tracing failures. In the Langfuse dashboard, you will see a `main_execution` trace with nested generation and span events for each node executed in the state machine path.
