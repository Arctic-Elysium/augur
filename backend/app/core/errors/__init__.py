class AppError(Exception):
    """Base for all domain errors. Carries an HTTP status and a stable code."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str = "", *, detail: dict | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail or {}


class AuthError(AppError):
    status_code = 401
    code = "unauthenticated"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class InvalidRequest(AppError):
    status_code = 422
    code = "invalid_request"


class RuleViolation(AppError):
    """The rules engine rejected an action. Never a 500 - it means the model or
    the player tried something the game does not permit."""

    status_code = 422
    code = "rule_violation"


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"


class BudgetExceeded(AppError):
    status_code = 429
    code = "budget_exceeded"
