# SPDX-License-Identifier: GPL-3.0-or-later
import array
import os
import unittest

from tests import fixtures


def _tree_equal(a, b, path="root"):
    assert a.id == b.id, f"{path}: id {a.id} != {b.id}"
    assert bytes(a.props_type) == bytes(b.props_type), f"{path}: prop types differ"
    for i, (x, y) in enumerate(zip(a.props, b.props)):
        if isinstance(x, array.array):
            assert x.tolist() == y.tolist(), f"{path}: array prop {i} differs"
        else:
            assert x == y, f"{path}: prop {i} {x!r} != {y!r}"
    assert len(a.elems) == len(b.elems), f"{path}: child count"
    for i, (x, y) in enumerate(zip(a.elems, b.elems)):
        _tree_equal(x, y, f"{path}/{a.id.decode()}[{i}]")


class FbxPatchTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.tree = fixtures.addon_module("fbxpatch.tree")
        self.I = fixtures.addon_module("fbxpatch.inspect")
        self.patches = fixtures.addon_module("fbxpatch.patches")

    def _export_cube(self):
        cube = fixtures.make_cube("Crate", size=100.0)
        done, _, report = fixtures.run_export([cube], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        return done[0].path

    def test_load_save_roundtrip_is_identical(self):
        path = self._export_cube()
        root, version = self.tree.load(path)
        out = os.path.join(self.tmp, "copy.fbx")
        self.tree.save(out, root, version)
        root2, version2 = self.tree.load(out)
        self.assertEqual(version, version2)
        # FileId / CreationTime are rewritten by the encoder; compare everything else
        for r in (root, root2):
            for e in list(r.elems):
                if e.id in (b"FileId", b"CreationTime"):
                    r.elems.remove(e)
        _tree_equal(root, root2)

    def test_rename_and_marker(self):
        path = self._export_cube()
        root, version = self.tree.load(path)
        models = fixtures.fbx_models(root, b"Mesh")
        self.assertIn("SM_Crate", models)
        self.assertEqual(self.patches.get_globalsettings_string(root, "MorennBridge|BoneAxes"), "XY")
        res = self.patches.rename_nodes(root, {"SM_Crate": "SM_Box"})
        self.assertTrue(res.changed)
        self.assertIn("SM_Box", fixtures.fbx_models(root, b"Mesh"))

    def test_retype_root_bones(self):
        rig = fixtures.make_armature("Rig", bones=2, bone_length=10.0)
        cube = fixtures.make_cube("Body", size=10.0)
        mod = cube.modifiers.new("Armature", "ARMATURE")
        mod.object = rig
        vg = cube.vertex_groups.new(name="root")
        vg.add(list(range(len(cube.data.vertices))), 1.0, "REPLACE")
        done, _, report = fixtures.run_export([rig], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, version = self.tree.load(done[0].path)
        limbs = fixtures.fbx_models(root, b"LimbNode")
        self.assertIn("root", limbs)
        self.assertNotIn("Armature", fixtures.fbx_models(root))
        # make it look like a UE export: root bone typed 'Root'
        root_model = limbs["root"]
        root_model.props[2] = b"Root"
        for kind, child, parent, _p, _e in self.I.connections(root):
            if kind == b"OO" and parent == self.I.object_id(root_model):
                attr = self.I.object_by_id(root, child)
                if attr is not None and attr.id == b"NodeAttribute":
                    attr.props[2] = b"Root"
        self.assertEqual(self.patches.root_bone_names(root), ["root"])
        res = self.patches.retype_root_bones(root)
        self.assertTrue(res.changed)
        self.assertEqual(self.patches.root_bone_names(root), [])
        self.assertEqual(limbs["root"].props[2], b"LimbNode")


if __name__ == "__main__":
    unittest.main()
