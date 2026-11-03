# SPDX-License-Identifier: GPL-3.0-or-later
"""Stand-in for the editor's ``unreal`` module, limited to what ``ue/scripts`` use.

Written by hand. Class, property and enum names, defaults and read-only flags follow the UE 5.8 Python API;
``dev/check_ue_names.py`` checks them against the editor's stub. Like the editor, an unknown or read-only editor
property raises ``Exception``, a deprecated one ``DeprecationWarning`` and a value of the wrong enum ``TypeError``.

Tests describe what an import creates with ``STATE.plan[<fbx path>] = [(class name, asset name), ...]``;
``None`` makes the import fail.
"""

import os

ENGINE_VERSION = "5.8.4-58902052+++UE5+Release-5.8"
HELPERS = ("reset", "messages", "add_asset", "plan_import", "Prop")


class _State:
    def __init__(self):
        self.assets = {}
        self.directories = set()
        self.logs = []
        self.saved = []
        self.selected = []
        self.plan = {}
        self.imports = []
        self.exports = []
        self.translator_config = {}


STATE = _State()


def reset():
    STATE.__init__()


def _log(level, message):
    STATE.logs.append((level, str(message)))


def log(message):
    _log("Info", message)


def log_warning(message):
    _log("Warning", message)


def log_error(message):
    _log("Error", message)


def messages(level=None):
    return [m for lvl, m in STATE.logs if level is None or lvl == level]


# ---------------------------------------------------------------------- enums


class EnumBase:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __repr__(self):
        return f"<{type(self).__name__}.{self.name}: {self.value}>"


def _enum(name, *members):
    cls = type(name, (EnumBase,), {})
    for value, member in enumerate(members):
        setattr(cls, member, cls(member, value))
    return cls


InterchangeForceMeshType = _enum("InterchangeForceMeshType", "IFMT_NONE", "IFMT_STATIC_MESH", "IFMT_SKELETAL_MESH")
InterchangeVertexColorImportOption = _enum(
    "InterchangeVertexColorImportOption", "IVCIO_REPLACE", "IVCIO_IGNORE", "IVCIO_OVERRIDE"
)
InterchangeCombineStaticMeshesBehavior = _enum(
    "InterchangeCombineStaticMeshesBehavior", "ALL", "VISIBLE_ONLY", "DO_NOT_COMBINE"
)
InterchangeCombineSkeletalMeshesBehavior = _enum(
    "InterchangeCombineSkeletalMeshesBehavior", "BY_SKELETON", "BY_SKELETON_VISIBLE_ONLY", "DO_NOT_COMBINE"
)
InterchangeAnimationRange = _enum("InterchangeAnimationRange", "TIMELINE", "ANIMATED", "SET_RANGE")
InterchangeFrameAlignment = _enum(
    "InterchangeFrameAlignment", "NONE", "SNAP_TO_FLOOR", "SNAP_TO_CLOSEST", "SNAP_TO_CEILING"
)
InterchangeMaterialImportOption = _enum(
    "InterchangeMaterialImportOption", "IMPORT_AS_MATERIALS", "IMPORT_AS_MATERIAL_INSTANCES"
)
FBXImportType = _enum("FBXImportType", "FBXIT_STATIC_MESH", "FBXIT_SKELETAL_MESH", "FBXIT_ANIMATION")
FBXNormalImportMethod = _enum(
    "FBXNormalImportMethod", "FBXNIM_COMPUTE_NORMALS", "FBXNIM_IMPORT_NORMALS", "FBXNIM_IMPORT_NORMALS_AND_TANGENTS"
)
VertexColorImportOption = _enum("VertexColorImportOption", "REPLACE", "IGNORE", "OVERRIDE")
FBXAnimationLengthImportType = _enum(
    "FBXAnimationLengthImportType", "FBXALIT_EXPORTED_TIME", "FBXALIT_ANIMATED_KEY", "FBXALIT_SET_RANGE"
)
FbxExportCompatibility = _enum(
    "FbxExportCompatibility",
    "FBX_2011",
    "FBX_2012",
    "FBX_2013",
    "FBX_2014",
    "FBX_2016",
    "FBX_2018",
    "FBX_2019",
    "FBX_2020",
)


# ---------------------------------------------------------------------- objects and editor properties


class Prop:
    """One editor property: kind is bool/int/float/str/list, an enum class or an object class."""

    def __init__(self, kind, default=None, read_only=False, factory=None):
        self.kind = kind
        self.default = default
        self.read_only = read_only
        self.factory = factory

    def initial(self):
        if self.factory is not None:
            return self.factory()
        return list(self.default) if isinstance(self.default, list) else self.default

    def convert(self, owner, name, value):
        kind = self.kind
        if isinstance(kind, type) and issubclass(kind, EnumBase):
            if not isinstance(value, kind):
                raise TypeError(
                    f"{owner}: Failed to convert type '{type(value).__name__}' to property '{name}' (EnumProperty)"
                )
            return value
        if kind is int and isinstance(value, float):
            return int(value)
        if kind is float and isinstance(value, int):
            return float(value)
        if kind is list:
            return list(value)
        if kind in (bool, int, float, str) and not isinstance(value, kind):
            raise TypeError(f"{owner}: Failed to convert type '{type(value).__name__}' for property '{name}'")
        return value


class _Class:
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name


class _Wrapper:
    _props = {}
    _deprecated = {}

    def __init__(self, **values):
        self._values = {}
        for cls in reversed(type(self).__mro__):
            for name, prop in cls.__dict__.get("_props", {}).items():
                self._values[name] = prop.initial()
        for name, value in values.items():
            self.set_editor_property(name, value)

    @classmethod
    def _prop(cls, name):
        for klass in cls.__mro__:
            if name in klass.__dict__.get("_deprecated", {}):
                raise DeprecationWarning(
                    f"{cls.__name__}: Property '{name}' on '{cls.__name__}' is deprecated: {klass._deprecated[name]}"
                )
            prop = klass.__dict__.get("_props", {}).get(name)
            if prop is not None:
                return prop
        raise Exception(f"{cls.__name__}: Failed to find property '{name}' for attribute '{name}' on '{cls.__name__}'")

    def get_editor_property(self, name):
        self._prop(name)
        return self._values[name]

    def set_editor_property(self, name, value):
        prop = self._prop(name)
        if prop.read_only:
            raise Exception(
                f"{type(self).__name__}: Property '{name}' for attribute '{name}' on '{type(self).__name__}' "
                "is read-only and cannot be set"
            )
        self._values[name] = prop.convert(type(self).__name__, name, value)


class Object(_Wrapper):
    _counter = 0

    def __init__(self, path=None, **values):
        super().__init__(**values)
        if path is None:
            Object._counter += 1
            path = f"/Engine/Transient.{type(self).__name__}_{Object._counter}"
        self._path = path

    def get_path_name(self):
        return self._path

    def get_name(self):
        return self._path.rsplit(".", 1)[-1]

    def get_class(self):
        return _Class(type(self).__name__)


class StructBase(_Wrapper):
    pass


class Skeleton(Object):
    pass


class PhysicsAsset(Object):
    pass


class Material(Object):
    pass


class StaticMesh(Object):
    _props = {"complex_collision_mesh": Prop(Object, None)}


class SkeletalMesh(Object):
    _props = {"skeleton": Prop(Skeleton, None, read_only=True)}


class AnimSequence(Object):
    _props = {"skeleton": Prop(Skeleton, None, read_only=True)}


ASSET_CLASSES = {c.__name__: c for c in (StaticMesh, SkeletalMesh, AnimSequence, Skeleton, PhysicsAsset, Material)}


class SoftObjectPath(StructBase):
    def __init__(self, path_string=""):
        super().__init__()
        self._path_string = path_string

    def export_text(self):
        return self._path_string


class Array(list):
    pass


# ---------------------------------------------------------------------- asset registry helpers


def _object_path(path):
    path = path.split(".")[0]
    return path.rstrip("/")


def add_asset(class_name, path):
    """Create an asset at ``/Game/Folder/Name`` (``class_name`` from ASSET_CLASSES)."""
    package = _object_path(path)
    asset = ASSET_CLASSES[class_name](f"{package}.{package.rsplit('/', 1)[-1]}")
    STATE.assets[package] = asset
    STATE.directories.add(package.rsplit("/", 1)[0])
    return asset


def load_asset(path):
    asset = STATE.assets.get(_object_path(path))
    if asset is None:
        log_warning(f"Failed to find object '{path}'")
    return asset


class EditorAssetLibrary:
    @staticmethod
    def does_directory_exist(directory_path):
        return directory_path.rstrip("/") in STATE.directories

    @staticmethod
    def make_directory(directory_path):
        STATE.directories.add(directory_path.rstrip("/"))
        return True

    @staticmethod
    def does_asset_exist(asset_path):
        return _object_path(asset_path) in STATE.assets

    @staticmethod
    def rename_asset(source_asset_path, destination_asset_path):
        source = _object_path(source_asset_path)
        target = _object_path(destination_asset_path)
        if source not in STATE.assets or target in STATE.assets:
            return False
        asset = STATE.assets.pop(source)
        asset._path = f"{target}.{target.rsplit('/', 1)[-1]}"
        STATE.assets[target] = asset
        return True

    @staticmethod
    def save_loaded_asset(asset_to_save, only_if_is_dirty=True):
        STATE.saved.append(_object_path(asset_to_save.get_path_name()))
        return True


class EditorUtilityLibrary:
    @staticmethod
    def get_selected_assets():
        return Array(STATE.selected)


class SystemLibrary:
    @staticmethod
    def get_engine_version():
        return ENGINE_VERSION


def _create_planned_assets(filename, destination_path):
    planned = STATE.plan.get(os.path.normcase(os.path.abspath(filename)))
    if planned is None:
        return None
    return [add_asset(class_name, f"{destination_path.rstrip('/')}/{name}") for class_name, name in planned]


def plan_import(filename, assets):
    STATE.plan[os.path.normcase(os.path.abspath(filename))] = assets


# ---------------------------------------------------------------------- Interchange


class InterchangePipelineBase(Object):
    pass


class InterchangeGenericTexturePipeline(InterchangePipelineBase):
    _props = {"import_textures": Prop(bool, True)}


class InterchangeGenericMaterialPipeline(InterchangePipelineBase):
    _props = {
        "import_materials": Prop(bool, True),
        "material_import": Prop(InterchangeMaterialImportOption, InterchangeMaterialImportOption.IMPORT_AS_MATERIALS),
        "texture_pipeline": Prop(
            InterchangeGenericTexturePipeline, read_only=True, factory=InterchangeGenericTexturePipeline
        ),
    }


class InterchangeGenericCommonMeshesProperties(InterchangePipelineBase):
    _props = {
        "force_all_mesh_as_type": Prop(InterchangeForceMeshType, InterchangeForceMeshType.IFMT_NONE),
        "bake_meshes": Prop(bool, True),
        "import_lods": Prop(bool, True),
        "import_sockets": Prop(bool, True),
        "recompute_normals": Prop(bool, True),
        "recompute_tangents": Prop(bool, True),
        "use_mikk_t_space": Prop(bool, True),
        "vertex_color_import_option": Prop(
            InterchangeVertexColorImportOption, InterchangeVertexColorImportOption.IVCIO_REPLACE
        ),
        "convert_statics_in_bone_hierarchy_to_skeletals": Prop(bool, True),
        "convert_statics_with_morph_targets_to_skeletals": Prop(bool, False),
    }
    _deprecated = {"auto_detect_mesh_type": "Use bConvertStaticsWithAnimatedTransformToSkeletals instead."}


class InterchangeGenericCommonSkeletalMeshesAndAnimationsProperties(InterchangePipelineBase):
    _props = {
        "skeleton": Prop(Skeleton, None),
        "import_only_animations": Prop(bool, False),
        "use_t0_as_ref_pose": Prop(bool, False),
        "add_curve_metadata_to_skeleton": Prop(bool, True),
        "convert_statics_with_morph_targets_to_skeletals": Prop(bool, False),
    }
    _deprecated = {"import_meshes_in_bone_hierarchy": "Use bConvertStaticsInBoneHierarchyToSkeletals instead."}


class InterchangeGenericMeshPipeline(InterchangePipelineBase):
    _props = {
        "import_static_meshes": Prop(bool, True),
        "import_skeletal_meshes": Prop(bool, True),
        "combine_static_meshes_behavior": Prop(
            InterchangeCombineStaticMeshesBehavior, InterchangeCombineStaticMeshesBehavior.DO_NOT_COMBINE
        ),
        "combine_skeletal_meshes_behavior": Prop(
            InterchangeCombineSkeletalMeshesBehavior, InterchangeCombineSkeletalMeshesBehavior.BY_SKELETON
        ),
        "collision": Prop(bool, True),
        "import_collision_according_to_mesh_name": Prop(bool, True),
        "one_convex_hull_per_ucx": Prop(bool, True),
        "build_nanite": Prop(bool, True),
        "generate_lightmap_u_vs": Prop(bool, False),
        "import_morph_targets": Prop(bool, True),
        "create_physics_asset": Prop(bool, True),
        "bone_influence_limit": Prop(int, 0),
    }
    _deprecated = {
        "import_collision": "Use Collision instead.",
        "combine_static_meshes": "bCombineStaticMeshes is no longer used. Use CombineStaticMeshesBehavior",
    }


class InterchangeGenericAnimationPipeline(InterchangePipelineBase):
    _props = {
        "import_animations": Prop(bool, True),
        "import_bone_tracks": Prop(bool, True),
        "animation_range": Prop(InterchangeAnimationRange, InterchangeAnimationRange.TIMELINE),
        "custom_bone_animation_sample_rate": Prop(int, 0),
        "use30_hz_to_bake_bone_animation": Prop(bool, False),
        "frame_alignment": Prop(InterchangeFrameAlignment, InterchangeFrameAlignment.NONE),
        "import_custom_attribute": Prop(bool, True),
        "remove_curve_redundant_keys": Prop(bool, False),
    }


class InterchangeGenericAssetsPipeline(InterchangePipelineBase):
    _props = {
        "use_source_name_for_asset": Prop(bool, True),
        "common_meshes_properties": Prop(
            InterchangeGenericCommonMeshesProperties, read_only=True, factory=InterchangeGenericCommonMeshesProperties
        ),
        "common_skeletal_meshes_and_animations_properties": Prop(
            InterchangeGenericCommonSkeletalMeshesAndAnimationsProperties,
            read_only=True,
            factory=InterchangeGenericCommonSkeletalMeshesAndAnimationsProperties,
        ),
        "mesh_pipeline": Prop(InterchangeGenericMeshPipeline, read_only=True, factory=InterchangeGenericMeshPipeline),
        "animation_pipeline": Prop(
            InterchangeGenericAnimationPipeline, read_only=True, factory=InterchangeGenericAnimationPipeline
        ),
        "material_pipeline": Prop(
            InterchangeGenericMaterialPipeline, read_only=True, factory=InterchangeGenericMaterialPipeline
        ),
    }

    def __init__(self, **values):
        super().__init__(**values)
        _TRANSIENT[self.get_path_name()] = self


_TRANSIENT = {}


class InterchangeTranslatorSettings(Object):
    def load_settings(self):
        for name, value in STATE.translator_config.get(type(self).__name__, {}).items():
            self._values[name] = value

    def save_settings(self):
        STATE.translator_config[type(self).__name__] = dict(self._values)


class InterchangeFbxTranslatorSettings(InterchangeTranslatorSettings):
    _props = {
        "convert_scene": Prop(bool, True),
        "force_front_x_axis": Prop(bool, False),
        "convert_scene_unit": Prop(bool, True),
        "keep_fbx_namespace": Prop(bool, False),
        "use_ufbx_parser": Prop(bool, False),
    }


class InterchangeSourceData(Object):
    def __init__(self, filename=""):
        super().__init__()
        self._filename = filename

    def get_filename(self):
        return self._filename


class ImportAssetParameters(StructBase):
    _props = {
        "is_automated": Prop(bool, False),
        "replace_existing": Prop(bool, True),
        "override_pipelines": Prop(list, []),
        "destination_name": Prop(str, ""),
        "reimport_asset": Prop(Object, None),
    }


class InterchangeManager(Object):
    @staticmethod
    def get_interchange_manager_scripted():
        return _MANAGER

    @staticmethod
    def create_source_data(in_file_name):
        return InterchangeSourceData(in_file_name)

    def import_asset(self, content_path, source_data, import_asset_parameters):
        """Like the editor: the imported objects on success, None when the C++ call returns false."""
        pipelines = [
            _TRANSIENT.get(path.export_text())
            for path in import_asset_parameters.get_editor_property("override_pipelines")
        ]
        translator = InterchangeFbxTranslatorSettings()
        translator.load_settings()
        STATE.imports.append(
            {
                "content_path": content_path,
                "filename": source_data.get_filename(),
                "params": import_asset_parameters,
                "pipelines": pipelines,
                "translator": dict(translator._values),
            }
        )
        created = _create_planned_assets(source_data.get_filename(), content_path)
        return None if created is None else Array(created)


_MANAGER = InterchangeManager()


# ---------------------------------------------------------------------- legacy FBX import


class Factory(Object):
    pass


class FbxFactory(Factory):
    pass


class FbxAssetImportData(Object):
    _props = {
        "convert_scene": Prop(bool, True),
        "force_front_x_axis": Prop(bool, False),
        "convert_scene_unit": Prop(bool, False),
    }


class FbxMeshImportData(FbxAssetImportData):
    _props = {
        "normal_import_method": Prop(FBXNormalImportMethod, FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS),
        "import_mesh_lods": Prop(bool, False),
    }


class FbxStaticMeshImportData(FbxMeshImportData):
    _props = {
        "combine_meshes": Prop(bool, False),
        "auto_generate_collision": Prop(bool, True),
        "generate_lightmap_u_vs": Prop(bool, True),
        "build_nanite": Prop(bool, False),
        "one_convex_hull_per_ucx": Prop(bool, True),
        "vertex_color_import_option": Prop(VertexColorImportOption, VertexColorImportOption.IGNORE),
    }


class FbxSkeletalMeshImportData(FbxMeshImportData):
    _props = {
        "import_morph_targets": Prop(bool, False),
        "import_meshes_in_bone_hierarchy": Prop(bool, True),
        "use_t0_as_ref_pose": Prop(bool, False),
        "vertex_color_import_option": Prop(VertexColorImportOption, VertexColorImportOption.REPLACE),
    }


class FbxAnimSequenceImportData(FbxAssetImportData):
    _props = {
        "animation_length": Prop(FBXAnimationLengthImportType, FBXAnimationLengthImportType.FBXALIT_EXPORTED_TIME),
        "import_bone_tracks": Prop(bool, True),
        "remove_redundant_keys": Prop(bool, True),
        "use_default_sample_rate": Prop(bool, False),
        "custom_sample_rate": Prop(int, 0),
        "snap_to_closest_frame_boundary": Prop(bool, False),
        "import_meshes_in_bone_hierarchy": Prop(bool, True),
    }


class FbxImportUI(Object):
    _props = {
        "automated_import_should_detect_type": Prop(bool, True),
        "import_mesh": Prop(bool, True),
        "import_as_skeletal": Prop(bool, False),
        "import_animations": Prop(bool, True),
        "import_materials": Prop(bool, True),
        "import_textures": Prop(bool, True),
        "create_physics_asset": Prop(bool, True),
        "mesh_type_to_import": Prop(FBXImportType, FBXImportType.FBXIT_STATIC_MESH),
        "skeleton": Prop(Skeleton, None),
        "static_mesh_import_data": Prop(FbxStaticMeshImportData, factory=FbxStaticMeshImportData),
        "skeletal_mesh_import_data": Prop(FbxSkeletalMeshImportData, factory=FbxSkeletalMeshImportData),
        "anim_sequence_import_data": Prop(FbxAnimSequenceImportData, factory=FbxAnimSequenceImportData),
    }


class AssetImportTask(Object):
    _props = {
        "filename": Prop(str, ""),
        "destination_path": Prop(str, ""),
        "destination_name": Prop(str, ""),
        "replace_existing": Prop(bool, True),
        "automated": Prop(bool, False),
        "save": Prop(bool, False),
        "factory": Prop(Factory, None),
        "options": Prop(Object, None),
        "imported_object_paths": Prop(list, []),
    }


class AssetTools(Object):
    def import_asset_tasks(self, import_tasks):
        for task in import_tasks:
            STATE.imports.append({"task": task})
            created = _create_planned_assets(
                task.get_editor_property("filename"), task.get_editor_property("destination_path")
            )
            paths = [a.get_path_name() for a in created or []]
            task.set_editor_property("imported_object_paths", paths)


class AssetToolsHelpers:
    @staticmethod
    def get_asset_tools():
        return AssetTools()


# ---------------------------------------------------------------------- FBX export


class FbxExportOption(Object):
    _props = {
        "fbx_export_compatibility": Prop(FbxExportCompatibility, FbxExportCompatibility.FBX_2013),
        "ascii": Prop(bool, False),
        "force_front_x_axis": Prop(bool, False),
        "vertex_color": Prop(bool, True),
        "level_of_detail": Prop(bool, True),
        "collision": Prop(bool, True),
        "export_source_mesh": Prop(bool, False),
        "export_morph_targets": Prop(bool, True),
        "export_preview_mesh": Prop(bool, False),
        "map_skeletal_motion_to_root": Prop(bool, False),
        "export_local_time": Prop(bool, True),
    }


class AssetExportTask(Object):
    _props = {
        "object": Prop(Object, None),
        "filename": Prop(str, ""),
        "automated": Prop(bool, False),
        "replace_identical": Prop(bool, True),
        "prompt": Prop(bool, True),
        "write_empty_files": Prop(bool, False),
        "options": Prop(Object, None),
        "exporter": Prop(Object, None),
        "errors": Prop(list, []),
    }


class Exporter(Object):
    @staticmethod
    def run_asset_export_task(task):
        STATE.exports.append(task)
        with open(task.get_editor_property("filename"), "wb") as fh:
            fh.write(b"Kaydara FBX Binary  \x00")
        return True


class ExporterFBX(Exporter):
    pass


class StaticMeshExporterFBX(ExporterFBX):
    pass


class SkeletalMeshExporterFBX(ExporterFBX):
    pass


class AnimSequenceExporterFBX(ExporterFBX):
    pass
