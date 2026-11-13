"""One model job and at most one live I/O slice under the maintenance lock."""
import asyncio
from contextvars import ContextVar
from sqlalchemy import select
from .db import Session, Job
from .queue_policy import next_job

LIVE_IO = ('reviews', 'questions', 'products', 'scheduled_publication',
           'publication_reconcile', 'wb_publish_send')
MODEL_WORK = ('draft_recheck', 'draft_audit', 'review_library_audit', 'knowledge_research')
parallel_slice = ContextVar('parallel_pipeline_slice', default=False)


async def run_slice(job_id, execute, session_factory=Session, poll_seconds=1):
    """Caller owns sync/maintenance locks until BOTH tasks have finished.

    The existing generation batch already has its own safe concurrent I/O lane.
    Its nested dispatcher must not be duplicated here. Audit/check model calls
    used to hold the only consumer for up to the whole model deadline.
    """
    with session_factory() as db:
        job = db.get(Job, job_id)
        parallel = bool(job and job.kind in MODEL_WORK)
    if not parallel:
        return await execute(job_id)
    token = parallel_slice.set(True)
    parent = asyncio.create_task(execute(job_id))
    io_task = None
    try:
        while not parent.done() or io_task:
            if io_task and io_task.done():
                io_task.result()
                io_task = None
            if not parent.done() and io_task is None:
                with session_factory() as db:
                    job = next_job(db, LIVE_IO)
                    io_id = job.id if job else None
                if io_id:
                    io_task = asyncio.create_task(execute(io_id))
            pending = {task for task in (parent, io_task) if task and not task.done()}
            if pending:
                await asyncio.wait(pending, timeout=poll_seconds, return_when=asyncio.FIRST_COMPLETED)
            else:
                break
        return parent.result()
    finally:
        tasks = [task for task in (parent, io_task) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        parallel_slice.reset(token)
