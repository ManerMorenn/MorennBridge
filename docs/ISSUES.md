# Issue codes

The Pre-flight panel and the Log show these codes. Errors stop an export until they are fixed or you press
*Export Anyway*. Warnings and notes never stop it. Some codes only appear during the export, in the Log.

## Errors

| Code | Meaning | What to do |
|---|---|---|
| E001 | The armature has several root bones, or no bone to export. Unreal needs exactly one root. | *Add a single 'root' bone*, or tag bones *Always export*. |
| E002 | Animation export: the armature object, or one of its parents, moves as an object. Unreal only reads bone tracks, so that motion would be lost. | Move the motion onto the root bone, or use *Export Anyway* to drop it. |
| E003 | Mirrored (negative) scale. Normals would flip. | *Apply scale*. Shared mesh data gets its own copy. |
| E004 | A mesh is parented to a bone without skinning. Unreal would turn it into a bone. | *Convert bone parenting to skinning*. |
| E008 | Several objects would export under the same asset name. | Set *Name* in the Object panel on one of them. |
| E010 | Bone names that Unreal would rewrite, such as `spine.001`. | *Rename bones like Unreal would*, so names match on both sides. |
| E012 | A modifier changes the vertex count between shape keys, so they cannot be kept. | Remove the modifier or apply it on the shape keys first. Raised during export. |
| E013 | The armature has a non-uniform scale. | *Apply scale*. |
| E019 | The mesh has no faces once its modifiers are applied. | Check the modifiers, or exclude the object. |
| E020 | A combined file was requested with the legacy importer, which would name assets `<File>_<Node>`. | Use one file per asset, or Interchange. Raised during export. |
| E022 | An animation has an empty frame range. | Set a frame range on the action. |
| E025 | A generated collision mesh in the same file as its mesh, with the legacy importer. | Set the collision mesh file to *Separate* in the preferences. Raised during export. |

## Warnings

| Code | Meaning | What to do |
|---|---|---|
| W002 | The armature moves as an object. The mesh export is fine, but animations will not carry that motion. | Move the motion onto the root bone before exporting animations. |
| W003 | Non-uniform scale on a static mesh. It is baked into the mesh. | Nothing, unless the scale is a mistake. |
| W005 | No UV map. Tangents are not exported, and Unreal has to generate lightmap UVs. | Unwrap the mesh. |
| W007 | Empty material slot, or no material at all. | *Assign M_Default*, or assign your own. |
| W012 | Shape keys together with modifiers. The export fails if a modifier changes the vertex count. | Check the export, see E012. |
| W014 | Weights on bones that are not exported. They are merged into the nearest exported ancestor. | Nothing, unless those bones should be exported. |
| W015 | Several Armature modifiers point at different armatures. Only the first one is exported. | Keep one armature per mesh. |
| W016 | The scene is not in centimetres. Exports are normalised, imports get rescaled. | *Set up scene for Unreal*. |
| W017 | The armature has no skinned mesh. Only the skeleton is exported. | Nothing, unless a mesh is hidden or not bound. |
| W018 | Several colour attributes. Only the active one is exported. | Set the one you want as active. |
| W021 | Vertices have no weight on any exported bone. | Weight them, or Unreal attaches them to the root. |
| W024 | A generated collision mesh would have too few faces at this ratio. The undecimated copy is used. | Raise the ratio, or lower the minimum face count in the preferences. Raised during export. |
| W025 | A collection instance contains objects other than meshes. They are ignored. | Convert them to meshes inside the collection. |

## Notes

| Code | Meaning |
|---|---|
| I006 | The mesh has n-gons. They are triangulated at export. |
| I009 | Vertices with more bone influences than the limit. The weakest are dropped. |
| I011 | The object name changes on export, for example `Crate.001` becomes `SM_Crate`. |
