import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from realm_reader import RealmFile, Table
from exporter import export_csv, export_sqlite

ROOT = Path(__file__).resolve().parents[1]


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db = RealmFile(ROOT / 'sample.realm')

    def test_csv_all_tables_and_unicode(self):
        path = self.directory / 'export'
        self.assertEqual(export_csv(self.db, path), [])
        manifest = json.loads((path / 'manifest.json').read_text())
        self.assertEqual([t['rows'] for t in manifest['tables']], [6, 4])
        with (path / manifest['tables'][0]['file']).open(newline='', encoding='utf-8') as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0], self.db.tables()[0].columns)
        self.assertEqual(rows[5][1], 'Élodie Martin')
        self.assertEqual(rows[6][-2], '-1')

    def test_sqlite_values_and_types(self):
        path = self.directory / 'export.sqlite'
        self.assertEqual(export_sqlite(self.db, path), [])
        with sqlite3.connect(path) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM Notes').fetchone()[0], 4)
            self.assertEqual(connection.execute('SELECT name, age, active FROM People WHERE id=5').fetchone(), ('Élodie Martin', 29, 1))
            self.assertEqual(connection.execute('SELECT age FROM People WHERE id=6').fetchone()[0], -1)
            columns = connection.execute('PRAGMA table_info(People)').fetchall()
            self.assertEqual([column[2] for column in columns], ['INTEGER', 'TEXT', 'TEXT', 'TEXT', 'INTEGER', 'INTEGER'])
            self.assertEqual(json.loads(connection.execute("SELECT value FROM _realm_export_metadata WHERE key='warnings'").fetchone()[0]), [])

    def test_no_overwrite_or_source_changes(self):
        original = self.db.path.read_bytes()
        path = self.directory / 'existing.sqlite'
        path.write_bytes(b'existing data')
        with self.assertRaises(FileExistsError):
            export_sqlite(self.db, path)
        self.assertEqual(path.read_bytes(), b'existing data')
        for operation in [export_csv, export_sqlite]:
            with self.assertRaises(ValueError):
                operation(self.db, self.db.path)
        path = self.directory / 'existing_folder'
        path.mkdir()
        with self.assertRaises(FileExistsError):
            export_csv(self.db, path)
        self.assertEqual(self.db.path.read_bytes(), original)

    def test_csv_escaping_and_sql_identifiers(self):
        table = Table('odd" name;--', ['value"', 'other'], [('a,"b"\nc', None), ('é', 3)])
        with patch.object(self.db, 'tables', return_value=[table]):
            export_csv(self.db, self.directory / 'csv')
            export_sqlite(self.db, self.directory / 'safe.sqlite')
        with (self.directory / 'csv' / '001_odd__name_--.csv').open(newline='', encoding='utf-8') as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[1], ['a,"b"\nc', ''])
        with sqlite3.connect(self.directory / 'safe.sqlite') as connection:
            self.assertEqual(connection.execute('SELECT * FROM "odd"" name;--"').fetchall(), table.rows)

    def test_empty_typed_table(self):
        table = Table('Empty', ['number', 'text'], [], [0, 2])
        with patch.object(self.db, 'tables', return_value=[table]):
            export_sqlite(self.db, self.directory / 'empty.sqlite')
        with sqlite3.connect(self.directory / 'empty.sqlite') as connection:
            self.assertEqual([c[2] for c in connection.execute('PRAGMA table_info(Empty)')], ['INTEGER', 'TEXT'])

    def test_partial_warning_is_preserved(self):
        self.db.warnings = ['Unsupported table omitted']
        with patch.object(self.db, 'tables', return_value=[Table('Good', ['v'], [(1,)])]):
            warnings = export_csv(self.db, self.directory / 'partial')
        self.assertEqual(warnings, self.db.warnings)
        self.assertEqual(json.loads((self.directory / 'partial' / 'manifest.json').read_text())['warnings'], warnings)

    def test_failure_cleans_partial_output(self):
        bad = Table('Bad', ['v'], [(float('nan'),)])
        with patch.object(self.db, 'tables', return_value=[bad]), self.assertRaises(ValueError):
            export_sqlite(self.db, self.directory / 'bad.sqlite')
        self.assertFalse((self.directory / 'bad.sqlite').exists())
        with patch('exporter.csv.writer', side_effect=OSError('disk full')), self.assertRaises(OSError):
            export_csv(self.db, self.directory / 'failed')
        self.assertFalse((self.directory / 'failed').exists())

    def test_no_decoded_tables(self):
        with patch.object(self.db, 'tables', return_value=[]), self.assertRaises(ValueError):
            export_sqlite(self.db, self.directory / 'none.sqlite')
        self.assertFalse((self.directory / 'none.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
