"""Behavioral regressions for cached snapshots and sorted search positions."""
from copy import copy
import math
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from browser import Browser
from realm_reader import RealmFile, Table
from recovery import recover

ROOT = Path(__file__).resolve().parents[1]


class CacheTests(unittest.TestCase):
    def test_decode_once_and_restore_warnings(self):
        db = RealmFile(ROOT / 'sample.realm')
        with patch.object(db, '_read_tables', wraps=db._read_tables) as decode:
            tables = db.tables()
            db.warnings = ['unrelated warning']
            self.assertIs(db.tables(), tables)
            self.assertFalse(db.warnings)
            decode.assert_called_once()
        self.assertIs(db.nodes(), db.nodes())
        self.assertIsInstance(db.node(db.top).payload, memoryview)

    def test_bytes_and_version_invalidate_caches(self):
        db = RealmFile(ROOT / 'sample.realm')
        original = db.tables()
        db.data = bytes(bytearray(db.data))
        self.assertIsNot(db.tables(), original)
        db.version = 24
        self.assertFalse(db.tables())
        self.assertTrue(db.warnings)
        db.version = 9
        self.assertEqual(len(db.tables()), 2)

    def test_historical_copy_does_not_reuse_live_tables(self):
        db = RealmFile(ROOT / 'sample_deleted.realm')
        live = db.tables()
        rows, _ = recover(db)
        historical = copy(db)
        historical.top = int(next(r[1] for r in rows.rows if r[0].startswith('Historical row')), 16)
        self.assertEqual(len(historical.tables()[0].rows), 6)
        self.assertIs(db.tables(), live)
        self.assertEqual(len(live[0].rows), 5)

    def test_metadata_walk_never_formats_nodes(self):
        db = RealmFile(ROOT / 'sample_deleted.realm')
        with patch.object(db, 'nodes', side_effect=AssertionError('Do not format node payloads')):
            result, _ = recover(db)
            self.assertTrue(result.rows)

    def test_all_integer_widths(self):
        from create_sample import Writer
        db = RealmFile(ROOT / 'sample.realm')
        for width, code in [(8, 'b'), (16, 'h'), (32, 'i'), (64, 'q')]:
            writer = Writer()
            ref = writer.node(struct.pack('<'+code*3, -1, 0, 12), 3, width)
            db.data = bytes(writer.data)
            self.assertEqual(db.node(ref).integers(True), [-1, 0, 12])
            self.assertEqual(db.node(ref).integers(False)[0], (1 << width)-1)

    def test_carving_skips_gaps_and_unaligned_markers(self):
        from create_sample import Writer
        db = RealmFile(ROOT / 'sample_deleted.realm')
        group = db.array(db.top)
        writer, fragment = Writer(), Writer()
        fragment.strings(['Recovered tail'])
        node_bytes = bytearray(fragment.data[24:])
        node_bytes[:4] = b'AAAA'
        start, size = len(db.data), 4096
        writer.data = bytearray(db.data) + bytes(size)
        writer.data[start+1:start+5] = b'AAAA'
        tail = start+size-len(node_bytes)
        writer.data[tail:tail+len(node_bytes)] = node_bytes
        positions = writer.array(db.array(group[3])+[start])
        lengths = writer.array(db.array(group[4])+[size])
        db.top = writer.array([group[0], group[1], 1, positions, lengths], True)
        struct.pack_into('<q', writer.data, db.top+24, len(writer.data)*2+1)
        db.data = bytes(writer.data)
        recovered, _ = recover(db)
        tail_rows = [row for row in recovered.rows if row[-1] == 'Recovered tail']
        self.assertEqual(len(tail_rows), 1)
        self.assertEqual(tail_rows[0][1], f'{tail:#x}')
        with patch('recovery.MAX_SCAN_BYTES', 1024):
            recovered, notes = recover(db)
        self.assertFalse(any(row[-1] == 'Recovered tail' for row in recovered.rows))
        self.assertTrue(any('limit' in note or 'budget' in note for note in notes))


class SortTests(unittest.TestCase):
    def browser(self, table):
        fake = SimpleNamespace(tables=[table], current=0, sorts={}, _orders={}, _positions={})
        fake.row_order = lambda: Browser.row_order(fake)
        return fake

    def test_cached_positions_follow_sort_direction(self):
        fake = self.browser(Table('T', ['number'], [(10,), (-1,), (2,)]))
        self.assertIsInstance(fake.row_order(), range)
        self.assertEqual(Browser.row_position(fake, 2), 2)
        fake.sorts['T'] = ('0', False)
        ascending = fake.row_order()
        self.assertEqual(ascending, [1, 2, 0])
        self.assertIs(fake.row_order(), ascending)
        self.assertEqual(Browser.row_position(fake, 0), 2)
        fake.sorts['T'] = ('0', True)
        self.assertEqual(Browser.row_position(fake, 0), 0)
        self.assertEqual(fake.row_order(), [0, 2, 1])
        fake.sorts['T'] = ('row', True)
        self.assertEqual(Browser.row_position(fake, 0), 2)

    def test_nan_none_and_text_have_deterministic_order(self):
        fake = self.browser(Table('T', ['value'], [('z',), (2,), (None,), (math.nan,), ('A',), (-1,)]))
        fake.sorts['T'] = ('0', False)
        self.assertEqual(fake.row_order(), [2, 3, 5, 1, 4, 0])


if __name__ == '__main__':
    unittest.main()
