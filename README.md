# Realm Browser

A simple, read-only desktop browser built entirely with Python's standard library
(Tkinter, struct, re, and multiprocessing). No Realm SDK or native database bridge
is needed to run it. Python 3.9+ with Tkinter and a desktop display is required.

```sh
python3 browser.py sample.realm
```

Use **Open Realm…** to choose a database. Select a table on the left to browse
rows. Large tables use pages of 200 rows; selecting a row shows its full values
below the grid.

Click a column header to sort the whole table, including rows on other pages.
Click again to reverse the order; an arrow indicates the direction. Numbers sort
numerically and text sorts without regard to case. Click **Row** to sort by the
original row number. Sorting is remembered for each table while the database is
open, and result navigation follows the sorted row positions.

Enter a Python regular expression and press Enter or **Search**. Search covers
all decoded tables and all reachable storage nodes, regardless of the selected
table, page or view. Search switches to **Database** view, which lists decoded
tables and storage nodes together. This includes schema names and storage for
tables the reader cannot decode. A value may match both a table cell and its
storage node. After running recovery, search also includes recovery candidates.
**Previous result / Next result**,
**Shift-F3 / F3** cycle through individual matches, including multiple matches
in one cell. The browser selects the table and row, shows the matched cell's full
text, and highlights the match. Examples:

- `London` — find a city in both People and Notes.
- `@example\.(com|org)$` — find matching email addresses.
- `[A-Z]{3}-\d{3}` — find both ticket numbers in the same note.

Search runs in a separate process with a three-second timeout and a 10,000-match
limit. Invalid expressions display an error. Searching does not hide other rows.

## Export to CSV or SQLite

**Export database to CSV…** exports every decoded live table into a new
`<database-name>_csv` folder inside the parent folder you choose. Each table gets
its own UTF-8 CSV with a header row; filenames use a numbered, sanitized table
name. `manifest.json` records original table and column names, row counts, Realm
column types, the source path and any skipped-data warnings. Commas, quotes and
newlines are escaped using standard CSV quoting. Boolean values use `True`/`False`;
null values, if supported, use empty cells (indistinguishable from empty strings).

**Export database to SQLite…** creates one SQLite file containing all decoded
live tables. Table and column names are preserved and safely quoted. Integers,
Booleans and old datetime seconds use `INTEGER` (Booleans become 0/1), floating
point columns use `REAL`, and strings use `TEXT`. Empty tables retain their
decoded column types. `_realm_export_metadata` contains JSON-encoded source,
warnings and schema information; its name gets extra underscores if it conflicts
with a source table. Realm indexes, constraints and relationships are not recreated.
Names that SQLite cannot represent, or NaN values that SQLite would silently
convert to NULL, cause an explicit export failure.

Both exports include all decoded live rows, independent of the visible page,
sorting or search. Storage nodes and recovery candidates are excluded; use
**Export recovery…** for the separate recovery report. Unsupported tables produce
a **Partial export** notice and warnings in the output metadata. A database with
no decoded tables cannot be exported. Existing files and CSV folders are never
overwritten, and failed exports remove their newly created partial outputs.

## Test database

`sample.realm` is a binary Realm format-9 file, not JSON or SQLite with a changed
extension. It contains People (6 rows) and Notes (4 rows), including Unicode,
Boolean and signed integer values and repeated regex matches. Regenerate it at a
new path with:

```sh
python3 create_sample.py test.realm
```

The GUI also offers **Create test Realm…**. The generator refuses to overwrite
an existing file, even if a save dialog confirms replacement.

`sample_deleted.realm` is an additional fixture produced by Realm Core v5.23.6:
Core opened `sample.realm`, wrote a native copy with `Group::write()`, reopened
that copy in `mode_ReadWrite`, called `get_table("People")->remove(0)`, committed,
and ran `Group::verify()`. Its live People table has five rows; Alice Morgan is
absent. Recovery finds Alice's complete historical row and free-space string
fragments. No native library is required to browse or recover this fixture.

## Recover deleted data

Open a database and click **Recover deleted data**. The **Recovery** view shows
candidates in amber, with a status, source byte offset and recovered content.
Complete historical rows include their table name and historical row number;
fragments have no known table or field. Column sorting and regex result navigation
also work with recovery candidates. **Export recovery…** writes a new JSON report
containing candidates, their provenance and coverage notes. It refuses to
overwrite files. Recovery never modifies or reinserts data into the source Realm.

### What Realm does when deleting a row

The legacy Core implementation removes the row's values from the table's columns
and updates links/backlinks. Ordered removal shifts later rows; unordered removal
can move the last row into the removed row's position. Thus a row number is not
a durable object identity. These operations are described in
[Table deletion](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/table.cpp).

Realm uses copy-on-write for persisted arrays. Changes can create new arrays and
leave the previous arrays in file storage. The group records free-space positions,
lengths and, for shared transactions, versions. Old readers can delay reuse;
later writes may allocate those blocks again. See
[Array copy-on-write](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/array.cpp)
and [GroupWriter free-space reuse](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/group_writer.cpp).
Compaction writes live data to a separate file, discarding recoverable free-space
remnants from the compacted copy; see
[Group's compact-copy implementation](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/group.cpp).
There is no dependable per-record "deleted" flag in these legacy storage arrays.

### How this browser recovers remnants

1. It checks the inactive file-header root and searches the current group's
   declared free-space ranges for intact historical group roots. If supported
   schema and column arrays survive, it decodes historical rows and compares them
   with current rows using table name, column names and all values. Duplicate row
   counts are preserved. Rows absent from the current decoded table are labelled
   **Historical row — deletion/update candidate**. Current tables that were
   omitted because of unsupported schemas are not assumed to be deleted.
2. It scans eight-byte-aligned locations within validated free-space ranges for
   legacy Realm node headers. It requires Core v5's on-disk `AAAA` placeholder
   checksum marker, valid payload bounds and no overlap with live storage. This
   marker is not an integrity checksum. It extracts properly padded short-string
   slots and null-terminated UTF-8 blob fragments with printable content. These
   are labelled **unverified fragments**, with their offsets. It does not join
   unrelated column remnants into invented records or assign them a table name.
3. All candidates are highlighted and retained separately from live records.
   Once recovered, they are included in database-wide search and can be exported.

Recovery supports unencrypted file formats 5–9 and the same decoded table types
as the browser. Scanning is bounded to 16 MiB of free space, 10,000 candidates and
1,000 historical-root candidates; coverage notes report limits and skipped data.
No free-space list means only the inactive header root can be examined. Older
snapshots may be missing, partially overwritten or contain unsupported types.

**A recovered candidate is not proof of deletion.** An update or schema change
can leave an old row that is absent from the current snapshot. Free-space arrays
may contain unchanged values belonging to records that are still live. Arbitrary
bytes can resemble a valid node. Results have no reliable deletion timestamp,
and historical row numbers do not identify current rows. Recovery is best effort:
overwritten, compacted, encrypted or unsupported data may be unrecoverable. Work
on a copy of a database closed by its writer to avoid inconsistent snapshots.

## Compatibility

This is an initial format reader, not a full replacement for the Realm SDK.

- Decoded table browsing: unencrypted legacy file formats 5–9, with integer,
  Boolean, old datetime (raw seconds), float, double, and string columns. Includes
  short/long strings, B+ tree columns, and indexed-column reference ordering.
- Nullable, enumerated string, binary, timestamp, link, collection, mixed and
  hidden-column schemas are not yet decoded. A table containing an unsupported
  column is omitted with an explicit message rather than displaying invented
  values or misaligned rows.
- Newer file formats open in **Nodes** view. This traverses live storage references
  and exposes offsets, array metadata and payloads. Regex search works there,
  but storage nodes are **not decoded table records**. Encrypted files are not
  supported; no key-entry/decryption feature is provided.
- Both active header slots and streaming-file footers are handled. Files are read
  as snapshots without writes, lock files or migrations. Use a copy of a database
  closed by its writer; reading an actively changing file is not coordinated.
- Limits: 256 MiB per file, one million items per node/table and 100,000 reachable
  nodes. Structural corruption raises an error.

## Format references

Realm's authoritative storage definitions are in the open source
[Realm Core repository](https://github.com/realm/realm-core). The reader and
sample writer use these definitions, particularly the pinned legacy layout:

- [NodeHeader: bit widths, flags and payload sizes](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/node_header.hpp)
- [SlabAlloc: file header, active slots and streaming footer](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/alloc_slab.hpp)
- [Group: table names, table refs and tagged logical file size](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/group.hpp)
- [Table: separate schema and column-reference arrays](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/table.hpp)
- [Spec: types, names and attributes](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/spec.cpp)
- [Array: B+ tree child and total-size layout](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/array.cpp)
- [ArrayString and ArrayStringLong: string encodings](https://github.com/realm/realm-core/blob/v5.23.6/src/realm/array_string.cpp)

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 tests/ui_smoke.py
```

The unit tests cover sample values, active header slots, streaming footers,
corruption, packed signed integers, B+ trees, long strings, cycles, format
fallback, regex matching, timeouts and result limits. The UI smoke test requires
a display and checks table selection, regex navigation and highlighting,
invalid expressions, sorting, recovery highlighting and searching, JSON export
and protection of the source database. Recovery tests use the real Core deletion
fixture, check fragment versus row classification, reject malformed free-space
metadata and verify the source bytes remain unchanged.

Export tests check all-table coverage, Unicode, CSV quoting, SQL identifiers,
typed empty tables, partial-export warnings, source protection and cleanup after
failure. The UI smoke test also exercises both export dialogs.

Validation performed: all 35 unit tests and the UI smoke test passed. A separately
built Realm Core v5.23.6 opened `sample.realm`, ran `Group::verify()`, and confirmed
both table row counts, Alice's name and the signed integer value -1. That native
build was used only for independent validation; the browser has no native Realm
dependency.

## Performance

The reader caches decoded tables and formatted storage nodes for each immutable
file snapshot. A change to the snapshot bytes, root or format version invalidates
the caches, including when recovery opens a historical root. Internal consumers
must treat the returned tables as read-only. Up to 4,096 parsed node headers are
cached; their payloads use memory views into the original bytes rather than
copies. Byte-aligned integer arrays use bulk `struct` decoding.

The UI sorts once per table and sort setting, caches original-row positions for
result navigation, and renders only the current 200-row page. Default row order
uses a range without allocating a list. Repeated page changes reuse column
configuration and preserve column widths. Search reuses its decoded tables and
streams cells inside the subprocess, avoiding a second per-cell list and string
conversion in the parent. Timeout accounting includes subprocess startup.

Recovery traverses live node metadata without formatting payloads and searches
free-space gaps using Python's native byte search, retaining alignment and scan
limits. SQLite export streams rows and checks for NaN only in columns that can
contain floating point values. Numeric type inference no longer copies columns.

Run the synthetic benchmark with:

```sh
python3 benchmarks/performance.py
```

Local results for 100,000 rows (milliseconds, medians; full results in
[benchmarks/results.json](benchmarks/results.json)):

| Operation | Before | After |
| --- | ---: | ---: |
| Initial table decoding | 182.4 | 119.8 |
| Repeated table access | 176.7 | <0.01 |
| Initial storage-node view | 168.4 | 104.5 |
| Repeated sorted order access | 43.1 | <0.01 |
| Ten sorted result-position lookups | 443.7 | 0.013 |
| Regex search with no matches | 478.1 | 313.5 |
| Recovery scanning 8 MiB of empty free space | 361.2 | 3.7 |
| SQLite export from an opened database | 316.1 | 126.0 |

The benchmark uses five repetitions, or three for search, sparse recovery and
SQLite export. Repeated operations are measured after their first access;
initial decoding uses a new reader each time. These are synthetic measurements,
not guarantees for every Realm file or hardware configuration. Retaining decoded
data and sort indexes trades some memory for faster repeated operations. The
snapshot size and recovery limits remain in place. First decoding, process
startup and exports still run synchronously from the UI.
