# extensions.blender.org listing

**Name:** MorennBridge

**Tagline:** Fast and correct FBX bridge between Blender and Unreal Engine

**Tags:** Import-Export, Pipeline

**Website:** https://morennbridge.manermorenn.com

**Support:** https://github.com/ManerMorenn/MorennBridge/issues

**Files:** `icon.png` for the icon, `featured.png` for the featured image, and `docs/images` for screenshots:
`03_export_static_setup.png`, `06_import_compare.png`, `05_preflight_issues.png`, `07_armature_panel.png`.

## Description

MorennBridge makes FBX interchange between Blender and Unreal Engine 5 correct and one click.

Blender and Unreal disagree on units, axes, bone orientation and a dozen exporter checkboxes. MorennBridge encodes the
right conventions once and turns them into buttons.

- **Scene setup:** centimetre units, clip ranges and grid that work at Unreal scale. A converter rescales metre scenes
  and keeps physical sizes.
- **Export:** static meshes, skeletal meshes and animations, auto-named (`SM_`, `SKM_`, `A_`). Files use Unreal's axes
  and centimetres, with smoothing groups, tangents and no leaf bones. Curves, text, Geometry Nodes instances and
  collection instances export as meshes.
- **Unreal conventions:** `UCX_` collision, sockets, LOD chains and an optional generated collision mesh.
- **Import from Unreal:** the root bone comes back, every bone points to its child, and exporting again reproduces the
  original skeleton.
- **Pre-flight validation:** errors and warnings with one-click fixes, before anything is written.
- **Unreal Link:** push exports to a running editor over Unreal's Python remote execution.

Your scene is never touched by an export: everything happens on temporary copies.

Requires Blender 4.5 or newer. Unreal Link needs the Python Editor Script Plugin and remote execution enabled in your
project. It only talks to an editor on your own machine, and only when *Allow Online Access* is on.
