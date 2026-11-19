"""Business errors carry messages that are safe to show to the shop owner."""


class BusinessError(Exception):
    """Base class: the message is shown to the user as-is."""


class ValidationError(BusinessError):
    pass


class InsufficientStockError(BusinessError):
    def __init__(self, product_name: str, available: int, requested: int):
        self.product_name = product_name
        self.available = available
        self.requested = requested
        super().__init__(
            f"Not enough stock for '{product_name}'. Available: {available}, requested: {requested}.")


class DayClosedError(BusinessError):
    pass


class DuplicateError(BusinessError):
    pass


class PermissionDenied(BusinessError):
    """*perm* names the missing permission, so the screen can offer owner / manager approval."""

    def __init__(self, message: str = "You do not have permission for this action. Please ask the owner/admin.",
                 perm: str | None = None):
        super().__init__(message)
        self.perm = perm


class NotFoundError(BusinessError):
    pass
