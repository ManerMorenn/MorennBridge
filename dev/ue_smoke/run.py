# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless smoke test of MorennBridge against a real Unreal Editor project.

    python dev/ue_smoke/run.py --blender <blender> --editor <UnrealEditor-Cmd> --project <.uproject>
                               [--out <dir>] [--content-root /Game/MorennBridgeSmoke] [--roundtrip]
    python dev/ue_smoke/run.py --blender <blender> --project <.uproject> --remote

1. With --roundtrip, Unreal exports the template's mannequin and two of its animations (mb_ue_export.py).
2. Blender builds the cases and exports them with MorennBridge, one folder per case with its .blend.
3. Unreal imports every case with mb_ue_import.py into <content-root>/<case>, then checks the assets.

With --remote, Blender talks to the project's running editor instead (remote execution on): discovery, a statement,
push, pull and push back through the add-on's operators, into <content-root>/remote.

Use a test project: the content root is deleted and recreated. Do not open its assets in the editor during a run.
Prints one line per check and exits with status 1 when a check fails.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def run_unreal(args, stage, env_extra, log_name):
    env = dict(os.environ, MB_SMOKE_STAGE=stage, **env_extra)
    script = os.path.join(HERE, "unreal_checks.py")
    flags = "-unattended -nosplash -nop4 -NoSound -stdout -FullStdOutLogOutput"
    if stage == "export":
        # the skeletal mesh FBX exporter needs a rendered component, even with material baking off
        flags += " -AllowCommandletRendering"
    if os.name == "nt":
        command = f'"{args.editor}" "{args.project}" -run=pythonscript -script="{script}" {flags}'
    else:
        command = [args.editor, args.project, "-run=pythonscript", f"-script={script}", *flags.split()]
    log = os.path.join(args.out, log_name)
    started = time.monotonic()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        code = subprocess.run(command, env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
    print(f"[smoke] Unreal {stage}: exit {code} after {time.monotonic() - started:.0f} s, log {log}")
    return code


def run_blender(args, script_name, *script_args):
    env = dict(os.environ)
    env.setdefault("BLENDER_USER_RESOURCES", os.path.join(args.out, "blender_profile"))
    script = os.path.join(HERE, script_name)
    command = [args.blender, "-b", "--factory-startup", "--python", script, "--", args.out, *script_args]
    log = os.path.join(args.out, "blender.log")
    started = time.monotonic()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        code = subprocess.run(command, env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
    print(f"[smoke] Blender: exit {code} after {time.monotonic() - started:.0f} s, log {log}")
    return code


def report(results_path):
    with open(results_path, encoding="utf-8") as fh:
        results = json.load(fh)
    failed = 0
    print(f"[smoke] {results['engine']}")
    for case in results["cases"]:
        checks = [r for r in case["results"] if r["ok"] is not None]
        bad = [r for r in checks if not r["ok"]]
        failed += len(bad)
        print(f"\n{case['name']} (checklist item {case['item']}): {len(checks) - len(bad)}/{len(checks)} ok")
        for r in case["results"]:
            label = "note" if r["ok"] is None else ("ok  " if r["ok"] else "FAIL")
            shown = r["detail"] and (r["ok"] is not True or os.environ.get("MB_SMOKE_VERBOSE"))
            detail = f": {r['detail']}" if shown else ""
            print(f"  {label} {r['check']}{detail}")
    print(f"\n[smoke] {failed} failed check(s)")
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blender", required=True)
    parser.add_argument("--editor", help="UnrealEditor-Cmd executable (not needed with --remote)")
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "morenn_ue_smoke"))
    parser.add_argument("--content-root", default="/Game/MorennBridgeSmoke")
    parser.add_argument("--roundtrip", action="store_true", help="also round-trip the template mannequin")
    parser.add_argument("--remote", action="store_true", help="check Unreal Link against the running editor")
    args = parser.parse_args()
    args.out = os.path.abspath(args.out)
    os.makedirs(args.out, exist_ok=True)
    if args.remote:
        project = os.path.splitext(os.path.basename(args.project))[0]
        run_blender(args, "remote_check.py", args.content_root, project)
        results = os.path.join(args.out, "remote_results.json")
        return 1 if not os.path.exists(results) or report(results) else 0
    if not args.editor:
        parser.error("--editor is required without --remote")
    ue_export = ""
    if args.roundtrip:
        ue_export = os.path.join(args.out, "from_unreal")
        if run_unreal(args, "export", {"MB_SMOKE_UE_EXPORT": ue_export}, "unreal_export.log"):
            return 1
    if run_blender(args, "blender_cases.py", args.content_root, *([ue_export] if ue_export else [])):
        return 1
    results = os.path.join(args.out, "results.json")
    if os.path.exists(results):
        os.remove(results)
    env = {"MB_SMOKE_CASES": os.path.join(args.out, "cases.json"), "MB_SMOKE_RESULTS": results}
    run_unreal(args, "check", env, "unreal_check.log")
    if not os.path.exists(results):
        print("[smoke] Unreal wrote no results; see the log")
        return 1
    return 1 if report(results) else 0


if __name__ == "__main__":
    sys.exit(main())
