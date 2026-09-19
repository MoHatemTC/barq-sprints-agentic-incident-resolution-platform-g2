from src.orchestrator.state_manager import StateManager


class IncidentWorkflow:
    """
    Coordinates the incident-processing workflow.

    PostgreSQL persistence is handled by StateManager.
    ServiceNow integration is optional and handled by ServiceNowClient.
    """

    def __init__(
        self,
        state_manager: StateManager,
        servicenow_client=None,
    ):
        self.state = state_manager
        self.servicenow = servicenow_client

    def _write_service_now_log(
        self,
        incident_sys_id,
        execution_id,
        action,
        status,
        result=None,
        error=None,
    ):
        """
        Write an execution log to ServiceNow when integration is enabled.

        ServiceNow logging is best-effort because the database workflow
        state must not be rolled back just because audit logging fails.
        """

        if self.servicenow is None:
            return None

        return self.servicenow.write_execution_log(
            incident_sys_id=incident_sys_id,
            execution_id=execution_id,
            action=action,
            status=status,
            agent="incident-workflow",
            result=result,
            error=error,
        )

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
        #
        # Idempotency is checked before any ServiceNow side effect.
        event = self.state.process_event(
            event_identifier=event_identifier,
            incident_sys_id=incident_sys_id,
            incident_number=incident_number,
            event_type=event_type,
            contract_version=contract_version,
        )

        # Duplicate event: stop immediately.
        if event is None:
            return {
                "status": "duplicate",
                "event": None,
                "execution": None,
            }

        # Step 2: create one execution for the accepted event.
        execution = self.state.create_execution(
            incident_reference=incident_number,
            agent_version="v1",
            model_name="workflow-test",
        )

        execution_id = execution.execution_identifier

        # Step 3: notify ServiceNow that the execution started.
        self._write_service_now_log(
            incident_sys_id=incident_sys_id,
            execution_id=execution_id,
            action="workflow_started",
            status="started",
            result="Incident workflow execution started",
        )

        try:
            # Step 4: save the first workflow checkpoint.
            self.state.save_checkpoint(
                execution_id,
                '{"node":"workflow_started","step":1}',
            )

            # Temporary failure simulation.
            if simulate_failure:
                raise RuntimeError(
                    "Simulated workflow node failure"
                )

            # Step 5: mark the execution as successful.
            completed_execution = self.state.update_execution_status(
                execution_id,
                "succeeded",
            )

            # Step 6: record the successful execution in ServiceNow.
            self._write_service_now_log(
                incident_sys_id=incident_sys_id,
                execution_id=execution_id,
                action="workflow_completed",
                status="succeeded",
                result="Incident workflow execution completed successfully",
            )

            return {
                "status": "started",
                "event": event,
                "execution": completed_execution,
            }

        except Exception as error:
            # Step 7: persist the workflow failure.
            self.state.record_failure(
                execution_reference=execution_id,
                failing_node="workflow_node",
                error_class=type(error).__name__,
                message=str(error),
                retry_count=0,
            )

            # Step 8: mark the execution as failed.
            failed_execution = self.state.update_execution_status(
                execution_id,
                "failed",
            )

            # Step 9: record the failure in ServiceNow.
            self._write_service_now_log(
                incident_sys_id=incident_sys_id,
                execution_id=execution_id,
                action="workflow_failed",
                status="failed",
                error=str(error),
            )

            return {
                "status": "failed",
                "event": event,
                "execution": failed_execution,
                "error": str(error),
            }