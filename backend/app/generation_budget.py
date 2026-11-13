import asyncio


# Observed CPU responses take ~68 seconds, with ~12 seconds for a cold load.
# Keep a finite shared deadline; ordinary no-claims templates use no model.
MODEL_GENERATION_SECONDS = 90
OWNER_RULE_CHECK_SECONDS = 120
AUTO_RULE_CHECK_SECONDS = 120
MANUAL_RULE_CHECK_SECONDS = 180


class GenerationBudget:
    """Share one wall-clock deadline across all model attempts for a draft."""

    def __init__(self, seconds=MODEL_GENERATION_SECONDS):
        self.deadline = asyncio.get_running_loop().time() + seconds

    async def run(self, operation):
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError('Время подготовки ответа моделью истекло')
        return await asyncio.wait_for(operation(), timeout=remaining)
