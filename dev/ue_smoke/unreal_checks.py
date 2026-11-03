# SPDX-License-Identifier: GPL-3.0-or-later
"""Unreal side of the smoke test, run by the editor's Python (UnrealEditor-Cmd -run=pythonscript).

MB_SMOKE_STAGE=export   exports the template's mannequin assets with mb_ue_export.py into MB_SMOKE_UE_EXPORT.
MB_SMOKE_STAGE=check    imports the cases listed in MB_SMOKE_CASES with mb_ue_import.py, each into its own folder
                        (deleted first), checks the assets and writes MB_SMOKE_RESULTS.
"""

import importlib
import json
import math
import os
import sys
import traceback

import unreal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "ue", "scripts"))
mb_import = importlib.import_module("mb_ue_import")
mb_export = importlib.import_module("mb_ue_export")

LIB = unreal.EditorAssetLibrary
POSE = unreal.AnimPoseExtensions
WORLD = unreal.AnimPoseSpaces.WORLD
LOCAL = unreal.AnimPoseSpaces.LOCAL
ASSET_ONLY_SETTINGS = ("retarget_source_asset", "retarget_source", "enable_root_motion", "force_root_lock")
MANNEQUIN = (
    "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple",
    "/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle",
    "/Game/Characters/Mannequins/Anims/Unarmed/Jog/MF_Unarmed_Jog_Fwd",
)


def vec(v):
    return [round(v.x, 4), round(v.y, 4), round(v.z, 4)]


def close(a, b, tolerance):
    return len(a) == len(b) and all(abs(x - y) <= tolerance for x, y in zip(a, b))


def short(path):
    return path.rsplit("/", 1)[-1] if path else "None"


class Case:
    def __init__(self, data, folder):
        self.data = data
        self.folder = folder
        self.results = []
        self.reports = []

    def check(self, ok, what, detail=""):
        self.results.append({"ok": bool(ok), "check": what, "detail": str(detail)})
        return bool(ok)

    def note(self, what, detail):
        self.results.append({"ok": None, "check": what, "detail": str(detail)})

    def load(self, name):
        return unreal.load_asset(f"{self.folder}/{name}")


def asset_classes(folder):
    out = {}
    for path in LIB.list_assets(folder, recursive=True, include_folder=False):
        data = LIB.find_asset_data(path)
        out[path.split(".")[0].rsplit("/", 1)[-1]] = str(data.asset_class_path.asset_name)
    return out


def collision_shapes(mesh):
    """Simple collision shapes by kind (the editor subsystems are not available in a commandlet)."""
    body = mesh.get_editor_property("body_setup")
    geometry = body.get_editor_property("agg_geom") if body else None
    out = {}
    for kind in ("box_elems", "sphere_elems", "sphyl_elems", "convex_elems"):
        count = len(geometry.get_editor_property(kind)) if geometry else 0
        if count:
            out[kind] = count
    return out


def check_static(case, name, exp):
    mesh = case.load(name)
    if not case.check(isinstance(mesh, unreal.StaticMesh), f"{name} is a static mesh", type(mesh).__name__):
        return
    tris = [mesh.get_num_triangles(i) for i in range(mesh.get_num_lods())]
    if "triangles" in exp:
        case.check(tris == exp["triangles"], f"{name}: triangles per LOD", f"{tris}, expected {exp['triangles']}")
    if "triangles_at_most" in exp:
        case.check(
            0 < tris[0] <= exp["triangles_at_most"], f"{name}: decimated", f"{tris[0]} <= {exp['triangles_at_most']}"
        )
    if "bounds" in exp:
        box = mesh.get_bounding_box()
        got = [vec(box.min), vec(box.max)]
        tolerance = exp.get("bounds_tolerance", 0.01)
        ok = close(got[0], exp["bounds"][0], tolerance) and close(got[1], exp["bounds"][1], tolerance)
        case.check(ok, f"{name}: bounds (pivot and size)", f"{got}, expected {exp['bounds']}")
    if "simple_collision" in exp or "convex_collision" in exp:
        shapes = collision_shapes(mesh)
        total = sum(shapes.values())
        case.check(total == exp.get("simple_collision", total), f"{name}: simple collision shapes", shapes)
        convex = shapes.get("convex_elems", 0)
        case.check(convex == exp.get("convex_collision", convex), f"{name}: convex pieces", convex)
    if "complex_collision" in exp:
        linked = mesh.get_editor_property("complex_collision_mesh")
        got = linked.get_name() if linked else None
        case.check(got == exp["complex_collision"], f"{name}: complex collision mesh", got)
    for socket_name, location in exp.get("sockets", {}).items():
        socket = mesh.find_socket(socket_name)
        got = vec(socket.get_editor_property("relative_location")) if socket else None
        if location is None:
            case.check(got is None, f"{name}: no socket {socket_name} (expected loss)", got)
            continue
        case.check(
            got is not None and close(got, location, 0.01),
            f"{name}: socket {socket_name}",
            f"{got}, expected {location}",
        )


def check_skeletal(case, name, exp):
    mesh = case.load(name)
    if not case.check(isinstance(mesh, unreal.SkeletalMesh), f"{name} is a skeletal mesh", type(mesh).__name__):
        return
    skeleton = mesh.get_editor_property("skeleton")
    physics = mesh.get_editor_property("physics_asset")
    case.check(
        skeleton and skeleton.get_name() == exp["skeleton"],
        f"{name}: skeleton asset",
        short(skeleton and skeleton.get_path_name()),
    )
    if "physics_asset" in exp:
        got = physics.get_name() if physics else None
        case.check(got == exp["physics_asset"], f"{name}: physics asset", got)
    pose = POSE.get_reference_pose(skeleton)
    names = sorted(str(n) for n in POSE.get_bone_names(pose))
    case.check(names == sorted(exp["bones"]), f"{name}: bones", names)
    for bone, b in exp["bones"].items():
        parent = str(mesh.get_bone_parent(bone))
        case.check(parent == b["parent"], f"{name}: parent of {bone}", parent)
        world = POSE.get_ref_bone_pose(pose, bone, WORLD)
        position = vec(world.translation)
        case.check(
            close(position, b["position"], 0.01),
            f"{name}: rest position of {bone}",
            f"{position}, expected {b['position']}",
        )
        axis = vec(world.rotation.rotate_vector(unreal.Vector(1, 0, 0)))
        case.check(close(axis, b["x_axis"], 1e-3), f"{name}: X axis along {bone}", f"{axis}, expected {b['x_axis']}")
        scale = vec(POSE.get_ref_bone_pose(pose, bone, LOCAL).scale3d)
        case.check(close(scale, [1, 1, 1], 1e-5), f"{name}: scale of {bone}", scale)
    for socket_name, s in exp.get("sockets", {}).items():
        socket = mesh.find_socket(socket_name)
        if not case.check(socket is not None, f"{name}: socket {socket_name}"):
            continue
        bone = str(socket.get_editor_property("bone_name"))
        case.check(bone == s["bone"], f"{name}: socket {socket_name} bone", bone)
        world = POSE.get_ref_bone_pose(pose, bone, WORLD).transform_location(
            socket.get_editor_property("relative_location")
        )
        case.check(
            close(vec(world), s["position"], 0.01),
            f"{name}: socket {socket_name} position",
            f"{vec(world)}, expected {s['position']}",
        )
    if "morph_targets" in exp:
        morphs = sorted(str(m) for m in mesh.get_all_morph_target_names())
        case.check(morphs == sorted(exp["morph_targets"]), f"{name}: morph targets", morphs)


def check_animation(case, name, exp):
    anim = case.load(name)
    if not case.check(isinstance(anim, unreal.AnimSequence), f"{name} is an animation", type(anim).__name__):
        return
    skeleton = anim.get_editor_property("skeleton")
    got = skeleton.get_path_name().split(".")[0] if skeleton else None
    case.check(got == exp["skeleton"], f"{name}: skeleton", got)
    keys = unreal.AnimationLibrary.get_num_keys(anim)
    length = anim.get_play_length()
    case.check(keys == exp["keys"], f"{name}: keys", f"{keys}, expected {exp['keys']}")
    case.check(abs(length - exp["length"]) < 1e-4, f"{name}: length", f"{length:.5f} s, expected {exp['length']:.5f}")
    options = unreal.AnimPoseEvaluationOptions()
    for frame, bones in exp.get("poses", {}).items():
        pose = POSE.get_anim_pose_at_frame(anim, int(frame), options)
        worst = max(
            max(abs(a - b) for a, b in zip(vec(POSE.get_bone_pose(pose, bone, WORLD).translation), position))
            for bone, position in bones.items()
        )
        case.check(worst < 0.05, f"{name}: bone positions at frame {frame}", f"largest difference {worst:.4f} cm")


def srgb_byte(value):
    """GeometryScript copies vertex colours in sRGB (FMeshDescriptionToDynamicMesh converts them), as FColor stores."""
    return int(round(min(max(value, 0.0), 1.0) * 255))


def mesh_colours(mesh):
    dynamic = copy_to_dynamic_mesh(mesh)
    _mesh, colours, valid, _gaps = unreal.GeometryScript_VertexColors.get_mesh_per_vertex_colors(dynamic, False)
    values = unreal.GeometryScript_List.convert_color_list_to_array(colours)
    return valid, sorted({(srgb_byte(c.r), srgb_byte(c.g), srgb_byte(c.b)) for c in values})


def copy_to_dynamic_mesh(mesh):
    """LOD0 of a static mesh, as imported (source model)."""
    options = unreal.GeometryScriptCopyMeshFromAssetOptions()
    lod = unreal.GeometryScriptMeshReadLOD()
    lod.set_editor_property("lod_type", unreal.GeometryScriptLODType.SOURCE_MODEL)
    dynamic, _outcome = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        mesh, unreal.DynamicMesh(), options, lod
    )
    return dynamic


def degrees_between(a, b):
    length = math.sqrt(a.dot(a) * b.dot(b))
    return math.degrees(math.acos(max(-1.0, min(1.0, a.dot(b) / length)))) if length else 180.0


def check_shading(case, name, mode):
    """``smooth``: corner normals point away from the centre (a sphere at its pivot); ``flat``: equal the face's."""
    mesh = case.load(name)
    if not case.check(isinstance(mesh, unreal.StaticMesh), f"{name} is a static mesh", type(mesh).__name__):
        return
    dynamic = copy_to_dynamic_mesh(mesh)
    worst = 0.0
    for triangle in range(dynamic.get_triangle_count()):
        _valid, *corners = dynamic.get_triangle_positions(triangle)
        normals = [v for v in dynamic.get_triangle_normals(triangle) if isinstance(v, unreal.Vector)]
        face, _ok = dynamic.get_triangle_face_normal(triangle)
        for corner, normal in zip(corners, normals):
            worst = max(worst, degrees_between(normal, corner if mode == "smooth" else face))
    case.check(worst < 1.0, f"{name}: {mode} shading", f"largest normal deviation {worst:.3f} deg")


def check_vertex_colours(case, name, expected):
    mesh = case.load(name)
    if not case.check(isinstance(mesh, unreal.StaticMesh), f"{name} is a static mesh", type(mesh).__name__):
        return
    valid, got = mesh_colours(mesh)
    wanted = sorted(tuple(c) for c in expected)
    same = len(got) == len(wanted) and all(close(a, b, 1) for a, b in zip(got, wanted))
    case.check(valid and same, f"{name}: vertex colours (8-bit sRGB)", f"{got}, expected {wanted}")


def angle_between(a, b):
    """Degrees between two rotations (unreal.Quat)."""
    dot = abs(a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w)
    return math.degrees(2.0 * math.acos(min(1.0, dot)))


def pose_difference(pose, reference, bones, use_reference_pose=False):
    """Largest position (cm) and rotation (degrees) difference over ``bones``, in component space."""
    get = POSE.get_ref_bone_pose if use_reference_pose else POSE.get_bone_pose
    worst = (0.0, "", 0.0, "")
    for bone in bones:
        a, b = get(pose, bone, WORLD), get(reference, bone, WORLD)
        distance = max(abs(x - y) for x, y in zip(vec(a.translation), vec(b.translation)))
        angle = angle_between(a.rotation, b.rotation)
        worst = (
            max(worst[0], distance),
            bone if distance >= worst[0] else worst[1],
            max(worst[2], angle),
            bone if angle >= worst[2] else worst[3],
        )
    return worst


def compare_skeletal(case, name, exp):
    mesh, reference = case.load(name), unreal.load_asset(exp["reference"])
    if not case.check(isinstance(mesh, unreal.SkeletalMesh), f"{name} is a skeletal mesh", type(mesh).__name__):
        return
    skeleton = mesh.get_editor_property("skeleton")
    got = skeleton.get_path_name().split(".")[0] if skeleton else None
    expected = exp["skeleton"] if exp["skeleton"].startswith("/") else f"{case.folder}/{exp['skeleton']}"
    case.check(got == expected, f"{name}: skeleton", got)
    ref_skeleton = reference.get_editor_property("skeleton")
    pose, ref_pose = POSE.get_reference_pose(skeleton), POSE.get_reference_pose(ref_skeleton)
    shared = exp["bones"]  # the original mesh's bones; a shared skeleton may hold more
    if skeleton != ref_skeleton:
        bones = [str(n) for n in POSE.get_bone_names(pose)]
        missing, extra = sorted(set(shared) - set(bones)), sorted(set(bones) - set(shared))
        case.check(
            not (missing or extra), f"{name}: same bones as the original mesh", f"missing {missing}, extra {extra}"
        )
    parents = [b for b in shared if str(mesh.get_bone_parent(b)) != str(reference.get_bone_parent(b))]
    case.check(not parents, f"{name}: same parents", parents[:5])
    if skeleton != ref_skeleton:
        distance, at, angle, at_angle = pose_difference(pose, ref_pose, shared, use_reference_pose=True)
        case.check(distance < 0.01, f"{name}: rest positions", f"largest difference {distance:.5f} cm ({at})")
        case.check(angle < 0.01, f"{name}: rest rotations", f"largest difference {angle:.5f} deg ({at_angle})")
    bounds, ref_bounds = mesh.get_imported_bounds(), reference.get_imported_bounds()
    got = vec(bounds.origin) + vec(bounds.box_extent)
    wanted = vec(ref_bounds.origin) + vec(ref_bounds.box_extent)
    case.check(close(got, wanted, 0.1), f"{name}: bounds", f"{got}, original {wanted}")
    slots = len(mesh.get_editor_property("materials"))
    ref_slots = len(reference.get_editor_property("materials"))
    case.check(slots == ref_slots, f"{name}: material slots", f"{slots}, original {ref_slots}")


def compare_animation(case, name, exp):
    anim, reference = case.load(name), unreal.load_asset(exp["reference"])
    if not case.check(isinstance(anim, unreal.AnimSequence), f"{name} is an animation", type(anim).__name__):
        return
    skeleton = anim.get_editor_property("skeleton")
    ref_skeleton = reference.get_editor_property("skeleton")
    case.check(
        skeleton == ref_skeleton, f"{name}: on the original skeleton", short(skeleton and skeleton.get_path_name())
    )
    keys, ref_keys = unreal.AnimationLibrary.get_num_keys(anim), unreal.AnimationLibrary.get_num_keys(reference)
    case.check(keys == ref_keys, f"{name}: keys", f"{keys}, original {ref_keys}")
    length, ref_length = anim.get_play_length(), reference.get_play_length()
    case.check(abs(length - ref_length) < 1e-4, f"{name}: length", f"{length:.5f} s, original {ref_length:.5f}")
    lost = {}
    for setting in ASSET_ONLY_SETTINGS:  # not in FBX: copied here (not saved) so poses compare like for like
        value = reference.get_editor_property(setting)
        if anim.get_editor_property(setting) != value:
            lost[setting] = value.get_name() if hasattr(value, "get_name") else value
            anim.set_editor_property(setting, value)
    case.note(f"{name}: Unreal asset settings the round trip loses", lost or "none")
    options = unreal.AnimPoseEvaluationOptions()
    # Unreal's FBX export holds the preview mesh's bones only; the others cannot come back
    bones = [str(n) for n in unreal.AnimationLibrary.get_animation_track_names(anim)]
    case.check(bones, f"{name}: bone tracks", f"{len(bones)} tracks")
    for frame in sorted({0, ref_keys // 2, ref_keys - 1}):
        pose = POSE.get_anim_pose_at_frame(anim, frame, options)
        ref_pose = POSE.get_anim_pose_at_frame(reference, frame, options)
        distance, at, angle, at_angle = pose_difference(pose, ref_pose, bones)
        case.check(
            distance < 0.05, f"{name}: positions at frame {frame}", f"largest difference {distance:.4f} cm ({at})"
        )
        case.check(
            angle < 0.05, f"{name}: rotations at frame {frame}", f"largest difference {angle:.4f} deg ({at_angle})"
        )


def run_case(data, root):
    folder = f"{root}/{data['name']}"
    case = Case(data, folder)
    if LIB.does_directory_exist(folder):
        LIB.delete_directory(folder)
    for manifest in data["manifests"]:
        report = mb_import.run(manifest)
        case.reports.append(report)
        problems = [e for i in report["items"] for e in i["errors"] + i["warnings"]] + report.get("warnings", [])
        case.check(report["ok"], f"import {os.path.basename(os.path.dirname(manifest))}", "; ".join(problems))
    exp = data["expect"]
    if "only_static_meshes" in exp:
        found = sorted(n for n, c in asset_classes(folder).items() if c == "StaticMesh")
        case.check(found == sorted(exp["only_static_meshes"]), "static mesh assets created", found)
    for name, e in exp.get("static_meshes", {}).items():
        check_static(case, name, e)
    for name, e in exp.get("skeletal_meshes", {}).items():
        check_skeletal(case, name, e)
    for name, e in exp.get("animations", {}).items():
        check_animation(case, name, e)
    for name, e in exp.get("vertex_colours", {}).items():
        check_vertex_colours(case, name, e)
    for name, e in exp.get("shading", {}).items():
        check_shading(case, name, e)
    for name, e in exp.get("compare_skeletal", {}).items():
        compare_skeletal(case, name, e)
    for name, e in exp.get("compare_animations", {}).items():
        compare_animation(case, name, e)
    classes = asset_classes(folder)
    for name in exp.get("absent", []):
        case.check(classes.get(name) in (None, "ObjectRedirector"), f"no asset left as {name}", classes.get(name))
    return case


def check(cases_path, results_path):
    with open(cases_path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for case_data in data["cases"]:
        try:
            case = run_case(case_data, data["content_root"])
        except Exception:
            case = Case(case_data, "")
            case.check(False, "case raised", traceback.format_exc())
        out.append(
            {"name": case_data["name"], "item": case_data["item"], "results": case.results, "reports": case.reports}
        )
    result = {"engine": unreal.SystemLibrary.get_engine_version(), "cases": out}
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
    unreal.log(f"[smoke] results written to {results_path}")


def export_mannequin(out_dir):
    report = mb_export.run(out_dir, list(MANNEQUIN))
    unreal.log(f"[smoke] mannequin export: {'ok' if report['ok'] else 'FAILED'}")


if os.environ.get("MB_SMOKE_STAGE") == "export":
    export_mannequin(os.environ["MB_SMOKE_UE_EXPORT"])
else:
    check(os.environ["MB_SMOKE_CASES"], os.environ["MB_SMOKE_RESULTS"])
