# SPDX-License-Identifier: GPL-3.0-or-later
import os
import unittest

import bpy

from tests import fixtures

ASCII_SKELETON = b"""; FBX 7.3.0 project file
FBXHeaderExtension:  {
    FBXHeaderVersion: 1003
    FBXVersion: 7300
    Creator: "FBX SDK/FBX Plugins version 2020.2"
}
GlobalSettings:  {
    Version: 1000
    Properties70:  {
        P: "UpAxis", "int", "Integer", "",2
        P: "UpAxisSign", "int", "Integer", "",1
        P: "FrontAxis", "int", "Integer", "",1
        P: "FrontAxisSign", "int", "Integer", "",-1
        P: "CoordAxis", "int", "Integer", "",0
        P: "CoordAxisSign", "int", "Integer", "",1
        P: "UnitScaleFactor", "double", "Number", "",1
    }
}
Objects:  {
    NodeAttribute: 1001, "NodeAttribute::root", "Root" {
        TypeFlags: "Skeleton"
    }
    NodeAttribute: 1002, "NodeAttribute::pelvis", "LimbNode" {
        TypeFlags: "Skeleton"
    }
    Model: 2001, "Model::root", "Root" {
        Version: 232
        Properties70:  {
            P: "Lcl Translation", "Lcl Translation", "", "A",0,0,0
        }
    }
    Model: 2002, "Model::pelvis", "LimbNode" {
        Version: 232
        Properties70:  {
            P: "Lcl Translation", "Lcl Translation", "", "A",0,0,90
        }
    }
}
Connections:  {
    C: "OO",2001,0
    C: "OO",2002,2001
    C: "OO",1001,2001
    C: "OO",1002,2002
}
"""


class BadFileTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.scene = bpy.context.scene

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def import_cancelled(self, path):
        self.assertEqual(fixtures.call(bpy.ops.morenn.import_ue, filepath=path), {"CANCELLED"})
        self.assertEqual(len(bpy.data.objects), 0)
        return fixtures.log_text()

    def valid_file(self):
        done, _manifest, report = fixtures.run_export([fixtures.make_cube("Crate")], self.tmp)
        self.assertTrue(report.ok)
        path = done[0].path
        with open(path, "rb") as fh:
            data = fh.read()
        fixtures.fresh_scene(cm=True)
        return data

    def test_truncated_file(self):
        data = self.valid_file()
        text = self.import_cancelled(self.write("cut.fbx", data[: len(data) // 2]))
        self.assertIn("'cut.fbx' is damaged or incomplete", text)

    def test_empty_file(self):
        self.assertIn("is not an FBX file", self.import_cancelled(self.write("empty.fbx", b"")))

    def test_random_bytes(self):
        self.assertIn("is not an FBX file", self.import_cancelled(self.write("noise.fbx", os.urandom(4096))))

    def test_corrupted_body(self):
        data = bytearray(self.valid_file())
        for i in range(200, 240):
            data[i] = 0xFF
        text = self.import_cancelled(self.write("corrupt.fbx", bytes(data)))
        self.assertIn("corrupt.fbx", text)

    def test_missing_file(self):
        text = self.import_cancelled(os.path.join(self.tmp, "missing.fbx"))
        self.assertIn("does not exist", text)

    def test_ascii_file_imports_with_its_root_bone(self):
        path = self.write("SK_Ascii.fbx", ASCII_SKELETON)
        self.assertEqual(bpy.ops.morenn.import_ue(filepath=path), {"FINISHED"})
        armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        self.assertEqual(len(armatures), 1)
        self.assertEqual({b.name for b in armatures[0].data.bones}, {"root", "pelvis"})
        self.assertIsNone(armatures[0].data.bones["root"].parent)
        text = fixtures.log_text()
        self.assertIn("ASCII FBX", text)
        self.assertIn("restored as bones", text)

    def test_ascii_analysis(self):
        ascii_fbx = fixtures.addon_module("fbxpatch.ascii")
        self.assertTrue(ascii_fbx.looks_like_fbx(ASCII_SKELETON))
        self.assertFalse(ascii_fbx.looks_like_fbx(b"hello"))
        self.assertEqual(ascii_fbx.creator(ASCII_SKELETON), "FBX SDK/FBX Plugins version 2020.2")
        self.assertEqual(ascii_fbx.unit_scale(ASCII_SKELETON), 1.0)
        self.assertEqual(ascii_fbx.root_bone_names(ASCII_SKELETON), ["root"])
        self.assertEqual(ascii_fbx.anim_stacks(ASCII_SKELETON), [])
        self.assertNotIn(b'"Root"', ascii_fbx.retype_root_bones(ASCII_SKELETON))

    def test_temporary_copy_is_removed(self):
        path = self.write("SK_Ascii.fbx", ASCII_SKELETON)
        bpy.ops.morenn.import_ue(filepath=path)
        leftovers = [f for f in os.listdir(bpy.app.tempdir or self.tmp) if f.startswith("morenn_")]
        self.assertEqual(leftovers, [])


class NonLatinPathTests(unittest.TestCase):
    def test_export_and_import_through_a_non_latin_folder(self):
        fixtures.fresh_scene(cm=True)
        folder = os.path.join(fixtures.temp_dir(), "dossier été", "日本語")
        cube = fixtures.make_cube("木箱")
        fixtures.select_only(cube)
        bpy.context.scene.morenn.export_dir = folder
        self.assertEqual(bpy.ops.morenn.export_ue(), {"FINISHED"})
        path = os.path.join(folder, "SM_木箱.fbx")
        self.assertTrue(os.path.exists(path))
        fixtures.fresh_scene(cm=True)
        self.assertEqual(bpy.ops.morenn.import_ue(filepath=path), {"FINISHED"})
        self.assertIn("SM_木箱", bpy.data.objects)


if __name__ == "__main__":
    unittest.main()
