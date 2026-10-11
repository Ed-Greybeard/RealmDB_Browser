"""Conservative, read-only recovery of legacy Realm historical data."""
from collections import Counter
from copy import copy
import json
import struct
from realm_reader import RealmError, Table

MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_RESULTS = 10000


def recover(db):
    """Return candidate historical rows/fragments and an explanation of coverage.

    Absence from a current snapshot does not prove deletion: updates, dropped
    tables and stale references can produce the same evidence.
    """
    columns = ['Status', 'Source offset', 'Table / field', 'Historical row', 'Recovered data']
    rows, notes, seen = [], [], set()
    result = Table('Recovery candidates', columns, rows)
    if db.version not in (5, 6, 7, 8, 9):
        return result, ['Recovery supports unencrypted legacy formats 5–9 only.']
    if not db.top:
        return result, ['Empty database: no root or free-space metadata.']
    group = db.array(db.top)
    if len(group) < 3 or not group[2] & 1:
        raise RealmError('Recovery: invalid group root or logical file size')
    inactive = 1 - (db.data[23] & 1)
    old_ref = struct.unpack_from('<Q', db.data, inactive * 8)[0]
    has_snapshot = old_ref not in (0, db.top, (1 << 64) - 1)
    has_free_space = len(group) >= 5 and group[3] and group[4]
    if not has_snapshot and not has_free_space:
        return result, ['No free-space list: only the inactive header snapshot was examined.']
    live = db.tables()
    current = {(t.name, tuple(t.columns)): Counter(t.rows) for t in live}
    live_names = {table.name for table in live}
    notes.extend(db.warnings)
    live_ranges = []
    for node in db.iter_live_nodes():
        ref = node.offset
        live_ranges.append((ref, ref + ((8 + len(node.payload) + 7) & ~7)))
    live_ranges.sort()

    def add(status, ref, name, row, content):
        identity = (status, ref, name, row, content)
        if identity not in seen and len(rows) < MAX_RESULTS:
            seen.add(identity)
            rows.append((status, f'{ref:#x}', name, row, content))

    inspected = set()

    def snapshot(ref, version):
        if ref in inspected or ref == db.top or version not in (5, 6, 7, 8, 9):
            return
        inspected.add(ref)
        if len(inspected) > 1000:
            if len(inspected) == 1001:
                notes.append('Historical-root decoding limited to 1,000 candidates.')
            return
        try:
            group = db.array(ref)
            if len(group) not in (3, 5, 7, 9, 10) or not group[2] & 1:
                return
            logical_size = group[2] >> 1
            if logical_size < ref + 8 or logical_size > len(db.data):
                return
            historical = copy(db)
            historical.top, historical.version = ref, version
            tables = historical.tables()
            if historical.warnings:
                notes.append(f'Historical root {ref:#x}: some unsupported tables were skipped.')
            for table in tables:
                # Avoid mistaking omitted current tables for deleted tables.
                if table.name not in live_names and db.warnings:
                    continue
                remaining = current.get((table.name, tuple(table.columns)), Counter()).copy()
                for ri, values in enumerate(table.rows):
                    if len(rows) >= MAX_RESULTS:
                        return
                    if remaining[values]:
                        remaining[values] -= 1
                    else:
                        content = json.dumps(dict(zip(table.columns, values)), ensure_ascii=False)
                        add('Historical row — deletion/update candidate', ref, table.name, ri+1, content)
        except (RealmError, UnicodeError, IndexError):
            notes.append(f'Historical root {ref:#x} could not be decoded; its bytes may have been reused.')

    if has_snapshot:
        snapshot(old_ref, db.data[20+inactive])

    if not has_free_space:
        notes.append('No free-space list: only the inactive header snapshot was examined.')
        return result, notes
    positions, lengths = db.array(group[3]), db.array(group[4])
    if len(positions) != len(lengths):
        raise RealmError('Recovery: inconsistent free-space lists')
    logical_size = group[2] >> 1
    ranges = sorted((start, start+size) for start, size in zip(positions, lengths) if size)
    previous_end = 24
    live_cursor = 0
    for start, end in ranges:
        if start < previous_end or start % 8 or end % 8 or end > min(logical_size, len(db.data)):
            raise RealmError('Recovery: invalid or overlapping free-space range')
        while live_cursor < len(live_ranges) and live_ranges[live_cursor][1] <= start:
            live_cursor += 1
        if live_cursor < len(live_ranges) and live_ranges[live_cursor][0] < end:
            raise RealmError('Recovery: free-space range overlaps live storage')
        previous_end = end
    scanned = 0
    for start, end in ranges:
        ref = start
        while ref + 8 <= end and scanned < MAX_SCAN_BYTES and len(rows) < MAX_RESULTS:
            scan_end = min(end, ref + MAX_SCAN_BYTES - scanned)
            candidate = db.data.find(b'AAAA', ref, scan_end)
            if candidate < 0:
                scanned += scan_end - ref
                break
            scanned += candidate - ref
            ref = candidate
            if ref % 8:
                advance = 8 - ref % 8
                ref += advance
                scanned += advance
                continue
            if ref + 8 > scan_end:
                scanned += scan_end - ref
                break
            scanned += 8
            # Core v5 writes AAAA as its on-disk placeholder checksum. Requiring
            # this marker and a bounded payload reduces arbitrary-byte matches.
            if db.data[ref:ref+4] == b'AAAA':
                try:
                    node = db.node(ref)
                    node_end = ref + ((8 + len(node.payload) + 7) & ~7)
                    if scanned + node_end - ref - 8 > MAX_SCAN_BYTES:
                        notes.append(f'Skipped large free-space node at {ref:#x}; scan budget exhausted.')
                        scanned = MAX_SCAN_BYTES
                        break
                    if node_end > end or node.size == 0 or node.inner:
                        ref += 8
                        continue
                    if node.refs and node.wtype == 0:
                        snapshot(ref, db.version)
                    elif node.wtype == 1 and not node.refs and node.width:
                        for i in range(node.size):
                            if len(rows) >= MAX_RESULTS:
                                break
                            slot = node.payload[i*node.width:(i+1)*node.width]
                            tail = slot[-1]
                            if tail >= node.width:
                                continue
                            content_bytes = slot[:node.width-1-tail]
                            padding = slot[node.width-1-tail:-1]
                            if any(padding):
                                continue
                            content = bytes(content_bytes).decode('utf-8')
                            if content and all(c.isprintable() or c in '\n\r\t' for c in content):
                                add('Free-space string fragment — unverified', ref, 'Unknown field', i+1, content)
                    elif node.wtype == 2 and not node.refs:
                        # Blob fragments have no dependable table/row identity.
                        offset = 0
                        for fragment in bytes(node.payload).split(b'\0')[:-1]:
                            if len(rows) >= MAX_RESULTS:
                                break
                            content = fragment.decode('utf-8')
                            if content and all(c.isprintable() or c in '\n\r\t' for c in content):
                                add('Free-space text fragment — unverified', ref+8+offset, 'Unknown field', '', content)
                            offset += len(fragment)+1
                    scanned += node_end - ref - 8
                    ref = node_end
                    continue
                except (RealmError, UnicodeError):
                    pass
            ref += 8
        if scanned >= MAX_SCAN_BYTES or len(rows) >= MAX_RESULTS:
            notes.append('Recovery scan limited to 16 MiB of free space and 10,000 candidates; results may be incomplete.')
            break
    notes.append('Highlighted candidates are historical data, not proof of deletion; updates can leave the same remnants.')
    if not rows:
        notes.append('No recoverable candidates found. Reuse or compaction may have removed old bytes.')
    return result, notes
