"""Read-only Realm binary reader based on Realm Core v5.23.6 storage layouts."""
from dataclasses import dataclass
from pathlib import Path
import struct


class RealmError(ValueError):
    pass


@dataclass
class Node:
    offset: int
    size: int
    width: int
    wtype: int
    refs: bool
    inner: bool
    context: bool
    payload: memoryview

    def integers(self, signed=False):
        if self.wtype != 0:
            raise RealmError('Expected a bit-packed integer array')
        if self.width == 0:
            return [0] * self.size
        if self.width >= 8:
            code = {8: 'b', 16: 'h', 32: 'i', 64: 'q'}[self.width]
            return [value[0] for value in struct.iter_unpack('<' + (code if signed else code.upper()), self.payload)]
        width, payload = self.width, self.payload
        mask = (1 << width) - 1
        return [(payload[i * width // 8] >> (i * width % 8)) & mask for i in range(self.size)]


@dataclass
class Table:
    name: str
    columns: list
    rows: list
    column_types: list = None


class RealmFile:
    MAX_BYTES = 256 * 1024 * 1024
    MAX_ITEMS = 1_000_000

    def __init__(self, path):
        self.path = Path(path)
        if self.path.stat().st_size > self.MAX_BYTES:
            raise RealmError('File exceeds the 256 MiB browser limit')
        with self.path.open('rb') as stream:
            self.data = stream.read(self.MAX_BYTES + 1)
        if len(self.data) > self.MAX_BYTES:
            raise RealmError('File exceeds the 256 MiB browser limit')
        if len(self.data) < 24 or self.data[16:20] != b'T-DB':
            raise RealmError('Not an unencrypted Realm file (missing T-DB header)')
        flags = self.data[23]
        if flags & ~1:
            raise RealmError(f'Unsupported header flags: {flags:#x}')
        selected = flags & 1
        self.version = self.data[20 + selected]
        self.top = struct.unpack_from('<Q', self.data, selected * 8)[0]
        if self.top == (1 << 64) - 1:
            if len(self.data) < 40:
                raise RealmError('Missing streaming footer')
            self.top, cookie = struct.unpack_from('<QQ', self.data, len(self.data)-16)
            if cookie != 0x3034125237E526C8:
                raise RealmError('Invalid streaming footer')
        if self.version == 0 and self.top:
            raise RealmError('Invalid file format version')
        if self.top:
            self.node(self.top)
        self.warnings = []

    def _ensure_cache(self):
        # A historical recovery reader is a shallow copy with a different root.
        # Replace caches, rather than clear shared dictionaries, on any change.
        key = (self.top, self.version)
        if getattr(self, '_cache_data', None) is not self.data or self._cache_key != key:
            self._cache_data = self.data
            self._cache_key = key
            self._view = memoryview(self.data)
            self._node_cache = {}
            self._tables_cache = None
            self._nodes_cache = None

    def node(self, ref):
        self._ensure_cache()
        cached = self._node_cache.get(ref)
        if cached is not None:
            return cached
        if ref < 24 or ref % 8 or ref + 8 > len(self.data):
            raise RealmError(f'Invalid node reference {ref:#x}')
        h = self.data[ref:ref+8]
        flags = h[4]
        width = (1 << (flags & 7)) >> 1
        size = int.from_bytes(h[5:8], 'big')
        wtype = (flags >> 3) & 3
        if wtype == 3:
            raise RealmError(f'Invalid node width type at {ref:#x}')
        if size > self.MAX_ITEMS:
            raise RealmError(f'Node at {ref:#x} exceeds the one million item limit')
        length = ((size * width + 7)//8 if wtype == 0 else size * width if wtype == 1 else size)
        if ref + 8 + length > len(self.data):
            raise RealmError(f'Truncated node at {ref:#x}')
        node = Node(ref, size, width, wtype, bool(flags & 64), bool(flags & 128),
                    bool(flags & 32), self._view[ref+8:ref+8+length])
        if len(self._node_cache) < 4096:
            self._node_cache[ref] = node
        return node

    def array(self, ref):
        return self.node(ref).integers()

    def strings(self, ref, nullable=False, ancestors=()):
        node = self.node(ref)
        if ref in ancestors or len(ancestors) >= 64:
            raise RealmError('Cyclic or excessively deep string tree')
        ancestors += (ref,)
        if node.inner:
            return self._children(ref, lambda child: self.strings(child, nullable, ancestors))
        if not node.refs:
            if node.wtype != 1:
                raise RealmError('Unsupported string leaf')
            result = []
            for i in range(node.size):
                slot = node.payload[i*node.width:(i+1)*node.width]
                if not slot:
                    result.append(None if nullable else '')
                elif slot[-1] == node.width and nullable:
                    result.append(None)
                elif slot[-1] >= node.width:
                    raise RealmError('Invalid short string length')
                else:
                    result.append(bytes(slot[:node.width-1-slot[-1]]).decode('utf-8', errors='replace'))
            return result
        if node.context:
            raise RealmError('Big string leaves are not yet decoded; use Nodes view')
        refs = node.integers()
        if len(refs) not in (2, 3):
            raise RealmError('Unsupported long string layout')
        offsets = self.array(refs[0])
        blob = self.node(refs[1])
        if blob.wtype != 2:
            raise RealmError('Unsupported string blob')
        nulls = self.array(refs[2]) if len(refs) == 3 else [1] * len(offsets)
        if len(nulls) != len(offsets):
            raise RealmError('Inconsistent string null bitmap')
        start, result = 0, []
        for end, present in zip(offsets, nulls):
            if end <= start or end > len(blob.payload) or blob.payload[end-1] != 0:
                raise RealmError('Invalid string offsets')
            result.append(bytes(blob.payload[start:end-1]).decode('utf-8', errors='replace') if present else None)
            start = end
        return result

    def _children(self, ref, read):
        # Core B+ tree: [offsets-or-tagged-size, child refs..., tagged-total-size].
        values = self.array(ref)
        if len(values) < 3 or not values[-1] & 1:
            raise RealmError('Invalid B+ tree inner node')
        result = []
        for child in values[1:-1]:
            result.extend(read(child))
            if len(result) > self.MAX_ITEMS:
                raise RealmError('Table exceeds the one million row limit')
        if len(result) != values[-1] >> 1:
            raise RealmError('B+ tree row count mismatch')
        return result

    def values(self, ref, kind, ancestors=()):
        if ref in ancestors or len(ancestors) >= 64:
            raise RealmError('Cyclic or excessively deep column tree')
        node = self.node(ref)
        ancestors += (ref,)
        if node.inner:
            return self._children(ref, lambda child: self.values(child, kind, ancestors))
        if kind == 2:
            return self.strings(ref)
        if kind in (0, 1, 7):
            if node.refs:
                raise RealmError('Unsupported referenced integer leaf')
            values = node.integers(signed=True)
            if kind == 1:
                if any(v not in (0, 1) for v in values):
                    raise RealmError('Invalid Boolean value')
                return [bool(v) for v in values]
            return values
        if kind in (9, 10):
            width, code = (4, 'f') if kind == 9 else (8, 'd')
            if node.wtype != 1 or node.width != width:
                raise RealmError('Unsupported floating point layout')
            return [v[0] for v in struct.iter_unpack('<' + code, node.payload)]
        raise RealmError(f'Column type {kind} is not yet decoded; use Nodes view')

    def tables(self):
        """Decoded tables shared within this immutable file snapshot; do not mutate."""
        self._ensure_cache()
        if self._tables_cache is None:
            result = self._read_tables()
            self._tables_cache = (result, tuple(self.warnings))
        result, warnings = self._tables_cache
        self.warnings = list(warnings)
        return result

    def _read_tables(self):
        self.warnings = []
        if not self.top:
            return []
        if self.version not in (5, 6, 7, 8, 9):
            self.warnings.append(f'Format {self.version}: table decoding supports formats 5–9. Use Nodes view.')
            return []
        group = self.array(self.top)
        if len(group) < 3:
            raise RealmError('Invalid group root')
        names, refs = self.strings(group[0]), self.array(group[1])
        if len(names) != len(refs):
            raise RealmError('Table names and references differ in length')
        result = []
        for name, ref in zip(names, refs):
            try:
                top = self.array(ref)
                if len(top) != 2:
                    raise RealmError('Unsupported table root')
                spec, columns = self.array(top[0]), self.array(top[1])
                if len(spec) < 3:
                    raise RealmError('Invalid schema')
                types, labels, attrs = self.array(spec[0]), self.strings(spec[1]), self.array(spec[2])
                if len(types) != len(labels) or len(attrs) != len(labels):
                    raise RealmError('Unsupported hidden columns or inconsistent schema')
                data, cursor = [], 0
                for kind, attr in zip(types, attrs):
                    if attr & 16:
                        raise RealmError('Nullable columns are not yet decoded; use Nodes view')
                    if cursor >= len(columns):
                        raise RealmError('Missing column reference')
                    data.append(self.values(columns[cursor], kind))
                    cursor += 2 if attr & 1 else 1
                if cursor != len(columns) or len({len(v) for v in data}) > 1:
                    raise RealmError('Column count or row count mismatch')
                result.append(Table(name, labels, list(zip(*data)), types))
            except RealmError as exc:
                self.warnings.append(f'{name}: {exc}')
        return result

    def iter_live_nodes(self):
        """Walk only reachable references; do not mistake free space for live records."""
        pending, seen = [self.top] if self.top else [], set()
        while pending:
            ref = pending.pop()
            if ref in seen:
                continue
            seen.add(ref)
            if len(seen) > 100_000:
                raise RealmError('Node count exceeds browser limit')
            node = self.node(ref)
            if node.refs and node.wtype == 0:
                pending.extend(v for v in reversed(node.integers()) if v and not v & 1)
            yield node

    def nodes(self):
        self._ensure_cache()
        if self._nodes_cache is not None:
            return self._nodes_cache
        rows = []
        for node in self.iter_live_nodes():
            if node.wtype == 0:
                values = node.integers()
                content = ', '.join(str(v) for v in values)
            elif node.wtype == 1:
                content = ' | '.join(bytes(node.payload[i*node.width:(i+1)*node.width]).decode('utf-8', 'replace')
                                     for i in range(node.size)) if node.width else ''
            else:
                content = bytes(node.payload).decode('utf-8', 'replace')
            rows.append((f'{node.offset:#x}', node.size, node.width, node.wtype,
                         'refs' if node.refs else 'values', content))
        self._nodes_cache = Table('Nodes (storage, not records)', ['Offset', 'Size', 'Width', 'Width type', 'Kind', 'Content'], rows)
        return self._nodes_cache
