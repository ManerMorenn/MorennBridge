# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset / node naming rules shared by export, import and validation.

Unreal's importers rewrite names they do not like; MorennBridge produces names Unreal keeps untouched so
that round trips stay exact. The character sets below mirror ``InterchangeHelper.cpp`` (a superset of the
legacy importer's set).
"""

import re

# Characters Interchange replaces with '_' (INVALID_OBJECTNAME_CHARACTERS); space -> '-', '+' -> '_'.
UE_INVALID_CHARS = frozenset("' ,/.:|&!~\n\r\t@#(){}[]=;^%$`")
COLLISION_PREFIXES = ("UCX", "UBX", "USP", "UCP", "MCDCX")
SOCKET_PREFIX = "SOCKET_"

_NUMERIC_SUFFIX = re.compile(r"\.\d{3,}$")
_LOD_SUFFIX = re.compile(r"^(?P<base>.+)_LOD(?P<index>\d+)$", re.IGNORECASE)
_LOD_PREFIX = re.compile(r"^LOD(?P<index>\d+)_(?P<base>.+)$", re.IGNORECASE)
_COLLISION = re.compile(r"^(?P<kind>UCX|UBX|USP|UCP|MCDCX)_(?P<body>.+)$", re.IGNORECASE)


def split_prefixes(csv):
    return [p for p in (s.strip() for s in csv.split(",")) if p]


def strip_known_prefix(name, prefixes):
    """Remove one known prefix (longest match first). Returns ``(base, prefix_or_empty)``."""
    for prefix in sorted(prefixes, key=len, reverse=True):
        if prefix and name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix) :], prefix
    return name, ""


def strip_numeric_suffix(name):
    return _NUMERIC_SUFFIX.sub("", name)


def sanitize_asset_name(name):
    """Conservative asset-name sanitiser: ``[A-Za-z0-9_]`` only, no runs of ``_``, no leading digit."""
    out = re.sub(r"[^A-Za-z0-9_]", "_", name)
    out = re.sub(r"_+", "_", out).strip("_")
    if not out:
        out = "Asset"
    if out[0].isdigit():
        out = "_" + out
    return out


def sanitize_ue_name(name):
    """Exactly what Unreal Interchange does to a node/bone name (so Blender names can match UE names)."""
    out = []
    for ch in name:
        if ch == " ":
            out.append("-")
        elif ch == "+" or ch in UE_INVALID_CHARS:
            out.append("_")
        else:
            out.append(ch)
    result = "".join(out)
    return result if result else "Null"


def is_ue_safe(name):
    return sanitize_ue_name(name) == name


def base_name(raw_name, known_prefixes, override=""):
    """Base asset name (no prefix) from an object name or an explicit override."""
    source = override.strip() if override and override.strip() else raw_name
    base, _ = strip_known_prefix(source, known_prefixes)
    base = strip_numeric_suffix(base)
    return sanitize_asset_name(base)


def asset_name(base, prefix):
    if prefix and base.startswith(prefix):
        return base
    return f"{prefix}{base}"


def collision_node_name(kind, render_node, index):
    return f"{kind}_{render_node}_{index:02d}"


def parse_collision_name(name):
    m = _COLLISION.match(name)
    if not m:
        return None
    return m.group("kind").upper(), m.group("body")


def collision_body_candidates(name):
    """Names a collision node may refer to, in Unreal's matching order."""
    parsed = parse_collision_name(name)
    if parsed is None:
        return []
    body = parsed[1]
    candidates = [body]
    if "_" in body:
        candidates.append(body.rsplit("_", 1)[0])
    return candidates


def socket_node_name(name):
    return name if name.startswith(SOCKET_PREFIX) else f"{SOCKET_PREFIX}{name}"


def is_socket_name(name):
    return name.startswith(SOCKET_PREFIX)


def lod_node_name(asset, index, mode):
    if mode == "LOD_NAMING":
        return f"LOD{index}_{asset}"
    return f"{asset}_LOD{index}"


def parse_lod_name(name):
    """Return ``(base, index)`` for ``Name_LOD2`` or ``LOD2_Name`` style names, else None."""
    m = _LOD_SUFFIX.match(name) or _LOD_PREFIX.match(name)
    if not m:
        return None
    return m.group("base"), int(m.group("index"))


def collision_mesh_name(template, asset):
    try:
        return template.format(asset=asset)
    except (KeyError, IndexError, ValueError):
        return f"{asset}_COLL"


def anim_asset_name(template, prefix, rig, action):
    try:
        name = template.format(prefix=prefix, rig=rig, action=action)
    except (KeyError, IndexError, ValueError):
        name = f"{prefix}{rig}_{action}"
    return sanitize_asset_name(name) if not is_ue_safe(name) else name


def unique(names):
    """Return the names that occur more than once."""
    seen, dupes = set(), []
    for n in names:
        if n in seen and n not in dupes:
            dupes.append(n)
        seen.add(n)
    return dupes
