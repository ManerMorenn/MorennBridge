# Tutorials

Three short walkthroughs. The Blender side of each is covered by the test suite. The Unreal steps follow the engine
source and have not all been confirmed in a live editor yet, see [Support](SUPPORT.md).

## Static prop with collision and a socket

1. Open a new file. In the **Scene** panel, press **Set Up Scene for Unreal**.
2. Model a crate and name it `Crate`. Remember that 1 unit is 1 cm, so a 1 m crate is 100 units wide.
3. With the crate active, open the **Object** panel. Press **Box** to add `UBX_Crate_01`, fitted to the bounds. Press
   **Socket** and name it `Lid` to add `SOCKET_Lid` as a child, then move it where the lid sits.
4. Duplicate the crate and name the copy `Crate_LOD1`. Reduce it with a Decimate modifier. Keep the modifier: exports
   evaluate modifiers without changing your object.
5. In the **Export** panel, choose the folder and the Unreal path, then press **Export Selected** with only `Crate`
   selected. Collision, socket and LOD follow automatically.
6. The **Log** shows `Exported SM_Crate.fbx`. If an issue stops the export, the **Pre-flight** panel lists it. Most
   have a one-click fix, and every code is explained in [Issue codes](ISSUES.md).
7. In Unreal, drop `SM_Crate.fbx` into the Content Browser with the settings from
   [Conventions](CONVENTIONS.md#manual-unreal-import-settings).

To reuse the mesh as collision instead of primitives, tick **Generate collision mesh** before exporting. You get
`SM_Crate_COLL` next to the mesh, for Unreal's *Complex Collision Mesh* slot.

## Character

1. Set up the scene, then build the rig with a single root bone named `root` at the origin. Animation moves the
   character by moving this bone, never the armature object.
2. Bind the mesh with an Armature modifier. Mark control bones as non-deforming: only deform bones are exported, along
   with bones tagged *Always export* in the Armature panel and `ik_*` and `SOCKET_*` bones.
3. Name bones without dots. `spine.001` would become `spine_001` in Unreal; *Rename bones like Unreal would* in the
   Pre-flight panel fixes it everywhere, vertex groups and animation included.
4. Select the armature and press **Export Selected**. You get `SKM_<Armature name>.fbx`, with every visible mesh
   skinned to it. Hidden meshes stay out, which is handy for proxies.
5. In Unreal, import the file. The skeleton it creates is what the animations below import onto.

To start from an existing Unreal character instead, export it from Unreal as FBX and use **Import from Unreal (FBX)**
in the **Import** panel. The root bone comes back as a real bone, every bone points to its child, and each bone
remembers its Unreal axis, so exporting again reproduces the original skeleton.

## Animation

1. Animate on the bones. Constraints, drivers and NLA are fine: the visual pose is baked on every frame.
2. Push finished actions to NLA tracks, or leave them in the action list. In the **Export** panel, set **Source**:
   - *Active action* exports what is assigned to the rig.
   - *All actions of the rig* exports its actions, plus unassigned actions that only use its bones.
   - *NLA strips* exports one file per strip.
   - *Scene frame range* exports the timeline as one animation.
3. Select the rig and press **Export Animations**. Files are named `A_<Rig>_<Action>` and hold the skeleton only.
   Tick *Include mesh in animation files* in the preferences to add the mesh.
4. If you get `E002`, something moves the armature object. Unreal would never see that motion. Move it onto the root
   bone, or use **Export Anyway** to export the bone motion alone.
5. In Unreal, import the files onto the Skeleton created by the character import.
