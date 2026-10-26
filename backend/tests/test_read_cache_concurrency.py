from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import pytest
from app.read_cache import cached_read, clear_reads


def test_slow_report_does_not_block_other_keys_and_identical_reads_share_work():
    clear_reads()
    db = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')))
    started, release, joined = Event(), Event(), Event()
    calls = []

    def slow():
        calls.append(1)
        started.set()
        assert release.wait(3)
        return {'value': [1]}

    def duplicate():
        joined.set()
        return cached_read(db, ('slow-report',), slow)

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(cached_read, db, ('slow-report',), slow)
        assert started.wait(1)
        second = pool.submit(duplicate)
        assert joined.wait(1)
        try:
            fast = pool.submit(cached_read, db, ('different-store',), lambda: 'ready')
            assert fast.result(timeout=1) == 'ready'
        finally:
            release.set()
        assert first.result() == second.result() == {'value': [1]}
    assert calls == [1]


def test_failure_is_not_cached_and_invalidation_during_load_discards_old_cache():
    clear_reads()
    db = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')))
    with pytest.raises(ValueError):
        cached_read(db, ('error',), lambda: (_ for _ in ()).throw(ValueError('failed')))
    assert cached_read(db, ('error',), lambda: 'recovered') == 'recovered'
    def invalidate():
        clear_reads()
        return 'old'
    assert cached_read(db, ('scope',), invalidate) == 'old'
    assert cached_read(db, ('scope',), lambda: 'new') == 'new'
