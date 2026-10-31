# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutable FBX element tree: parse with ``io_scene_fbx.parse_fbx``, edit, write with ``encode_bin``."""

import array

try:
    from io_scene_fbx import data_types, encode_bin, parse_fbx
except ImportError:
    from ._vendor import data_types, encode_bin, parse_fbx  # type: ignore

import numpy as np

T_BOOL = data_types.BOOL
T_CHAR = data_types.CHAR
T_INT8 = data_types.INT8
T_INT16 = data_types.INT16
T_INT32 = data_types.INT32
T_INT64 = data_types.INT64
T_FLOAT32 = data_types.FLOAT32
T_FLOAT64 = data_types.FLOAT64
T_BYTES = data_types.BYTES
T_STRING = data_types.STRING
T_INT32_ARRAY = data_types.INT32_ARRAY
T_INT64_ARRAY = data_types.INT64_ARRAY
T_FLOAT32_ARRAY = data_types.FLOAT32_ARRAY
T_FLOAT64_ARRAY = data_types.FLOAT64_ARRAY
T_BOOL_ARRAY = data_types.BOOL_ARRAY
T_BYTE_ARRAY = data_types.BYTE_ARRAY

_ARRAY_TYPECODES = {
    T_INT32_ARRAY: data_types.ARRAY_INT32,
    T_INT64_ARRAY: data_types.ARRAY_INT64,
    T_FLOAT32_ARRAY: data_types.ARRAY_FLOAT32,
    T_FLOAT64_ARRAY: data_types.ARRAY_FLOAT64,
    T_BOOL_ARRAY: data_types.ARRAY_BOOL,
    T_BYTE_ARRAY: data_types.ARRAY_BYTE,
}
NAME_SEP = b"\x00\x01"


class Elem:
    """A mutable copy of ``parse_fbx.FBXElem`` (id, props, props_type, elems)."""

    __slots__ = ("id", "props", "props_type", "elems")

    def __init__(self, id, props=None, props_type=None, elems=None):
        self.id = bytes(id)
        self.props = list(props) if props else []
        self.props_type = bytearray(props_type) if props_type else bytearray()
        self.elems = list(elems) if elems else []

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_parsed(cls, parsed):
        return cls(parsed.id, parsed.props, parsed.props_type, [cls.from_parsed(e) for e in parsed.elems])

    def copy(self):
        return Elem(self.id, [_copy_prop(p) for p in self.props], self.props_type, [e.copy() for e in self.elems])

    # ------------------------------------------------------------------ props
    def add_prop(self, type_code, value):
        self.props.append(value)
        self.props_type.append(type_code)
        return self

    def add_int32(self, v):
        return self.add_prop(T_INT32, int(v))

    def add_int64(self, v):
        return self.add_prop(T_INT64, int(v))

    def add_float64(self, v):
        return self.add_prop(T_FLOAT64, float(v))

    def add_bool(self, v):
        return self.add_prop(T_BOOL, bool(v))

    def add_string(self, v):
        return self.add_prop(T_STRING, v if isinstance(v, bytes) else str(v).encode("utf-8"))

    def add_bytes(self, v):
        return self.add_prop(T_BYTES, bytes(v))

    def add_float64_array(self, values):
        return self.add_prop(T_FLOAT64_ARRAY, array.array(data_types.ARRAY_FLOAT64, values))

    def add_int32_array(self, values):
        return self.add_prop(T_INT32_ARRAY, array.array(data_types.ARRAY_INT32, values))

    def set_prop(self, index, value):
        self.props[index] = value
        return self

    # ------------------------------------------------------------------ children
    def add(self, elem):
        self.elems.append(elem)
        return elem

    def new(self, id):
        return self.add(Elem(id))

    def find(self, id, nth=0):
        id = id if isinstance(id, bytes) else id.encode()
        n = 0
        for e in self.elems:
            if e.id == id:
                if n == nth:
                    return e
                n += 1
        return None

    def find_all(self, id):
        id = id if isinstance(id, bytes) else id.encode()
        return [e for e in self.elems if e.id == id]

    def remove(self, elem):
        self.elems.remove(elem)

    def iter_tree(self):
        yield self
        for e in self.elems:
            yield from e.iter_tree()

    # ------------------------------------------------------------------ encode
    def to_fbxelem(self):
        out = encode_bin.FBXElem(self.id)
        for value, code in zip(self.props, self.props_type):
            _add_to_fbxelem(out, code, value)
        for child in self.elems:
            out.elems.append(child.to_fbxelem())
        return out

    def __repr__(self):
        return f"Elem({self.id!r}, props={self.props[:3]!r}{'...' if len(self.props) > 3 else ''}, elems={len(self.elems)})"


def _copy_prop(p):
    if isinstance(p, array.array):
        return array.array(p.typecode, p)
    if isinstance(p, np.ndarray):
        return p.copy()
    return p


def _add_to_fbxelem(out, code, value):
    if code == T_BOOL:
        out.add_bool(bool(value))
    elif code == T_CHAR:
        out.add_char(value if isinstance(value, bytes) else bytes([value]))
    elif code == T_INT8:
        out.add_int8(int(value))
    elif code == T_INT16:
        out.add_int16(int(value))
    elif code == T_INT32:
        out.add_int32(int(value))
    elif code == T_INT64:
        out.add_int64(int(value))
    elif code == T_FLOAT32:
        out.add_float32(float(value))
    elif code == T_FLOAT64:
        out.add_float64(float(value))
    elif code == T_BYTES:
        out.add_bytes(bytes(value))
    elif code == T_STRING:
        out.add_string(value if isinstance(value, bytes) else str(value).encode("utf-8"))
    elif code in _ARRAY_TYPECODES:
        arr = value if isinstance(value, array.array) else array.array(_ARRAY_TYPECODES[code], value)
        {
            T_INT32_ARRAY: out.add_int32_array,
            T_INT64_ARRAY: out.add_int64_array,
            T_FLOAT32_ARRAY: out.add_float32_array,
            T_FLOAT64_ARRAY: out.add_float64_array,
            T_BOOL_ARRAY: out.add_bool_array,
            T_BYTE_ARRAY: out.add_byte_array,
        }[code](arr)
    else:
        raise ValueError(f"Unknown FBX property type code {code!r}")


# ---------------------------------------------------------------------- file level


def load(path):
    """Parse a binary FBX file. Returns ``(root Elem, version)``."""
    parsed, version = parse_fbx.parse(path, use_namedtuple=True)
    return Elem.from_parsed(parsed), version


def save(path, root, version):
    """Write ``root`` (an Elem whose id is b'') with ``encode_bin``."""
    if root.id != b"":
        raise ValueError("root element must have an empty id")
    with encode_bin.FBXElem.enable_multithreading_cm():
        fbx_root = root.to_fbxelem()
    encode_bin.write(path, fbx_root, version)


def split_name_class(name_class):
    """``b'Name\\x00\\x01Class'`` -> ``(b'Name', b'Class')``."""
    if NAME_SEP in name_class:
        name, cls = name_class.split(NAME_SEP, 1)
        return name, cls
    return name_class, b""


def join_name_class(name, cls):
    name = name if isinstance(name, bytes) else name.encode("utf-8")
    cls = cls if isinstance(cls, bytes) else cls.encode("utf-8")
    return name + NAME_SEP + cls
