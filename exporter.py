"""Export all decoded live Realm tables using the Python standard library."""
import csv
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3


def _tables(db):
    tables = db.tables()
    warnings = list(db.warnings)
    if not tables:
        raise ValueError('No decoded tables are available to export. ' + ' '.join(warnings))
    return tables, warnings


def _destination(db, path):
    path = Path(path)
    if path.resolve() == db.path.resolve():
        raise ValueError('The export destination must differ from the source Realm.')
    return path


def export_csv(db, directory):
    """Create a new folder with one UTF-8 CSV per table and a manifest."""
    tables, warnings = _tables(db)
    directory = _destination(db, directory)
    directory.mkdir()  # Exclusive creation; do not overwrite existing exports.
    try:
        manifest = {'source': str(db.path), 'warnings': warnings, 'tables': []}
        for index, table in enumerate(tables, 1):
            safe_name = re.sub(r'[^\w.-]', '_', table.name)[:100] or 'table'
            filename = f'{index:03d}_{safe_name}.csv'
            with (directory / filename).open('x', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(table.columns)
                writer.writerows(table.rows)
            manifest['tables'].append({'table': table.name, 'file': filename,
                                       'columns': table.columns, 'column_types': table.column_types,
                                       'rows': len(table.rows)})
        with (directory / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
    except BaseException:
        shutil.rmtree(directory)
        raise
    return warnings


def _quote(name):
    if '\0' in name:
        raise ValueError('SQLite identifiers cannot contain NUL characters')
    return '"' + name.replace('"', '""') + '"'


def _type(table, ci):
    if table.column_types is not None:
        return {0: 'INTEGER', 1: 'INTEGER', 7: 'INTEGER', 9: 'REAL', 10: 'REAL', 2: 'TEXT'}.get(table.column_types[ci], 'BLOB')
    found, floating = False, False
    for row in table.rows:
        value = row[ci]
        if value is None:
            continue
        found = True
        if isinstance(value, float):
            floating = True
        elif not isinstance(value, (bool, int)):
            return 'TEXT'
    return 'REAL' if floating else 'INTEGER' if found else 'TEXT'


def export_sqlite(db, path):
    """Create a new SQLite database, preserving decoded table and column names."""
    tables, warnings = _tables(db)
    path = _destination(db, path)
    names = [table.name.lower() for table in tables]
    if len(set(names)) != len(names) or any(name.startswith('sqlite_') for name in names):
        raise ValueError('Realm table names conflict with SQLite naming rules')
    metadata = '_realm_export_metadata'
    while metadata.lower() in names:
        metadata += '_'
    # Reserve exclusively before sqlite3.connect, which would otherwise open an
    # existing database. Remove only our newly created file on failure.
    with path.open('xb'):
        pass
    connection = None
    try:
        connection = sqlite3.connect(path)
        with connection:
            connection.execute('BEGIN')
            for table in tables:
                if not table.columns:
                    raise ValueError(f'Table {table.name!r} has no exportable columns')
                definition = ', '.join(f'{_quote(name)} {_type(table, ci)}' for ci, name in enumerate(table.columns))
                connection.execute(f'CREATE TABLE {_quote(table.name)} ({definition})')
                slots = ', '.join('?' for _ in table.columns)
                float_columns = ([ci for ci, kind in enumerate(table.column_types) if kind in (9, 10)]
                                 if table.column_types is not None else list(range(len(table.columns))))
                # SQLite cannot retain NaN as a floating point value (it becomes
                # NULL); reject it rather than silently changing the value.
                def values():
                    for row in table.rows:
                        if any(isinstance(row[ci], float) and math.isnan(row[ci]) for ci in float_columns):
                            raise ValueError(f'Table {table.name!r} contains NaN, which SQLite cannot preserve')
                        yield row
                connection.executemany(f'INSERT INTO {_quote(table.name)} VALUES ({slots})',
                                       values() if float_columns else table.rows)
            connection.execute(f'CREATE TABLE {_quote(metadata)} (key TEXT PRIMARY KEY, value TEXT)')
            report = {'source': str(db.path), 'warnings': warnings,
                      'tables': [{'name': t.name, 'columns': t.columns, 'column_types': t.column_types, 'rows': len(t.rows)} for t in tables]}
            connection.executemany(f'INSERT INTO {_quote(metadata)} VALUES (?, ?)',
                                   [(key, json.dumps(value, ensure_ascii=False)) for key, value in report.items()])
    except BaseException:
        if connection is not None:
            connection.close()
            connection = None
        path.unlink()
        raise
    finally:
        if connection is not None:
            connection.close()
    return warnings
