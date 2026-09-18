from src.orchestrator.state_manager import StateManager


class IncidentWorkflow:
    """
    Coordinates the incident-processing workflow.

    Database persistence is handled by StateManager.
    """

    def __init__(self, state_manager: StateManager):
        self.state = state_manager

    def run(
        self,
        event_identifier: str,
        incident_sys_id: str,
        incident_number: str,
        event_type: str,
        contract_version: str,
        simulate_failure: bool = False,
    ):
        # Step 1: process the incoming event.
        event = self.state.process_event(
            event_identifier=event_identifier,
            incident_sys_id=incident_sys_id,
            incident_number=incident_number,
            event_type=event_type,
            contract_version=contract_version,
        )

        # Duplicate event: do not start another execution.
        if event is None:
            return {
                "status": "duplicate",
                "event": None,
                "execution": None,
            }

        # Step 2: create an execution.
        execution = self.state.create_execution(
            incident_reference=incident_number,
            agent_version="v1",
            model_name="workflow-test",
        )

        try:
            # Step 3: save the first checkpoint.
            self.state.save_checkpoint(
                execution.execution_identifier,
                '{"node":"workflow_started","step":1}',
            )

            # Temporary failure simulation.
            # This will later be replaced by a real workflow node.
            if simulate_failure:
                raise RuntimeError(
                    "Simulated workflow node failure"
                )

            return {
                "status": "started",
                "event": event,
                "execution": execution,
            }

        except Exception as error:
            # Record the failure.
            self.state.record_failure(
                execution_reference=execution.execution_identifier,
                failing_node="workflow_node",
                error_class=type(error).__name__,
                message=str(error),
                retry_count=0,
            )

            # Mark the execution as failed.
            failed_execution = self.state.update_execution_status(
                execution.execution_identifier,
                "failed",
            )

            return {
                "status": "failed",
                "event": event,
                "execution": failed_execution,
                "error": str(error),
            }