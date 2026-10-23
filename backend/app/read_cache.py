"""Small, bounded shared read cache. Never used for publication/authorization."""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from time import monotonic

_values = OrderedDict()
_lock = RLock()


def clear_reads():
    with _lock:
        _values.clear()


def cached_read(db, key, load, seconds=5):
    # SQLite tools/tests retain immediate read-your-writes semantics.
    if db.bind.dialect.name != 'postgresql':
        return load()
    key = (id(db.bind), *key)
    # Single-flight: colleagues opening the same dashboard share one calculation.
    with _lock:
        current = _values.get(key)
        if current and current[0] > monotonic():
            _values.move_to_end(key)
            return deepcopy(current[1])
        value = load()
        _values[key] = (monotonic() + seconds, deepcopy(value))
        while len(_values) > 128:
            _values.popitem(last=False)
        return value
