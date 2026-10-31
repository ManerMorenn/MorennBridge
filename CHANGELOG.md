# Changelog

## 0.1.0 (unreleased)

First public version.

- Scene setup for Unreal units (1 unit = 1 cm) and conversion of existing metre scenes.
- Static mesh, skeletal mesh and animation export with Unreal's axis convention, centimetres, smoothing groups,
  tangents, collision/socket/LOD naming, generated collision meshes and a JSON manifest.
- Import of Unreal FBX exports: root bone restored, per-bone axis conversion, bone lengths, hierarchy clean-up.
- Pre-flight validation with automatic fixes.
- Unreal Link: Python remote execution client and editor-side import/export scripts (not yet verified against a
  live editor).
