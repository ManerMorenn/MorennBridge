# SPDX-License-Identifier: GPL-3.0-or-later
"""Download a Blender build for CI and print ``executable=<path>`` (GitHub step output format).

python dev/ci/get_blender.py 4.5.15 ~/blender-4.5.15
python dev/ci/get_blender.py daily:main ~/blender-daily
"""

import json
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

RELEASES = "https://download.blender.org/release"
DAILY_INDEX = "https://builder.blender.org/download/daily/?format=json&v=2"


def daily_url(branch):
    with urllib.request.urlopen(DAILY_INDEX) as response:
        builds = json.load(response)
    wanted = {
        "Linux": ("linux", "x86_64", "xz"),
        "Windows": ("windows", "amd64", "zip"),
        "Darwin": ("darwin", "arm64", "dmg"),
    }[platform.system()]
    for build in builds:
        found = (build.get("platform"), build.get("architecture"), build.get("file_extension"))
        if found == wanted and build.get("branch") == branch:
            return build["url"]
    raise SystemExit(f"no daily build for branch {branch} on {wanted}")


def release_url(version):
    series = version.rsplit(".", 1)[0]
    name = {
        "Linux": f"blender-{version}-linux-x64.tar.xz",
        "Windows": f"blender-{version}-windows-x64.zip",
        "Darwin": f"blender-{version}-macos-arm64.dmg",
    }[platform.system()]
    return f"{RELEASES}/Blender{series}/{name}"


def strip_top_folder(extracted, target):
    top = next(pathlib.Path(extracted).iterdir())
    shutil.move(str(top), str(target))


def install(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        archive = pathlib.Path(work) / url.rsplit("/", 1)[-1]
        urllib.request.urlretrieve(url, archive)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(pathlib.Path(work) / "out")
            strip_top_folder(pathlib.Path(work) / "out", target)
        elif archive.suffix == ".dmg":
            mount = pathlib.Path(work) / "mount"
            subprocess.run(
                ["hdiutil", "attach", str(archive), "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount)],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            target.mkdir()
            subprocess.run(["cp", "-R", str(mount / "Blender.app"), str(target)], check=True)
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, stdout=subprocess.DEVNULL)
        else:
            target.mkdir()
            subprocess.run(["tar", "-xJf", str(archive), "--strip-components=1", "-C", str(target)], check=True)


def executable(target):
    return {
        "Linux": target / "blender",
        "Windows": target / "blender.exe",
        "Darwin": target / "Blender.app" / "Contents" / "MacOS" / "Blender",
    }[platform.system()]


def main():
    version, target = sys.argv[1], pathlib.Path(sys.argv[2]).expanduser()
    if not executable(target).exists():
        install(daily_url(version[6:]) if version.startswith("daily:") else release_url(version), target)
    print(f"executable={executable(target)}")


if __name__ == "__main__":
    main()
