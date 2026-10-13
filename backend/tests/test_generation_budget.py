import asyncio
import unittest
from unittest.mock import patch

from app.generation_budget import GenerationBudget


class GenerationBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_completed_result(self):
        async def answer():
            return 'checked reply'

        self.assertEqual(await GenerationBudget(1).run(answer), 'checked reply')

    async def test_stalled_model_is_cancelled(self):
        cancelled = asyncio.Event()

        async def stalled():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with self.assertRaises(TimeoutError):
            await GenerationBudget(0.01).run(stalled)
        self.assertTrue(cancelled.is_set())

    async def test_retries_share_the_original_deadline(self):
        budget = GenerationBudget(150)
        timeouts = []

        async def candidate():
            return 'checked candidate'

        async def record_timeout(operation, timeout):
            timeouts.append(timeout)
            return await operation

        with patch('app.generation_budget.asyncio.wait_for', record_timeout):
            await budget.run(candidate)
            budget.deadline -= 100  # Simulate time spent by the first call.
            await budget.run(candidate)
        self.assertGreater(timeouts[0], 149)
        self.assertLessEqual(timeouts[1], 50)

    async def test_expired_budget_does_not_start_another_request(self):
        called = False

        async def request():
            nonlocal called
            called = True

        with self.assertRaises(TimeoutError):
            await GenerationBudget(0).run(request)
        self.assertFalse(called)

    async def test_external_cancellation_is_preserved(self):
        started = asyncio.Event()

        async def request():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(GenerationBudget(1).run(request))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == '__main__':
    unittest.main()
