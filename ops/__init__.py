# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from . import export_ops, helpers_ops, import_ops, log_ops, scene_ops, ue_ops, validate_ops

MODULES = (log_ops, scene_ops, validate_ops, export_ops, import_ops, helpers_ops, ue_ops)


def register():
    for module in MODULES:
        for cls in module.classes:
            bpy.utils.register_class(cls)


def unregister():
    for module in reversed(MODULES):
        for cls in reversed(module.classes):
            bpy.utils.unregister_class(cls)
