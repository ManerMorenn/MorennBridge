# SPDX-License-Identifier: GPL-3.0-or-later
"""Unreal side of the smoke test, run by the editor's Python (UnrealEditor-Cmd -run=pythonscript).

MB_SMOKE_STAGE=export   exports the template's mannequin assets with mb_ue_export.py into MB_SMOKE_UE_EXPORT.
MB_SMOKE_STAGE=check    imports the cases listed in MB_SMOKE_CASES with mb_ue_import.py, each into its own folder
                        (deleted first), checks the assets and writes MB_SMOKE_RESULTS.
"""

import importlib
import json
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
