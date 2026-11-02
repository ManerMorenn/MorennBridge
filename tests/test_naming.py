# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from tests import fixtures


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.naming = fixtures.addon_module("core.naming")

    def test_known_prefix_is_stripped_once(self):
        prefixes = ["SM_", "SKM_", "SK_"]
        self.assertEqual(self.naming.strip_known_prefix("SM_Crate", prefixes), ("Crate", "SM_"))
        self.assertEqual(self.naming.strip_known_prefix("SKM_Hero", prefixes), ("Hero", "SKM_"))
        self.assertEqual(self.naming.strip_known_prefix("Crate", prefixes), ("Crate", ""))
        self.assertEqual(self.naming.strip_known_prefix("SM_", prefixes), ("SM_", ""))

    def test_numeric_suffix(self):
        self.assertEqual(self.naming.strip_numeric_suffix("Crate.001"), "Crate")
        self.assertEqual(self.naming.strip_numeric_suffix("Crate.01"), "Crate.01")

    def test_asset_names_replace_ascii_punctuation(self):
        sanitize = self.naming.sanitize_asset_name
        self.assertEqual(sanitize("Big Crate (old)"), "Big_Crate_old")
        self.assertEqual(sanitize("a--b..c"), "a_b_c")
        self.assertEqual(sanitize("___"), "Asset")
        self.assertEqual(sanitize("1st"), "_1st")

    def test_asset_names_keep_non_ascii_letters(self):
        sanitize = self.naming.sanitize_asset_name
        self.assertEqual(sanitize("Caisse_été"), "Caisse_été")
        self.assertEqual(sanitize("木箱"), "木箱")
        self.assertEqual(sanitize("été"), "été")  # decomposed input is normalised

    def test_ue_sanitiser_matches_interchange(self):
        self.assertEqual(self.naming.sanitize_ue_name("spine.001"), "spine_001")
        self.assertEqual(self.naming.sanitize_ue_name("my bone+1"), "my-bone_1")
        self.assertEqual(self.naming.sanitize_ue_name(""), "Null")
        self.assertTrue(self.naming.is_ue_safe("spine_01"))
        self.assertFalse(self.naming.is_ue_safe("spine.01"))

    def test_base_name_and_asset_name(self):
        known = ["SM_", "SKM_"]
        self.assertEqual(self.naming.base_name("SM_Crate.003", known), "Crate")
        self.assertEqual(self.naming.base_name("SM_Crate", known, override="  Box "), "Box")
        self.assertEqual(self.naming.asset_name("Crate", "SM_"), "SM_Crate")
        self.assertEqual(self.naming.asset_name("SM_Crate", "SM_"), "SM_Crate")

    def test_collision_names(self):
        self.assertEqual(self.naming.parse_collision_name("UCX_Crate_01"), ("UCX", "Crate_01"))
        self.assertEqual(self.naming.parse_collision_name("ubx_Crate"), ("UBX", "Crate"))
        self.assertIsNone(self.naming.parse_collision_name("Crate"))
        self.assertEqual(self.naming.collision_body_candidates("UCX_Crate_01"), ["Crate_01", "Crate"])
        self.assertEqual(self.naming.collision_body_candidates("Crate"), [])
        self.assertEqual(self.naming.collision_node_name("UBX", "SM_Crate", 2), "UBX_SM_Crate_02")

    def test_socket_names(self):
        self.assertEqual(self.naming.socket_node_name("Lid"), "SOCKET_Lid")
        self.assertEqual(self.naming.socket_node_name("SOCKET_Lid"), "SOCKET_Lid")
        self.assertTrue(self.naming.is_socket_name("SOCKET_Lid"))

    def test_lod_names(self):
        self.assertEqual(self.naming.lod_node_name("SM_Rock", 1, "LOD_NAMING"), "LOD1_SM_Rock")
        self.assertEqual(self.naming.lod_node_name("SM_Rock", 1, "LODGROUP_NODES"), "SM_Rock_LOD1")
        self.assertEqual(self.naming.parse_lod_name("Rock_LOD2"), ("Rock", 2))
        self.assertEqual(self.naming.parse_lod_name("LOD2_Rock"), ("Rock", 2))
        self.assertIsNone(self.naming.parse_lod_name("Rock"))

    def test_templates_fall_back_on_invalid_placeholders(self):
        self.assertEqual(self.naming.collision_mesh_name("{asset}_C", "SM_A"), "SM_A_C")
        self.assertEqual(self.naming.collision_mesh_name("{nope}", "SM_A"), "SM_A_COLL")
        self.assertEqual(self.naming.anim_asset_name("{prefix}{rig}_{action}", "A_", "Hero", "Run"), "A_Hero_Run")
        self.assertEqual(self.naming.anim_asset_name("{nope}", "A_", "Hero", "Run"), "A_Hero_Run")

    def test_unique_reports_duplicates_once(self):
        self.assertEqual(self.naming.unique(["a", "b", "a", "a"]), ["a"])


if __name__ == "__main__":
    unittest.main()
