# SPDX-License-Identifier: GPL-3.0-or-later
"""Open MorennBridge's FBX files with the Autodesk FBX SDK and report what it sees.

    python dev/check_fbx_sdk.py <file or folder> [...]

Needs the FBX Python SDK (Autodesk licence; install it yourself, it is not part of this repository) in the Python
that runs this script. For every file: the SDK must import it without errors; the summary lists the axis system and
unit, LOD groups and their levels, skinned meshes with their clusters, blend shape channels, bind poses and animation
stacks, and flags clusters without a bone, LOD levels without a mesh and missing bind poses. Exit status 1 when a
file fails.
"""

import os
import sys

try:
    import fbx
except ImportError:
    fbx = None


def _walk(node):
    yield node
    for i in range(node.GetChildCount()):
        yield from _walk(node.GetChild(i))


def _attribute_type(node):
    attr = node.GetNodeAttribute()
    return attr.GetAttributeType() if attr is not None else None


def _has_mesh(node):
    return any(_attribute_type(n) == fbx.FbxNodeAttribute.EType.eMesh for n in _walk(node))


def check(manager, path):
    problems, notes = [], []
    importer = fbx.FbxImporter.Create(manager, "")
    if not importer.Initialize(path, -1, manager.GetIOSettings()):
        return [f"cannot open: {importer.GetStatus().GetErrorString()}"], notes
    scene = fbx.FbxScene.Create(manager, "")
    if not importer.Import(scene):
        problems.append(f"import failed: {importer.GetStatus().GetErrorString()}")
    importer.Destroy()
    settings = scene.GetGlobalSettings()
    axes = settings.GetAxisSystem()
    up, up_sign = axes.GetUpVector()
    notes.append(f"up axis {up} (sign {up_sign}), unit {settings.GetSystemUnit().GetScaleFactor()} cm")
    nodes = list(_walk(scene.GetRootNode()))
    lod_type = fbx.FbxNodeAttribute.EType.eLODGroup
    for node in nodes:
        if _attribute_type(node) == lod_type:
            levels = [node.GetChild(i) for i in range(node.GetChildCount())]
            empty = [lvl.GetName() for lvl in levels if not _has_mesh(lvl)]
            notes.append(f"LOD group {node.GetName()}: {len(levels)} levels")
            if empty:
                problems.append(f"LOD group {node.GetName()}: levels without a mesh {empty}")
    skin_type = fbx.FbxDeformer.EDeformerType.eSkin
    shape_type = fbx.FbxDeformer.EDeformerType.eBlendShape
    meshes = [n.GetMesh() for n in nodes if _attribute_type(n) == fbx.FbxNodeAttribute.EType.eMesh]
    skinned = channels = 0
    for mesh in meshes:
        if mesh.GetPolygonCount() == 0:
            problems.append(f"mesh {mesh.GetNode().GetName()} has no polygons")
        for d in range(mesh.GetDeformerCount(skin_type)):
            skin = mesh.GetDeformer(d, skin_type)
            skinned += 1
            unlinked = [
                skin.GetCluster(c).GetName()
                for c in range(skin.GetClusterCount())
                if skin.GetCluster(c).GetLink() is None
            ]
            if unlinked:
                problems.append(f"mesh {mesh.GetNode().GetName()}: clusters without a bone {unlinked[:5]}")
        for d in range(mesh.GetDeformerCount(shape_type)):
            channels += mesh.GetDeformer(d, shape_type).GetBlendShapeChannelCount()
    notes.append(f"{len(meshes)} meshes ({skinned} skinned), {channels} blend shape channels")
    binds = [scene.GetPose(i) for i in range(scene.GetPoseCount()) if scene.GetPose(i).IsBindPose()]
    if skinned and not binds:
        problems.append("skinned meshes but no bind pose")
    stacks = scene.GetSrcObjectCount(fbx.FbxCriteria.ObjectType(fbx.FbxAnimStack.ClassId))
    notes.append(f"{len(binds)} bind pose(s), {stacks} animation stack(s)")
    scene.Destroy()
    return problems, notes


def main(argv):
    if fbx is None:
        print("The Autodesk FBX Python SDK (module 'fbx') is not installed in this Python.")
        return 2
    paths = []
    for arg in argv:
        if os.path.isdir(arg):
            paths += [os.path.join(r, f) for r, _d, fs in os.walk(arg) for f in fs if f.lower().endswith(".fbx")]
        else:
            paths.append(arg)
    manager = fbx.FbxManager.Create()
    manager.SetIOSettings(fbx.FbxIOSettings.Create(manager, fbx.IOSROOT))
    failed = 0
    for path in sorted(paths):
        problems, notes = check(manager, path)
        failed += bool(problems)
        print(f"{'FAIL' if problems else 'ok  '} {path}")
        for line in problems + notes:
            print(f"     {line}")
    manager.Destroy()
    print(f"{len(paths)} file(s), {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
