# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the Unreal names used by ue/scripts and tests/fake_unreal.py against the editor's Python stub.

    python dev/check_ue_names.py <Project>/Intermediate/PythonStub/unreal.py [-v]

The editor writes the stub at startup when Editor Preferences > Plugins > Python > Developer Mode is on. Checked:
classes and functions used as ``unreal.X``, members used as ``unreal.X.y``, editor property names passed to
``setp``/``getp``/``set_editor_property``/``get_editor_property`` (on the receiver's class when it can be inferred)
and enum members passed to ``enum_value``. The first name of every fallback list must exist. The fake module's
classes, editor properties (with their read-only flag), enum members and methods must exist too.
Exit status 1 when something is missing.
"""

import ast
import importlib.util
import inspect
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "ue", "scripts")
FAKE = os.path.join(ROOT, "tests", "fake_unreal.py")

CLASS = re.compile(r"^class (\w+)\(([^)]*)\):")
MEMBER = re.compile(r"^    (?:def (\w+)\(|(\w+)\s*[:=])")
FUNCTION = re.compile(r"^def (\w+)\(")
CONSTANT = re.compile(r"^(\w+)\s*[:=]")
EDITOR_PROPERTY = re.compile(r"^    - ``(\w+)`` \((.*)\):\s+\[(Read-Only|Read-Write)\]")
PROPERTY_CALLS = {"setp": 1, "getp": 1, "set_editor_property": 0, "get_editor_property": 0}


class Stub:
    def __init__(self, path):
        self.classes = {}
        self.functions = set()
        current = None
        in_doc = False
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.rstrip("\r\n")
                toggles = line.count('"""') % 2 == 1
                if in_doc:
                    match = EDITOR_PROPERTY.match(line) if current is not None else None
                    if match:
                        current["props"][match[1]] = (match[2], match[3] == "Read-Only")
                    in_doc = not toggles
                    continue
                in_doc = toggles
                if match := CLASS.match(line):
                    bases = [b.strip().split("[")[0] for b in match[2].split(",") if b.strip()]
                    current = self.classes[match[1]] = {"bases": bases, "members": set(), "props": {}}
                elif match := FUNCTION.match(line):
                    self.functions.add(match[1])
                    current = None
                elif match := CONSTANT.match(line):
                    self.functions.add(match[1])
                    current = None
                elif current is not None and (match := MEMBER.match(line)):
                    current["members"].add(match[1] or match[2])

    def has(self, name):
        return name in self.classes or name in self.functions

    def lineage(self, name):
        seen = []
        while name in self.classes and name not in seen:
            seen.append(name)
            bases = self.classes[name]["bases"]
            name = bases[0] if bases else None
        return seen

    def prop(self, cls, name):
        for klass in self.lineage(cls):
            if name in self.classes[klass]["props"]:
                return self.classes[klass]["props"][name]
        return None

    def member(self, cls, name):
        return any(name in self.classes[k]["members"] or name in self.classes[k]["props"] for k in self.lineage(cls))

    def any_class_prop(self, name):
        return any(name in c["props"] for c in self.classes.values())

    def prop_type(self, cls, name):
        found = self.prop(cls, name) if cls else None
        return found[0] if found and found[0] in self.classes else None


class Findings:
    def __init__(self, verbose):
        self.verbose = verbose
        self.problems = []
        self.checked = 0

    def check(self, ok, where, text):
        self.checked += 1
        if not ok:
            self.problems.append(f"{where}: {text}")
        if self.verbose or not ok:
            print(f"{'ok     ' if ok else 'MISSING'} {where}: {text}")
        return ok


def _constants(node):
    """String constants of an argument; both branches of ``a if cond else b``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _constants(node.body) + _constants(node.orelse)
    return []


def _str_args(call, start):
    return [value for arg in call.args[start:] for value in _constants(arg)[:1]]


def _alternatives(call):
    for kw in call.keywords:
        if kw.arg == "alternatives" and isinstance(kw.value, ast.Tuple | ast.List):
            return [e.value for e in kw.value.elts if isinstance(e, ast.Constant)]
    return []


def _unreal_name(node):
    """``unreal.X`` -> "X", ``unreal.X.y`` -> ("X", "y")."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "unreal":
        return node.attr
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
        owner = _unreal_name(node.value)
        if isinstance(owner, str):
            return owner, node.attr
    return None


class ScriptChecker(ast.NodeVisitor):
    def __init__(self, stub, findings, filename):
        self.stub = stub
        self.findings = findings
        self.filename = filename
        self.types = [{}]

    def where(self, node):
        return f"{os.path.relpath(self.filename, ROOT)}:{node.lineno}"

    def visit_FunctionDef(self, node):
        self.types.append({})
        self.generic_visit(node)
        self.types.pop()

    def type_of(self, node):
        if isinstance(node, ast.Name):
            return self.types[-1].get(node.id) or self.types[0].get(node.id)
        return None

    def visit_Assign(self, node):
        self.generic_visit(node)
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        value = node.value
        kind = None
        if isinstance(value, ast.Call):
            name = _unreal_name(value.func)
            if isinstance(name, str) and name in self.stub.classes:
                kind = name
            elif isinstance(value.func, ast.Attribute) and value.func.attr in ("getp", "get_editor_property"):
                offset = PROPERTY_CALLS[value.func.attr]
                receiver = value.args[0] if offset else value.func.value
                names = _str_args(value, offset)
                if names:
                    kind = self.stub.prop_type(self.type_of(receiver), names[0])
        self.types[-1][node.targets[0].id] = kind

    def visit_Attribute(self, node):
        name = _unreal_name(node)
        if isinstance(name, str):
            self.findings.check(self.stub.has(name), self.where(node), f"unreal.{name}")
        elif isinstance(name, tuple):
            cls, member = name
            self.findings.check(self.stub.member(cls, member), self.where(node), f"unreal.{cls}.{member}")
            return
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            if isinstance(node.args[0], ast.Name) and node.args[0].id == "unreal":
                names = _str_args(node, 1)
                if names:
                    self.findings.check(self.stub.has(names[0]), self.where(node), f"unreal.{names[0]}")
        elif isinstance(func, ast.Attribute) and func.attr == "enum_value":
            self.check_enum(node)
        elif isinstance(func, ast.Attribute) and func.attr in PROPERTY_CALLS:
            self.check_property(node)
        self.generic_visit(node)

    def check_enum(self, node):
        enum = _str_args(node, 0)[:1]
        if not enum:
            return
        enum = enum[0]
        if not self.findings.check(enum in self.stub.classes, self.where(node), f"enum {enum}"):
            return
        required = _constants(node.args[1]) if len(node.args) > 1 else []
        fallbacks = _str_args(node, 2)
        for member in required:
            self.findings.check(self.stub.member(enum, member), self.where(node), f"{enum}.{member}")
        for member in fallbacks:
            if self.stub.member(enum, member):
                self.findings.check(True, self.where(node), f"{enum}.{member} (fallback)")

    def check_property(self, node):
        offset = PROPERTY_CALLS[node.func.attr]
        names = _str_args(node, offset)[:1] + _alternatives(node)
        if not names:
            return
        receiver = node.args[0] if offset else node.func.value
        cls = self.type_of(receiver)
        for index, name in enumerate(names):
            if cls:
                ok = self.stub.prop(cls, name) is not None
                text = f"{cls}.{name}"
            else:
                ok = self.stub.any_class_prop(name)
                text = f"<some class>.{name}"
            if index == 0 or ok:
                self.findings.check(ok, self.where(node), text + (" (fallback)" if index else ""))


def check_scripts(stub, findings):
    for name in sorted(os.listdir(SCRIPTS)):
        if name.endswith(".py"):
            path = os.path.join(SCRIPTS, name)
            with open(path, encoding="utf-8") as fh:
                ScriptChecker(stub, findings, path).visit(ast.parse(fh.read(), path))


def check_fake(stub, findings):
    spec = importlib.util.spec_from_file_location("fake_unreal", FAKE)
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    helpers = set(getattr(fake, "HELPERS", ()))
    where = os.path.relpath(FAKE, ROOT)
    for name, value in vars(fake).items():
        if name.startswith("_") or name in helpers or inspect.ismodule(value) or name.isupper():
            continue
        if inspect.isfunction(value):
            findings.check(name in stub.functions, where, f"function {name}")
            continue
        if not inspect.isclass(value):
            continue
        if not findings.check(name in stub.classes, where, f"class {name}"):
            continue
        if issubclass(value, fake.EnumBase) and value is not fake.EnumBase:
            for member, entry in vars(value).items():
                if isinstance(entry, fake.EnumBase):
                    findings.check(stub.member(name, member), where, f"{name}.{member}")
            continue
        for prop_name, prop in vars(value).get("_props", {}).items():
            found = stub.prop(name, prop_name)
            same = found is not None and found[1] == prop.read_only
            detail = "" if found is None or same else f" (stub says read-only={found[1]})"
            findings.check(same, where, f"{name}.{prop_name}{detail}")
        for prop_name in vars(value).get("_deprecated", {}):
            findings.check(stub.prop(name, prop_name) is not None, where, f"{name}.{prop_name} (deprecated)")
        for member, entry in vars(value).items():
            if not member.startswith("_") and callable(getattr(value, member)) and not inspect.isclass(entry):
                findings.check(stub.member(name, member), where, f"{name}.{member}()")


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    if len(args) != 1:
        print(__doc__)
        return 2
    stub = Stub(args[0])
    if len(stub.classes) < 100 or not any(c["props"] for c in stub.classes.values()):
        print(f"{args[0]} does not look like the editor's Python stub")
        return 2
    findings = Findings("-v" in argv)
    check_scripts(stub, findings)
    check_fake(stub, findings)
    print(f"{findings.checked} names checked against {args[0]}: {len(findings.problems)} missing")
    return 1 if findings.problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
