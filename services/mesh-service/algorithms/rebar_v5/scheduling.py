"""Ordered spatial work with explicit CPU, memory and in-flight bounds."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import multiprocessing
import os
from pathlib import Path

from .chunks import available_memory


def worker_count(p, cache_reserve=0):
    requested = max(1, min(4, int(os.environ.get('REBAR_SPATIAL_WORKERS', '4'))))
    available = available_memory()
    if available is None:
        return 1
    try:
        cpu = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpu = os.cpu_count() or 1
    root = Path('/sys/fs/cgroup')
    paths = {root}
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            if line.startswith('0::'):
                current = root / line[3:].lstrip('/')
                while current.is_relative_to(root):
                    paths.add(current)
                    current = current.parent
        for path in paths:
            try:
                quota, period = (path/'cpu.max').read_text().split()
                if quota != 'max':
                    cpu = min(cpu, max(1, int(quota)//int(period)))
            except (OSError, ValueError):
                pass
        fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        rss = int(fields['VmRSS'].split()[0]) * 1024
        available = min(available, max(0, 8 * 1024**3-rss))
    except (OSError, KeyError, ValueError):
        return 1
    # Include input copies, trees, PCA matrices, returned columns, and spawned
    # worker imports. Keep a further 2 GiB for orchestration and native scratch.
    per_job = (256 * 1024**2 + p.neighbourhood_point_limit * 160
               + p.block_point_limit * 256
               + p.query_batch_size * p.feature_max_neighbors * 128)
    room = max(0, available - 2 * 1024**3 - max(0, cache_reserve))
    return max(1, min(requested, cpu, room // per_job))


def _initialize_process():
    # Spawn avoids forking the service's existing native worker threads.
    from threadpoolctl import threadpool_limits
    threadpool_limits(limits=1, user_api='blas')


def ordered_work(function, jobs, workers, *, processes=False):
    """Keep at most workers jobs/results live and yield original input order."""
    if workers <= 1:
        for job in jobs:
            yield function(job)
        return
    options = dict(max_workers=workers)
    if processes:
        options.update(mp_context=multiprocessing.get_context('spawn'), initializer=_initialize_process)
    executor_type = ProcessPoolExecutor if processes else ThreadPoolExecutor
    with executor_type(**options) as executor:
        pending = deque()
        iterator = iter(jobs)
        try:
            for _ in range(workers):
                try:
                    pending.append(executor.submit(function, next(iterator)))
                except StopIteration:
                    break
            while pending:
                yield pending.popleft().result()
                try:
                    pending.append(executor.submit(function, next(iterator)))
                except StopIteration:
                    pass
        finally:
            for future in pending:
                future.cancel()
