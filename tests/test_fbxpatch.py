# SPDX-License-Identifier: GPL-3.0-or-later
import array
import os
import unittest

from tests import fixtures
from tests.test_export_skeletal import _rigged_character


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

    def test_a_skin_keeps_one_cluster_when_none_weights_a_vertex(self):
        rig, _body = _rigged_character("Hero", shape_key=False)
        done, _, report = fixtures.run_export([rig], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _version = self.tree.load(done[0].path)
        clusters = self.I.find_objects(root, b"Deformer", subclass=b"Cluster")
        for cluster in clusters:
            for elem in cluster.find_all(b"Indexes") + cluster.find_all(b"Weights"):
                cluster.remove(elem)
        result = self.patches.drop_empty_clusters(root)
        kept = self.I.find_objects(root, b"Deformer", subclass=b"Cluster")
        self.assertEqual(len(kept), 1)
        self.assertEqual(result.notes, ["2 skin cluster(s) that weight no vertex left out"])
        gone = {self.I.object_id(c) for c in clusters} - {self.I.object_id(kept[0])}
        self.assertFalse([c for c in self.I.connections(root) if c[1] in gone or c[2] in gone])
        self.assertFalse(self.patches.drop_empty_clusters(root))

    def _bones_pose_links(self, root):
        """``(bones, pose, links)``: LimbNode ids -> (name, parent id), pose node matrices and cluster matrices by id."""
        tree = self.patches.model_tree(root)
        bones = {mid: (name, parent) for mid, (name, sub, parent) in tree.items() if sub == "LimbNode"}
        pose = {}
        for p in self.I.find_objects(root, b"Pose"):
            for node in p.find_all(b"PoseNode"):
                pose[node.find(b"Node").props[0]] = node.find(b"Matrix").props[0].tolist()
        clusters = {self.I.object_id(c): c for c in self.I.find_objects(root, b"Deformer", subclass=b"Cluster")}
        links = {}
        for kind, child, parent, _prop, _e in self.I.connections(root):
            if kind == b"OO" and parent in clusters:
                links[child] = clusters[parent].find(b"TransformLink").props[0].tolist()
        return bones, pose, links

    def _composed_drift(self, root):
        """Largest |composed node transforms - bind pose| over the bones, in the FBX SDK's own convention."""
        import numpy as np

        bones, pose, _links = self._bones_pose_links(root)
        models = {self.I.object_id(m): m for m in self.I.find_objects(root, b"Model")}
        globals_ = {}

        def global_of(mid):
            if mid not in globals_:
                m = models[mid]
                local = self.patches.fbx_local_matrix(
                    self.patches._lcl(m, b"Lcl Translation", (0, 0, 0)),
                    self.patches._lcl(m, b"Lcl Rotation", (0, 0, 0)),
                    self.patches._lcl(m, b"Lcl Scaling", (1, 1, 1)),
                )
                parent = bones[mid][1]
                globals_[mid] = local @ (global_of(parent) if parent in bones else np.eye(4))
            return globals_[mid]

        return max(
            float(np.abs(global_of(mid) - np.array(pose[mid]).reshape(4, 4)).max()) for mid in bones if mid in pose
        )

    def _cluster_drift(self, root):
        """Largest |Transform @ TransformLink - mesh bind matrix| over the skin clusters (what the SDK multiplies back)."""
        import numpy as np

        pose = {}
        for p in self.I.find_objects(root, b"Pose"):
            for node in p.find_all(b"PoseNode"):
                pose[node.find(b"Node").props[0]] = np.array(node.find(b"Matrix").props[0]).reshape(4, 4)
        parent_of = {}
        for kind, child, parent, _prop, _e in self.I.connections(root):
            if kind == b"OO":
                parent_of.setdefault(child, parent)
        worst = 0.0
        for cluster in self.I.find_objects(root, b"Deformer", subclass=b"Cluster"):
            mesh = parent_of[parent_of[parent_of[self.I.object_id(cluster)]]]
            transform = np.array(cluster.find(b"Transform").props[0]).reshape(4, 4)
            link = np.array(cluster.find(b"TransformLink").props[0]).reshape(4, 4)
            worst = max(worst, float(np.abs(transform @ link - pose[mesh]).max()))
        return worst

    def test_bind_matrices_follow_the_rest_transforms(self):
        rig, body = _rigged_character("Hero", bones=4, shape_key=False)
        done, _, report = fixtures.run_export([rig, body], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _version = self.tree.load(done[0].path)
        bones, pose, links = self._bones_pose_links(root)
        self.assertEqual(len(bones), 4)
        self.assertLess(self._composed_drift(root), 1e-9)
        self.assertLess(self._cluster_drift(root), 1e-9)
        for mid in links:
            self.assertEqual(pose[mid], links[mid])
        # a rest transform that moved takes its bind matrices (and its children's) along; the second pass is a no-op
        model = next(m for m in self.I.find_objects(root, b"Model") if self.I.object_name(m) == b"bone_02")
        rotation = self.patches._lcl(model, b"Lcl Rotation", (0, 0, 0))
        self.I.set_prop(model, b"Lcl Rotation", b"Lcl Rotation", b"", b"A", rotation[0], rotation[1] + 1.0, rotation[2])
        self.assertGreater(self._composed_drift(root), 1e-3)
        result = self.patches.bind_pose_from_rest(root)
        self.assertRegex(result.notes[0], r"^[12] node\(s\): bind matrices recomputed from the rest transforms$")
        self.assertLess(self._composed_drift(root), 1e-9)
        self.assertLess(self._cluster_drift(root), 1e-9)
        self.assertFalse(self.patches.bind_pose_from_rest(root))

    def test_bind_pose_from_rest_leaves_animation_files_alone(self):
        rig, body = _rigged_character("Hero", bones=2, shape_key=False)
        done, _, report = fixtures.run_export([rig, body], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _version = self.tree.load(done[0].path)
        root.find(b"Objects").new(b"AnimationStack")
        model = next(m for m in self.I.find_objects(root, b"Model") if self.I.object_name(m) == b"bone_01")
        self.I.set_prop(model, b"Lcl Rotation", b"Lcl Rotation", b"", b"A", 0.0, 5.0, 0.0)
        before = self._composed_drift(root)
        self.assertFalse(self.patches.bind_pose_from_rest(root))
        self.assertEqual(self._composed_drift(root), before)

    def _ball(self, smooth=True, shape_key=True):
        ball = fixtures.make_sphere("Ball", radius=50.0, segments=12, rings=8)
        if smooth:
            ball.data.shade_smooth()
        if shape_key:
            ball.shape_key_add(name="Basis", from_mix=False)
            for point in ball.shape_key_add(name="Grow", from_mix=False).data:
                point.co *= 1.2
        return ball

    def _normal_layer(self, path):
        root, _version = self.tree.load(path)
        model = fixtures.fbx_models(root, b"Mesh")["SM_Ball"]
        return fixtures.fbx_geometry_for_model(root, model).find(b"LayerElementNormal")

    def test_smooth_mesh_with_shape_keys_gets_normals_per_vertex(self):
        ball = self._ball()
        done, _, report = fixtures.run_export([ball], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        layer = self._normal_layer(done[0].path)
        self.assertEqual(layer.find(b"MappingInformationType").props[0], b"ByVertice")
        self.assertEqual(layer.find(b"ReferenceInformationType").props[0], b"Direct")
        self.assertIsNone(layer.find(b"NormalsIndex"))
        normals = list(layer.find(b"Normals").props[0])
        self.assertEqual(len(normals), 3 * len(ball.data.vertices))
        for vertex in ball.data.vertices:
            fixtures.assert_vec_close(self, normals[3 * vertex.index : 3 * vertex.index + 3], vertex.normal, places=3)
        self.assertIn("normals stored per vertex on 1 mesh(es) with shape keys", fixtures.report_text(report))

    def test_normals_stay_per_corner_without_shape_keys_or_with_split_normals(self):
        for smooth, shape_key in ((True, False), (False, True)):
            with self.subTest(smooth=smooth, shape_key=shape_key):
                fixtures.fresh_scene(cm=True)
                done, _, report = fixtures.run_export([self._ball(smooth, shape_key)], self.tmp)
                self.assertTrue(report.ok, [str(e) for e in report.entries])
                layer = self._normal_layer(done[0].path)
                self.assertEqual(layer.find(b"MappingInformationType").props[0], b"ByPolygonVertex")
                self.assertNotIn("normals stored per vertex", fixtures.report_text(report))

    def test_static_files_keep_shape_keys_that_move_nothing(self):
        ball = self._ball()
        ball.shape_key_add(name="Still", from_mix=False)
        done, _, report = fixtures.run_export([ball], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _version = self.tree.load(done[0].path)
        self.assertEqual(self.patches.blend_shape_names(root), {"SM_Ball": ["Grow", "Still"]})

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
