# SPDX-License-Identifier: GPL-3.0-or-later
"""Import the files listed in a MorennBridge manifest into the Unreal Editor.

Run inside UE:  mb_ue_import.py "<path/to/batch.morenn.json>"
Writes "<manifest>.result.json" next to the manifest and keeps the report in mb_ue_common.LAST_RESULT.
"""

import importlib.util
import json
import os
import sys

import unreal

_HERE = os.path.dirname(os.path.abspath(__file__ if "__file__" in globals() else sys.argv[0]))


def _load_common():
    """mb_ue_common from this folder, loaded again on each run: the editor keeps the modules of earlier runs."""
    spec = importlib.util.spec_from_file_location("mb_ue_common", os.path.join(_HERE, "mb_ue_common.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["mb_ue_common"] = module
    spec.loader.exec_module(module)
    return module


C = _load_common()

KIND_STATIC = "STATIC_MESH"
KIND_SKELETAL = "SKELETAL_MESH"
KIND_ANIMATION = "ANIMATION"


# ---------------------------------------------------------------------- interchange


def build_interchange_pipeline(item, options):
    kind = item["kind"]
    p = unreal.InterchangeGenericAssetsPipeline()
    C.setp(p, "use_source_name_for_asset", bool(item.get("single_asset", True)))

    cm = C.getp(p, "common_meshes_properties")
    if cm is not None:
        force = {KIND_STATIC: ("IFMT_STATIC_MESH",), KIND_SKELETAL: ("IFMT_SKELETAL_MESH",)}.get(kind, ("IFMT_NONE",))
        ft = C.enum_value("InterchangeForceMeshType", *force)
        if ft is not None:
            C.setp(cm, "force_all_mesh_as_type", ft)
        C.setp(cm, "bake_meshes", True)
        C.setp(cm, "import_lods", True)
        C.setp(cm, "import_sockets", True)
        C.setp(cm, "recompute_normals", False)
        C.setp(cm, "recompute_tangents", bool(options.get("recompute_tangents", True)))
        C.setp(cm, "use_mikk_t_space", True)
        vc = C.enum_value("InterchangeVertexColorImportOption", "IVCIO_REPLACE", "REPLACE")
        if vc is not None:
            C.setp(cm, "vertex_color_import_option", vc)
        C.setp(cm, "convert_statics_in_bone_hierarchy_to_skeletals", False, optional=True)
        C.setp(cm, "convert_statics_with_morph_targets_to_skeletals", False, optional=True)

    sk = C.getp(p, "common_skeletal_meshes_and_animations_properties")
    if sk is not None:
        skeleton = C.load(item["skeleton"]) if item.get("skeleton") else None
        if skeleton is not None:
            C.setp(sk, "skeleton", skeleton)
        C.setp(sk, "import_only_animations", kind == KIND_ANIMATION)
        C.setp(sk, "use_t0_as_ref_pose", False)
        C.setp(sk, "add_curve_metadata_to_skeleton", True, optional=True)

    mp = C.getp(p, "mesh_pipeline")
    if mp is not None:
        C.setp(mp, "import_static_meshes", kind == KIND_STATIC)
        C.setp(mp, "import_skeletal_meshes", kind == KIND_SKELETAL)
        combine_static = C.enum_value("InterchangeCombineStaticMeshesBehavior", "DO_NOT_COMBINE", "DONT_COMBINE")
        if combine_static is not None:
            C.setp(mp, "combine_static_meshes_behavior", combine_static, optional=True)
        combine_skel = C.enum_value("InterchangeCombineSkeletalMeshesBehavior", "BY_SKELETON")
        if combine_skel is not None:
            C.setp(mp, "combine_skeletal_meshes_behavior", combine_skel, optional=True)
        C.setp(mp, "collision", True, optional=True)
        C.setp(mp, "import_collision_according_to_mesh_name", True)
        C.setp(mp, "one_convex_hull_per_ucx", True)
        C.setp(mp, "build_nanite", bool(options.get("build_nanite", False)))
        C.setp(
            mp,
            "generate_lightmap_u_vs",
            bool(options.get("generate_lightmap_uvs", False)),
            alternatives=("generate_lightmap_uvs",),
        )
        C.setp(mp, "import_morph_targets", True)
        C.setp(mp, "create_physics_asset", bool(options.get("create_physics_asset", True)))
        C.setp(mp, "bone_influence_limit", int(options.get("influence_limit", 0)), optional=True)

    ap = C.getp(p, "animation_pipeline")
    if ap is not None:
        C.setp(ap, "import_animations", kind == KIND_ANIMATION)
        C.setp(ap, "import_bone_tracks", True)
        rng = C.enum_value("InterchangeAnimationRange", "TIMELINE")
        if rng is not None:
            C.setp(ap, "animation_range", rng)
        fps = float(item.get("fps", 0) or 0)
        if fps > 0:
            C.setp(ap, "custom_bone_animation_sample_rate", int(round(fps)), optional=True)
        C.setp(
            ap,
            "use30_hz_to_bake_bone_animation",
            False,
            alternatives=("use_30hz_to_bake_bone_animation",),
            optional=True,
        )
        snap = options.get("frame_alignment") == "SNAP_TO_CLOSEST"
        fa = C.enum_value("InterchangeFrameAlignment", "SNAP_TO_CLOSEST" if snap else "NONE")
        if fa is not None:
            C.setp(ap, "frame_alignment", fa)
        C.setp(ap, "import_custom_attribute", True, optional=True)
        C.setp(ap, "remove_curve_redundant_keys", False, optional=True)

    mat = C.getp(p, "material_pipeline")
    if mat is not None:
        C.setp(mat, "import_materials", bool(options.get("import_materials", True)))
        mi = C.enum_value("InterchangeMaterialImportOption", "IMPORT_AS_MATERIALS")
        if mi is not None:
            C.setp(mat, "material_import", mi, optional=True)
        tex = C.getp(mat, "texture_pipeline")
        if tex is not None:
            C.setp(tex, "import_textures", bool(options.get("import_textures", False)))
    return p


# Interchange reads these from the user's EditorPerProjectUserSettings.ini, never from the pipeline
TRANSLATOR_REQUIRED = {"force_front_x_axis": False, "use_ufbx_parser": False}
TRANSLATOR_LABELS = {"force_front_x_axis": "Force Front X Axis", "use_ufbx_parser": "Use uFBX Translator"}


class TranslatorSettingsOverride:
    """Turns off the FBX translator settings our files cannot use during the import, then restores the user's."""

    def __init__(self, active=True):
        self.active = active
        self.settings = None
        self.changed = {}
        self.warnings = []

    def __enter__(self):
        cls = getattr(unreal, "InterchangeFbxTranslatorSettings", None) if self.active else None
        if cls is None:
            return self
        try:
            settings = cls()
            settings.load_settings()
            for name, value in TRANSLATOR_REQUIRED.items():
                current = C.getp(settings, name)
                if current is not None and current != value and C.setp(settings, name, value):
                    self.changed[name] = current
            if self.changed:
                settings.save_settings()
                self.settings = settings
                labels = ", ".join(TRANSLATOR_LABELS[n] for n in self.changed)
                self.note(f"Imported with the FBX translator setting(s) {labels} off; your settings are restored")
        except Exception as exc:
            self.note(f"Could not check the Interchange FBX translator settings: {exc}")
        return self

    def __exit__(self, *exc_info):
        if self.settings is not None:
            try:
                for name, value in self.changed.items():
                    C.setp(self.settings, name, value)
                self.settings.save_settings()
            except Exception as exc:
                self.note(f"Could not restore the Interchange FBX translator settings: {exc}")
        return False

    def note(self, message):
        self.warnings.append(message)
        C.log(message)


def import_interchange(item, options):
    pipeline = build_interchange_pipeline(item, options)
    params = unreal.ImportAssetParameters()
    C.setp(params, "is_automated", True)
    C.setp(params, "replace_existing", True)
    C.setp(params, "override_pipelines", [unreal.SoftObjectPath(pipeline.get_path_name())])
    if item.get("single_asset") and item.get("assets"):
        C.setp(params, "destination_name", item["assets"][0]["name"], optional=True)
    manager = unreal.InterchangeManager.get_interchange_manager_scripted()
    source = unreal.InterchangeManager.create_source_data(item["path"])
    # Python drops the bool return value: the imported objects on success, None on failure
    objects = manager.import_asset(item["destination_path"], source, params)
    paths = list(dict.fromkeys(C.asset_path(o) for o in objects or [] if o is not None))
    return bool(paths), paths


# ---------------------------------------------------------------------- legacy


def import_legacy(item, options):
    kind = item["kind"]
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", item["path"])
    task.set_editor_property("destination_path", item["destination_path"])
    if item.get("single_asset") and item.get("assets"):
        task.set_editor_property("destination_name", item["assets"][0]["name"])
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("automated", True)
    task.set_editor_property("save", False)
    task.set_editor_property("factory", unreal.FbxFactory())
    ui = unreal.FbxImportUI()
    C.setp(ui, "automated_import_should_detect_type", False, optional=True)
    C.setp(ui, "import_mesh", kind != KIND_ANIMATION)
    C.setp(ui, "import_as_skeletal", kind == KIND_SKELETAL)
    C.setp(ui, "import_animations", kind == KIND_ANIMATION)
    C.setp(ui, "import_materials", bool(options.get("import_materials", True)))
    C.setp(ui, "import_textures", bool(options.get("import_textures", False)))
    C.setp(ui, "create_physics_asset", bool(options.get("create_physics_asset", True)))
    mesh_type = {
        KIND_STATIC: "FBXIT_STATIC_MESH",
        KIND_SKELETAL: "FBXIT_SKELETAL_MESH",
        KIND_ANIMATION: "FBXIT_ANIMATION",
    }[kind]
    mt = C.enum_value("FBXImportType", mesh_type)
    if mt is not None:
        C.setp(ui, "mesh_type_to_import", mt)
    skeleton = C.load(item["skeleton"]) if item.get("skeleton") else None
    if skeleton is not None:
        C.setp(ui, "skeleton", skeleton)
    normals = C.enum_value("FBXNormalImportMethod", "FBXNIM_IMPORT_NORMALS_AND_TANGENTS")
    smd = C.getp(ui, "static_mesh_import_data")
    if smd is not None:
        if normals is not None:
            C.setp(smd, "normal_import_method", normals)
        C.setp(smd, "import_mesh_lods", True, alternatives=("import_mesh_lo_ds",))
        C.setp(smd, "combine_meshes", False)
        C.setp(smd, "auto_generate_collision", False)
        C.setp(
            smd,
            "generate_lightmap_u_vs",
            bool(options.get("generate_lightmap_uvs", False)),
            alternatives=("generate_lightmap_uvs",),
        )
        C.setp(smd, "convert_scene", True)
        C.setp(smd, "convert_scene_unit", True)
        C.setp(smd, "force_front_x_axis", False)
        C.setp(smd, "build_nanite", bool(options.get("build_nanite", False)), optional=True)
        vc = C.enum_value("VertexColorImportOption", "REPLACE")
        if vc is not None:
            C.setp(smd, "vertex_color_import_option", vc)
    skd = C.getp(ui, "skeletal_mesh_import_data")
    if skd is not None:
        if normals is not None:
            C.setp(skd, "normal_import_method", normals)
        C.setp(skd, "import_morph_targets", True)
        C.setp(skd, "import_mesh_lods", True, alternatives=("import_mesh_lo_ds",))
        C.setp(skd, "import_meshes_in_bone_hierarchy", False)
        C.setp(skd, "use_t0_as_ref_pose", False)
        C.setp(skd, "convert_scene", True)
        C.setp(skd, "convert_scene_unit", True)
        C.setp(skd, "force_front_x_axis", False)
    asd = C.getp(ui, "anim_sequence_import_data")
    if asd is not None:
        al = C.enum_value("FBXAnimationLengthImportType", "FBXALIT_EXPORTED_TIME")
        if al is not None:
            C.setp(asd, "animation_length", al)
        C.setp(asd, "import_bone_tracks", True)
        C.setp(asd, "remove_redundant_keys", False)
        C.setp(asd, "use_default_sample_rate", False)
        fps = int(round(float(item.get("fps", 0) or 0)))
        if fps > 0:
            C.setp(asd, "custom_sample_rate", fps)
        C.setp(
            asd, "snap_to_closest_frame_boundary", options.get("frame_alignment") == "SNAP_TO_CLOSEST", optional=True
        )
        C.setp(asd, "convert_scene", True)
        C.setp(asd, "convert_scene_unit", True)
        C.setp(asd, "force_front_x_axis", False)
    task.set_editor_property("options", ui)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    paths = list(dict.fromkeys(p.split(".")[0] for p in task.get_editor_property("imported_object_paths")))
    return bool(paths), paths


# ---------------------------------------------------------------------- post-processing


def missing_assets(item, imported_paths):
    """Asset paths the manifest announces that the import did not create."""
    folder = item["destination_path"].rstrip("/")
    return [f"{folder}/{a['name']}" for a in item.get("assets", []) if f"{folder}/{a['name']}" not in imported_paths]


def post_process(item, options, paths, report_item):
    renames = []
    skeleton_path = None
    base = None
    for a in item.get("assets", []):
        if a.get("role") != "COLLISION_MESH":
            base = a["name"]
            break
    base_noprefix = base.split("_", 1)[1] if base and "_" in base else base
    for index, path in enumerate(list(paths)):
        asset = C.load(path)
        if asset is None:
            continue
        cls = asset.get_class().get_name()
        new_path = path
        if cls == "Skeleton":
            if options.get("rename_skeleton") and base_noprefix:
                new_path = C.rename_asset(asset, f"{options.get('skeleton_prefix', 'SKEL_')}{base_noprefix}")
            skeleton_path = new_path
        elif cls == "PhysicsAsset" and options.get("rename_skeleton") and base_noprefix:
            new_path = C.rename_asset(asset, f"{options.get('physics_prefix', 'PHYS_')}{base_noprefix}")
        if new_path != path:
            renames.append([path, new_path])
            paths[index] = new_path
    # complex collision mesh link
    if options.get("link_complex_collision"):
        for a in item.get("assets", []):
            coll_name = a.get("collision_mesh")
            if not coll_name:
                continue
            folder = item["destination_path"]
            render = C.load(f"{folder}/{a['name']}")
            coll = C.load(f"{folder}/{coll_name}")
            if render is not None and coll is not None:
                if C.setp(render, "complex_collision_mesh", coll):
                    report_item["complex_collision"] = f"{folder}/{coll_name}"
    if options.get("save", True):
        for path in paths:
            asset = C.load(path)
            if asset is not None:
                C.save_asset(asset)
    report_item["renamed"] = renames
    if skeleton_path:
        report_item["skeleton"] = skeleton_path


def import_item(item, options, use_interchange):
    report_item = {
        "file": item["file"],
        "kind": item["kind"],
        "success": False,
        "imported": [],
        "warnings": [],
        "errors": [],
    }
    try:
        C.ensure_directory(item["destination_path"])
        if use_interchange:
            ok, paths = import_interchange(item, options)
        else:
            ok, paths = import_legacy(item, options)
        report_item["imported"] = paths
        report_item["success"] = ok
        if not ok:
            report_item["errors"].append("Importer reported failure")
        else:
            for path in missing_assets(item, paths):
                report_item["warnings"].append(f"{path} was not created")
            post_process(item, options, paths, report_item)
    except Exception as exc:
        report_item["errors"].append(f"{exc!r}\n{C.exc_text()}")
        C.error(f"{item['file']}: {exc!r}")
    return report_item


def run(manifest_path):
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    options = manifest.get("options", {})
    use_interchange = manifest.get("ue_importer", "INTERCHANGE") == "INTERCHANGE"
    report = {"ok": True, "engine": unreal.SystemLibrary.get_engine_version(), "items": []}
    with TranslatorSettingsOverride(active=use_interchange) as translator:
        for item in manifest.get("items", []):
            # animations need the skeleton created by a previous item of the same batch
            if item["kind"] == KIND_ANIMATION and not item.get("skeleton"):
                src = item.get("source_armature")
                for done in report["items"]:
                    if done.get("skeleton") and done.get("source_armature") == src:
                        item["skeleton"] = done["skeleton"]
                        break
            r = import_item(item, options, use_interchange)
            r["source_armature"] = item.get("source_armature")
            report["items"].append(r)
            report["ok"] = report["ok"] and r["success"]
            C.log(f"{item['file']}: {'ok' if r['success'] else 'FAILED'} -> {', '.join(r['imported'])}")
    report["warnings"] = translator.warnings
    C.set_result(report, manifest_path + ".result.json")
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a]
    if not args:
        C.error("usage: mb_ue_import.py <manifest.morenn.json>")
    else:
        run(args[0])
