# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a temporary scene holding export-ready copies of the user's objects.

Nothing in the user's scene is modified permanently: every temporary change is journaled and undone,
every created datablock is removed, even when an exception is raised.
"""

import uuid

import bpy
import numpy as np
from mathutils import Matrix

from . import units

TEMP_PREFIX = "MB__"


class StagingError(RuntimeError):
    pass


class Staging:
    """``with Staging(context, report) as st: ...``: temp scene + collection + copies."""

    def __init__(self, context, report, fps=None, frame_range=None, scene_name=None):
        self.context = context
        self.report = report
        self.src_scene = context.scene
        self.U = units.unit_factor(self.src_scene)
        self.S_U = Matrix.Scale(self.U, 4)
        self.fps = fps
        self.frame_range = frame_range
        self.scene_name = scene_name or "MB_EXPORT"
        self.temp_scene = None
        self.coll = None
        self.objects = []  # created objects (temp scene only)
        self.datablocks = []  # created meshes / armatures / actions / curves
        self.journal = []  # callables undoing temporary changes to user data
        self.name_map = {}  # temp name -> final FBX node name
        self._counter = 0
        self._src_frame = self.src_scene.frame_current

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self):
        # object matrices are only up to date after an evaluation of the user's view layer
        self.context.view_layer.update()
        scene = bpy.data.scenes.new(self.scene_name)
        self.temp_scene = scene
        units.set_cm_units(scene)
        src = self.src_scene
        scene.render.fps = src.render.fps if self.fps is None else int(self.fps)
        scene.render.fps_base = src.render.fps_base if self.fps is None else 1.0
        if self.frame_range is not None:
            scene.frame_start, scene.frame_end = int(self.frame_range[0]), int(self.frame_range[1])
        self.coll = bpy.data.collections.new(f"MB_EXPORT_{uuid.uuid4().hex[:8]}")
        scene.collection.children.link(self.coll)
        return self

    def __exit__(self, exc_type, exc, tb):
        for undo in reversed(self.journal):
            try:
                undo()
            except (RuntimeError, ReferenceError, AttributeError) as exc:
                self.report.warning(f"Could not undo a temporary change: {exc!r}")
        self.journal.clear()
        for obj in self.objects:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except (RuntimeError, ReferenceError):
                pass
        self.objects.clear()
        for block in self.datablocks:
            try:
                coll = _collection_for(block)
                if coll is not None and block.users == 0:
                    coll.remove(block)
            except (RuntimeError, ReferenceError):
                pass
        self.datablocks.clear()
        if self.coll is not None:
            try:
                bpy.data.collections.remove(self.coll)
            except (RuntimeError, ReferenceError):
                pass
        if self.temp_scene is not None:
            try:
                bpy.data.scenes.remove(self.temp_scene)
            except (RuntimeError, ReferenceError):
                pass
        try:
            if self.src_scene.frame_current != self._src_frame:
                self.src_scene.frame_set(self._src_frame)
        except (RuntimeError, ReferenceError):
            pass
        return False

    def temp_name(self):
        self._counter += 1
        return f"{TEMP_PREFIX}{self._counter:03d}_{uuid.uuid4().hex[:6]}"

    def override(self):
        """Keywords for ``bpy.context.temp_override`` so operators act on the temp scene."""
        kw = {"scene": self.temp_scene, "view_layer": self.temp_scene.view_layers[0]}
        from .. import compat

        kw.update(compat.window_override())
        return kw

    def depsgraph(self):
        vl = self.temp_scene.view_layers[0]
        vl.update()
        return vl.depsgraph

    # ------------------------------------------------------------------ helpers
    def _new_object(self, data, final_name, role):
        name = self.temp_name()
        obj = bpy.data.objects.new(name, data)
        if data is not None:
            try:
                data.name = name
            except AttributeError:
                pass
        self.coll.objects.link(obj)
        self.objects.append(obj)
        if data is not None:
            self.datablocks.append(data)
        self.name_map[name] = final_name
        obj["morenn_role"] = role
        return obj

    def source_depsgraph(self):
        return self.context.evaluated_depsgraph_get()

    # ------------------------------------------------------------------ meshes
    def evaluate_source_mesh(self, src, triangulate=True, keep_shape_keys=True):
        """Evaluated copy of ``src``'s mesh (modifiers applied, armature deformation excluded).

        Returns ``(mesh, shape_specs)``; shape keys are returned as specs because they can only be
        attached once the mesh belongs to an object.
        """
        if src.type != "MESH":
            raise StagingError(f"'{src.name}' is not a mesh")
        hidden = []
        for mod in src.modifiers:
            if mod.type == "ARMATURE" and (mod.show_viewport or mod.show_render):
                hidden.append((mod, mod.show_viewport, mod.show_render))
                mod.show_viewport = False
                mod.show_render = False
        tri = None
        if triangulate:
            tri = src.modifiers.new("MB_TRIANGULATE", "TRIANGULATE")
            tri.quad_method = "BEAUTY"
            tri.ngon_method = "BEAUTY"
            tri.min_vertices = 4
            if hasattr(tri, "keep_custom_normals"):
                tri.keep_custom_normals = True
        keys = src.data.shape_keys
        saved_keys = None
        if keys is not None and len(keys.key_blocks) > 1 and keep_shape_keys:
            saved_keys = (src.show_only_shape_key, src.active_shape_key_index)
        try:
            dg = self.source_depsgraph()
            if saved_keys is None:
                dg.update()
                me = bpy.data.meshes.new_from_object(src.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
                self.datablocks.append(me)
                return me, []
            src.show_only_shape_key = True
            evaluated = []
            for i, _kb in enumerate(keys.key_blocks):
                src.active_shape_key_index = i
                dg.update()
                m = bpy.data.meshes.new_from_object(src.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
                evaluated.append(m)
            base = evaluated[0]
            self.datablocks.append(base)
            n = len(base.vertices)
            if any(len(m.vertices) != n for m in evaluated[1:]):
                for m in evaluated[1:]:
                    bpy.data.meshes.remove(m)
                raise StagingError(
                    f"'{src.name}': a modifier changes the vertex count, shape keys cannot be preserved (E012)"
                )
            specs = [{"name": keys.key_blocks[0].name, "co": None, "relative": None}]
            for kb, m in zip(keys.key_blocks[1:], evaluated[1:]):
                buf = np.empty(n * 3, dtype=np.float32)
                m.vertices.foreach_get("co", buf)
                specs.append(
                    {
                        "name": kb.name,
                        "co": buf,
                        "slider_min": kb.slider_min,
                        "slider_max": kb.slider_max,
                        "vertex_group": kb.vertex_group,
                        "interpolation": kb.interpolation,
                        "mute": kb.mute,
                        "relative": kb.relative_key.name,
                    }
                )
                bpy.data.meshes.remove(m)
            return base, specs
        finally:
            if tri is not None:
                src.modifiers.remove(tri)
            for mod, sv, sr in hidden:
                mod.show_viewport = sv
                mod.show_render = sr
            if saved_keys is not None:
                src.show_only_shape_key, src.active_shape_key_index = saved_keys

    @staticmethod
    def _apply_shape_specs(obj, specs):
        if not specs:
            return
        obj.shape_key_add(name=specs[0]["name"], from_mix=False)
        for spec in specs[1:]:
            sk = obj.shape_key_add(name=spec["name"], from_mix=False)
            sk.data.foreach_set("co", spec["co"])
            sk.slider_min, sk.slider_max = spec["slider_min"], spec["slider_max"]
            sk.vertex_group = spec["vertex_group"]
            sk.interpolation = spec["interpolation"]
            sk.mute = spec["mute"]
            sk.value = 0.0
        blocks = obj.data.shape_keys.key_blocks
        for spec in specs[1:]:
            rel = blocks.get(spec["relative"]) if spec["relative"] else None
            if rel is not None:
                blocks[spec["name"]].relative_key = rel

    def stage_mesh(
        self,
        src,
        final_name,
        matrix,
        role="RENDER",
        triangulate=True,
        keep_shape_keys=True,
        strip_attributes=False,
        keep_materials=True,
        armature=None,
    ):
        """Copy ``src``'s evaluated mesh into the temp scene, baked by ``matrix`` (object at identity)."""
        me, shape_specs = self.evaluate_source_mesh(src, triangulate=triangulate, keep_shape_keys=keep_shape_keys)
        if strip_attributes:
            while me.uv_layers:
                me.uv_layers.remove(me.uv_layers[0])
            while me.color_attributes:
                me.color_attributes.remove(me.color_attributes[0])
        if not keep_materials:
            me.materials.clear()
        obj = self._new_object(me, final_name, role)
        for vg in src.vertex_groups:
            obj.vertex_groups.new(name=vg.name)
        self._apply_shape_specs(obj, shape_specs)
        me.transform(self.S_U @ matrix, shape_keys=True)
        obj.matrix_world = Matrix.Identity(4)
        if armature is not None:
            mod = obj.modifiers.new("Armature", "ARMATURE")
            mod.object = armature
            mod.use_vertex_groups = True
        return obj

    def duplicate_mesh_object(self, obj, final_name, role):
        me = obj.data.copy()
        self.datablocks.append(me)
        new = self._new_object(me, final_name, role)
        new.matrix_world = obj.matrix_world.copy()
        return new

    def decimate(self, obj, ratio, min_faces):
        """Decimate (collapse) ``obj`` in place through the temp depsgraph. Returns the new face count."""
        if ratio >= 1.0:
            return len(obj.data.polygons)
        original_faces = len(obj.data.polygons)
        mod = obj.modifiers.new("MB_DECIMATE", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        mod.use_symmetry = False
        dg = self.depsgraph()
        me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=False, depsgraph=dg)
        obj.modifiers.remove(mod)
        if len(me.polygons) < min_faces:
            bpy.data.meshes.remove(me)
            self.report.warning(
                f"Collision mesh for '{self.name_map.get(obj.name, obj.name)}' would have fewer than {min_faces} faces "
                f"at ratio {ratio:g}; undecimated copy used (W024)"
            )
            return original_faces
        old = obj.data
        me.name = old.name
        obj.data = me
        self.datablocks.append(me)
        try:
            bpy.data.meshes.remove(old)
            self.datablocks.remove(old)
        except (ValueError, RuntimeError):
            pass
        return len(me.polygons)

    # ------------------------------------------------------------------ empties / sockets
    def stage_empty(self, src, final_name, matrix, parent=None, role="SOCKET"):
        obj = self._new_object(None, final_name, role)
        obj.empty_display_type = src.empty_display_type if src is not None else "PLAIN_AXES"
        obj.empty_display_size = (src.empty_display_size * self.U) if src is not None else 1.0
        obj.matrix_world = self.S_U @ matrix
        if parent is not None:
            obj.parent = parent
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = self.S_U @ matrix
        return obj

    # ------------------------------------------------------------------ armatures
    def stage_armature(self, src, matrix, export_bones, final_name="Armature", pose_position="REST", corrections=None):
        """Copy of ``src`` (armature object) with its world rotation/scale baked, at identity, constraint-free.

        ``corrections``: optional ``{bone: C}`` right-multiplied onto every edit-bone matrix (per-bone Unreal axis
        convention); the exporter is then run with its own bone correction disabled.
        """
        data = src.data.copy()
        self.datablocks.append(data)
        data.transform(self.S_U @ matrix)
        obj = self._new_object(data, final_name, "ARMATURE")
        obj.matrix_world = Matrix.Identity(4)
        obj.animation_data_clear()
        data.animation_data_clear()
        data.pose_position = pose_position
        self.depsgraph()  # builds the object's pose
        if obj.pose is not None:
            for pb in obj.pose.bones:
                for c in list(pb.constraints):
                    pb.constraints.remove(c)
                pb.matrix_basis = Matrix.Identity(4)
                pb.rotation_mode = "QUATERNION"
        for bone in data.bones:
            bone.use_deform = bone.name in export_bones
        if corrections:
            from . import bones as bones_mod

            self.edit_in_user_scene(obj, lambda: bones_mod.convert_rest_axes(self.context, obj, corrections))
            self.depsgraph()
        return obj

    def edit_in_user_scene(self, obj, fn):
        """Run ``fn()`` with ``obj`` temporarily linked into the user's scene.

        Edit mode only works for objects of the window's real view layer (a scene/view_layer context override
        reports success but changes nothing), so the staged copy is linked for the duration of the edit.
        """
        scene_coll = self.src_scene.collection
        vl = self.context.view_layer
        prev_active = vl.objects.active
        linked = False
        if obj.name not in scene_coll.objects:
            scene_coll.objects.link(obj)
            linked = True
        try:
            vl.update()
            fn()
        finally:
            if linked:
                try:
                    scene_coll.objects.unlink(obj)
                except RuntimeError:
                    pass
            try:
                vl.objects.active = prev_active
            except (RuntimeError, ReferenceError):
                pass
            vl.update()


def _collection_for(block):
    if isinstance(block, bpy.types.Mesh):
        return bpy.data.meshes
    if isinstance(block, bpy.types.Armature):
        return bpy.data.armatures
    if isinstance(block, bpy.types.Action):
        return bpy.data.actions
    if isinstance(block, bpy.types.Curve):
        return bpy.data.curves
    return None


def world_without_translation(matrix):
    m = matrix.copy()
    m.translation = (0.0, 0.0, 0.0)
    return m


def translation_removed(anchor_world):
    """Matrix that moves ``anchor_world``'s translation to the origin (applied on the left)."""
    return Matrix.Translation(-anchor_world.translation)


def limit_influences(obj, kept_groups, limit, merge_targets=None):
    """Keep at most ``limit`` strongest weights per vertex (renormalised); drop/merge unknown groups.

    ``kept_groups``: set of vertex-group names that correspond to exported bones.
    ``merge_targets``: ``{group_name: target_group_name}`` for groups whose weight should move to an ancestor.
    Returns ``(n_truncated, n_unweighted)``.
    """
    me = obj.data
    groups = obj.vertex_groups
    index_to_name = {vg.index: vg.name for vg in groups}
    name_to_index = {vg.name: vg.index for vg in groups}
    merge_targets = merge_targets or {}
    n_truncated = n_unweighted = 0
    for v in me.vertices:
        weights = {}
        for g in v.groups:
            name = index_to_name.get(g.group)
            if name is None or g.weight <= 0.0:
                continue
            if name not in kept_groups:
                name = merge_targets.get(name)
                if name is None or name not in kept_groups:
                    continue
            weights[name] = weights.get(name, 0.0) + g.weight
        items = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
        if len(items) > limit:
            items = items[:limit]
            n_truncated += 1
        total = sum(w for _, w in items)
        if total <= 0.0:
            n_unweighted += 1
            items = []
        present = {g.group for g in v.groups}
        keep = {name_to_index[name]: w / total for name, w in items}
        for gi in present - set(keep):
            groups[gi].remove([v.index])
        for gi, w in keep.items():
            groups[gi].add([v.index], w, "REPLACE")
    return n_truncated, n_unweighted
