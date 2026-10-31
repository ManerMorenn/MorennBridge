# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for the scripts MorennBridge runs inside the Unreal Editor's Python."""

import json
import traceback

import unreal

LAST_RESULT = "{}"


def log(msg):
    unreal.log(f"[MorennBridge] {msg}")


def warn(msg):
    unreal.log_warning(f"[MorennBridge] {msg}")


def error(msg):
    unreal.log_error(f"[MorennBridge] {msg}")


def set_result(data, path=None):
    global LAST_RESULT
    LAST_RESULT = json.dumps(data, indent=2)
    if path:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(LAST_RESULT)
        except OSError as exc:
            warn(f"Could not write result file {path}: {exc}")
    return LAST_RESULT


def last_result_json():
    return LAST_RESULT


def setp(obj, name, value, alternatives=(), optional=False):
    """``obj.set_editor_property`` with fallbacks; returns True when a property was set."""
    for candidate in (name, *alternatives):
        try:
            obj.set_editor_property(candidate, value)
            return True
        except Exception:
            continue
    if not optional:
        warn(f"Property '{name}' not found on {type(obj).__name__}; left at its default")
    return False


def getp(obj, name, default=None):
    try:
        return obj.get_editor_property(name)
    except Exception:
        return default


def enum_value(enum_name, *member_names):
    enum_cls = getattr(unreal, enum_name, None)
    if enum_cls is None:
        return None
    for member in member_names:
        value = getattr(enum_cls, member, None)
        if value is not None:
            return value
    return None


def ensure_directory(content_path):
    lib = unreal.EditorAssetLibrary
    if not lib.does_directory_exist(content_path):
        lib.make_directory(content_path)


def asset_path(asset):
    return asset.get_path_name().split(".")[0]


def rename_asset(asset, new_name):
    """Rename in place; returns the final object path (existing asset with that name is reused, not overwritten)."""
    lib = unreal.EditorAssetLibrary
    old_path = asset_path(asset)
    folder = old_path.rsplit("/", 1)[0]
    new_path = f"{folder}/{new_name}"
    if new_path == old_path:
        return old_path
    if lib.does_asset_exist(new_path):
        warn(f"{new_path} already exists; keeping {old_path}")
        return old_path
    if lib.rename_asset(old_path, new_path):
        return new_path
    warn(f"Could not rename {old_path} to {new_name}")
    return old_path


def save_asset(asset):
    try:
        unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False)
    except Exception as exc:
        warn(f"Could not save {asset.get_name()}: {exc}")


def list_assets(folder, recursive=False):
    try:
        return list(unreal.EditorAssetLibrary.list_assets(folder, recursive=recursive, include_folder=False))
    except Exception:
        return []


def load(path):
    try:
        return unreal.load_asset(path)
    except Exception:
        return None


def exc_text():
    return traceback.format_exc()
