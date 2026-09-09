"""Shared column arrays with a byte-bounded LRU and lossless disk fallback."""
from collections import OrderedDict
import os
from pathlib import Path

import numpy as np


def available_memory():
    """Available host memory intersected with all enclosing cgroup limits."""
    available = None
    try:
        fields = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        available = int(fields['MemAvailable'].split()[0]) * 1024
    except (OSError, KeyError, ValueError):
        pass
    root = Path('/sys/fs/cgroup')
    paths = {root}
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            if line.startswith('0::'):
                current = root / line[3:].lstrip('/')
                while current.is_relative_to(root):
                    paths.add(current)
                    current = current.parent
    except OSError:
        pass
    # Host scopes can be nested below user.slice; containers commonly expose
    # their own group as the mount root. Ancestor limits also constrain us.
    for directory in paths:
        try:
            limit = int((directory/'memory.max').read_text())
            remaining = max(0, limit - int((directory/'memory.current').read_text()))
            available = remaining if available is None else min(available, remaining)
        except (OSError, ValueError):
            pass
    return available


def memory_budget():
    """Keep headroom for geometric queries, the OS and other applications."""
    requested = max(0, int(os.environ.get('REBAR_MEMORY_CACHE_MB', '4096'))) * 1024**2
    available = available_memory()
    # At most half the currently available memory after reserving 2 GiB for
    # active neighbourhoods and unrelated processes. Zero means disk-only.
    return min(requested, max(0, available - 2 * 1024**3) // 2) if available is not None else min(requested, 512 * 1024**2)


class Columns(dict):
    """The same named-array interface for resident and spilled chunks."""
    @property
    def files(self):
        return list(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class ChunkCache:
    def __init__(self, budget_bytes):
        self.budget = max(0, int(budget_bytes))
        self._entries = OrderedDict()
        self.stats = dict(budgetBytes=self.budget, residentBytes=0, peakResidentBytes=0,
                          hits=0, diskReads=0, diskWrites=0, spilledBytes=0)

    def _remove(self, path):
        entry = self._entries.pop(path, None)
        if entry is not None:
            self.stats['residentBytes'] -= entry[1]
        return entry

    def _write(self, path, columns):
        np.savez(path, **columns)
        self.stats['diskWrites'] += 1

    def _admit(self, path, columns, persisted):
        size = sum(value.nbytes for value in columns.values())
        if size > self.budget or self.budget == 0:
            if not persisted:
                self._write(path, columns)
                self.stats['spilledBytes'] += size
            return
        while self.stats['residentBytes'] + size > self.budget:
            old_path = next(iter(self._entries))
            old_columns, old_size, old_persisted = self._entries[old_path]
            # Do not drop the only copy if a disk write fails.
            if not old_persisted:
                self._write(old_path, old_columns)
                self.stats['spilledBytes'] += old_size
            self._remove(old_path)
        self._entries[path] = (columns, size, persisted)
        self.stats['residentBytes'] += size
        self.stats['peakResidentBytes'] = max(self.stats['peakResidentBytes'], self.stats['residentBytes'])

    def save(self, path, **values):
        path = Path(path)
        columns = Columns({name: np.asarray(value) for name, value in values.items()})
        self._remove(path)
        self._admit(path, columns, persisted=False)
        return columns

    def read(self, path):
        path = Path(path)
        if path in self._entries:
            self.stats['hits'] += 1
            self._entries.move_to_end(path)
            return self._entries[path][0]
        with np.load(path, allow_pickle=False) as saved:
            columns = Columns({name: saved[name] for name in saved.files})
        self.stats['diskReads'] += 1
        self._admit(path, columns, persisted=True)
        return columns

    def clear(self):
        self._entries.clear()
        self.stats['residentBytes'] = 0


class DerivedCache:
    """Bounded reusable query indexes; evicted indexes can be rebuilt."""
    def __init__(self, budget_bytes):
        self.budget = max(0, int(budget_bytes))
        self.entries = OrderedDict()
        self.stats = dict(budgetBytes=self.budget, residentBytes=0, peakResidentBytes=0, hits=0, misses=0)

    def get(self, key):
        if key not in self.entries:
            self.stats['misses'] += 1
            return None
        self.stats['hits'] += 1
        self.entries.move_to_end(key)
        return self.entries[key][0]

    def save(self, key, value, size):
        if key in self.entries:
            self.stats['residentBytes'] -= self.entries.pop(key)[1]
        if size > self.budget:
            return
        while self.stats['residentBytes'] + size > self.budget:
            _, (_, old_size) = self.entries.popitem(last=False)
            self.stats['residentBytes'] -= old_size
        self.entries[key] = (value, size)
        self.stats['residentBytes'] += size
        self.stats['peakResidentBytes'] = max(self.stats['peakResidentBytes'], self.stats['residentBytes'])

    def clear(self):
        self.entries.clear()
        self.stats['residentBytes'] = 0
