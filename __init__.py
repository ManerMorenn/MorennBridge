# SPDX-License-Identifier: GPL-3.0-or-later
"""MorennBridge: FBX interchange between Blender and Unreal Engine."""

__version__ = "0.1.0"

from . import ops, prefs, props, ui  # noqa: E402  (the version must exist before the submodules import it)

_MODULES = (props, prefs, ops, ui)


def register():
    for module in _MODULES:
        module.register()


def unregister():
    for module in reversed(_MODULES):
        module.unregister()
