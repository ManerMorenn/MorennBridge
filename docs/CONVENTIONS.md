# MorennBridge conventions

See the [user guide](USER_GUIDE.md) for the panel-by-panel walkthrough.

## Units and axes
- Blender scene: **1 unit = 1 cm** (Scene panel → *Set Up Scene for Unreal*). Metre scenes still export correctly (data is rescaled while staging) but importing UE files into a metre scene needs a rescale, so work in centimetres.
- Exported files are written in Unreal's own axis convention (Z-up, front −Y, right-handed, centimetres). Unreal does not rotate or rescale anything on import. A model facing −Y in Blender (visible in *Front* view) faces +Y in Unreal, the same orientation as Epic's `SKM_Manny`.
- Every exported file carries `MorennBridge|BoneAxes` in its FBX global settings so the importer knows which bone convention was used.

## Names
| Thing | Blender | FBX node / UE asset |
|---|---|---|
| Static mesh | `Crate`, `SM_Crate`, `Crate.001` | `SM_Crate` |
| Skeletal mesh (armature + skinned meshes) | armature `Hero` | `SKM_Hero` (skeleton `SKM_Hero_Skeleton`, renamed to `SKEL_Hero` by the UE script) |
| Simple collision | child mesh `UCX_Crate` / `UBX_…` / `USP_…` / `UCP_…` (or role = Collision) | `UCX_SM_Crate_01` |
| Generated collision mesh | *Generate collision mesh* checkbox | `SM_Crate_COLL` (separate asset; assign it as **Complex Collision Mesh** on `SM_Crate`) |
| Socket | child empty `SOCKET_Lid` | `SOCKET_Lid` (child of the mesh node) |
| LOD | `Crate_LOD1`, `Crate_LOD2` (or role = LOD + index) | Interchange: `LOD0_SM_Crate`, `LOD1_SM_Crate`; legacy: LodGroup `SM_Crate` with `SM_Crate_LOD0`… |
| Animation | action `Run` on armature `Hero` | `A_Hero_Run` |

Prefixes are stripped before being re-applied (`SM_Crate` does not become `SM_SM_Crate`); `.001` suffixes are removed; characters Unreal rewrites (`.`, space, `/`, …) are replaced. Use *Export name* on the object to override the base name.

Bone names must survive Unreal's sanitising (`spine.001` would become `spine_001`): the pre-flight check offers a one-click rename so Blender and Unreal use identical names.

## Skeletons
- One root bone (`root` by convention). Root motion lives on the root bone; the armature *object* must not be animated.
- Bones exported: deform bones, bones tagged *Always export*, `ik_*` and `SOCKET_*` bones, plus their ancestors. Weights on other bones are merged into the nearest exported ancestor. At most *Max bone influences* (default 8) per vertex.
- Blender-made rigs are exported with the X axis along the bone (Unreal/Maya convention). Rigs imported from Unreal keep their original convention (stored on the armature).
- The temporary `Armature` null node is removed from the file, so the root bone is the FBX root (works for both Unreal importers and other engines).

## Manual Unreal import settings (until the push-to-Unreal feature exists)
**Interchange (UE 5.8 default)**, in the import dialog:
- Common Meshes: *Recompute Normals* **off**, *Recompute Tangents* on (MikkTSpace), *Bake Meshes* on, *Import LODs* on, *Import Sockets* on, Vertex Color *Replace*.
- Static Meshes: *Combine Static Meshes* **Do Not Combine** (a combined MorennBridge file then yields one asset per node), *Import Collision According To Mesh Name* on, *One Convex Hull Per UCX* on, Nanite/lightmap UVs as you prefer.
- Skeletal Meshes: *Import Morph Targets* on, *Use T0 As Ref Pose* off, *Create Physics Asset* as you prefer, *Bone Influence Limit* 0 (engine max) or 8.
- Animations: *Animation Range* Timeline, *Import Bone Tracks* on; pick the Skeleton created by the mesh import.
- Project settings → Interchange → FBX: *Force Front X Axis* **off**.

**Legacy FBX importer** (set `Interchange.FeatureFlags.Import.FBX=0`): Normal Import Method *Import Normals* (and Tangents), *Import Mesh LODs* on, *Convert Scene Unit* on, *Force Front X Axis* off, *Auto Generate Collision* off when UCX pieces are provided. Several meshes in one file are named `<File>_<Node>` by this importer; use one file per asset.

## Manifest
Every export writes `<batch>.morenn.json` next to the FBX files: which files were written, their kind, destination path, asset/node names, LOD/collision/socket counts, skeleton to reuse, animation range. The Unreal-side script consumes it; it is also handy for your own tooling.
