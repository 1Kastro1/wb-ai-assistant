"""Evidence about a write attempt, separate from its user-facing error message."""


class PublicationError(ValueError):
    def __init__(self, message, *, outcome='uncertain', retryable=False, retry_after=60):
        super().__init__(message)
        self.outcome = outcome
        self.retryable = retryable
        self.retry_after = max(30, min(86400, retry_after))


def before_send(error):
    """A failing credential lookup/read occurred before entering the write call."""
    return PublicationError(str(error), outcome='not_sent', retryable=True)
