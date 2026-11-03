# SPDX-License-Identifier: GPL-3.0-or-later
"""Patches applied to exported / imported FBX files (binary level)."""

from . import inspect as I
from .tree import Elem, join_name_class, split_name_class

IDENTITY_TOL = 1e-6


class PatchResult:
    def __init__(self, changed=False, notes=None):
        self.changed = changed
        self.notes = list(notes or [])

    def note(self, text):
        self.notes.append(text)
        return self

    def __bool__(self):
        return self.changed


def _b(s):
    return s if isinstance(s, bytes) else str(s).encode("utf-8")


# ---------------------------------------------------------------------- renaming

RENAME_CLASSES = (b"Model", b"Geometry", b"NodeAttribute", b"Deformer")


def rename_nodes(root, mapping, classes=RENAME_CLASSES):
    """Rename objects whose name (not class) is a key of ``mapping``. Returns PatchResult."""
    mapping_b = {_b(k): _b(v) for k, v in mapping.items()}
    result = PatchResult()
    for elem in I.objects(root):
        if elem.id not in classes or len(elem.props) < 2:
            continue
        name, cls = split_name_class(elem.props[1])
        if name in mapping_b:
            elem.props[1] = join_name_class(mapping_b[name], cls)
            result.changed = True
    return result


def rename_anim_stack(root, new_name):
    """Rename every AnimationStack / AnimationLayer and the matching ``Takes`` entries."""
    new_b = _b(new_name)
    result = PatchResult()
    for elem in I.objects(root):
        if elem.id in (b"AnimationStack", b"AnimationLayer") and len(elem.props) > 1:
            _, cls = split_name_class(elem.props[1])
            elem.props[1] = join_name_class(new_b, cls)
            result.changed = True
    takes = root.find(b"Takes")
    if takes is not None:
        for take in takes.find_all(b"Take"):
            if take.props:
                take.props[0] = new_b
            fn = take.find(b"FileName")
            if fn is not None and fn.props:
                fn.props[0] = new_b + b".tak"
    return result


# ---------------------------------------------------------------------- global settings marker


def set_globalsettings_string(root, name, value):
    gs = I.global_settings(root)
    if gs is None:
        return PatchResult()
    I.set_prop(gs, _b(name), b"KString", b"", b"", _b(value))
    return PatchResult(True)


def get_globalsettings_string(root, name):
    values = I.global_setting_values(root, _b(name))
    if not values:
        return None
    v = values[0]
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)


# ---------------------------------------------------------------------- armature null


def _lcl(elem, name, default):
    values = I.prop_values(elem, name)
    return [float(v) for v in values[:3]] if values else list(default)


def is_identity_model(elem):
    t = _lcl(elem, b"Lcl Translation", (0, 0, 0))
    r = _lcl(elem, b"Lcl Rotation", (0, 0, 0))
    s = _lcl(elem, b"Lcl Scaling", (1, 1, 1))
    return all(abs(v) <= IDENTITY_TOL for v in t + r) and all(abs(v - 1.0) <= IDENTITY_TOL for v in s)


def strip_armature_null(root, name="Armature"):
    """Remove the identity ``Null`` model called ``name`` that sits under the scene root.

    Its children are re-parented to the scene root (keeping order); its node attribute, animation curve
    nodes (+ curves), pose entries and connections are deleted; Definitions counts are updated.
    """
    result = PatchResult()
    candidates = [m for m in I.find_objects(root, b"Model", name=_b(name), subclass=b"Null")]
    for model in candidates:
        mid = I.object_id(model)
        if not any(p[2] == 0 for p in I.parents_of(root, mid, b"OO")):
            result.note(f"'{name}' is not a scene-root child; skipped")
            continue
        if not is_identity_model(model):
            result.note(f"'{name}' has a non-identity transform; skipped")
            continue

        removed_curve_nodes = removed_curves = removed_attrs = 0
        # animation: OP connections (curve node -> model)
        for kind, child, parent, prop, _elem in list(I.connections(root)):
            if parent != mid and child != mid:
                continue
            if kind == b"OP" and parent == mid:
                node = I.object_by_id(root, child)
                if node is not None and node.id == b"AnimationCurveNode":
                    for k2, curve_id, p2, _pr, _e in list(I.connections(root)):
                        if k2 == b"OP" and p2 == child:
                            curve = I.object_by_id(root, curve_id)
                            if curve is not None and curve.id == b"AnimationCurve":
                                I.remove_object(root, curve_id)
                                removed_curves += 1
                    I.remove_object(root, child)
                    removed_curve_nodes += 1
            elif kind == b"OO" and parent == mid:
                attr = I.object_by_id(root, child)
                if attr is not None and attr.id == b"NodeAttribute":
                    I.remove_object(root, child)
                    removed_attrs += 1
        # re-parent remaining children to the scene root
        for c in I.connections_elem(root).elems:
            if c.id == b"C" and len(c.props) >= 3 and c.props[0] == b"OO" and c.props[2] == mid:
                c.props[2] = 0
        # pose nodes
        for pose in I.find_objects(root, b"Pose"):
            nodes = pose.find_all(b"PoseNode")
            for pn in nodes:
                node_ref = pn.find(b"Node")
                if node_ref is not None and node_ref.props and node_ref.props[0] == mid:
                    pose.remove(pn)
                    nb = pose.find(b"NbPoseNodes")
                    if nb is not None and nb.props:
                        nb.props[0] = int(nb.props[0]) - 1
        I.remove_object(root, mid)
        I.definition_count(root, b"Model", -1)
        if removed_attrs:
            I.definition_count(root, b"NodeAttribute", -removed_attrs)
        if removed_curve_nodes:
            I.definition_count(root, b"AnimationCurveNode", -removed_curve_nodes)
        if removed_curves:
            I.definition_count(root, b"AnimationCurve", -removed_curves)
        result.changed = True
        result.note(
            f"removed '{name}' null (attrs {removed_attrs}, curve nodes {removed_curve_nodes}, curves {removed_curves})"
        )
    return result


# ---------------------------------------------------------------------- root bones (import pre-pass)


def retype_root_bones(root):
    """Turn ``Root`` skeleton attributes/models into ``LimbNode`` so Blender imports them as bones."""
    result = PatchResult()
    for attr in I.find_objects(root, b"NodeAttribute", subclass=b"Root"):
        attr.props[2] = b"LimbNode"
        aid = I.object_id(attr)
        for kind, child, parent, _prop, _e in I.connections(root):
            if kind == b"OO" and child == aid:
                model = I.object_by_id(root, parent)
                if model is not None and model.id == b"Model" and I.object_subclass(model) == b"Root":
                    model.props[2] = b"LimbNode"
                    result.note(I.object_name(model).decode("utf-8", "replace"))
        result.changed = True
    return result


def root_bone_names(root):
    """Names of ``Root``-typed skeleton models (before retyping)."""
    names = []
    for attr in I.find_objects(root, b"NodeAttribute", subclass=b"Root"):
        aid = I.object_id(attr)
        for kind, child, parent, _prop, _e in I.connections(root):
            if kind == b"OO" and child == aid:
                model = I.object_by_id(root, parent)
                if model is not None:
                    names.append(I.object_name(model).decode("utf-8", "replace"))
    return names


# ---------------------------------------------------------------------- LOD groups


def _lcl_identity_props(elem):
    I.set_prop(
        elem,
        b"Lcl Translation",
        b"Lcl Translation",
        b"",
        b"A",
        0.0,
        0.0,
        0.0,
        type_codes=(ord("D"), ord("D"), ord("D")),
    )
    I.set_prop(
        elem, b"Lcl Rotation", b"Lcl Rotation", b"", b"A", 0.0, 0.0, 0.0, type_codes=(ord("D"), ord("D"), ord("D"))
    )
    I.set_prop(
        elem, b"Lcl Scaling", b"Lcl Scaling", b"", b"A", 1.0, 1.0, 1.0, type_codes=(ord("D"), ord("D"), ord("D"))
    )


def _model_like(root, name, subclass, template):
    """A new Model at identity; its other children (version, shading, culling) are copied from ``template``."""
    mid = I.new_id(root)
    model = Elem(b"Model")
    model.add_int64(mid).add_string(join_name_class(_b(name), b"Model")).add_string(subclass)
    for child in template.elems:
        if child.id != b"Properties70":
            model.add(child.copy())
    _lcl_identity_props(model)
    return mid, model


def inject_lod_groups(root, specs, thresholds_percent=None):
    """``specs``: list of ``(group_name, levels)``. A level is a node name, or ``(null_name, [node names])`` for a
    level made of several meshes, which then sit under a new null.

    Creates a ``LodGroup`` model + attribute per spec and re-parents the levels under it in LOD order: both Unreal
    importers take the child index as the LOD index.
    """
    result = PatchResult()
    models = {I.object_name(m): m for m in I.find_objects(root, b"Model")}
    objects = root.find(b"Objects")
    for group_name, levels in specs:
        levels = [(None, [level]) if isinstance(level, str) else (level[0], list(level[1])) for level in levels]
        level_models = [[models.get(_b(n)) for n in names] for _null, names in levels]
        if len(levels) < 2 or any(not ms or None in ms for ms in level_models):
            result.note(f"LOD group '{group_name}': nodes missing or fewer than two LODs; skipped")
            continue
        lod0 = level_models[0][0]
        gid, group = _model_like(root, group_name, b"LodGroup", lod0)
        aid = I.new_id(root)
        # without it FbxNode::GetNodeAttribute() is null and the legacy importer imports each LOD as a mesh
        I.set_prop(group, b"DefaultAttributeIndex", b"int", b"Integer", b"", 0, type_codes=(ord("I"),))

        attr = Elem(b"NodeAttribute")
        attr.add_int64(aid).add_string(join_name_class(_b(group_name) + b"_LodGroup", b"NodeAttribute"))
        attr.add_string(b"LodGroup")
        I.set_prop(attr, b"MinMaxDistance", b"bool", b"", b"", 0, type_codes=(ord("I"),))
        I.set_prop(attr, b"MinDistance", b"double", b"Number", b"", -100.0, type_codes=(ord("D"),))
        I.set_prop(attr, b"MaxDistance", b"double", b"Number", b"", 100.0, type_codes=(ord("D"),))
        I.set_prop(attr, b"WorldSpace", b"bool", b"", b"", 0, type_codes=(ord("I"),))
        I.set_prop(attr, b"ThresholdsUsedAsPercentage", b"bool", b"", b"", 1, type_codes=(ord("I"),))
        I.set_prop(attr, b"Thresholds", b"Compound", b"", b"")
        n = len(levels)
        percents = thresholds_percent or [max(1.0, 100.0 / (2 ** (i + 1))) for i in range(n - 1)]
        for i in range(n - 1):
            I.set_prop(
                attr,
                f"Thresholds|Level{i}".encode(),
                b"Distance",
                b"",
                b"A",
                float(percents[i]),
                b"cm",
                type_codes=(ord("D"), ord("S")),
            )
        I.set_prop(attr, b"DisplayLevels", b"Compound", b"", b"")
        for i in range(n):
            I.set_prop(attr, f"DisplayLevels|Level{i}".encode(), b"enum", b"", b"", 0, type_codes=(ord("I"),))

        insert_at = objects.elems.index(lod0)
        objects.elems.insert(insert_at, attr)
        objects.elems.insert(insert_at, group)

        # the members leave their parent; the levels are connected to the group in LOD order
        members = {I.object_id(m) for ms in level_models for m in ms}
        conns = I.connections_elem(root)
        parent_links = [
            idx
            for idx, c in enumerate(conns.elems)
            if c.id == b"C" and len(c.props) >= 3 and c.props[0] == b"OO" and c.props[1] in members
        ]
        first_index = parent_links[0] if parent_links else len(conns.elems)
        for idx in reversed(parent_links):
            del conns.elems[idx]
        links = [(aid, gid), (gid, 0)]
        nulls = []
        for (null_name, _names), ms in zip(levels, level_models):
            ids = [I.object_id(m) for m in ms]
            if null_name is None:
                links.append((ids[0], gid))
                continue
            nid, null = _model_like(root, null_name, b"Null", lod0)
            nulls.append(null)
            links.append((nid, gid))
            links.extend((mid, nid) for mid in ids)
        objects.elems[insert_at + 2 : insert_at + 2] = nulls
        for offset, (child, parent) in enumerate(links):
            I.add_connection(root, child, parent, b"OO", index=first_index + offset)
        I.definition_count(root, b"Model", +1 + len(nulls))
        I.definition_count(root, b"NodeAttribute", +1)
        result.changed = True
        result.note(f"LOD group '{group_name}' with {n} levels")
    return result


# ---------------------------------------------------------------------- queries used by the importer


def model_tree(root):
    """``{model_id: (name, subclass, parent_id)}`` for every Model."""
    out = {}
    parents = {}
    for kind, child, parent, _prop, _e in I.connections(root):
        if kind == b"OO":
            parents.setdefault(child, parent)
    for m in I.find_objects(root, b"Model"):
        mid = I.object_id(m)
        out[mid] = (I.object_name(m).decode("utf-8", "replace"), I.object_subclass(m).decode(), parents.get(mid, 0))
    return out


def lod_levels(root):
    """``{node name: LOD index}`` for the models under each LodGroup: a level's node and everything below it.

    The index is the child position, as in both Unreal importers.
    """
    tree = model_tree(root)
    kids = {}
    for kind, child, parent, _prop, _e in I.connections(root):
        if kind == b"OO" and child in tree and parent in tree:
            kids.setdefault(parent, []).append(child)
    out = {}
    for mid, (_name, sub, _parent) in tree.items():
        if sub != "LodGroup":
            continue
        for level, child in enumerate(kids.get(mid, [])):
            stack = [child]
            while stack:
                node = stack.pop()
                out[tree[node][0]] = level
                stack.extend(kids.get(node, []))
    return out


def lod_groups(root):
    """``[(group_name, [child names in connection order])]`` for every LodGroup model."""
    tree = model_tree(root)
    groups = []
    for mid, (name, sub, _parent) in tree.items():
        if sub != "LodGroup":
            continue
        children = []
        for kind, child, parent, _prop, _e in I.connections(root):
            if kind == b"OO" and parent == mid and child in tree:
                children.append(tree[child][0])
        groups.append((name, children))
    return groups
