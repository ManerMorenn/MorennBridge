# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers that set up Unreal conventions in the scene: sockets, collision primitives, LOD chains, roles."""

import bmesh
import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from mathutils import Matrix, Vector

from ..core import naming
from ..props import COLLISION_TYPE_ITEMS, ROLE_ITEMS


def _active_mesh(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == "MESH" else None


def _link_like(obj, like):
    colls = like.users_collection or [bpy.context.scene.collection]
    for c in colls:
        c.objects.link(obj)


def _bounds(obj):
    pts = [Vector(c) for c in obj.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


class MORENN_OT_add_socket(bpy.types.Operator):
    """Add a SOCKET_ empty parented to the active mesh (exported as an Unreal socket)"""

    bl_idname = "morenn.add_socket"
    bl_label = "Add Socket"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Socket name", default="Socket")

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        mesh = _active_mesh(context)
        empty = bpy.data.objects.new(naming.socket_node_name(naming.sanitize_ue_name(self.name)), None)
        empty.empty_display_type = "ARROWS"
        empty.empty_display_size = 10.0
        _link_like(empty, mesh)
        empty.parent = mesh
        empty.matrix_parent_inverse = Matrix.Identity(4)
        empty.location = (0.0, 0.0, 0.0)
        empty.morenn.role = "SOCKET"
        return {"FINISHED"}


class MORENN_OT_add_collision(bpy.types.Operator):
    """Add a UCX/UBX/USP/UCP collision primitive fitted to the active mesh's bounds"""

    bl_idname = "morenn.add_collision"
    bl_label = "Add Collision"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Type", items=COLLISION_TYPE_ITEMS, default="UBX")
    padding: FloatProperty(name="Padding", default=0.0)

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None

    def execute(self, context):
        mesh = _active_mesh(context)
        lo, hi = _bounds(mesh)
        lo -= Vector((self.padding,) * 3)
        hi += Vector((self.padding,) * 3)
        size = hi - lo
        center = (lo + hi) * 0.5
        bm = bmesh.new()
        if self.kind == "UBX":
            bmesh.ops.create_cube(bm, size=1.0)
            bmesh.ops.scale(bm, vec=size, verts=bm.verts)
        elif self.kind == "USP":
            bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=max(size) * 0.5)
        elif self.kind == "UCP":
            radius = max(size.x, size.y) * 0.5
            height = max(size.z - 2 * radius, 0.0)
            bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=radius)
            for v in bm.verts:
                v.co.z += height * 0.5 if v.co.z >= 0 else -height * 0.5
        else:  # UCX: convex hull of the mesh
            for v in mesh.data.vertices:
                bm.verts.new(v.co)
            bm.verts.ensure_lookup_table()
            hull = bmesh.ops.convex_hull(bm, input=bm.verts)
            bmesh.ops.delete(
                bm, geom=[g for g in hull["geom_interior"] if isinstance(g, bmesh.types.BMVert)], context="VERTS"
            )
            center = Vector((0, 0, 0))
        bmesh.ops.translate(bm, vec=center, verts=bm.verts)
        existing = [o for o in bpy.data.objects if o.morenn.collision_of == mesh or o.parent == mesh]
        index = 1 + sum(1 for o in existing if naming.parse_collision_name(o.name))
        me = bpy.data.meshes.new(f"{self.kind}_{mesh.name}_{index:02d}")
        bm.to_mesh(me)
        bm.free()
        obj = bpy.data.objects.new(me.name, me)
        _link_like(obj, mesh)
        obj.parent = mesh
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.display_type = "WIRE"
        obj.hide_render = True
        obj.morenn.role = "COLLISION"
        obj.morenn.collision_type = self.kind
        obj.morenn.collision_of = mesh
        return {"FINISHED"}


class MORENN_OT_make_lod_chain(bpy.types.Operator):
    """Turn the selected meshes into a LOD chain of the active mesh (sorted by name = LOD order)"""

    bl_idname = "morenn.make_lod_chain"
    bl_label = "Make LOD Chain"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_mesh(context) is not None and len(context.selected_objects) > 1

    def execute(self, context):
        lod0 = _active_mesh(context)
        others = sorted([o for o in context.selected_objects if o != lod0 and o.type == "MESH"], key=lambda o: o.name)
        lod0.morenn.role = "RENDER"
        lod0.morenn.lod_index = 0
        for i, obj in enumerate(others, start=1):
            obj.morenn.role = "LOD"
            obj.morenn.lod_index = i
            obj.morenn.lod_of = lod0
        self.report({"INFO"}, f"{len(others)} LOD(s) attached to {lod0.name}")
        return {"FINISHED"}


class MORENN_OT_set_role(bpy.types.Operator):
    """Set the export role of the selected objects"""

    bl_idname = "morenn.set_role"
    bl_label = "Set Role"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(name="Role", items=ROLE_ITEMS, default="AUTO")

    def execute(self, context):
        for obj in context.selected_objects:
            obj.morenn.role = self.role
        return {"FINISHED"}


class MORENN_OT_convert_bone_axes(bpy.types.Operator):
    """Re-orient the active armature's bones between conventions (exact; animations are converted too)"""

    bl_idname = "morenn.convert_bone_axes"
    bl_label = "Convert Bone Axes"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(
        name="Direction",
        items=[
            ("TO_BLENDER", "Unreal (X along bone) → Blender (Y along bone)", ""),
            ("TO_UNREAL", "Blender (Y along bone) → Unreal (X along bone)", ""),
        ],
        default="TO_BLENDER",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "ARMATURE" and context.mode == "OBJECT"

    def execute(self, context):
        from ..core import bones
        from ..prefs import get_prefs

        arm = context.active_object
        prefs = get_prefs(context)
        m = arm.data.morenn
        default = (
            prefs.bone_primary if m.bone_primary == "DEFAULT" else m.bone_primary,
            prefs.bone_secondary if m.bone_secondary == "DEFAULT" else m.bone_secondary,
        )
        if default == ("Y", "X"):
            default = ("X", "Y")
        if self.direction == "TO_BLENDER":
            axes_map = bones.detect_bone_axes(arm, default, tolerance_deg=prefs.import_axis_tolerance)
            K = bones.matrices_for(axes_map)
            bones.store_bone_axes(arm, axes_map, default)
            m.bone_primary, m.bone_secondary = default
            m.axes_converted = True
        else:
            axes_map = bones.axes_map_from_stored(arm, default)
            K = {name: C for name, C in bones.matrices_for(axes_map, inverse=True).items()}
            bones.clear_bone_axes(arm)
            m.axes_converted = False
        bones.convert_rest_axes(context, arm, K)
        bones.conjugate_pose(arm, K)
        names = {b.name for b in arm.data.bones}
        actions = set()
        ad = arm.animation_data
        if ad is not None:
            if ad.action is not None:
                actions.add(ad.action)
            for t in ad.nla_tracks:
                for s in t.strips:
                    if s.action is not None:
                        actions.add(s.action)
        for action in actions:
            bones.conjugate_bone_fcurves(action, K)
        self.report({"INFO"}, f"{len(names)} bones converted, {len(actions)} action(s) updated")
        return {"FINISHED"}


classes = (
    MORENN_OT_add_socket,
    MORENN_OT_add_collision,
    MORENN_OT_make_lod_chain,
    MORENN_OT_set_role,
    MORENN_OT_convert_bone_axes,
)
