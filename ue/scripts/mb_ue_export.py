# SPDX-License-Identifier: GPL-3.0-or-later
"""Export Unreal assets (selected in the Content Browser, or given by path) to FBX for MorennBridge.

Run inside UE:  mb_ue_export.py "<out_dir>" ["/Game/Path/Asset" ...]
Writes "<out_dir>/pull.result.json".
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__ if "__file__" in globals() else sys.argv[0]))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import mb_ue_common as C
import unreal

EXPORTERS = {
    "StaticMesh": ("StaticMeshExporterFBX", "STATIC_MESH"),
    "SkeletalMesh": ("SkeletalMeshExporterFBX", "SKELETAL_MESH"),
    "AnimSequence": ("AnimSequenceExporterFBX", "ANIMATION"),
}


def export_options():
    opts = unreal.FbxExportOption()
    compat = C.enum_value("FbxExportCompatibility", "FBX_2013")
    if compat is not None:
        C.setp(opts, "fbx_export_compatibility", compat)
    C.setp(opts, "ascii", False)
    C.setp(opts, "force_front_x_axis", False)
    C.setp(opts, "vertex_color", True)
    C.setp(opts, "level_of_detail", True)
    C.setp(opts, "collision", True)
    C.setp(opts, "export_source_mesh", False, optional=True)
    C.setp(opts, "export_morph_targets", True)
    C.setp(opts, "export_preview_mesh", False)
    C.setp(opts, "map_skeletal_motion_to_root", False)
    C.setp(opts, "export_local_time", True)
    return opts


def export_asset(asset, out_dir):
    cls = asset.get_class().get_name()
    if cls not in EXPORTERS:
        return {"asset_path": C.asset_path(asset), "success": False, "errors": [f"Unsupported class {cls}"]}
    exporter_cls, kind = EXPORTERS[cls]
    filename = os.path.join(out_dir, f"{asset.get_name()}.fbx")
    task = unreal.AssetExportTask()
    task.set_editor_property("object", asset)
    task.set_editor_property("filename", filename)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_identical", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("write_empty_files", False)
    task.set_editor_property("options", export_options())
    exporter_type = getattr(unreal, exporter_cls, None)
    if exporter_type is not None:
        task.set_editor_property("exporter", exporter_type())
    ok = unreal.Exporter.run_asset_export_task(task)
    item = {
        "asset_path": C.asset_path(asset),
        "kind": kind,
        "file": filename,
        "success": bool(ok) and os.path.exists(filename),
        "errors": [str(e) for e in task.get_editor_property("errors")],
    }
    if cls == "SkeletalMesh":
        skel = C.getp(asset, "skeleton")
        item["skeleton"] = C.asset_path(skel) if skel is not None else None
    elif cls == "AnimSequence":
        skel = C.getp(asset, "skeleton")
        item["skeleton"] = C.asset_path(skel) if skel is not None else None
    return item


def run(out_dir, asset_paths):
    os.makedirs(out_dir, exist_ok=True)
    assets = []
    if asset_paths:
        for p in asset_paths:
            a = C.load(p)
            if a is not None:
                assets.append(a)
            else:
                C.warn(f"Asset not found: {p}")
    else:
        try:
            assets = list(unreal.EditorUtilityLibrary.get_selected_assets())
        except Exception:
            assets = []
    report = {"ok": True, "out_dir": out_dir, "items": []}
    for asset in assets:
        item = export_asset(asset, out_dir)
        report["items"].append(item)
        report["ok"] = report["ok"] and item["success"]
        C.log(f"{item['asset_path']}: {'exported' if item['success'] else 'FAILED'}")
    C.set_result(report, os.path.join(out_dir, "pull.result.json"))
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a]
    if not args:
        C.error("usage: mb_ue_export.py <out_dir> [asset paths...]")
    else:
        run(args[0], args[1:])
