"""Reproducible synthetic hot-path benchmarks; not a GUI responsiveness test."""
import argparse
import json
from pathlib import Path
import statistics
import struct
import sys
import tempfile
import time
from types import SimpleNamespace


def measure(operation, repeats=5):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        samples.append(time.perf_counter()-start)
    return round(statistics.median(samples)*1000, 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--implementation', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--rows', type=int, default=100000)
    args = parser.parse_args()
    sys.path.insert(0, args.implementation)
    from create_sample import Writer
    from realm_reader import RealmFile
    from browser import Browser
    from recovery import recover
    from search import search
    writer = Writer()
    table_ref = writer.table(['id', 'name', 'age'], [0, 2, 0],
                             [(i, f'Person {i}', i % 101) for i in range(args.rows)])
    root = writer.array([writer.strings(['People']), writer.array([table_ref], True), 1], True)
    struct.pack_into('<q', writer.data, root+24, len(writer.data)*2+1)
    writer.data[:24] = struct.pack('<QQ4sBBBB', root, 0, b'T-DB', 9, 9, 0, 0)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'large.realm'
        path.write_bytes(writer.data)
        db = RealmFile(path)
        metrics = {'rows': args.rows}
        metrics['cold_tables_ms'] = measure(lambda: RealmFile(path).tables())
        tables = db.tables()
        metrics['repeated_tables_ms'] = measure(db.tables)
        metrics['cold_nodes_ms'] = measure(lambda: RealmFile(path).nodes())
        db.nodes()
        metrics['repeated_nodes_ms'] = measure(db.nodes)
        fake = SimpleNamespace(tables=tables, current=0, sorts={'People': ('2', False)}, _orders={}, _positions={})
        fake.row_order = lambda: Browser.row_order(fake)
        Browser.row_order(fake)
        metrics['repeated_sorted_order_ms'] = measure(lambda: Browser.row_order(fake))
        def lookups():
            for ri in range(0, args.rows, max(1, args.rows // 10)):
                if hasattr(Browser, 'row_position'):
                    Browser.row_position(fake, ri)
                else:
                    Browser.row_order(fake).index(ri)
        lookups()
        metrics['ten_sorted_position_lookups_ms'] = measure(lookups)
        metrics['search_no_matches_ms'] = measure(lambda: search(tables, 'missing-pattern', timeout=15), 3)
        metrics['recovery_without_free_space_ms'] = measure(lambda: recover(db))
        # Extend a real deletion fixture with 8 MiB of explicitly declared,
        # empty free space. This exercises carving gaps rather than cold I/O.
        sparse = RealmFile(Path(__file__).resolve().parents[1] / 'sample_deleted.realm')
        group = sparse.array(sparse.top)
        extension = Writer()
        free_start, free_length = len(sparse.data), 8 * 1024 * 1024
        extension.data = bytearray(sparse.data) + bytes(free_length)
        positions = extension.array(sparse.array(group[3]) + [free_start])
        lengths = extension.array(sparse.array(group[4]) + [free_length])
        sparse.top = extension.array([group[0], group[1], 1, positions, lengths], True)
        struct.pack_into('<q', extension.data, sparse.top+24, len(extension.data)*2+1)
        sparse.data = bytes(extension.data)
        metrics['recovery_8mib_empty_free_space_ms'] = measure(lambda: recover(sparse), 3)
        from exporter import export_sqlite
        def sqlite_export():
            with tempfile.TemporaryDirectory() as output:
                export_sqlite(db, Path(output) / 'export.sqlite')
        metrics['sqlite_export_ms'] = measure(sqlite_export, 3)
        print(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    main()
