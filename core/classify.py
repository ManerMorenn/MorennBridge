# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn a selection into export groups: one render mesh (+LODs, collisions, sockets) or one rig per group."""

from dataclasses import dataclass, field

from . import naming

KIND_STATIC = "STATIC_MESH"
KIND_SKELETAL = "SKELETAL_MESH"
KIND_ANIMATION = "ANIMATION"


@dataclass
class AssetGroup:
    kind: str
    base: str
    asset_name: str
    render: list = field(default_factory=list)  # LOD0 render mesh objects (several for skeletal)
    lods: dict = field(default_factory=dict)  # index -> [objects] (index >= 1)
    collisions: list = field(default_factory=list)  # (object, 'UCX'|'UBX'|'USP'|'UCP')
    sockets: list = field(default_factory=list)  # empty objects
    armature: object = None
    destination_path: str = ""
    source_object: object = None
    warnings: list = field(default_factory=list)
    # animation groups
    action: object = None
    nla_track: object = None
    frame_range: tuple = None

    @property
    def all_objects(self):
        out = list(self.render)
        for objs in self.lods.values():
            out.extend(objs)
        out.extend(o for o, _ in self.collisions)
        out.extend(self.sockets)
        if self.armature is not None:
            out.append(self.armature)
        return out

    @property
    def lod_count(self):
        return 1 + (max(self.lods) if self.lods else 0)


def armature_of(obj):
    """The armature deforming ``obj`` (first Armature modifier target, else armature parent)."""
    if obj.type != "MESH":
        return None
    for mod in obj.modifiers:
        if mod.type == "ARMATURE" and mod.object is not None:
            return mod.object
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    return None


def role_of(obj):
    """Resolve the object's export role from the override or its name."""
    role = obj.morenn.role
    if role != "AUTO":
        return role
    if obj.type == "EMPTY":
        return "SOCKET" if naming.is_socket_name(obj.name) else "OTHER"
    if obj.type != "MESH":
        return "OTHER"
    if naming.parse_collision_name(obj.name):
        return "COLLISION"
    if obj.morenn.lod_of is not None or (naming.parse_lod_name(obj.name) and naming.parse_lod_name(obj.name)[1] > 0):
        return "LOD"
    return "RENDER"


def collision_type_of(obj):
    parsed = naming.parse_collision_name(obj.name)
    if obj.morenn.role == "COLLISION" or parsed is None:
        return obj.morenn.collision_type
    kind = parsed[0]
    return "UCX" if kind == "MCDCX" else kind


def lod_index_of(obj):
    if obj.morenn.lod_of is not None or obj.morenn.role == "LOD":
        return obj.morenn.lod_index
    parsed = naming.parse_lod_name(obj.name)
    return parsed[1] if parsed else 0


def _satellites(candidate, scene_objects):
    """Collision / socket / LOD objects that belong to ``candidate`` (children or name-linked)."""
    cols, socks, lods = [], [], {}
    base_lod = naming.parse_lod_name(candidate.name)
    base_lod_name = base_lod[0] if base_lod and base_lod[1] == 0 else candidate.name
    for obj in scene_objects:
        if obj == candidate or obj.morenn.asset_type == "EXCLUDE":
            continue
        role = role_of(obj)
        linked = obj.parent == candidate or obj.morenn.collision_of == candidate or obj.morenn.lod_of == candidate
        if role == "COLLISION":
            bodies = naming.collision_body_candidates(obj.name)
            if linked or candidate.name in bodies or base_lod_name in bodies:
                cols.append((obj, collision_type_of(obj)))
        elif role == "SOCKET":
            if obj.parent == candidate:
                socks.append(obj)
        elif role == "LOD":
            parsed = naming.parse_lod_name(obj.name)
            if linked or (parsed and parsed[0] == base_lod_name):
                lods.setdefault(lod_index_of(obj), []).append(obj)
    return cols, socks, lods


def _base_and_asset(obj, prefs, prefix):
    known = naming.split_prefixes(prefs.known_prefixes)
    raw = obj.name
    parsed = naming.parse_lod_name(raw)
    if parsed and parsed[1] == 0:
        raw = parsed[0]
    base = naming.base_name(raw, known, obj.morenn.export_name)
    return base, naming.asset_name(base, prefix)


def _kind_for(obj):
    if obj.morenn.asset_type == "STATIC":
        return KIND_STATIC
    if obj.morenn.asset_type == "SKELETAL" or obj.type == "ARMATURE" or armature_of(obj) is not None:
        return KIND_SKELETAL
    return KIND_STATIC


def build_groups(objects, prefs, scene, mode="AUTO", destination=""):
    """Group ``objects`` into AssetGroups. ``mode`` in {'AUTO','STATIC','SKELETAL'}."""
    scene_objects = list(scene.objects)
    selected = [o for o in objects if o.morenn.asset_type != "EXCLUDE" and o.type in {"MESH", "ARMATURE"}]
    groups = []
    seen = set()
    for obj in selected:
        if obj.name in seen:
            continue
        if obj.type == "MESH" and role_of(obj) in {"COLLISION", "LOD"}:
            continue  # satellites never form their own group
        kind = _kind_for(obj) if mode == "AUTO" else (KIND_STATIC if mode == "STATIC" else KIND_SKELETAL)
        if kind == KIND_SKELETAL:
            arm = obj if obj.type == "ARMATURE" else armature_of(obj)
            if arm is None:
                kind = KIND_STATIC
        if kind == KIND_STATIC:
            if obj.type != "MESH":
                continue
            base, asset = _base_and_asset(obj, prefs, prefs.prefix_static)
            cols, socks, lods = _satellites(obj, scene_objects)
            g = AssetGroup(
                KIND_STATIC,
                base,
                asset,
                render=[obj],
                lods=lods,
                collisions=cols,
                sockets=socks,
                destination_path=obj.morenn.destination_path or destination,
                source_object=obj,
            )
            seen.add(obj.name)
            seen.update(o.name for o in g.all_objects)
            groups.append(g)
        else:
            if arm.name in seen:
                continue
            meshes = [
                o
                for o in scene_objects
                if o.type == "MESH"
                and armature_of(o) == arm
                and o.morenn.asset_type != "EXCLUDE"
                and role_of(o) == "RENDER"
            ]
            name_source = arm
            base, asset = _base_and_asset(name_source, prefs, prefs.prefix_skeletal)
            cols, socks, lods = [], [], {}
            for m in meshes:
                m_cols, m_socks, m_lods = _satellites(m, scene_objects)
                cols.extend(m_cols)
                socks.extend(m_socks)
                for i, objs in m_lods.items():
                    lods.setdefault(i, []).extend(objs)
            g = AssetGroup(
                KIND_SKELETAL,
                base,
                asset,
                render=meshes,
                lods=lods,
                collisions=cols,
                sockets=socks,
                armature=arm,
                destination_path=arm.morenn.destination_path or destination,
                source_object=arm,
            )
            if not meshes:
                g.warnings.append(f"Armature '{arm.name}' has no skinned meshes")
            seen.add(arm.name)
            seen.update(o.name for o in g.all_objects)
            groups.append(g)
    return groups


def _anim_group(prefs, arm, rig_base, meshes, destination, anim_label, action=None, track=None, frame_range=None):
    name = naming.anim_asset_name(
        prefs.anim_name_template, prefs.prefix_anim, rig_base, naming.sanitize_asset_name(anim_label)
    )
    return AssetGroup(
        KIND_ANIMATION,
        rig_base,
        name,
        render=meshes,
        armature=arm,
        destination_path=arm.morenn.destination_path or destination,
        source_object=arm,
        action=action,
        nla_track=track,
        frame_range=frame_range,
    )


def build_anim_groups(objects, prefs, scene, source="ACTIVE_ACTION", destination="", include_mesh=False):
    """One ANIMATION group per (armature, action/strip/range) according to ``source``."""
    from . import anim_bake

    arms = []
    for obj in objects:
        arm = obj if obj.type == "ARMATURE" else armature_of(obj)
        if arm is not None and arm not in arms:
            arms.append(arm)
    groups = []
    scene_objects = list(scene.objects)
    for arm in arms:
        rig_base, _ = _base_and_asset(arm, prefs, "")
        meshes = (
            [o for o in scene_objects if o.type == "MESH" and armature_of(o) == arm and role_of(o) == "RENDER"]
            if include_mesh
            else []
        )
        ad = arm.animation_data
        if source == "ACTIVE_ACTION":
            if ad is None or ad.action is None:
                continue
            groups.append(
                _anim_group(
                    prefs,
                    arm,
                    rig_base,
                    meshes,
                    destination,
                    ad.action.name,
                    action=ad.action,
                    frame_range=anim_bake.action_frame_range(ad.action),
                )
            )
        elif source == "ALL_ACTIONS":
            for action in anim_bake.actions_for_armature(arm):
                groups.append(
                    _anim_group(
                        prefs,
                        arm,
                        rig_base,
                        meshes,
                        destination,
                        action.name,
                        action=action,
                        frame_range=anim_bake.action_frame_range(action),
                    )
                )
        elif source == "NLA_STRIPS":
            if ad is not None:
                for track in ad.nla_tracks:
                    if track.mute:
                        continue
                    for strip in track.strips:
                        if strip.mute:
                            continue
                        groups.append(
                            _anim_group(
                                prefs,
                                arm,
                                rig_base,
                                meshes,
                                destination,
                                strip.name,
                                track=track,
                                frame_range=(int(round(strip.frame_start)), int(round(strip.frame_end))),
                            )
                        )
        else:  # SCENE_RANGE
            label = ad.action.name if (ad is not None and ad.action is not None) else scene.name
            groups.append(
                _anim_group(
                    prefs, arm, rig_base, meshes, destination, label, frame_range=(scene.frame_start, scene.frame_end)
                )
            )
    return groups
