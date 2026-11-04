# Support

## What is supported

| Component | Status |
|---|---|
| Blender 4.5 LTS | Supported, tested from 4.5.0 |
| Blender 5.0, 5.1 | Supported |
| Blender 5.2 LTS | Supported |
| Blender 4.4 and older | Not supported: the FBX importer and smoothing options the add-on relies on do not exist there |
| Linux, Windows, macOS | Tested in CI |
| Unreal Engine 5.8 | Target version, written against the engine source. Not yet confirmed in a live editor |
| Other Unreal versions | Not supported |

The test suite runs on every supported Blender version, and on Blender's daily builds twice a week.

Unreal Link, the push and pull feature, is experimental for the same reason: it has only been tested against a fake
editor that speaks the same protocol.

## Known limitations

- Collection instances export their meshes only. Curves and other objects inside the collection are ignored (`W025`).
- Hair curves, point clouds, volumes and Grease Pencil objects cannot be exported as meshes. They are listed in the
  Log.
- Shape key animation is not exported. Shape keys themselves are.
- Importing an animation creates a new rig. It cannot be applied to an existing one yet.
- Motion of the armature object is never exported (`E002`).
- ASCII FBX files import, but their LOD groups and bone axis markers are not read. Binary is the better choice.
- Convert Scene does not rescale particle sizes, driver expressions or plain numbers in Geometry Nodes inputs. It
  warns about them. Inputs declared as distances are scaled.
- Convert Bone Axes clears the *Connected* flag, because tails move. Euler-keyed bones become quaternion bones.
- Pull: Unreal writes an animation with the bones of its skeleton's preview mesh only. Bones that only the
  skeleton has (correctives and attachment bones of a shared skeleton) are not in the file, so an animation pushed
  back plays them in their reference pose.
- Animation settings that exist only on the Unreal asset (root motion, root lock, retarget source, rate scale,
  looping, additive settings) are not part of FBX. Pull keeps them on the Blender action and a push of that action
  sets them again, as they were when pulled. Animations made in Blender get Unreal's defaults.
- Unreal's FBX export names materials after the material assets, not after the slots. Pull keeps each mesh's slot
  names and materials; a push names the FBX materials after the slots and gives each slot its material again,
  deleting the copies the import made for them. Materials made in Blender become slots of the same name.

## FAQ

**My character is tiny or huge in Unreal.** Check that it was exported with MorennBridge. Files are always in
centimetres, whatever the scene units. If it was imported with an old file, run *Convert Scene to Unreal Units* in
Blender and export again.

**An extra `Armature` bone appears in Unreal.** The `Armature` node was kept in the file. Turn *Strip armature null
node* back on in the preferences.

**Why is my object not exported?** Check the Log. Hidden objects are skipped, including objects in excluded
collections, and so are objects with *Export* turned off in the Object panel or with *Never export* as asset type.
Collision, LOD and socket objects are exported with their mesh, even when hidden.

**A curve is not exported.** A curve needs a bevel or an extrusion to have a surface. The Log says so.

**Can I keep accents or Japanese in asset names?** Yes. Only ASCII punctuation is replaced, because that is what
Unreal restricts.

**Can I reuse one FBX for several assets?** Set *Static mesh files* to *Combined file* in the preferences. Each
mesh becomes a node, and Interchange turns each node into an asset.

**Does the add-on change my scene?** Exports do not. They work on temporary copies in a temporary scene. Convert Scene
and the Pre-flight fixes do change it, and all of them can be undone.

**The export says the FBX add-on is disabled.** Enable *FBX format* in *Preferences → Add-ons*. MorennBridge uses
Blender's exporter and does not change other add-ons by itself.

## Getting help

Report bugs on the [issue tracker](https://github.com/ManerMorenn/MorennBridge/issues). The **Copy** button of the
Log panel gives a useful starting point. Questions are welcome in
[Discussions](https://github.com/ManerMorenn/MorennBridge/discussions).

## Privacy and security

The add-on has no telemetry and no update check, and it never contacts a server. Its only network use is Unreal Link,
which talks to an editor on your own machine and is refused while Blender's *Allow Online Access* is off.

Unreal's remote execution lets any local program run Python in the editor while it is enabled, whichever tool uses
it. Keep *Multicast TTL* at 0 and turn the setting off on shared machines.

This website has no analytics and sets no cookies. Fonts are served from this site. The Star button asks the GitHub
API for the star count from your browser, and the result is cached there for an hour.
