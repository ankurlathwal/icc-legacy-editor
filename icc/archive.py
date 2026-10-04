"""Minimal reader/writer for the MFC CArchive primitives used by ICC 2002 data files."""
import struct

# MFC object tags
NEW_CLASS_TAG = 0xFFFF
CLASS_TAG = 0x8000


class Reader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def eof(self):
        return self.pos >= len(self.data)

    def byte(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def word(self):
        (v,) = struct.unpack_from('<H', self.data, self.pos)
        self.pos += 2
        return v

    def dword(self):
        (v,) = struct.unpack_from('<I', self.data, self.pos)
        self.pos += 4
        return v

    def raw(self, n):
        v = self.data[self.pos:self.pos + n]
        if len(v) != n:
            raise EOFError('unexpected end of data at %#x' % self.pos)
        self.pos += n
        return v

    def string(self):
        """MFC CString: BYTE length, or 0xFF + WORD length (0xFFFF + DWORD)."""
        n = self.byte()
        if n == 0xFF:
            n = self.word()
            if n == 0xFFFF:
                n = self.dword()
        return self.raw(n).decode('latin-1')

    def object_tag(self, expected_class):
        """Read a CObject tag: either a new-class declaration or a class reference."""
        tag = self.word()
        if tag == NEW_CLASS_TAG:
            schema = self.word()
            name = self.raw(self.word()).decode('ascii')
            if name != expected_class:
                raise ValueError('expected class %s, got %s' % (expected_class, name))
            return schema
        if not tag & CLASS_TAG:
            raise ValueError('unsupported object tag %#06x at %#x' % (tag, self.pos - 2))
        return None


class Writer:
    def __init__(self):
        self.buf = bytearray()
        self._classes = {}   # class name -> class index
        self._next_index = 1

    def getvalue(self):
        return bytes(self.buf)

    def byte(self, v):
        self.buf.append(v)

    def word(self, v):
        self.buf += struct.pack('<H', v)

    def dword(self, v):
        self.buf += struct.pack('<I', v)

    def raw(self, b):
        self.buf += b

    def string(self, s):
        b = s.encode('latin-1')
        n = len(b)
        if n < 0xFF:
            self.byte(n)
        elif n < 0xFFFF:
            self.byte(0xFF)
            self.word(n)
        else:
            self.byte(0xFF)
            self.word(0xFFFF)
            self.dword(n)
        self.raw(b)

    def object_tag(self, class_name, schema):
        """Write a CObject tag, declaring the class the first time it is used.

        MFC numbers classes and objects in one shared index space: a new class
        takes one index and every object written takes another.
        """
        if class_name in self._classes:
            self.word(CLASS_TAG | self._classes[class_name])
        else:
            self._classes[class_name] = self._next_index
            self._next_index += 1
            self.word(NEW_CLASS_TAG)
            self.word(schema)
            name = class_name.encode('ascii')
            self.word(len(name))
            self.raw(name)
        self._next_index += 1
