# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only queries on an FBX ``Elem`` tree (objects, connections, properties, definitions)."""

import random

from .tree import Elem, split_name_class

OBJECT_SECTIONS = (b"Objects",)


def section(root, name):
    return root.find(name)


def objects(root):
    sec = root.find(b"Objects")
    return sec.elems if sec is not None else []


def object_id(elem):
    return elem.props[0] if elem.props else None


def object_name(elem):
    return split_name_class(elem.props[1])[0] if len(elem.props) > 1 else b""


def object_class(elem):
    return split_name_class(elem.props[1])[1] if len(elem.props) > 1 else b""


def object_subclass(elem):
    return elem.props[2] if len(elem.props) > 2 else b""


def find_objects(root, elem_id=None, name=None, subclass=None):
    out = []
    want_id = elem_id if elem_id is None or isinstance(elem_id, bytes) else elem_id.encode()
    want_name = name if name is None or isinstance(name, bytes) else name.encode()
    want_sub = subclass if subclass is None or isinstance(subclass, bytes) else subclass.encode()
    for e in objects(root):
        if want_id is not None and e.id != want_id:
            continue
        if want_name is not None and object_name(e) != want_name:
            continue
        if want_sub is not None and object_subclass(e) != want_sub:
            continue
        out.append(e)
    return out


def object_by_id(root, oid):
    for e in objects(root):
        if e.props and e.props[0] == oid:
            return e
    return None


# ---------------------------------------------------------------------- connections


def connections_elem(root):
    return root.find(b"Connections")


def connections(root):
    """List of ``(kind, child_id, parent_id, prop_name_or_None, elem)``."""
    sec = connections_elem(root)
    if sec is None:
        return []
    out = []
    for c in sec.elems:
        if c.id != b"C" or len(c.props) < 3:
            continue
        kind = c.props[0]
        prop = c.props[3] if len(c.props) > 3 else None
        out.append((kind, c.props[1], c.props[2], prop, c))
    return out


def children_of(root, parent_id, kind=b"OO"):
    return [c for c in connections(root) if c[2] == parent_id and (kind is None or c[0] == kind)]


def parents_of(root, child_id, kind=None):
    return [c for c in connections(root) if c[1] == child_id and (kind is None or c[0] == kind)]


def add_connection(root, child_id, parent_id, kind=b"OO", prop=None, index=None):
    sec = connections_elem(root)
    c = Elem(b"C")
    c.add_string(kind).add_int64(child_id).add_int64(parent_id)
    if prop is not None:
        c.add_string(prop)
    if index is None:
        sec.elems.append(c)
    else:
        sec.elems.insert(index, c)
    return c


def remove_connections(root, pred):
    sec = connections_elem(root)
    if sec is None:
        return 0
    before = len(sec.elems)
    sec.elems = [c for c in sec.elems if not (c.id == b"C" and len(c.props) >= 3 and pred(c))]
    return before - len(sec.elems)


# ---------------------------------------------------------------------- properties70


def props70(elem):
    p = elem.find(b"Properties70")
    return p.elems if p is not None else []


def prop_elem(elem, name):
    name = name if isinstance(name, bytes) else name.encode()
    for p in props70(elem):
        if p.id == b"P" and p.props and p.props[0] == name:
            return p
    return None


def prop_values(elem, name):
    p = prop_elem(elem, name)
    return list(p.props[4:]) if p is not None else None


def set_prop(elem, name, type_name, type_label, flags, *values, type_codes=None):
    """Upsert a ``P`` entry. ``values`` are stored after the 4 header strings."""
    name_b = name if isinstance(name, bytes) else name.encode()
    container = elem.find(b"Properties70")
    if container is None:
        container = elem.new(b"Properties70")
    p = prop_elem(elem, name_b)
    if p is None:
        p = container.new(b"P")
    p.props = []
    p.props_type = bytearray()
    p.add_string(name_b).add_string(type_name).add_string(type_label).add_string(flags)
    for i, v in enumerate(values):
        code = type_codes[i] if type_codes else None
        if code is not None:
            p.add_prop(code, v)
        elif isinstance(v, bool):
            p.add_prop(ord("I"), int(v))
        elif isinstance(v, int):
            p.add_prop(ord("I"), v)
        elif isinstance(v, float):
            p.add_prop(ord("D"), v)
        else:
            p.add_string(v)
    return p


# ---------------------------------------------------------------------- definitions


def definition_count(root, object_type, delta=0):
    defs = root.find(b"Definitions")
    if defs is None:
        return None
    want = object_type if isinstance(object_type, bytes) else object_type.encode()
    total = None
    for ot in defs.find_all(b"ObjectType"):
        if ot.props and ot.props[0] == want:
            cnt = ot.find(b"Count")
            if cnt is not None and cnt.props:
                if delta:
                    cnt.props[0] = max(0, int(cnt.props[0]) + delta)
                total = int(cnt.props[0])
    if delta:
        cnt = defs.find(b"Count")
        if cnt is not None and cnt.props:
            cnt.props[0] = max(0, int(cnt.props[0]) + delta)
    return total


def new_id(root):
    used = {object_id(e) for e in objects(root) if e.props}
    rng = random.Random()
    while True:
        candidate = rng.randrange(1 << 20, 1 << 62)
        if candidate not in used:
            return candidate


def remove_object(root, oid):
    """Delete an object element and every connection that references it."""
    sec = root.find(b"Objects")
    removed = 0
    if sec is not None:
        before = len(sec.elems)
        sec.elems = [e for e in sec.elems if not (e.props and e.props[0] == oid)]
        removed = before - len(sec.elems)
    remove_connections(root, lambda c: c.props[1] == oid or c.props[2] == oid)
    return removed


def global_settings(root):
    return root.find(b"GlobalSettings")


def global_setting_values(root, name):
    gs = global_settings(root)
    return prop_values(gs, name) if gs is not None else None


def anim_stacks(root):
    return find_objects(root, elem_id=b"AnimationStack")


def model_names(root, subclass=None):
    return [object_name(m).decode("utf-8", "replace") for m in find_objects(root, elem_id=b"Model", subclass=subclass)]
