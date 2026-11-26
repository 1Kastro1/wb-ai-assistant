import asyncio
from math import ceil


# Observed CPU responses take ~68 seconds, with ~12 seconds for a cold load.
# Keep a finite shared deadline; ordinary no-claims templates use no model.
MODEL_GENERATION_SECONDS = 90
OWNER_RULE_CHECK_SECONDS = 120
AUTO_RULE_CHECK_SECONDS = 120
MINION_RULE_CHECK_SECONDS = 240
MANUAL_RULE_CHECK_SECONDS = 180


def cpu_request_seconds(messages, schema=None, *, minimum=120, maximum=600):
    from .model_context import request_tokens

    # On the Aeza CPU, 2,605 actual prompt tokens took 209 seconds to
    # evaluate, before decoding. The estimator also reserves output tokens.
    return min(maximum, max(minimum, ceil(100 + request_tokens(messages, schema) / 18)))


class GenerationBudget:
    """Share one wall-clock deadline across all model attempts for a draft."""

    def __init__(self, seconds=MODEL_GENERATION_SECONDS):
        self.deadline = asyncio.get_running_loop().time() + seconds

    async def run(self, operation):
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError('Время подготовки ответа моделью истекло')
        return await asyncio.wait_for(operation(), timeout=remaining)
