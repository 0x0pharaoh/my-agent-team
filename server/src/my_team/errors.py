class DomainError(Exception):
    status = 400

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class Invalid(DomainError):
    status = 422


class Unauthorized(DomainError):
    status = 401


class Forbidden(DomainError):
    status = 403


class NotFound(DomainError):
    status = 404


class Conflict(DomainError):
    status = 409


class Unavailable(DomainError):
    status = 503


class RateLimited(DomainError):
    status = 429
