"""One durable queue consumer for all employees; HTTP remains in another process."""
import asyncio
from .main import app, lifespan, run_job_queue
from .process_lock import ProcessLock


async def main():
    async with ProcessLock('queue-consumer'):
        async with lifespan(app):
            while True:
                await run_job_queue()
                await asyncio.sleep(1)


if __name__ == '__main__':
    asyncio.run(main())
