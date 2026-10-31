# SPDX-License-Identifier: GPL-3.0-or-later
"""Operation reports: collected during an operation, then flushed to the scene log and the operator."""

import datetime

LEVEL_ORDER = {"INFO": 0, "WARNING": 1, "ERROR": 2}


class Entry:
    __slots__ = ("level", "message", "path")

    def __init__(self, level, message, path=""):
        self.level = level
        self.message = message
        self.path = path

    def __repr__(self):
        return f"{self.level}: {self.message}"


class Report:
    """Collects log entries; ``flush`` writes them to ``scene.morenn.log`` and the operator."""

    def __init__(self, title=""):
        self.title = title
        self.entries = []

    def info(self, message, path=""):
        self.entries.append(Entry("INFO", message, path))

    def warning(self, message, path=""):
        self.entries.append(Entry("WARNING", message, path))

    def error(self, message, path=""):
        self.entries.append(Entry("ERROR", message, path))

    @property
    def errors(self):
        return [e for e in self.entries if e.level == "ERROR"]

    @property
    def warnings(self):
        return [e for e in self.entries if e.level == "WARNING"]

    @property
    def ok(self):
        return not self.errors

    def extend(self, other):
        self.entries.extend(other.entries)

    def summary(self):
        n_err, n_warn = len(self.errors), len(self.warnings)
        parts = [self.title] if self.title else []
        if n_err:
            parts.append(f"{n_err} error{'s' if n_err != 1 else ''}")
        if n_warn:
            parts.append(f"{n_warn} warning{'s' if n_warn != 1 else ''}")
        if not n_err and not n_warn:
            parts.append("OK")
        return ", ".join(parts)

    def flush(self, scene=None, operator=None, echo=True):
        for entry in self.entries:
            if scene is not None:
                add_scene_entry(scene, entry.level, entry.message, entry.path)
            if echo:
                print(f"[MorennBridge] {entry.level}: {entry.message}")
        if operator is not None:
            level = "ERROR" if self.errors else ("WARNING" if self.warnings else "INFO")
            operator.report({level}, self.summary())
        return self.ok


def add_scene_entry(scene, level, message, path=""):
    settings = getattr(scene, "morenn", None)
    if settings is None:
        return None
    entry = settings.log.add()
    entry.time = datetime.datetime.now().strftime("%H:%M:%S")
    entry.level = level if level in LEVEL_ORDER else "INFO"
    entry.message = message
    entry.path = path or ""
    settings.log_index = len(settings.log) - 1
    return entry
