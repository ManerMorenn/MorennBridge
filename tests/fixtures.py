# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural test fixtures (no .blend files)."""

import importlib
import os
import tempfile

import bpy
from mathutils import Vector


def addon():
    return importlib.import_module(os.environ["MORENN_ADDON_MODULE"])


def addon_module(dotted):
    return importlib.import_module(f"{os.environ['MORENN_ADDON_MODULE']}.{dotted}")


def temp_dir():
    return tempfile.mkdtemp(prefix="morenn_test_")


def fresh_scene(cm=False):
    """Remove every datablock and reset the scene to metres (or centimetres)."""
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in [
        bpy.data.meshes,
        bpy.data.armatures,
        bpy.data.actions,
        bpy.data.cameras,
        bpy.data.lights,
        bpy.data.curves,
        bpy.data.materials,
        bpy.data.collections,
        bpy.data.images,
        bpy.data.lattices,
    ]:
        for block in list(coll):
            coll.remove(block)
    for s in list(bpy.data.scenes):
        if s != scene:
            bpy.data.scenes.remove(s)
    bpy.data.orphans_purge(do_recursive=True)
    us = scene.unit_settings
    us.system = "METRIC"
    us.scale_length = 0.01 if cm else 1.0
    us.length_unit = "CENTIMETERS" if cm else "METERS"
    scene.render.fps = 24
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = 250
    scene.frame_set(1)
    scene.morenn.log.clear()
    scene.morenn.issues.clear()
    return scene


def link(obj, collection=None):
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def make_cube(name="Cube", size=2.0, location=(0, 0, 0), uv=True, sharp=True, color=True, material=True):
    import bmesh

    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    if uv:
        uv_layer = bm.loops.layers.uv.new("UVMap")
        for face in bm.faces:
            for i, loop in enumerate(face.loops):
                loop[uv_layer].uv = ((i in (1, 2)) * 1.0, (i in (2, 3)) * 1.0)
    bm.to_mesh(me)
    bm.free()
    if sharp:
        for edge in me.edges:
            edge.use_edge_sharp = True
    if color:
        attr = me.color_attributes.new("Color", "BYTE_COLOR", "CORNER")
        for i in range(len(attr.data)):
            attr.data[i].color = (0.5, 0.25, 1.0, 1.0)
    if material:
        mat = bpy.data.materials.get("M_Test") or bpy.data.materials.new("M_Test")
        me.materials.append(mat)
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    return link(obj)


def make_armature(name="Rig", bones=3, bone_length=1.0, location=(0, 0, 0), along="Y"):
    """A chain of ``bones`` bones stacked along +Z (bone local Y along the chain)."""
    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    obj.location = location
    link(obj)
    bpy.context.view_layer.objects.active = obj
    with bpy.context.temp_override(
        active_object=obj, object=obj, selected_objects=[obj], **addon_module("compat").window_override()
    ):
        bpy.ops.object.mode_set(mode="EDIT")
        parent = None
        for i in range(bones):
            eb = arm.edit_bones.new("root" if i == 0 else f"bone_{i:02d}")
            eb.head = (0, 0, i * bone_length)
            eb.tail = (0, 0, (i + 1) * bone_length)
            eb.parent = parent
            eb.use_connect = parent is not None
            parent = eb
        bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def key_location(obj, frames_values):
    """Insert object location keys: ``[(frame, (x, y, z)), ...]``."""
    for frame, value in frames_values:
        obj.location = value
        obj.keyframe_insert("location", frame=frame)
    return obj.animation_data.action


def assert_vec_close(testcase, a, b, places=4):
    a, b = tuple(a), tuple(b)
    testcase.assertEqual(len(a), len(b))
    for x, y in zip(a, b):
        testcase.assertAlmostEqual(x, y, places=places)


def make_camera(name="Camera"):
    cam = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, cam)
    return link(obj)


def make_sphere(name="Ball", segments=24, rings=16, radius=1.0, location=(0, 0, 0)):
    import bmesh

    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius)
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for face in bm.faces:
        for loop in face.loops:
            loop[uv_layer].uv = (loop.vert.co.x * 0.5 + 0.5, loop.vert.co.y * 0.5 + 0.5)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    return link(obj)


def make_empty(name, location=(0, 0, 0), parent=None):
    obj = bpy.data.objects.new(name, None)
    obj.location = location
    link(obj)
    if parent is not None:
        obj.parent = parent
    return obj


def export_settings(export_dir, **overrides):
    """ExportSettings built from the default preferences plus overrides."""
    prefs = addon_module("prefs").get_prefs()
    ExportSettings = addon_module("core.export_fbx").ExportSettings
    s = ExportSettings.from_prefs(prefs, bpy.context.scene)
    s.export_dir = export_dir
    s.batch_name = "batch"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def run_export(objects, export_dir, mode="AUTO", **overrides):
    """Classify + export ``objects``; returns (exported_files, manifest_path, report)."""
    prefs = addon_module("prefs").get_prefs()
    classify = addon_module("core.classify")
    export_fbx = addon_module("core.export_fbx")
    Report = addon_module("core.log").Report
    scene = bpy.context.scene
    settings = export_settings(export_dir, **overrides)
    groups = classify.build_groups(objects, prefs, scene, mode=mode, destination=settings.destination_path)
    report = Report("test export")
    done, manifest_path = export_fbx.export_groups(bpy.context, groups, settings, report)
    return done, manifest_path, report


def fbx_load(path):
    return addon_module("fbxpatch.tree").load(path)


def fbx_models(root, subclass=None):
    insp = addon_module("fbxpatch.inspect")
    return {insp.object_name(m).decode(): m for m in insp.find_objects(root, b"Model", subclass=subclass)}


def fbx_geometry_for_model(root, model_elem):
    insp = addon_module("fbxpatch.inspect")
    mid = insp.object_id(model_elem)
    for kind, child, parent, _p, _e in insp.connections(root):
        if kind == b"OO" and parent == mid:
            g = insp.object_by_id(root, child)
            if g is not None and g.id == b"Geometry":
                return g
    return None


def fbx_polygon_count(geometry):
    idx = geometry.find(b"PolygonVertexIndex")
    if idx is None:
        return 0
    return sum(1 for v in idx.props[0] if v < 0)


def no_temp_leftovers():
    """True when no MB__ temp objects/meshes/scenes remain after an export."""
    names = [o.name for o in bpy.data.objects if o.name.startswith("MB__")]
    names += [m.name for m in bpy.data.meshes if m.name.startswith("MB__")]
    names += [s.name for s in bpy.data.scenes if s.name.startswith("MB_EXPORT")]
    names += [c.name for c in bpy.data.collections if c.name.startswith("MB_EXPORT")]
    return not names, names


def bone_dir(bone):
    """Armature-space direction of a bone."""
    return (bone.matrix_local.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
