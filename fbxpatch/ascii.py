# SPDX-License-Identifier: GPL-3.0-or-later
"""Text-level handling of ASCII FBX files, which the binary tools cannot read."""

import re

_ROOT_OBJECT = re.compile(rb'^(\s*(?:Model|NodeAttribute):\s*\d+,\s*"[^"]*",\s*)"Root"', re.M)
_ROOT_MODEL_NAME = re.compile(rb'^\s*Model:\s*\d+,\s*"(?:Model::)?([^"]*)",\s*"Root"', re.M)
_CREATOR = re.compile(rb'^\s*Creator:\s*"([^"]*)"', re.M)
_UNIT_SCALE = re.compile(rb'^\s*P:\s*"UnitScaleFactor",[^\n]*?,\s*([-+\d.eE]+)\s*$', re.M)
_ANIM_STACK = re.compile(rb'^\s*AnimationStack:\s*\d+,\s*"(?:AnimStack::)?([^"]*)"', re.M)


def looks_like_fbx(data):
    head = data[:4096]
    return b"FBXHeaderExtension" in head or head.lstrip().startswith(b"; FBX")


def _text(raw):
    return raw.decode("utf-8", "replace")


def creator(data):
    match = _CREATOR.search(data)
    return _text(match.group(1)) if match else ""


def unit_scale(data):
    match = _UNIT_SCALE.search(data)
    return float(match.group(1)) if match else 1.0


def anim_stacks(data):
    return [_text(name) for name in _ANIM_STACK.findall(data)]


def root_bone_names(data):
    return [_text(name) for name in _ROOT_MODEL_NAME.findall(data)]


def retype_root_bones(data):
    """Turn ``Root`` models and attributes into ``LimbNode`` so Blender imports them as bones."""
    return _ROOT_OBJECT.sub(rb'\1"LimbNode"', data)
