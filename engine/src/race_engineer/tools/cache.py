"""Keep loaded data in memory between requests without serving it stale.

A long-running server (the API, the MCP server over HTTP) calls the same loaders again and again:
the session catalog on every tool call, the style tables for every comparison. Reading them each
time costs tenths of a second; caching them for good would keep serving old data after
`make data` or `make m4` rebuilt it. `memo_by_files` caches a function's result keyed on its
arguments and on the files it reads (modification time and size; for a folder, those of every
file in it, or with `listing_signature` its listing), plus the current scoring run when it reads
M4 results, so a rebuild takes effect on the next call. Checking a file costs one `stat`; the
263-file sessions folder is checked by its listing (one `stat` and one directory read), since on
a network filesystem such as the deployed API's data volume a `stat` can be a round trip.

Callers share the cached object: treat it as read-only (copy a DataFrame before changing it).
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import os
import stat
import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from race_engineer.inference import provenance

# Given the call's arguments by name (defaults filled in), the file or files a result depends on.
PathsOf = Callable[[Mapping[str, Any]], Path | Iterable[Path]]


def file_signature(path: Path) -> Hashable:
    """What changes when a file or folder is rebuilt: a file's (mtime_ns, size); a folder's
    mtime_ns plus the name, mtime_ns and size of every entry in it, since a coarse clock can give
    two quick writes the same folder time (`data.store.write_table` replaces a session's file,
    which shows in both); None when it's missing. The folder's entries are summed up as a
    digest that is the same in every process (Python's own `hash` of text isn't), so the value
    can go into an ETag."""
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        return info.st_mtime_ns, info.st_size
    entries = []
    with os.scandir(path) as it:
        for entry in it:
            try:
                e = entry.stat()
            except OSError:  # removed while listing
                continue
            entries.append((entry.name, e.st_mtime_ns, e.st_size))
    digest = hashlib.blake2b(repr(sorted(entries)).encode(), digest_size=16).hexdigest()
    return info.st_mtime_ns, len(entries), digest


def listing_signature(path: Path) -> Hashable:
    """`file_signature` for a folder whose files are only ever added, removed or replaced whole,
    by renaming a new file over the old one, as `data.store.write_table` writes every processed
    table: the folder's mtime_ns plus the names in its listing, no entry `stat`ed. One `stat`
    and one directory read where `file_signature` makes one `stat` per file: two round trips
    instead of 265 for the sessions folder on a network filesystem.

    Each of those writes changes the folder (the new file is created in it under a temporary
    name, then renamed over the old one), which moves the folder's time, and adding or removing
    a file changes the names. Not seen: a file rewritten in place, which keeps its name and
    leaves the folder's time alone (use `file_signature` for a folder written that way), and a
    file replaced within the same tick of the clock as the folder's previous change with the
    signature taken in between (a tick is nanoseconds on APFS, milliseconds on Linux; `make
    data` writes each session's file seconds after the one before).

    No inode numbers: a network or sandboxed filesystem, such as the deployed API's data volume,
    may number files differently in each container. Like `file_signature`, the value is the same
    in every process, so it can go into an ETag. A file's signature is file_signature's; None
    when it's missing."""
    try:
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode):
            return info.st_mtime_ns, info.st_size
        with os.scandir(path) as it:
            names = sorted(entry.name for entry in it)
    except OSError:  # missing, or removed while listing
        return None
    digest = hashlib.blake2b(repr(names).encode(), digest_size=16).hexdigest()
    return info.st_mtime_ns, len(names), digest


# The signature of a file or folder: file_signature or listing_signature.
SignatureOf = Callable[[Path], Hashable]


@dataclass(frozen=True)
class CacheInfo:
    hits: int
    misses: int
    size: int
    maxsize: int


class FileMemo[**P, R]:
    """A function whose results are cached until the files they came from change (see
    `memo_by_files`). Calls are thread-safe; two threads missing at once both compute."""

    def __init__(
        self,
        fn: Callable[P, R],
        paths: tuple[PathsOf, ...],
        results: Callable[[Mapping[str, Any]], Path] | None,
        maxsize: int,
        signature: SignatureOf = file_signature,
    ) -> None:
        self._fn = fn
        self._signature = inspect.signature(fn)
        self._paths = paths
        self._signature_of = signature
        self._results = results
        self._maxsize = maxsize
        self._cache: OrderedDict[Hashable, R] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = self._misses = 0
        functools.update_wrapper(self, fn)

    def _key(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Hashable:
        bound = self._signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = bound.arguments
        files: list[tuple[str, Hashable]] = []
        for paths_of in self._paths:
            found = paths_of(arguments)
            for path in [found] if isinstance(found, Path) else found:
                files.append((str(path), self._signature_of(path)))
        run = None
        if self._results is not None:
            try:
                run = provenance.scoring_run(self._results(arguments))
            except (OSError, ValueError):  # unreadable manifest: the function reports it
                run = "unreadable"
        return tuple(arguments.items()), tuple(files), run

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        key = self._key(args, kwargs)
        with self._lock:
            if key in self._cache:
                self._hits += 1
                self._cache.move_to_end(key)
                return self._cache[key]
            self._misses += 1
        value = self._fn(*args, **kwargs)
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)
        return value

    def cache_clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = self._misses = 0

    def cache_info(self) -> CacheInfo:
        with self._lock:
            return CacheInfo(self._hits, self._misses, len(self._cache), self._maxsize)


def memo_by_files[**P, R](
    *paths: PathsOf,
    results: Callable[[Mapping[str, Any]], Path] | None = None,
    maxsize: int = 8,
    signature: SignatureOf = file_signature,
) -> Callable[[Callable[P, R]], FileMemo[P, R]]:
    """Decorator caching a module-level function's results (at most `maxsize`, least recently
    used dropped first). The key is the arguments (which must be hashable), the `signature` of
    every file or folder `paths` name for them (`file_signature`, or `listing_signature` for a
    folder whose files are only replaced whole) and, with `results`, the scoring run recorded in
    that results folder (`provenance.scoring_run`). Raised exceptions aren't cached.

        @memo_by_files(lambda a: a["root"] / "sessions", signature=listing_signature)
        def catalog(root: Path) -> tuple[SessionInfo, ...]: ...
    """

    def decorate(fn: Callable[P, R]) -> FileMemo[P, R]:
        return FileMemo(fn, paths, results, maxsize, signature)

    return decorate
