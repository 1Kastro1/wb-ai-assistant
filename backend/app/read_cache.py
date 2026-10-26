"""Small, bounded shared read cache. Never used for publication/authorization."""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from time import monotonic
from concurrent.futures import Future

_values = OrderedDict()
_lock = RLock()
_generation = 0
_flights = {}


def clear_reads():
    # Called from HTTP middleware: never wait on a long-running statistics query
    # in a request thread. Old generations are bounded by the same LRU limit.
    global _generation
    with _lock:
        _generation += 1


def cached_read(db, key, load, seconds=5):
    # SQLite tools/tests retain immediate read-your-writes semantics.
    if db.bind.dialect.name != 'postgresql':
        return load()
    with _lock:
        generation = _generation
        key = (id(db.bind), generation, *key)
        current = _values.get(key)
        if current and current[0] > monotonic():
            _values.move_to_end(key)
            return deepcopy(current[1])
        pending = _flights.get(key)
        leader = pending is None
        if leader:
            pending = _flights[key] = Future()
    if not leader:
        return deepcopy(pending.result())
    # Share only identical reads. A slow report must never hold the global
    # cache lock while querying SQL and block unrelated stores or counters.
    try:
        value = load()
        with _lock:
            if generation == _generation:
                _values[key] = (monotonic() + seconds, deepcopy(value))
                while len(_values) > 128:
                    _values.popitem(last=False)
        pending.set_result(deepcopy(value))
        return value
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _lock:
            _flights.pop(key, None)
