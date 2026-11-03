# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender side of the Unreal smoke test: build the cases, export them with MorennBridge, write cases.json.

    blender -b --factory-startup --python dev/ue_smoke/blender_cases.py -- <out_dir> <content_root> [<ue_export_dir>]

Every case gets a folder with its .blend, FBX files and manifests. cases.json lists the manifests to import in order
and what Unreal should end up with, in Unreal's axes and centimetres. With <ue_export_dir> (files exported from
Unreal by unreal_checks.py), the mannequin round-trip case is added.
"""

import importlib
import json
import math
import os
import pathlib
import runpy
import sys
import traceback

import bpy
from mathutils import Quaternion, Vector

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MANNEQUIN_SKELETON = "/Game/Characters/Mannequins/Meshes/SK_Mannequin"


def ue(v):
    """Blender (cm) -> Unreal: the files use Unreal's axes, Unreal only flips Y."""
    return [round(v[0], 4), round(-v[1], 4), round(v[2], 4)]


def triangles(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def bounds(obj):
    """Unreal bounds of a static mesh: rotation and scale are baked, translation is not."""
    m = obj.matrix_world.to_3x3()
    points = [ue(m @ v.co) for v in obj.data.vertices]
    return [[min(p[i] for p in points) for i in range(3)], [max(p[i] for p in points) for i in range(3)]]


class Smoke:
    def __init__(self, out_dir, content_root):
        self.out = out_dir
        self.root = content_root
        self.cases = []
        self.prefs = fixtures.addon_module("prefs").get_prefs()

    def export(self, case, step, objects, mode="AUTO", **prefs):
        folder = os.path.join(self.out, case, step)
        os.makedirs(folder, exist_ok=True)
        scene = bpy.context.scene
        scene.morenn.export_dir = folder
        scene.morenn.ue_content_path = f"{self.root}/{case}"
        scene.morenn.log.clear()
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, f"{case}.blend"))
        saved = {name: getattr(self.prefs, name) for name in prefs}
        try:
            for name, value in prefs.items():
                setattr(self.prefs, name, value)
            fixtures.select_only(*objects)
            fixtures.call(bpy.ops.morenn.export_ue, mode=mode)
        finally:
            for name, value in saved.items():
                setattr(self.prefs, name, value)
        errors = [e.message for e in scene.morenn.log if e.level == "ERROR"]
        if errors or not scene.morenn.last_manifest.startswith(folder):
            raise RuntimeError(f"{case}/{step}: export failed: {errors}")
        return scene.morenn.last_manifest

    def add(self, name, item, manifests, expect):
        self.cases.append({"name": name, "item": item, "manifests": manifests, "expect": expect})
        print(f"[smoke] case {name}: {len(manifests)} manifest(s)")

    def write(self):
        path = os.path.join(self.out, "cases.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"content_root": self.root, "cases": self.cases}, fh, indent=1)
        return path


# ---------------------------------------------------------------------- static meshes


def static_expect(obj, collision_mesh=None, **extra):
    entry = {"triangles": [triangles(obj)], "bounds": bounds(obj)}
    if collision_mesh:
        entry["complex_collision"] = collision_mesh
    entry.update(extra)
    return entry


def case_combined(smoke):
    """Item 1: several meshes in one file, plus generated collision meshes in the same file."""
    scene = fixtures.fresh_scene(cm=True)
    crate = fixtures.make_cube("Crate", size=40.0)
    barrel = fixtures.make_cube("Barrel", size=30.0, location=(100, 0, 0))
    barrel.rotation_euler = (0, 0, math.radians(30))
    rock = fixtures.make_sphere("Rock", radius=25.0, location=(0, 200, 0))
    scene.morenn.generate_collision_mesh = True
    scene.morenn.collision_ratio = 0.5
    bpy.context.view_layer.update()
    manifest = smoke.export("combined", "export", [crate, barrel, rock], file_mode="COMBINED")
    meshes = {}
    for obj in (crate, barrel, rock):
        meshes[f"SM_{obj.name}"] = static_expect(obj, collision_mesh=f"SM_{obj.name}_COLL")
        meshes[f"SM_{obj.name}_COLL"] = {
            "triangles_at_most": triangles(obj),
            "bounds": bounds(obj),
            "bounds_tolerance": 1.0,
        }
    smoke.add("combined", 1, [manifest], {"static_meshes": meshes, "only_static_meshes": sorted(meshes)})


def case_single_file_collision(smoke):
    """Item 1: one mesh and its generated collision mesh in its own file; one plain mesh named after its file."""
    scene = fixtures.fresh_scene(cm=True)
    pillar = fixtures.make_sphere("Pillar", segments=12, rings=8, radius=15.0)
    pillar.scale = (1, 1, 3)
    bpy.context.view_layer.update()
    scene.morenn.generate_collision_mesh = True
    scene.morenn.collision_ratio = 0.3
    first = smoke.export("single_file", "with_collision", [pillar])
    scene.morenn.generate_collision_mesh = False
    box = fixtures.make_cube("Box", size=20.0, location=(50, 0, 0))
    second = smoke.export("single_file", "plain", [box])
    meshes = {
        "SM_Pillar": static_expect(pillar, collision_mesh="SM_Pillar_COLL"),
        "SM_Pillar_COLL": {"triangles_at_most": triangles(pillar)},
        "SM_Box": static_expect(box),
    }
    smoke.add("single_file", 1, [first, second], {"static_meshes": meshes, "only_static_meshes": sorted(meshes)})


def tower():
    """A sphere with two LODs, a convex and two box collision pieces (one turned) and a socket."""
    fixtures.fresh_scene(cm=True)
    lods = [
        fixtures.make_sphere("Tower", segments=32, rings=16, radius=20.0),
        fixtures.make_sphere("Tower_LOD1", segments=16, rings=8, radius=20.0),
        fixtures.make_sphere("Tower_LOD2", segments=8, rings=4, radius=20.0),
    ]
    for name, size, z in (("UCX_Tower", 10.0, -10.0), ("UBX_Tower", 8.0, 10.0), ("UBX_Tower_02", 10.0, 40.0)):
        fixtures.make_cube(name, size=size, location=(0, 0, z), uv=False, color=False, material=False)
    beam = bpy.data.objects["UBX_Tower_02"]
    beam.scale = (20.0, 1.0, 1.0)
    beam.rotation_euler = (0.0, 0.0, math.radians(40.0))  # 200 x 10 x 10, its sides are no axis planes
    fixtures.make_empty("SOCKET_Top", location=(5, 3, 30), parent=lods[0])
    bpy.context.view_layer.update()
    expect = {
        "triangles": [triangles(o) for o in lods],
        "bounds": bounds(lods[0]),
        "simple_collision": 3,
        "convex_collision": 1,
        "box_collision": 2,
        "sockets": {"Top": ue(Vector((5, 3, 30)))},
    }
    return lods[0], {"static_meshes": {"SM_Tower": expect}, "only_static_meshes": ["SM_Tower"]}


def case_lods(smoke):
    """Items 2 and 3: LOD chains, collision binding and sockets, for both LOD modes and the legacy importer."""
    for name, item, prefs in (
        ("lod_naming", 2, {"lod_mode": "LOD_NAMING"}),
        ("lod_groups", 2, {"lod_mode": "LODGROUP_NODES"}),
        ("lod_legacy", 3, {"lod_mode": "AUTO", "ue_importer": "LEGACY"}),
    ):
        obj, expect = tower()
        if name == "lod_groups":
            expect["static_meshes"]["SM_Tower"]["sockets"] = {"Top": None}  # Interchange skips LOD group children
        manifest = smoke.export(name, "export", [obj], **prefs)
        smoke.add(name, item, [manifest], expect)


# ---------------------------------------------------------------------- skeletal meshes and animations


def add_socket_bone(rig, parent, name, offset):
    compat = fixtures.addon_module("compat")
    bpy.context.view_layer.objects.active = rig
    with bpy.context.temp_override(active_object=rig, object=rig, selected_objects=[rig], **compat.window_override()):
        bpy.ops.object.mode_set(mode="EDIT")
        parent_bone = rig.data.edit_bones[parent]
        bone = rig.data.edit_bones.new(name)
        bone.head = parent_bone.tail
        bone.tail = parent_bone.tail + Vector(offset)
        bone.parent = parent_bone
        bone.use_deform = False
        bpy.ops.object.mode_set(mode="OBJECT")


def bone_expect(rig, names):
    out = {}
    for name in names:
        bone = rig.data.bones[name]
        head = rig.matrix_world @ bone.head_local
        direction = (rig.matrix_world.to_3x3() @ (bone.tail_local - bone.head_local)).normalized()
        out[name] = {
            "parent": bone.parent.name if bone.parent else "None",
            "position": ue(head),
            "x_axis": ue(direction),
        }
    return out


def case_skeletal(smoke):
    """Items 4, 5, 7, 9: socket bone, single root, reference pose, morph target, skeleton and physics renames."""
    fixtures.fresh_scene(cm=True)
    rig, _body = rigged_character("Hero", bones=3, bone_length=30.0, shape_key=True)
    add_socket_bone(rig, "bone_02", "SOCKET_Hand", (10.0, 0.0, 0.0))
    bpy.context.view_layer.update()
    manifest = smoke.export("skeletal", "export", [rig])
    hand = rig.matrix_world @ rig.data.bones["SOCKET_Hand"].head_local
    expect = {
        "skeletal_meshes": {
            "SKM_Hero": {
                "skeleton": "SKEL_Hero",
                "physics_asset": "PHYS_Hero",
                "bones": bone_expect(rig, ["root", "bone_01", "bone_02"]),
                "sockets": {"Hand": {"bone": "bone_02", "position": ue(hand)}},
                "morph_targets": ["Puff"],
            }
        },
        "absent": ["SKM_Hero_Skeleton", "SKM_Hero_PhysicsAsset"],
    }
    smoke.add("skeletal", 5, [manifest], expect)


def skinned_sphere(rig, name, segments, rings, radius=15.0, bottom=0.0, top=90.0):
    """A sphere stretched from ``bottom`` to ``top`` (cm); each vertex weighted to the 30 cm bone at its height."""
    centre = (bottom + top) / 2
    obj = fixtures.make_sphere(name, segments=segments, rings=rings, radius=radius, location=(0, 0, centre))
    for v in obj.data.vertices:
        v.co.z *= (top - bottom) / (2 * radius)
    names = [b.name for b in rig.data.bones]
    groups = {n: obj.vertex_groups.new(name=n) for n in names}
    for v in obj.data.vertices:
        groups[names[max(0, min(int((centre + v.co.z) // 30.0), len(names) - 1))]].add([v.index], 1.0, "REPLACE")
    obj.modifiers.new("Armature", "ARMATURE").object = rig
    obj.parent = rig
    return obj


def lod_of(obj, render, index):
    obj.morenn.role, obj.morenn.lod_index, obj.morenn.lod_of = "LOD", index, render
    return obj


def case_skeletal_lods(smoke, name, importer):
    """Skeletal LODs, written as one FBX LOD group: three levels, LOD0 and LOD2 made of two meshes each."""
    fixtures.fresh_scene(cm=True)
    rig = fixtures.make_armature("Knight", bones=3, bone_length=30.0)
    body = skinned_sphere(rig, "Body", 32, 16)
    helmet = skinned_sphere(rig, "Helmet", 16, 8, radius=8.0, bottom=80.0, top=96.0)
    levels = [[body, helmet]]
    levels.append([lod_of(skinned_sphere(rig, "Body_LOD1", 16, 8), body, 1)])
    levels.append(
        [
            lod_of(skinned_sphere(rig, "Body_LOD2", 8, 4), body, 2),
            lod_of(skinned_sphere(rig, "Helmet_LOD2", 8, 4, radius=8.0, bottom=80.0, top=96.0), helmet, 2),
        ]
    )
    bpy.context.view_layer.update()
    manifest = smoke.export(name, "export", [rig], ue_importer=importer)
    expect = {
        "skeletal_meshes": {
            "SKM_Knight": {
                "skeleton": "SKEL_Knight",
                "bones": bone_expect(rig, ["root", "bone_01", "bone_02"]),
                "lods": [sum(triangles(o) for o in level) for level in levels],
            }
        },
        "absent": ["SKM_Knight_Skeleton"],
    }
    smoke.add(name, 5, [manifest], expect)


def case_push_twice(smoke):
    """Re-import loop: a static mesh, a skeletal mesh and an animation pushed twice into the same folder."""
    fixtures.fresh_scene(cm=True).render.fps = 30
    crate = fixtures.make_cube("Crate", size=40.0, location=(100, 0, 0))
    rig, _body = rigged_character("Hero", bones=3, bone_length=30.0, shape_key=False)
    key_pose(rig, 1, (0, 0, 0), 0)
    key_pose(rig, 10, (0, 20, 0), 45)
    rig.animation_data.action.name = "Walk"
    bpy.context.view_layer.update()
    static = smoke.export("push_twice", "static", [crate])
    first = smoke.export("push_twice", "skeletal", [rig])
    rig.data.morenn.ue_skeleton_path = f"{smoke.root}/push_twice/SKEL_Hero"  # what the first push reports back
    again = smoke.export("push_twice", "skeletal_again", [rig])
    anim = smoke.export("push_twice", "anim", [rig], mode="ANIMATION")
    expect = {
        "folder_assets": {
            "SM_Crate": "StaticMesh",
            "M_Test": "MaterialInstanceConstant",  # Interchange makes every FBX material an instance
            "SKM_Hero": "SkeletalMesh",
            "SKEL_Hero": "Skeleton",
            "PHYS_Hero": "PhysicsAsset",
            "A_Hero_Walk": "AnimSequence",
        },
        "skeletal_meshes": {
            "SKM_Hero": {"skeleton": "SKEL_Hero", "bones": bone_expect(rig, ["root", "bone_01", "bone_02"])}
        },
        "animations": {"A_Hero_Walk": {"skeleton": f"{smoke.root}/push_twice/SKEL_Hero", "keys": 10, "length": 0.3}},
    }
    smoke.add("push_twice", "re-import loop", [static, first, anim, static, again, anim], expect)


def key_pose(rig, frame, root_location, bend_degrees):
    root = rig.pose.bones["root"]
    root.location = root_location
    root.keyframe_insert("location", frame=frame)
    bend = rig.pose.bones["bone_01"]
    bend.rotation_mode = "QUATERNION"
    bend.rotation_quaternion = Quaternion((1, 0, 0), math.radians(bend_degrees))
    bend.keyframe_insert("rotation_quaternion", frame=frame)


def case_animation(smoke, name, fps, start, end):
    """Item 6: naming, length and keys at a frame rate; the mesh is pushed first, then the animation."""
    scene = fixtures.fresh_scene(cm=True)
    scene.render.fps = fps
    rig, _body = rigged_character("Runner", bones=3, bone_length=30.0, shape_key=False)
    key_pose(rig, start, (0, 0, 0), 0)
    key_pose(rig, (start + end) // 2, (0, 20, 5), 45)
    key_pose(rig, end, (0, 50, 0), 90)
    rig.animation_data.action.name = "Run"
    bpy.context.view_layer.update()
    mesh = smoke.export(name, "mesh", [rig])
    rig.data.morenn.ue_skeleton_path = f"{smoke.root}/{name}/SKEL_Runner"
    anim = smoke.export(name, "anim", [rig], mode="ANIMATION")
    poses = {}
    for frame in (start, (start + end) // 2, end):
        scene.frame_set(frame)
        poses[str(frame - start)] = {pb.name: ue(rig.matrix_world @ pb.head) for pb in rig.pose.bones}
    expect = {
        "animations": {
            "A_Runner_Run": {
                "skeleton": f"{smoke.root}/{name}/SKEL_Runner",
                "keys": end - start + 1,
                "length": (end - start) / fps,
                "poses": poses,
            }
        }
    }
    smoke.add(name, 6, [mesh, anim], expect)


def case_shading(smoke):
    """Imported normals: a sphere shaded smooth stays smooth, flat faces and sharp edges stay hard."""
    fixtures.fresh_scene(cm=True)
    smooth = fixtures.make_sphere("SmoothBall", radius=25.0)
    for polygon in smooth.data.polygons:
        polygon.use_smooth = True
    flat = fixtures.make_sphere("FlatBall", radius=25.0, location=(60, 0, 0))
    crate = fixtures.make_cube("SharpCrate", size=40.0, location=(120, 0, 0))
    manifest = smoke.export("shading", "export", [smooth, flat, crate])
    expect = {"shading": {"SM_SmoothBall": "smooth", "SM_FlatBall": "flat", "SM_SharpCrate": "flat"}}
    smoke.add("shading", 1, [manifest], expect)


def srgb_to_linear(value):
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def coloured_cube(name, location, domain_type, colours):
    """Cube with one colour attribute; ``colours``: one sRGB byte triple, or one per face (faces split apart)."""
    import bmesh

    obj = fixtures.make_cube(name, size=20.0, location=location, color=False)
    if len(colours) > 1:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.split_edges(bm, edges=bm.edges[:])
        bm.to_mesh(obj.data)
        bm.free()
    data_type, domain = domain_type
    attribute = obj.data.color_attributes.new("Col", data_type, domain)
    obj.data.color_attributes.active_color = attribute
    for polygon in obj.data.polygons:
        rgb = colours[polygon.index % len(colours)]
        for loop_index in polygon.loop_indices:
            index = loop_index if domain == "CORNER" else obj.data.loops[loop_index].vertex_index
            if data_type == "BYTE_COLOR":
                attribute.data[index].color_srgb = (*(c / 255 for c in rgb), 1.0)
            else:
                attribute.data[index].color = (*(srgb_to_linear(c / 255) for c in rgb), 1.0)
    return obj


def case_vertex_colours(smoke):
    """Item 8: a colour painted in Blender comes out as the same 8-bit sRGB colour in Unreal, with both importers."""
    for name, importer in (("vertex_colours", "INTERCHANGE"), ("vertex_colours_legacy", "LEGACY")):
        fixtures.fresh_scene(cm=True)
        grey = [(128, 128, 128)]
        swatch = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (64, 64, 64), (200, 100, 50), (10, 20, 30)]
        cubes = {
            "SM_GreyByte": (coloured_cube("GreyByte", (0, 0, 0), ("BYTE_COLOR", "CORNER"), grey), grey),
            "SM_GreyFloat": (coloured_cube("GreyFloat", (40, 0, 0), ("FLOAT_COLOR", "POINT"), grey), grey),
            "SM_Swatch": (coloured_cube("Swatch", (80, 0, 0), ("BYTE_COLOR", "CORNER"), swatch), swatch),
        }
        manifest = smoke.export(name, "export", [obj for obj, _ in cubes.values()], ue_importer=importer)
        expect = {"vertex_colours": {asset: [list(c) for c in colours] for asset, (_obj, colours) in cubes.items()}}
        smoke.add(name, 8, [manifest], expect)


def file_fps(path):
    """Frame rate stored in an FBX file's global settings."""
    inspect = fixtures.addon_module("fbxpatch.inspect")
    root, _version = fixtures.fbx_load(path)
    settings = root.find(b"GlobalSettings")
    rate = inspect.prop_values(settings, b"CustomFrameRate") if settings is not None else None
    if rate and rate[0] > 0:
        return float(rate[0])
    modes = {6: 30.0, 11: 24.0, 10: 25.0, 3: 60.0}
    mode = inspect.prop_values(settings, b"TimeMode") if settings is not None else None
    return modes.get(mode[0], 30.0) if mode else 30.0


def import_unreal_file(item):
    """Import one item of Unreal's pull.result.json like Pull does."""
    prefs = fixtures.addon_module("prefs").get_prefs()
    report = fixtures.addon_module("core.log").Report("smoke import")
    result = fixtures.addon_module("core.import_fbx").import_file(bpy.context, item["file"], prefs, report)
    fixtures.addon_module("ops.ue_ops").remember_unreal_settings(result.actions, item.get("settings"), report)
    if not report.ok:
        raise RuntimeError(f"import of {item['file']} failed: {fixtures.report_text(report)}")
    return result


def case_mannequin(smoke, ue_export):
    """Items 5 and 6: the template mannequin and two animations, exported by Unreal, through Blender and back."""
    with open(os.path.join(ue_export, "pull.result.json"), encoding="utf-8") as fh:
        pulled = {os.path.basename(i["file"])[:-4]: i for i in json.load(fh)["items"] if i.get("success")}
    original = pulled["SKM_Manny_Simple"]["asset_path"]
    scene = fixtures.fresh_scene(cm=True)
    rig = import_unreal_file(pulled["SKM_Manny_Simple"]).armatures[0]
    bones = [b.name for b in rig.data.bones]  # the mesh's own bones, as Unreal exported them
    own_skeleton = smoke.export("mannequin", "mesh", [rig])
    rig.data.morenn.ue_skeleton_path = MANNEQUIN_SKELETON
    on_skeleton = smoke.export("mannequin_on_skeleton", "mesh", [rig])
    compare = {"reference": original, "skeleton": "SKEL_Manny_Simple", "bones": bones}
    smoke.add("mannequin", 5, [own_skeleton], {"compare_skeletal": {"SKM_Manny_Simple": compare}})
    # pulled, then pushed back over the asset (a copy of it), with and without its LODs
    for name, prefs in (("reimport_lods", {}), ("reimport_no_lods", {"lod_mode": "NONE"})):
        manifest = smoke.export(name, "mesh", [rig], **prefs)
        expect = {"reimport": {"SKM_Manny_Simple": {"source": original}}}
        smoke.add(name, "re-import loop", [manifest], dict(expect, folder_assets={"SKM_Manny_Simple": "SkeletalMesh"}))
    anims = {}
    manifests = [on_skeleton]
    for stem in ("MM_Idle", "MF_Unarmed_Jog_Fwd"):
        scene = fixtures.fresh_scene(cm=True)
        scene.render.fps = int(round(file_fps(pulled[stem]["file"])))
        rig = import_unreal_file(pulled[stem]).armatures[0]
        rig.name = "Manny"
        rig.data.morenn.ue_skeleton_path = MANNEQUIN_SKELETON
        manifests.append(smoke.export("mannequin_on_skeleton", stem, [rig], mode="ANIMATION"))
        anims[f"A_Manny_{stem}"] = {"reference": pulled[stem]["asset_path"]}
    expect = {
        "compare_skeletal": {"SKM_Manny_Simple": dict(compare, skeleton=MANNEQUIN_SKELETON)},
        "compare_animations": anims,
    }
    smoke.add("mannequin_on_skeleton", 6, manifests, expect)
    if "SKM_Manny" in pulled:  # the full mannequin, when the project has one (UE 5.8's template does not)
        fixtures.fresh_scene(cm=True)
        full = import_unreal_file(pulled["SKM_Manny"]).armatures[0]
        compare = {
            "reference": pulled["SKM_Manny"]["asset_path"],
            "skeleton": "SKEL_Manny",
            "bones": [b.name for b in full.data.bones],
        }
        smoke.add(
            "manny_full", 5, [smoke.export("manny_full", "mesh", [full])], {"compare_skeletal": {"SKM_Manny": compare}}
        )


CASES = (
    case_combined,
    case_single_file_collision,
    case_lods,
    case_skeletal,
    lambda smoke: case_skeletal_lods(smoke, "skeletal_lods", "INTERCHANGE"),
    lambda smoke: case_skeletal_lods(smoke, "skeletal_lods_legacy", "LEGACY"),
    lambda smoke: case_animation(smoke, "anim_30fps", 30, 0, 30),
    lambda smoke: case_animation(smoke, "anim_24fps", 24, 1, 24),
    case_vertex_colours,
    case_shading,
    case_push_twice,
)


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :]
    out_dir, content_root = argv[0], argv[1]
    smoke = Smoke(out_dir, content_root)
    failed = []
    cases = list(CASES)
    if len(argv) > 2:
        cases.append(lambda smoke: case_mannequin(smoke, argv[2]))
    for build in cases:
        try:
            build(smoke)
        except Exception:
            traceback.print_exc()
            failed.append(getattr(build, "__name__", "case"))
    path = smoke.write()
    print(f"[smoke] wrote {path}; {len(smoke.cases)} case(s), {len(failed)} failed to build: {failed}")
    sys.stdout.flush()
    os._exit(1 if failed else 0)


namespace = runpy.run_path(str(ROOT / "tests" / "run_tests.py"), run_name="ue_smoke")
os.environ["MORENN_ADDON_MODULE"] = namespace["load_addon"]().__name__
fixtures = importlib.import_module("tests.fixtures")
rigged_character = importlib.import_module("tests.test_export_skeletal")._rigged_character
main()
