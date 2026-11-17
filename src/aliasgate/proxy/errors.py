"""Errors the gateway answers itself, with fixed messages chosen by the gateway."""

from starlette.responses import JSONResponse

from aliasgate.dialects.base import Dialect


class GatewayError(Exception):
    """An error response produced by the gateway. Its message never contains request text."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


def error_response(dialect: Dialect, error: GatewayError) -> JSONResponse:
    """The error in the dialect's error shape, with the error's status."""
    body = dialect.error_body(error.status, error.code, error.message)
    return JSONResponse(body, status_code=error.status)


# Each call builds a new exception: a shared instance would carry one request's traceback
# and context into another.


def request_too_large() -> GatewayError:
    """413 request_too_large."""
    return GatewayError(413, "request_too_large", "Request body is larger than the gateway allows.")


def invalid_json() -> GatewayError:
    """400 invalid_json."""
    return GatewayError(400, "invalid_json", "Request body must be a JSON object encoded as UTF-8.")


def pseudonymization_failed() -> GatewayError:
    """500 pseudonymization_failed."""
    return GatewayError(
        500, "pseudonymization_failed", "Pseudonymisation failed; the request was not forwarded."
    )


def upstream_timeout() -> GatewayError:
    """504 upstream_timeout."""
    return GatewayError(504, "upstream_timeout", "The upstream provider did not answer in time.")


def upstream_unreachable() -> GatewayError:
    """502 upstream_unreachable."""
    return GatewayError(502, "upstream_unreachable", "The upstream provider could not be reached.")


def restoration_failed() -> GatewayError:
    """502 restoration_failed."""
    return GatewayError(502, "restoration_failed", "The upstream response could not be restored.")


def unknown_endpoint() -> GatewayError:
    """404 unknown_endpoint."""
    return GatewayError(404, "unknown_endpoint", "Unknown endpoint.")


def internal_error() -> GatewayError:
    """500 internal_error."""
    return GatewayError(500, "internal_error", "Internal gateway error.")


def unsupported_content(kind: str) -> GatewayError:
    """422 for content the gateway cannot inspect; `kind` is a sanitised label."""
    return GatewayError(422, "unsupported_content", f"Unsupported content: {kind}")


def malformed_request(position: str) -> GatewayError:
    """400 for a body without the dialect's required shape; `position` names fields, not text."""
    return GatewayError(400, "malformed_request", f"Malformed request: {position}")
