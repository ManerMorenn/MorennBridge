# Changelog

## 0.1.0 (unreleased)

First public version. Requires Blender 4.5 or newer.

- Scene setup for Unreal units (1 unit = 1 cm) and conversion of existing metre scenes, including constraint and
  metaball lengths.
- Static mesh, skeletal mesh and animation export with Unreal's axis convention, centimetres, smoothing groups,
  tangents, collision/socket/LOD naming, skeletal mesh LODs, generated collision meshes and a JSON manifest.
- Curves, surfaces, text, metaballs, Geometry Nodes instances and collection instances export as meshes.
- Import of Unreal FBX exports, binary or ASCII: root bone restored, per-bone axis conversion, bone lengths,
  hierarchy clean-up. Morph target names longer than Blender allows come back in full on export.
- Pre-flight validation with automatic fixes, documented issue codes and an Export Anyway button.
- Unreal Link: Python remote execution client and editor-side import/export scripts, verified with Unreal Engine
  5.8.4. Unreal-only animation settings (root motion, retarget source…) and material slots survive a pull and push.
