import json
from pathlib import Path
import struct
import tempfile
import unittest
from realm_reader import RealmFile, RealmError
from recovery import recover
from create_sample import create_sample

ROOT = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def test_real_core_deletion(self):
        path = ROOT / 'sample_deleted.realm'
        original = path.read_bytes()
        db = RealmFile(path)
        self.assertEqual(len(db.tables()[0].rows), 5)
        self.assertNotIn('Alice Morgan', [row[1] for row in db.tables()[0].rows])
        result, notes = recover(db)
        historical = [row for row in result.rows if row[0].startswith('Historical row')]
        self.assertEqual(len(historical), 1)
        self.assertEqual(json.loads(historical[0][-1])['name'], 'Alice Morgan')
        self.assertEqual(json.loads(historical[0][-1])['id'], 1)
        self.assertTrue(any(row[-1] == 'alice@example.com' for row in result.rows))
        self.assertTrue(any('not proof' in note for note in notes))
        self.assertEqual(path.read_bytes(), original)

    def test_live_values_are_not_claimed_deleted(self):
        result, _ = recover(RealmFile(ROOT / 'sample_deleted.realm'))
        bob = [row for row in result.rows if row[-1] == 'Bob Chen']
        self.assertTrue(bob)
        self.assertTrue(all('unverified' in row[0] for row in bob))
        self.assertFalse(any('Bob Chen' in row[-1] for row in result.rows if row[0].startswith('Historical row')))

    def test_no_free_space_or_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fresh.realm'
            create_sample(path)
            result, notes = recover(RealmFile(path))
            self.assertFalse(result.rows)
            self.assertTrue(any('No free-space' in note for note in notes))

    def test_unsupported_version(self):
        db = RealmFile(ROOT / 'sample_deleted.realm')
        db.version = 24
        result, notes = recover(db)
        self.assertFalse(result.rows)
        self.assertIn('5–9', notes[0])

    def test_invalid_free_list(self):
        db = RealmFile(ROOT / 'sample_deleted.realm')
        group = db.array(db.top)
        node = db.node(group[3])
        self.assertGreaterEqual(node.width, 8)
        data = bytearray(db.data)
        width = node.width // 8
        data[group[3]+8:group[3]+8+width] = (25).to_bytes(width, 'little')
        db.data = bytes(data)
        with self.assertRaises(RealmError):
            recover(db)

    def test_destroyed_old_root_retains_only_fragments(self):
        db = RealmFile(ROOT / 'sample_deleted.realm')
        result, _ = recover(db)
        root = int(next(row[1] for row in result.rows if row[0].startswith('Historical row')), 16)
        data = bytearray(db.data)
        node = db.node(root)
        data[root:root+8+len(node.payload)] = bytes(8+len(node.payload))
        db.data = bytes(data)
        result, _ = recover(db)
        self.assertFalse(any(row[0].startswith('Historical row') for row in result.rows))
        self.assertTrue(any(row[-1] == 'Alice Morgan' for row in result.rows))


if __name__ == '__main__':
    unittest.main()
