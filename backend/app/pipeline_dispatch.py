"""Bounded model and marketplace work under the maintenance lock."""
import asyncio
from contextvars import ContextVar
from sqlalchemy import select
from .db import Session, Job, StoreProfile
from .queue_policy import next_job
from .scheduled_publication import KIND as PUBLICATION, PROVIDERS

LIVE_IO = ('reviews', 'questions', 'products', 'scheduled_publication',
           'publication_reconcile', 'wb_publish_send')
MODEL_WORK = ('draft_recheck', 'draft_audit', 'review_library_audit', 'review_minion', 'fitment_minion', 'knowledge_research')
parallel_slice = ContextVar('parallel_pipeline_slice', default=False)


async def run_publication_group(job_id, execute, session_factory):
    """Run at most one scheduled slice per marketplace using one consumer."""
    job_ids = [job_id]
    with session_factory() as db:
        job = db.get(Job, job_id)
        store = db.get(StoreProfile, (job.result or {}).get('account_id', '')) if job else None
        if store and store.provider in PROVIDERS:
            for provider in PROVIDERS:
                if provider == store.provider:
                    continue
                accounts = select(StoreProfile.id).where(StoreProfile.provider == provider)
                peer = next_job(db, (PUBLICATION,), account_ids=accounts)
                if peer:
                    job_ids.append(peer.id)
    tasks = [asyncio.create_task(execute(item)) for item in job_ids]
    try:
        await asyncio.gather(*tasks)
    finally:
        # Never release the maintenance lock while an attempt is still active.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_slice(job_id, execute, session_factory=Session, poll_seconds=1):
    """Caller owns sync/maintenance locks until all child tasks have finished.

    The dedicated I/O lane can deliver a committed answer even while the first
    model call of a generation batch is pending. Disable nested dispatch from
    the batch in this context so there is still only one caller taking I/O jobs.
    """
    with session_factory() as db:
        job = db.get(Job, job_id)
        kind = job.kind if job else None
        scheduled_batch = kind in ('drafts', 'question_drafts') and bool((job.result or {}).get('scheduled'))
    if kind == PUBLICATION:
        return await run_publication_group(job_id, execute, session_factory)
    if kind not in MODEL_WORK and not scheduled_batch:
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
                    io_id, io_kind = (job.id, job.kind) if job else (None, None)
                if io_id:
                    operation = (run_publication_group(io_id, execute, session_factory)
                                 if io_kind == PUBLICATION else execute(io_id))
                    io_task = asyncio.create_task(operation)
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
