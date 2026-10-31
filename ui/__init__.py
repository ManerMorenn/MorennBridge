# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from . import lists, panels

MODULES = (lists, panels)


def register():
    for module in MODULES:
        for cls in module.classes:
            bpy.utils.register_class(cls)


def unregister():
    for module in reversed(MODULES):
        for cls in reversed(module.classes):
            if getattr(cls, "is_registered", False):
                bpy.utils.unregister_class(cls)
