# Unreal Engine setup for MorennBridge

Written for UE 5.8 from the engine source. See [Support](SUPPORT.md) for what has been confirmed so far.

## 1. Importing the files
Drop the exported `.fbx` files into the Content Browser. The files are already in Unreal's axis system and in
centimetres, so the defaults mostly work. The settings that matter are listed in
[Conventions](CONVENTIONS.md#manual-unreal-import-settings).

Recommended folder layout: `/Game/<Project>/Props/SM_*`, `/Game/<Project>/Characters/<Name>/SKM_* | SKEL_* | PHYS_*`,
`/Game/<Project>/Characters/<Name>/Animations/A_*`. The `destination_path` of each item in the `.morenn.json`
manifest records where MorennBridge expects the asset.

## 2. Push to Unreal: UE 5.8 project settings for remote execution
1. **Plugins**: enable *Python Editor Script Plugin* (it is loaded anyway by Niagara / RigVM) and
   *Editor Scripting Utilities*. Restart the editor.
2. **Edit → Project Settings → Plugins → Python**:
    - *Enable Remote Execution*: **on**
    - *Multicast Group Endpoint*: `239.0.0.1:6766`
    - *Multicast Bind Address*: `0.0.0.0` on macOS/Linux (`127.0.0.1` sometimes does not receive multicast there);
      `127.0.0.1` is fine on Windows
    - *Multicast TTL*: `0` (same machine only)

    Equivalent `Config/DefaultEngine.ini` block:

    ```ini
    [/Script/PythonScriptPlugin.PythonScriptPluginSettings]
    bRemoteExecution=True
    RemoteExecutionMulticastGroupEndpoint=239.0.0.1:6766
    RemoteExecutionMulticastBindAddress=0.0.0.0
    RemoteExecutionMulticastTtl=0
    ```

3. **Firewall**: allow the editor to accept local connections (the bridge only uses loopback traffic).
4. **Interchange FBX translator**: these settings belong to each user and are not in Project Settings. The
   *Translator Settings* button of the Interchange import dialog opens them; keep *Force Front X Axis* off for
   manual imports. A push imports with *Force Front X Axis* and the experimental uFBX translator off, then puts your
   settings back, and the Log says so.
5. **Blender**: Preferences → System → *Allow Online Access* must be enabled, otherwise the add-on refuses to open sockets.
6. In Blender's *Unreal Link* panel click *Find Unreal*; the project name appears in the list. Export with
   *Push to Unreal after export* enabled, or push the last export manually.

While remote execution is on, any program on this machine can run Python in the editor. Keep the TTL at `0`, and
turn the setting off on shared machines.

## 3. Legacy FBX importer (optional)
UE 5.8 imports FBX through Interchange. To target the legacy importer, set *UE importer = Legacy* in the
MorennBridge preferences (LOD groups are then written as real FBX LodGroup nodes, several meshes per file are not
supported, generated collision meshes go to separate files). A push then uses the legacy importer by itself. For
files you drag into the Content Browser, also add to `DefaultEngine.ini`:
```ini
[ConsoleVariables]
Interchange.FeatureFlags.Import.FBX=0
```

## 4. Troubleshooting
- **Extra `Armature` root bone**: the file was exported without the post-pass (or with *Strip armature null node*
  off) from a Blender armature not named `Armature`. Re-export with the option on.
- **Bones scaled ×100 / tiny character**: the scene was not in centimetres and the file was exported with another
  tool. MorennBridge always writes centimetres; run *Set Up Scene for Unreal*.
- **"No smoothing group information"**: exported with smoothing = *Normals only*; use *Smoothing groups*.
- **Animation not found / skeleton incompatible**: pick the Skeleton of the matching SKM_ asset; bone names must be
  identical (run *Validate* and fix `E010` on the Blender side).
