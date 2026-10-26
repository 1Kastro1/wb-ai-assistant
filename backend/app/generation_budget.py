import asyncio


MODEL_GENERATION_SECONDS = 30
OWNER_RULE_CHECK_SECONDS = 45


class GenerationBudget:
    """Share one wall-clock deadline across all model attempts for a draft."""

    def __init__(self, seconds=MODEL_GENERATION_SECONDS):
        self.deadline = asyncio.get_running_loop().time() + seconds

    async def run(self, operation):
        remaining = self.deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError('Время подготовки ответа моделью истекло')
        return await asyncio.wait_for(operation(), timeout=remaining)
