"""Isolate regular expressions so pathological patterns cannot freeze the UI."""
import multiprocessing
import re
import time


def _worker(tables, pattern, flags, output):
    try:
        regex = re.compile(pattern, flags)
        hits = []
        for ti, table in enumerate(tables):
            for ri, row in enumerate(table.rows):
                for ci, value in enumerate(row):
                    value = value if isinstance(value, str) else str(value)
                    for match in regex.finditer(value):
                        hits.append((ti, ri, ci, match.start(), match.end()))
                        if len(hits) >= 10000:
                            output.send((hits, True, None))
                            return
        output.send((hits, False, None))
    except re.error as exc:
        output.send(([], False, str(exc)))
    finally:
        output.close()


def search(tables, pattern, ignore_case=True, timeout=3):
    # Validate before starting a subprocess for fast feedback.
    flags = re.I if ignore_case else 0
    re.compile(pattern, flags)
    if not any(table.rows for table in tables):
        return [], False
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(tables, pattern, flags, child))
    started = False
    start = time.monotonic()
    try:
        process.start()
        started = True
        child.close()
        if not parent.poll(max(0, timeout - (time.monotonic() - start))):
            raise TimeoutError(f'Search exceeded {timeout:g} seconds. Try a simpler expression.')
        hits, capped, error = parent.recv()
        if error:
            raise re.error(error)
        return hits, capped
    finally:
        parent.close()
        child.close()
        if started and process.is_alive():
            process.terminate()
        if started:
            process.join()
        process.close()
