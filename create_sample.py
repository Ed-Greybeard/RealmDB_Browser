"""Write a small, actual format-9 Realm group using only Python."""
from pathlib import Path
import struct
import argparse


class Writer:
    def __init__(self):
        self.data = bytearray(24)

    def node(self, payload, size, width=64, wtype=0, refs=False):
        ref = len(self.data)
        length = (8 + len(payload) + 7) & ~7
        capacity = (length >> 3).to_bytes(3, 'big')
        flags = width.bit_length() | (wtype << 3) | (64 if refs else 0)
        self.data.extend(capacity + b'\0' + bytes([flags]) + size.to_bytes(3, 'big') + payload)
        self.data.extend(bytes(length - 8 - len(payload)))
        return ref

    def array(self, values, refs=False):
        return self.node(b''.join(struct.pack('<q', v) for v in values), len(values), refs=refs)

    def strings(self, values):
        encoded = [v.encode('utf-8') for v in values]
        longest = max((len(v) for v in encoded), default=0) + 1
        width = 1 << (longest-1).bit_length()
        if width > 64:
            raise ValueError('Sample writer supports strings up to 63 UTF-8 bytes')
        payload = b''.join(v + bytes(width-len(v)-1) + bytes([width-len(v)-1]) for v in encoded)
        return self.node(payload, len(values), width, 1)

    def table(self, columns, types, rows):
        spec = self.array([self.array(types), self.strings(columns), self.array([0]*len(types))], True)
        refs = []
        for kind, values in zip(types, zip(*rows)):
            refs.append(self.strings(values) if kind == 2 else self.array(values))
        return self.array([spec, self.array(refs, True)], True)


def create_sample(path):
    writer = Writer()
    people = writer.table(['id', 'name', 'email', 'city', 'age', 'active'], [0,2,2,2,0,1], [
        (1,'Alice Morgan','alice@example.com','London',31,1),
        (2,'Bob Chen','bob@example.org','Manchester',42,1),
        (3,'Carla Silva','carla@example.com','Lisbon',27,0),
        (4,'David Jones','david@example.net','London',38,1),
        (5,'Élodie Martin','elodie@example.fr','Paris',29,1),
        (6,'Frank Wilson','frank@example.org','Edinburgh',-1,0),
    ])
    notes = writer.table(['id','title','body'], [0,2,2], [
        (1,'Shopping','Milk, bread, and coffee'),
        (2,'Meeting','Meet Alice in London on Monday'),
        (3,'Regex example','Ticket ABC-123 and ticket XYZ-456'),
        (4,'Unicode','Café and résumé'),
    ])
    names = writer.strings(['People','Notes'])
    tables = writer.array([people, notes], True)
    # Group's logical file size is a tagged integer; root occupies 32 bytes.
    root = writer.array([names, tables, ((len(writer.data)+32) << 1) | 1], True)
    writer.data[:24] = struct.pack('<QQ4sBBBB', root, 0, b'T-DB', 9, 9, 0, 0)
    # Exclusive creation protects existing databases.
    with Path(path).open('xb') as stream:
        stream.write(writer.data)
    return Path(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', default='sample.realm')
    args = parser.parse_args()
    print(create_sample(args.path))
