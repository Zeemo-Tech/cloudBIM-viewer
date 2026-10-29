"""Wall-clock stage measurements for persisted pipeline diagnostics."""
import time


def timed_call(timings, name, function, *args, **kwargs):
    started = time.perf_counter()
    result = function(*args, **kwargs)
    timings[name] = time.perf_counter() - started
    return result
