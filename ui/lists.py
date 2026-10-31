# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

_ICONS = {"INFO": "INFO", "WARNING": "ERROR", "ERROR": "CANCEL"}


class MORENN_UL_log(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.time, icon=_ICONS.get(item.level, "INFO"))
        row.label(text=item.message)


class MORENN_UL_issues(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.code, icon=_ICONS.get(item.severity, "INFO"))
        row.label(text=item.message)
        if item.object_name:
            row.label(text=item.object_name, icon="OBJECT_DATA")


class MORENN_UL_uenodes(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=f"{item.project or '(no project)'}, UE {item.engine} on {item.machine}", icon="PLAY")


classes = (MORENN_UL_log, MORENN_UL_issues, MORENN_UL_uenodes)
