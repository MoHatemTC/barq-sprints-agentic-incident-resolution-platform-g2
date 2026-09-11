class ServiceNowError(Exception):
 # Base class for every error client raises

    retryable = False
    log_state = "failed"

    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super().__init__(f"[{status_code}] {message}")


class ServiceNowAuthError(ServiceNowError):
    # when 401 so token missing, invalid or expired
    retryable = True


class ServiceNowPermissionError(ServiceNowError):
    # when 403 so the integration user is not allowed to do this
    log_state = "blocked"


class ServiceNowNotFoundError(ServiceNowError):
      # when 404 so the record does not exist "not funded"
    pass


class ServiceNowConflictError(ServiceNowError):
     # when 409 so record changed underneath the request
    pass


class ServiceNowValidationError(ServiceNowError):
     # when 422 so the payload not right servicenow rejected it
    pass


class ServiceNowServerError(ServiceNowError):
    # when 5xx — ServiceNow is unavailable or have something wrong 
    retryable = True