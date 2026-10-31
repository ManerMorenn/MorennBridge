# SPDX-License-Identifier: GPL-3.0-or-later
"""Export manifest: what was written, how Unreal should import it. Consumed by the UE-side script."""

import datetime
import json
import os

import bpy

from .. import __version__

MANIFEST_VERSION = 1


def new_manifest(prefs, ue_importer):
    return {
        "version": MANIFEST_VERSION,
        "generator": f"MorennBridge {__version__}",
        "blender": bpy.app.version_string,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "ue_importer": ue_importer,
        "options": {
            "build_nanite": bool(prefs.ue_build_nanite),
            "generate_lightmap_uvs": bool(prefs.ue_generate_lightmap_uvs),
            "create_physics_asset": bool(prefs.ue_create_physics_asset),
            "import_materials": bool(prefs.ue_import_materials),
            "import_textures": bool(prefs.ue_import_textures),
            "influence_limit": int(prefs.influence_limit),
            "frame_alignment": prefs.ue_frame_alignment,
            "recompute_tangents": bool(prefs.ue_recompute_tangents),
            "save": bool(prefs.ue_save_assets),
            "rename_skeleton": bool(prefs.ue_rename_skeleton),
            "skeleton_prefix": prefs.prefix_skeleton,
            "physics_prefix": prefs.prefix_physics,
            "link_complex_collision": bool(prefs.ue_link_complex_collision),
        },
        "items": [],
    }


def add_item(manifest, file_path, kind, destination_path, assets, single_asset, **extra):
    item = {
        "file": os.path.basename(file_path),
        "path": file_path,
        "kind": kind,
        "destination_path": destination_path,
        "single_asset": bool(single_asset),
        "assets": assets,
    }
    item.update(extra)
    manifest["items"].append(item)
    return item


def write(manifest, export_dir, batch_name):
    path = os.path.join(export_dir, f"{batch_name}.morenn.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return path


def read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
