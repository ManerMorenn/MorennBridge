# SPDX-License-Identifier: GPL-3.0-or-later
"""Export orchestration: groups -> staging -> Blender FBX exporter -> binary post-pass -> manifest."""

import os
from dataclasses import dataclass, field

import bpy

from .. import __version__, compat
from ..fbxpatch import patches, tree
from . import anim_bake, bones, classify, naming, staging
from . import manifest as manifest_mod
from .classify import KIND_ANIMATION, KIND_SKELETAL, KIND_STATIC

MARKER_AXES = "MorennBridge|BoneAxes"
MARKER_AXES_MAP = "MorennBridge|BoneAxesMap"
EXPORTER_BONE_AXES = ("Y", "X")  # exporter correction disabled: MorennBridge applies per-bone corrections itself
MARKER_VERSION = "MorennBridge|Version"


@dataclass
class ExportSettings:
    export_dir: str
    destination_path: str = "/Game/Imports"
    file_mode: str = "PER_ASSET"
    lod_mode: str = "AUTO"
    ue_importer: str = "INTERCHANGE"
    triangulate: bool = True
    export_tangents: bool = True
    smoothing: str = "SMOOTH_GROUP"
    vertex_color_space: str = "SRGB"
    textures: str = "NONE"
    influence_limit: int = 8
    bone_primary: str = "X"
    bone_secondary: str = "Y"
    strip_armature_null: bool = True
    generate_collision_mesh: bool = False
    collision_ratio: float = 0.1
    collision_mesh_file: str = "SAME"
    collision_mesh_template: str = "{asset}_COLL"
    collision_mesh_min_faces: int = 12
    batch_name: str = "export"
    prefs: object = None

    @classmethod
    def from_prefs(cls, prefs, scene, export_dir=None, destination=None):
        s = scene.morenn
        return cls(
            export_dir=bpy.path.abspath(export_dir or s.export_dir),
            destination_path=destination or s.ue_content_path or prefs.ue_content_root,
            file_mode=prefs.file_mode,
            lod_mode=prefs.lod_mode,
            ue_importer=prefs.ue_importer,
            triangulate=prefs.triangulate,
            export_tangents=prefs.export_tangents,
            smoothing=prefs.smoothing,
            vertex_color_space=prefs.vertex_color_space,
            textures=prefs.textures,
            influence_limit=prefs.influence_limit,
            bone_primary=prefs.bone_primary,
            bone_secondary=prefs.bone_secondary,
            strip_armature_null=prefs.strip_armature_null,
            generate_collision_mesh=s.generate_collision_mesh,
            collision_ratio=s.collision_ratio,
            collision_mesh_file=prefs.collision_mesh_file,
            collision_mesh_template=prefs.collision_mesh_template,
            collision_mesh_min_faces=prefs.collision_mesh_min_faces,
            prefs=prefs,
        )

    @property
    def resolved_lod_mode(self):
        if self.lod_mode == "AUTO":
            return "LOD_NAMING" if self.ue_importer == "INTERCHANGE" else "LODGROUP_NODES"
        return self.lod_mode


@dataclass
class ExportedFile:
    path: str
    kind: str
    groups: list
    assets: list = field(default_factory=list)  # manifest asset entries
    node_names: list = field(default_factory=list)  # final FBX node names (Models)
    lod_specs: list = field(default_factory=list)
    single_asset: bool = True
    has_armature: bool = False
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------- planning


def plan_files(groups, settings):
    """Decide which groups share which FBX file."""
    files = []
    static = [g for g in groups if g.kind == KIND_STATIC]
    others = [g for g in groups if g.kind != KIND_STATIC]
    if static:
        if settings.file_mode == "COMBINED" and len(static) > 1:
            path = os.path.join(settings.export_dir, f"{naming.sanitize_asset_name(settings.batch_name)}.fbx")
            files.append(ExportedFile(path, KIND_STATIC, static))
        else:
            for g in static:
                files.append(ExportedFile(os.path.join(settings.export_dir, f"{g.asset_name}.fbx"), KIND_STATIC, [g]))
    for g in others:
        files.append(ExportedFile(os.path.join(settings.export_dir, f"{g.asset_name}.fbx"), g.kind, [g]))
    return files


# ---------------------------------------------------------------------- staging per file


def _stage_static_group(st, group, settings, lod_mode, node_names, report):
    """Stage one static-mesh group. Returns the manifest asset entries for it."""
    render = group.render[0]
    anchor = staging.translation_removed(render.matrix_world)
    lod_count = group.lod_count
    has_lods = lod_count > 1 and lod_mode != "NONE"
    render_node = naming.lod_node_name(group.asset_name, 0, lod_mode) if has_lods else group.asset_name

    def take(name):
        if name in node_names:
            raise staging.StagingError(f"Duplicate FBX node name '{name}' (E008)")
        node_names.append(name)
        return name

    render_obj = st.stage_mesh(
        render, take(render_node), anchor @ render.matrix_world, role="RENDER", triangulate=settings.triangulate
    )
    entry = {"node": group.asset_name, "name": group.asset_name, "lods": 1, "collisions": 0, "sockets": 0}
    lod_nodes = [render_node]
    if has_lods:
        for index in range(1, lod_count):
            objs = group.lods.get(index, [])
            if not objs:
                report.warning(f"'{group.asset_name}': LOD{index} missing; LOD chain has a gap")
                continue
            if len(objs) > 1:
                report.warning(f"'{group.asset_name}': several objects for LOD{index}; using '{objs[0].name}'")
            lod_obj = objs[0]
            node = take(naming.lod_node_name(group.asset_name, index, lod_mode))
            st.stage_mesh(lod_obj, node, anchor @ lod_obj.matrix_world, role="LOD", triangulate=settings.triangulate)
            lod_nodes.append(node)
        entry["lods"] = len(lod_nodes)
    elif group.lods:
        report.info(f"'{group.asset_name}': LOD objects ignored (LOD mode = NONE)")

    counters = {}
    for obj, kind in group.collisions:
        counters[kind] = counters.get(kind, 0) + 1
        node = take(naming.collision_node_name(kind, render_node, counters[kind]))
        st.stage_mesh(
            obj,
            node,
            anchor @ obj.matrix_world,
            role="COLLISION",
            triangulate=True,
            keep_shape_keys=False,
            strip_attributes=True,
            keep_materials=False,
        )
        entry["collisions"] += 1
    for sock in group.sockets:
        base = sock.name[len(naming.SOCKET_PREFIX) :] if naming.is_socket_name(sock.name) else sock.name
        node = take(naming.socket_node_name(naming.sanitize_ue_name(base)))
        st.stage_empty(sock, node, anchor @ sock.matrix_world, parent=render_obj)
        entry["sockets"] += 1

    assets = [entry]
    if settings.generate_collision_mesh and settings.collision_mesh_file == "SAME":
        coll_name = take(naming.collision_mesh_name(settings.collision_mesh_template, group.asset_name))
        coll_obj = st.duplicate_mesh_object(render_obj, coll_name, "COLLISION_MESH")
        _prepare_collision_mesh(st, coll_obj, settings)
        entry["collision_mesh"] = coll_name
        entry["collision_ratio"] = settings.collision_ratio
        assets.append({"node": coll_name, "name": coll_name, "role": "COLLISION_MESH"})
    return assets, lod_nodes


def _prepare_collision_mesh(st, obj, settings):
    me = obj.data
    while me.uv_layers:
        me.uv_layers.remove(me.uv_layers[0])
    while me.color_attributes:
        me.color_attributes.remove(me.color_attributes[0])
    me.materials.clear()
    if hasattr(me, "shape_keys") and me.shape_keys is not None:
        obj.shape_key_clear()
    faces = st.decimate(obj, settings.collision_ratio, settings.collision_mesh_min_faces)
    st.report.info(f"Collision mesh '{st.name_map[obj.name]}': {faces} faces")


def export_bone_names(arm_obj):
    """Bones to export: deform bones, INCLUDE-tagged bones, ik_/SOCKET_ helpers; minus EXCLUDE."""
    names = set()
    for bone in arm_obj.data.bones:
        tag = bone.morenn.export
        if tag == "EXCLUDE":
            continue
        lname = bone.name.lower()
        if tag == "INCLUDE" or bone.use_deform or lname.startswith("ik_") or bone.name.startswith("SOCKET_"):
            names.add(bone.name)
    return names


def bone_closure(arm_obj, names):
    """``names`` plus all their ancestors (what the exporter writes with deform-only)."""
    out = set(names)
    for name in names:
        bone = arm_obj.data.bones.get(name)
        while bone is not None and bone.parent is not None:
            out.add(bone.parent.name)
            bone = bone.parent
    return out


def merge_targets_for(arm_obj, exported):
    """For every non-exported bone, the nearest exported ancestor (weights are merged into it)."""
    targets = {}
    for bone in arm_obj.data.bones:
        if bone.name in exported:
            continue
        p = bone.parent
        while p is not None and p.name not in exported:
            p = p.parent
        if p is not None:
            targets[bone.name] = p.name
    return targets


def bone_axes_map(arm_obj, settings):
    """Per-bone ``(primary, secondary)`` for export (stored per-bone choices, else the armature convention)."""
    default = bone_axes_for(arm_obj, settings)
    return default, bones.axes_map_from_stored(arm_obj, default)


def _stage_skeletal_group(st, group, settings, node_names, report):
    arm = group.armature
    anchor = staging.translation_removed(arm.matrix_world)
    export_set = export_bone_names(arm)
    exported = bone_closure(arm, export_set)
    default, axes_map = bone_axes_map(arm, settings)
    arm_obj = st.stage_armature(
        arm,
        anchor @ arm.matrix_world,
        export_set,
        final_name="Armature",
        pose_position="REST",
        corrections=bones.matrices_for(axes_map, inverse=True),
    )
    node_names.append("Armature")
    targets = merge_targets_for(arm, exported)
    entry = {"node": group.asset_name, "name": group.asset_name, "meshes": 0, "bones": len(exported)}
    first = True
    for mesh in group.render:
        node = group.asset_name if first else f"{group.asset_name}_{naming.sanitize_asset_name(mesh.name)}"
        if node in node_names:
            raise staging.StagingError(f"Duplicate FBX node name '{node}' (E008)")
        node_names.append(node)
        obj = st.stage_mesh(
            mesh,
            node,
            anchor @ mesh.matrix_world,
            role="RENDER",
            triangulate=settings.triangulate,
            keep_shape_keys=True,
            armature=arm_obj,
        )
        n_trunc, n_unweighted = staging.limit_influences(obj, exported, settings.influence_limit, targets)
        if n_trunc:
            report.info(f"'{mesh.name}': {n_trunc} vertices had more than {settings.influence_limit} influences (I009)")
        if n_unweighted:
            report.warning(f"'{mesh.name}': {n_unweighted} vertices have no weights on exported bones (W021)")
        entry["meshes"] += 1
        first = False
    roots = [b.name for b in arm.data.bones if b.parent is None and b.name in exported]
    entry["root_bone"] = roots[0] if roots else ""
    if len(roots) != 1:
        raise staging.StagingError(f"'{arm.name}': Unreal needs exactly one root bone, found {len(roots)} (E001)")
    entry["morph_targets"] = any(m.data.shape_keys is not None for m in group.render)
    entry["bone_axes"] = f"{default[0]}{default[1]}"
    entry["bone_axes_map"] = bones.serialize_axes_map(axes_map, default)
    return [entry]


def _stage_anim_group(st, group, settings, node_names, report):
    arm = group.armature
    if group.frame_range is None or group.frame_range[1] < group.frame_range[0]:
        raise staging.StagingError(f"'{group.asset_name}': empty frame range (E022)")
    anchor = staging.translation_removed(arm.matrix_world)
    export_set = export_bone_names(arm)
    exported = bone_closure(arm, export_set)
    default, axes_map = bone_axes_map(arm, settings)
    corrections = bones.matrices_for(axes_map, inverse=True)
    arm_obj = st.stage_armature(
        arm, anchor @ arm.matrix_world, export_set, final_name="Armature", pose_position="POSE", corrections=corrections
    )
    node_names.append("Armature")
    world_scale = arm.matrix_world.to_scale()
    scale = st.U * world_scale.x
    act = anim_bake.bake_visual_pose(
        st.context,
        arm,
        arm_obj,
        sorted(exported),
        group.frame_range[0],
        group.frame_range[1],
        scale,
        group.asset_name,
        action=group.action,
        solo_track=group.nla_track,
        report=report,
        conjugate=corrections,
    )
    st.datablocks.append(act)
    st.temp_scene.frame_start, st.temp_scene.frame_end = int(group.frame_range[0]), int(group.frame_range[1])
    if group.render:
        targets = merge_targets_for(arm, exported)
        for i, mesh in enumerate(group.render):
            node = f"{group.base}_Mesh" if i == 0 else f"{group.base}_Mesh_{i}"
            node_names.append(node)
            obj = st.stage_mesh(
                mesh,
                node,
                anchor @ mesh.matrix_world,
                role="RENDER",
                triangulate=settings.triangulate,
                keep_shape_keys=True,
                armature=arm_obj,
            )
            staging.limit_influences(obj, exported, settings.influence_limit, targets)
    return [
        {
            "name": group.asset_name,
            "bones": len(exported),
            "frame_start": int(group.frame_range[0]),
            "frame_end": int(group.frame_range[1]),
            "bone_axes": f"{default[0]}{default[1]}",
            "bone_axes_map": bones.serialize_axes_map(axes_map, default),
        }
    ]


# ---------------------------------------------------------------------- kwargs + post-pass


def exporter_kwargs(path, collection_name, kind, settings, has_uvs, bone_axes, bake_anim=False):
    object_types = {"MESH", "EMPTY"}
    if kind == KIND_SKELETAL:
        object_types = {"ARMATURE", "MESH", "EMPTY"}
    elif kind == KIND_ANIMATION:
        object_types = {"ARMATURE", "MESH"}
    kw = dict(
        filepath=path,
        check_existing=False,
        use_selection=False,
        use_visible=False,
        use_active_collection=False,
        collection=collection_name,
        global_scale=1.0,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_NONE",
        use_space_transform=True,
        bake_space_transform=False,
        axis_forward="Y",
        axis_up="Z",
        object_types=object_types,
        use_mesh_modifiers=True,
        use_mesh_modifiers_render=False,
        mesh_smooth_type=settings.smoothing,
        colors_type=settings.vertex_color_space,
        prioritize_active_color=True,
        use_subsurf=False,
        use_mesh_edges=False,
        use_tspace=bool(settings.export_tangents and has_uvs),
        use_triangles=False,
        use_custom_props=False,
        add_leaf_bones=False,
        primary_bone_axis=bone_axes[0],
        secondary_bone_axis=bone_axes[1],
        use_armature_deform_only=True,
        armature_nodetype="NULL",
        bake_anim=bake_anim,
        path_mode="COPY" if settings.textures == "COPY" else "STRIP",
        embed_textures=False,
        batch_mode="OFF",
        use_metadata=True,
    )
    if bake_anim:
        kw.update(
            bake_anim_use_all_bones=True,
            bake_anim_use_nla_strips=False,
            bake_anim_use_all_actions=False,
            bake_anim_force_startend_keying=True,
            bake_anim_step=1.0,
            bake_anim_simplify_factor=0.0,
        )
    return kw


def run_exporter(st, kw, report):
    kw = compat.filter_kwargs(bpy.ops.export_scene.fbx, kw, report)
    with bpy.context.temp_override(**st.override()):
        result = bpy.ops.export_scene.fbx(**kw)
    if "FINISHED" not in result:
        raise staging.StagingError(f"Blender's FBX exporter returned {result}")


def post_process(path, name_map, bone_axes, strip_null, lod_specs, anim_stack_name=None, report=None, bone_axes_map=""):
    root, version = tree.load(path)
    patches.rename_nodes(root, name_map)
    patches.set_globalsettings_string(root, MARKER_AXES, f"{bone_axes[0]}{bone_axes[1]}")
    patches.set_globalsettings_string(root, MARKER_AXES_MAP, bone_axes_map or "")
    patches.set_globalsettings_string(root, MARKER_VERSION, __version__)
    if anim_stack_name:
        patches.rename_anim_stack(root, anim_stack_name)
    if strip_null:
        res = patches.strip_armature_null(root, "Armature")
        if report is not None:
            for note in res.notes:
                report.info(f"Post-pass: {note}")
    if lod_specs:
        res = patches.inject_lod_groups(root, lod_specs)
        if report is not None:
            for note in res.notes:
                report.info(f"Post-pass: {note}")
    tmp = path + ".mbtmp"
    tree.save(tmp, root, version)
    os.replace(tmp, path)


# ---------------------------------------------------------------------- entry point


def bone_axes_for(arm_obj, settings):
    p = arm_obj.data.morenn.bone_primary if arm_obj is not None else "DEFAULT"
    s = arm_obj.data.morenn.bone_secondary if arm_obj is not None else "DEFAULT"
    p = settings.bone_primary if p == "DEFAULT" else p
    s = settings.bone_secondary if s == "DEFAULT" else s
    return p, s


def export_file(context, exported, settings, report):
    """Stage, export and post-process one ExportedFile. Raises StagingError on failure."""
    os.makedirs(os.path.dirname(exported.path) or ".", exist_ok=True)
    lod_mode = settings.resolved_lod_mode
    node_names = []
    with staging.Staging(context, report) as st:
        bone_axes = (settings.bone_primary, settings.bone_secondary)
        for group in exported.groups:
            if group.kind == KIND_STATIC:
                assets, lod_nodes = _stage_static_group(st, group, settings, lod_mode, node_names, report)
                exported.assets.extend(assets)
                if len(lod_nodes) > 1 and lod_mode == "LODGROUP_NODES":
                    exported.lod_specs.append((group.asset_name, lod_nodes))
            elif group.kind == KIND_SKELETAL:
                bone_axes = bone_axes_for(group.armature, settings)
                exported.assets.extend(_stage_skeletal_group(st, group, settings, node_names, report))
                exported.has_armature = True
            elif group.kind == KIND_ANIMATION:
                bone_axes = bone_axes_for(group.armature, settings)
                exported.assets.extend(_stage_anim_group(st, group, settings, node_names, report))
                exported.has_armature = True
                exported.extra["fps"] = st.temp_scene.render.fps / st.temp_scene.render.fps_base
                exported.extra["frame_start"], exported.extra["frame_end"] = [int(v) for v in group.frame_range]
                exported.extra["stack"] = group.asset_name
            else:
                raise staging.StagingError(f"Unsupported group kind {group.kind}")
        render_meshes = [o for o in st.objects if o.type == "MESH" and o.get("morenn_role") in ("RENDER", "LOD")]
        has_uvs = all(len(o.data.uv_layers) > 0 for o in render_meshes)
        if not has_uvs and settings.export_tangents:
            report.warning("Some render meshes have no UV map; tangents not exported for this file (W005)")
        is_anim = exported.kind == KIND_ANIMATION
        kw = exporter_kwargs(
            exported.path, st.coll.name, exported.kind, settings, has_uvs, EXPORTER_BONE_AXES, bake_anim=is_anim
        )
        run_exporter(st, kw, report)
        name_map = dict(st.name_map)
    exported.node_names = node_names
    exported.single_asset = len([a for a in exported.assets]) == 1
    axes_map_text = exported.assets[0].get("bone_axes_map", "") if exported.assets else ""
    post_process(
        exported.path,
        name_map,
        bone_axes,
        settings.strip_armature_null and exported.has_armature,
        exported.lod_specs,
        anim_stack_name=exported.assets[0]["name"] if is_anim else None,
        report=report,
        bone_axes_map=axes_map_text,
    )
    exported.extra["bone_axes"] = f"{bone_axes[0]}{bone_axes[1]}"
    if axes_map_text:
        exported.extra["bone_axes_map"] = axes_map_text
    return exported


def export_groups(context, groups, settings, report):
    """Export every group; returns ``(exported_files, manifest_path)``."""
    if not groups:
        report.error("Nothing to export: select meshes or armatures")
        return [], None
    if (
        settings.file_mode == "COMBINED"
        and settings.ue_importer == "LEGACY"
        and len([g for g in groups if g.kind == KIND_STATIC]) > 1
    ):
        report.error(
            "Combined files are not supported with the legacy UE importer (assets would be named <File>_<Node>) (E020)"
        )
        return [], None
    if settings.generate_collision_mesh and settings.collision_mesh_file == "SAME" and settings.ue_importer == "LEGACY":
        report.error("Generated collision meshes must go to a separate file with the legacy UE importer (E025)")
        return [], None
    dupes = naming.unique([g.asset_name for g in groups])
    if dupes:
        report.error(f"Duplicate asset names: {', '.join(dupes)} (E008)")
        return [], None

    files = plan_files(groups, settings)
    if settings.generate_collision_mesh and settings.collision_mesh_file == "SEPARATE":
        for g in [g for g in groups if g.kind == KIND_STATIC]:
            coll_name = naming.collision_mesh_name(settings.collision_mesh_template, g.asset_name)
            cg = classify.AssetGroup(
                KIND_STATIC,
                g.base,
                coll_name,
                render=list(g.render),
                destination_path=g.destination_path,
                source_object=g.source_object,
            )
            cg.warnings.append("collision-only")
            path = os.path.join(settings.export_dir, f"{coll_name}.fbx")
            ef = ExportedFile(path, KIND_STATIC, [cg])
            ef.extra["collision_only"] = True
            files.append(ef)

    manifest = manifest_mod.new_manifest(settings.prefs, settings.ue_importer)
    done = []
    for ef in files:
        try:
            if ef.extra.get("collision_only"):
                _export_collision_only(context, ef, settings, report)
            else:
                export_file(context, ef, settings, report)
        except staging.StagingError as exc:
            report.error(str(exc))
            _remove_quietly(ef.path)
            continue
        except Exception as exc:
            report.error(f"Export of '{os.path.basename(ef.path)}' failed: {exc!r}")
            _remove_quietly(ef.path)
            continue
        dest = ef.groups[0].destination_path or settings.destination_path
        extra = {"lod_mode": settings.resolved_lod_mode}
        extra.update(ef.extra)
        if ef.kind == KIND_SKELETAL:
            extra["skeleton"] = ef.groups[0].armature.data.morenn.ue_skeleton_path or None
            extra["root_bone"] = ef.assets[0].get("root_bone", "")
            extra["morph_targets"] = ef.assets[0].get("morph_targets", False)
        elif ef.kind == KIND_ANIMATION:
            extra["skeleton"] = ef.groups[0].armature.data.morenn.ue_skeleton_path or None
        if ef.groups[0].armature is not None:
            extra["source_armature"] = ef.groups[0].armature.name
        manifest_mod.add_item(manifest, ef.path, ef.kind, dest, ef.assets, ef.single_asset, **extra)
        done.append(ef)
        report.info(f"Exported {os.path.basename(ef.path)} ({', '.join(a['name'] for a in ef.assets)})", ef.path)
    manifest_path = None
    if done:
        manifest_path = manifest_mod.write(
            manifest, settings.export_dir, naming.sanitize_asset_name(settings.batch_name)
        )
        report.info(f"Manifest written: {os.path.basename(manifest_path)}", manifest_path)
    return done, manifest_path


def _export_collision_only(context, exported, settings, report):
    os.makedirs(os.path.dirname(exported.path) or ".", exist_ok=True)
    group = exported.groups[0]
    render = group.render[0]
    anchor = staging.translation_removed(render.matrix_world)
    with staging.Staging(context, report) as st:
        obj = st.stage_mesh(
            render,
            group.asset_name,
            anchor @ render.matrix_world,
            role="COLLISION_MESH",
            triangulate=True,
            keep_shape_keys=False,
            strip_attributes=True,
            keep_materials=False,
        )
        _prepare_collision_mesh(st, obj, settings)
        kw = exporter_kwargs(
            exported.path, st.coll.name, KIND_STATIC, settings, False, (settings.bone_primary, settings.bone_secondary)
        )
        kw["use_tspace"] = False
        run_exporter(st, kw, report)
        name_map = dict(st.name_map)
    exported.assets = [
        {
            "node": group.asset_name,
            "name": group.asset_name,
            "role": "COLLISION_MESH",
            "collision_ratio": settings.collision_ratio,
        }
    ]
    exported.node_names = [group.asset_name]
    exported.single_asset = True
    post_process(exported.path, name_map, (settings.bone_primary, settings.bone_secondary), False, [], report=report)
    return exported


def _remove_quietly(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
