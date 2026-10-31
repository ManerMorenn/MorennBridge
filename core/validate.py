# SPDX-License-Identifier: GPL-3.0-or-later
"""Pre-flight checks run before any export, with optional one-click fixers."""

from dataclasses import dataclass

import bpy
from mathutils import Matrix

from .. import compat
from . import classify, naming, units
from .classify import KIND_SKELETAL, KIND_STATIC


@dataclass
class Issue:
    code: str
    severity: str  # 'ERROR' | 'WARNING' | 'INFO'
    message: str
    object_name: str = ""
    bone_name: str = ""
    fix_id: str = ""
    fix_label: str = ""

    def __str__(self):
        return f"{self.code} {self.severity}: {self.message}"


FIXERS = {}


def fixer(fix_id, label):
    def deco(fn):
        FIXERS[fix_id] = (label, fn)
        return fn

    return deco


def _issue(issues, code, severity, message, obj=None, bone="", fix_id=""):
    label = FIXERS[fix_id][0] if fix_id in FIXERS else ""
    issues.append(Issue(code, severity, message, obj.name if obj is not None else "", bone, fix_id, label))


# ---------------------------------------------------------------------- checks


def _check_mesh_object(issues, obj, kind, prefs, exported_bones=None):
    me = obj.data
    det = obj.matrix_world.determinant()
    if det < 0:
        _issue(
            issues,
            "E003",
            "ERROR",
            f"'{obj.name}' has a mirrored (negative) scale; normals would flip",
            obj,
            fix_id="apply_scale",
        )
    elif kind == KIND_STATIC:
        s = obj.matrix_world.to_scale()
        if max(s) - min(s) > 1e-4:
            _issue(
                issues,
                "W003",
                "WARNING",
                f"'{obj.name}' has non-uniform scale {tuple(round(v, 3) for v in s)}; baked into the mesh",
                obj,
            )
    if len(me.polygons) == 0:
        _issue(issues, "E019", "ERROR", f"'{obj.name}' has no faces", obj)
    if not me.uv_layers:
        _issue(
            issues,
            "W005",
            "WARNING",
            f"'{obj.name}' has no UV map (no tangents, lightmap UVs must be generated in UE)",
            obj,
        )
    if len(me.uv_layers) > 8:
        _issue(issues, "W018", "WARNING", f"'{obj.name}' has {len(me.uv_layers)} UV maps; Unreal keeps 8", obj)
    if len(me.color_attributes) > 1:
        _issue(
            issues,
            "W018",
            "WARNING",
            f"'{obj.name}' has several color attributes; only the active one is exported",
            obj,
        )
    if any(len(p.vertices) > 4 for p in me.polygons):
        _issue(issues, "I006", "INFO", f"'{obj.name}' has n-gons; triangulated at export", obj)
    if not me.materials or any(m is None for m in me.materials):
        _issue(
            issues, "W007", "WARNING", f"'{obj.name}' has an empty material slot", obj, fix_id="assign_default_material"
        )
    if me.shape_keys is not None and len(me.shape_keys.key_blocks) > 1:
        topo_mods = [
            m
            for m in obj.modifiers
            if m.type
            not in {"ARMATURE", "TRIANGULATE", "WEIGHTED_NORMAL", "DATA_TRANSFER", "NORMAL_EDIT", "SMOOTH", "DISPLACE"}
            and (m.show_viewport or m.show_render)
        ]
        if topo_mods:
            _issue(
                issues,
                "W012",
                "WARNING",
                f"'{obj.name}' has shape keys and modifiers ({', '.join(m.name for m in topo_mods)}); export fails if a modifier changes the vertex count",
                obj,
            )
    if kind == KIND_SKELETAL and exported_bones is not None:
        limit = prefs.influence_limit
        over = unweighted = 0
        names = {vg.index: vg.name for vg in obj.vertex_groups}
        for v in me.vertices:
            ws = [g.weight for g in v.groups if g.weight > 0 and names.get(g.group) in exported_bones]
            if len(ws) > limit:
                over += 1
            if not ws:
                unweighted += 1
        if over:
            _issue(
                issues,
                "I009",
                "INFO",
                f"'{obj.name}': {over} vertices exceed {limit} bone influences; weakest are dropped at export",
                obj,
            )
        if unweighted:
            _issue(
                issues, "W021", "WARNING", f"'{obj.name}': {unweighted} vertices have no weight on exported bones", obj
            )
        foreign = sorted(
            {
                names[g.group]
                for v in me.vertices
                for g in v.groups
                if g.weight > 0 and names.get(g.group) not in exported_bones and names.get(g.group) is not None
            }
        )
        if foreign:
            _issue(
                issues,
                "W014",
                "WARNING",
                f"'{obj.name}': weights on non-exported bones merged into ancestors: {', '.join(foreign[:6])}",
                obj,
            )


def _check_armature(issues, arm, prefs):
    from .export_fbx import bone_closure, export_bone_names

    exported = bone_closure(arm, export_bone_names(arm))
    roots = [b.name for b in arm.data.bones if b.parent is None and b.name in exported]
    if len(roots) > 1:
        _issue(
            issues,
            "E001",
            "ERROR",
            f"'{arm.name}' has {len(roots)} root bones ({', '.join(roots[:4])}); Unreal needs one",
            arm,
            fix_id="add_root_bone",
        )
    elif not roots:
        _issue(issues, "E001", "ERROR", f"'{arm.name}' has no exportable bones", arm)
    ad = arm.animation_data
    if ad is not None and (ad.action is not None or any(t.strips for t in ad.nla_tracks)):
        animated = False
        if ad.action is not None:
            for _slot, fc in compat.iter_fcurves(ad.action):
                if not fc.data_path.startswith("pose.bones"):
                    animated = True
                    break
        if animated or any(t.strips for t in ad.nla_tracks):
            _issue(
                issues,
                "E002",
                "ERROR",
                f"'{arm.name}' object itself is animated; Unreal ignores it (put root motion on the root bone)",
                arm,
            )
    s = arm.matrix_world.to_scale()
    if max(s) - min(s) > 1e-4:
        _issue(
            issues, "E013", "ERROR", f"'{arm.name}' has non-uniform scale; apply it first", arm, fix_id="apply_scale"
        )
    if arm.matrix_world.determinant() < 0:
        _issue(issues, "E003", "ERROR", f"'{arm.name}' has a mirrored (negative) scale", arm, fix_id="apply_scale")
    bad = [b.name for b in arm.data.bones if b.name in exported and not naming.is_ue_safe(b.name)]
    if bad:
        _issue(
            issues,
            "E010",
            "ERROR",
            f"'{arm.name}': {len(bad)} bone names will be rewritten by Unreal ({bad[0]} -> {naming.sanitize_ue_name(bad[0])}); rename them for exact round trips",
            arm,
            bone=bad[0],
            fix_id="rename_bones_ue_safe",
        )
    for child in arm.children:
        if child.type == "MESH" and child.parent_type == "BONE" and classify.armature_of(child) is None:
            _issue(
                issues,
                "E004",
                "ERROR",
                f"'{child.name}' is parented to bone '{child.parent_bone}' without skinning; it would become a bone in Unreal",
                child,
                bone=child.parent_bone,
                fix_id="rigid_bind",
            )
    return exported


def run(context, groups, prefs):
    """Return the list of Issues for ``groups`` (plus scene-level checks)."""
    issues = []
    scene = context.scene
    if not units.is_cm_scene(scene):
        _issue(
            issues,
            "W016",
            "WARNING",
            f"Scene unit scale is {units.unit_factor(scene):g} cm per unit; export normalises to centimetres",
            fix_id="scene_setup",
        )
    dupes = naming.unique([g.asset_name for g in groups])
    if dupes:
        _issue(issues, "E008", "ERROR", f"Duplicate asset names: {', '.join(dupes)}")
    for g in groups:
        base_from_name = naming.sanitize_asset_name(naming.strip_numeric_suffix(g.source_object.name))
        if (
            g.source_object is not None
            and g.source_object.name != base_from_name
            and not g.source_object.morenn.export_name
        ):
            src = g.source_object
            if not naming.is_ue_safe(src.name) or ".0" in src.name:
                _issue(issues, "I011", "INFO", f"'{src.name}' exports as '{g.asset_name}'", src)
        if g.kind == KIND_SKELETAL:
            exported = _check_armature(issues, g.armature, prefs)
            if not g.render:
                _issue(
                    issues,
                    "W017",
                    "WARNING",
                    f"'{g.armature.name}' has no skinned meshes; only the skeleton is exported",
                    g.armature,
                )
            for obj in g.render:
                _check_mesh_object(issues, obj, KIND_SKELETAL, prefs, exported)
        else:
            for obj in g.render:
                _check_mesh_object(issues, obj, KIND_STATIC, prefs)
            for objs in g.lods.values():
                for obj in objs:
                    if obj.matrix_world.determinant() < 0:
                        _issue(issues, "E003", "ERROR", f"'{obj.name}' has a mirrored scale", obj, fix_id="apply_scale")
            for obj, _kind in g.collisions:
                if obj.matrix_world.determinant() < 0:
                    _issue(issues, "E003", "ERROR", f"'{obj.name}' has a mirrored scale", obj, fix_id="apply_scale")
    return issues


def has_errors(issues):
    return any(i.severity == "ERROR" for i in issues)


def store(scene, issues):
    coll = scene.morenn.issues
    coll.clear()
    for i in issues:
        item = coll.add()
        item.code, item.severity, item.message = i.code, i.severity, i.message
        item.object_name, item.bone_name, item.fix_id, item.fix_label = (
            i.object_name,
            i.bone_name,
            i.fix_id,
            i.fix_label,
        )
    scene.morenn.issues_index = 0


# ---------------------------------------------------------------------- fixers


def _object(name):
    return bpy.data.objects.get(name)


@fixer("apply_scale", "Apply scale")
def fix_apply_scale(context, issue):
    obj = _object(issue.object_name)
    if obj is None:
        return False
    with context.temp_override(
        active_object=obj,
        object=obj,
        selected_objects=[obj],
        selected_editable_objects=[obj],
        **compat.window_override(),
    ):
        return "FINISHED" in bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


@fixer("assign_default_material", "Assign M_Default")
def fix_assign_default_material(context, issue):
    obj = _object(issue.object_name)
    if obj is None or obj.type != "MESH":
        return False
    mat = bpy.data.materials.get("M_Default") or bpy.data.materials.new("M_Default")
    me = obj.data
    if not me.materials:
        me.materials.append(mat)
    for i, m in enumerate(me.materials):
        if m is None:
            me.materials[i] = mat
    return True


@fixer("rename_bones_ue_safe", "Rename bones like Unreal would")
def fix_rename_bones(context, issue):
    arm = _object(issue.object_name)
    if arm is None or arm.type != "ARMATURE":
        return False
    renamed = 0
    for bone in arm.data.bones:
        safe = naming.sanitize_ue_name(bone.name)
        if safe != bone.name:
            if arm.data.bones.get(safe) is not None:
                safe = naming.sanitize_asset_name(safe) + "_" + str(renamed)
            bone.name = safe
            renamed += 1
    return renamed > 0


@fixer("add_root_bone", "Add a single 'root' bone")
def fix_add_root_bone(context, issue):
    arm = _object(issue.object_name)
    if arm is None or arm.type != "ARMATURE":
        return False
    name = "root" if arm.data.bones.get("root") is None else "root_bone"
    with context.temp_override(active_object=arm, object=arm, selected_objects=[arm], **compat.window_override()):
        bpy.ops.object.mode_set(mode="EDIT")
        eb = arm.data.edit_bones.new(name)
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, 10.0 * (1.0 if units.is_cm_scene(context.scene) else 0.01), 0.0)
        for other in arm.data.edit_bones:
            if other != eb and other.parent is None:
                other.parent = eb
                other.use_connect = False
        bpy.ops.object.mode_set(mode="OBJECT")
    return True


@fixer("rigid_bind", "Convert bone parenting to skinning")
def fix_rigid_bind(context, issue):
    obj = _object(issue.object_name)
    if obj is None or obj.type != "MESH" or obj.parent is None or obj.parent_type != "BONE":
        return False
    arm = obj.parent
    bone = obj.parent_bone
    world = obj.matrix_world.copy()
    obj.parent = arm
    obj.parent_type = "OBJECT"
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.matrix_world = world
    vg = obj.vertex_groups.get(bone) or obj.vertex_groups.new(name=bone)
    vg.add([v.index for v in obj.data.vertices], 1.0, "REPLACE")
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    return True


@fixer("scene_setup", "Set up scene for Unreal")
def fix_scene_setup(context, issue):
    return "FINISHED" in bpy.ops.morenn.scene_setup()
