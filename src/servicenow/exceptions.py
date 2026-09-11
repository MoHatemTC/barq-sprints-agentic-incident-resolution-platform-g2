class ServiceNowError(Exception):
 # Base class for every error client raises

    retryable = False
    log_state = "failed"

    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        # for debugging and 5xx errors only
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
    
# Status map to use for matching :
_STATUS_MAP = {
    401: ServiceNowAuthError,
    403: ServiceNowPermissionError,
    404: ServiceNowNotFoundError,
    409: ServiceNowConflictError,
    422: ServiceNowValidationError,
}


def exception_for_status(status_code):
    # Return the exception class matching an HTTP status code
    if status_code in _STATUS_MAP:
        return _STATUS_MAP[status_code]
    if status_code >= 500:
        return ServiceNowServerError
    return ServiceNowError


def raise_for_status(response):
    # Raise the matching typed exception if the response failed
    if response.status_code < 400:
        return
    error_class = exception_for_status(response.status_code)
    raise error_class(response.status_code, response.text[:300])