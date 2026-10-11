import struct
import re
import tempfile
import unittest
from pathlib import Path
from create_sample import create_sample, Writer
from realm_reader import RealmFile, RealmError, Table
from search import search


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sample.realm'
        create_sample(self.path)

    def write(self, data):
        self.path.write_bytes(data)
        return RealmFile(self.path)

    def test_sample(self):
        db = RealmFile(self.path)
        tables = db.tables()
        self.assertEqual([(t.name, len(t.rows)) for t in tables], [('People', 6), ('Notes', 4)])
        self.assertEqual(tables[0].rows[0], (1, 'Alice Morgan', 'alice@example.com', 'London', 31, True))
        self.assertEqual(tables[0].rows[-1][-2], -1)
        self.assertEqual(tables[0].rows[4][1], 'Élodie Martin')
        self.assertFalse(db.warnings)
        self.assertEqual(len(db.nodes().rows), 24)

    def test_no_overwrite(self):
        original = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            create_sample(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_active_second_slot(self):
        data = bytearray(self.path.read_bytes())
        data[8:16] = data[:8]
        data[:8] = bytes(8)
        data[20], data[23] = 0, 1
        self.assertEqual(len(self.write(data).tables()), 2)

    def test_streaming_footer(self):
        data = bytearray(self.path.read_bytes())
        root = struct.unpack_from('<Q', data)[0]
        struct.pack_into('<Q', data, 0, 2**64-1)
        data.extend(struct.pack('<QQ', root, 0x3034125237E526C8))
        self.assertEqual(len(self.write(data).tables()), 2)
        data[-1] ^= 1
        with self.assertRaises(RealmError):
            self.write(data)

    def test_corruption(self):
        for data in [b'', b'not a realm', self.path.read_bytes()[:30]]:
            with self.subTest(data=data), self.assertRaises(RealmError):
                self.write(data)

    def test_new_version_structural_view(self):
        data = bytearray(self.path.read_bytes())
        data[20] = 24
        db = self.write(data)
        self.assertEqual(db.tables(), [])
        self.assertTrue(db.warnings)
        self.assertEqual(len(db.nodes().rows), 24)

    def test_btree_and_packed_signed_integers(self):
        writer = Writer()
        left = writer.node(bytes([255, 2]), 2, 8)
        right = writer.array([3])
        inner = writer.array([5, left, right, 7], True)
        writer.data[inner+4] |= 128
        db = RealmFile(self.path)
        db.data = bytes(writer.data)
        self.assertEqual(db.values(inner, 0), [-1, 2, 3])

    def test_long_strings(self):
        writer = Writer()
        offsets = writer.array([6, 12])
        blob = writer.node(b'hello\0world\0', 12, 1, 2)
        leaf = writer.array([offsets, blob], True)
        db = RealmFile(self.path)
        db.data = bytes(writer.data)
        self.assertEqual(db.strings(leaf), ['hello', 'world'])

    def test_cycle(self):
        db = RealmFile(self.path)
        writer = Writer()
        ref = writer.array([3, 24, 3], True)
        writer.data[ref+4] |= 128
        db.data = bytes(writer.data)
        with self.assertRaises(RealmError):
            db.values(ref, 0)

    def test_search_across_tables(self):
        tables = RealmFile(self.path).tables()
        hits, capped = search(tables, 'London')
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[-1][:3], (1, 1, 2))
        self.assertFalse(capped)
        hits, _ = search(tables, r'[A-Z]{3}-\d{3}', ignore_case=False)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0][:3], hits[1][:3])

    def test_invalid_and_empty_results(self):
        with self.assertRaises(re.error):
            search([], '[')
        self.assertEqual(search([], 'absent'), ([], False))

    def test_pathological_regex_timeout(self):
        with self.assertRaises(TimeoutError):
            search([Table('bad', ['value'], [('a'*10000+'!',)])], '(a+)+$', timeout=0.5)

    def test_result_limit(self):
        hits, capped = search([Table('many', ['value'], [('a'*11000,)])], 'a')
        self.assertEqual(len(hits), 10000)
        self.assertTrue(capped)


if __name__ == '__main__':
    unittest.main()
