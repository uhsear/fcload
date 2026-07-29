#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fcload - load a dataset into a geodatabase, refusing the imports that corrupt silently.

WHAT IS NEW HERE (one claim only)
---------------------------------
Every step this tool performs is arcpy: FeatureClassToFeatureClass, Append, AddField,
ListFields, AddGlobalIDs, EnableEditorTracking, Describe. The novelty is the refusal
gate in front of them.

arcpy will happily import a source whose spatial reference is UNDEFINED (factoryCode 0,
name "Unknown" - e.g. a shapefile whose .prj was lost) at maxSeverity 0, with zero
warnings. The raw coordinates are copied unchanged and simply stamped with the TARGET
dataset's spatial reference. Afterwards arcpy.Exists is True, GetCount is right, and
Describe reports the target SR. Nothing anywhere says the features are in the wrong
place. Measured on ArcGIS Pro 3.6: a WGS84 point at (-82.14, 29.19) with its .prj
deleted landed in a WKID 2237 (NAD83 StatePlane Florida West, US survey feet)
dataset still reading X = -82.1399, Y = 29.1899, which is 1,868,308 feet (354
miles) from where it belongs.

The usual guard in hand-rolled import scripts makes this worse, not better:

    if src_wkid != TARGET_WKID and src_wkid != 0:    # reproject
    if info["wkid"] != TARGET_WKID and info["wkid"] != 0:   # warn

wkid == 0 IS the undefined case. Both guards exclude precisely the input that needs
them. This tool inverts that: undefined is the loudest possible failure, and the only
way past it is an explicit --assume-sr WKID that is written into the log.

REFUSALS (fcload stops instead of importing)
--------------------------------------------
1. UNDEFINED source spatial reference (factoryCode 0 or name "Unknown").
   Override: --assume-sr WKID, which is recorded.
2. Projected source spatial reference whose extent lies entirely inside degree bounds
   (|x| <= 180, |y| <= 90). The .prj is wrong; the coordinates are lat/long.
3. Geographic source spatial reference whose extent falls outside degree bounds.
   The .prj is wrong; the coordinates are projected units.
4. Row count after the load does not match the row count expected. The load is
   reported as failed rather than silently accepted.
5. Anything destructive without --apply. Dry run is the default for every subcommand.

VERSIONING (ERROR 001332)
-------------------------
Versioning cannot be registered on an individual feature class that lives inside a
feature dataset which is already registered as versioned - arcpy raises ERROR 001332.
The fix is not in this script: re-register versioning at the DATASET level in ArcGIS
Pro (right-click the dataset > Manage > Register As Versioned), which picks up the
newly added feature class. `fcload chores` prints this reminder rather than pretending.

USAGE
-----
    python fcload.py --self-test                     # offline, no arcpy, no network
    python fcload.py import  --source S --workspace W [--dataset D] [--name N] [--apply]
    python fcload.py chores  --workspace W --target T [--apply]
    python fcload.py diff    --source S --workspace W --target T [--add-missing] [--apply]

arcpy is imported only inside the functions that touch a geodatabase. The decision
logic - sr_verdict, field_diff, tracking_field_names, validate_args - is pure and is
exercised by --self-test on any Python 3.8+ with no Esri software installed.
"""

from __future__ import annotations

import argparse
import os
import sys

__version__ = "1.0.0"

ACCEPT = "ACCEPT"
REFUSE = "REFUSE"

# Longitude/latitude bounds. A coordinate pair inside this box is almost certainly
# degrees; a pair outside it is almost certainly projected units.
DEGREE_X = 180.0
DEGREE_Y = 90.0

# Names arcpy manages itself. Diffing or adding these is never the user's job.
SYSTEM_FIELDS = frozenset(
    n.lower()
    for n in (
        "OBJECTID", "OID", "FID", "SHAPE", "GEOMETRY", "GLOBALID",
        "SHAPE_LENGTH", "SHAPE_AREA", "SHAPE.LEN", "SHAPE.AREA",
        "SHAPE_LENG", "ST_LENGTH(SHAPE)", "ST_AREA(SHAPE)",
    )
)

UNDEFINED_SR_NAMES = frozenset(("", "unknown", "<undefined>", "undefined", "none"))

# Two editor-tracking naming conventions seen in the wild. Enterprise geodatabases
# built by hand often use the first; anything Esri created uses the second.
TRACKING_UPPER = ("CREATION_USER", "CREATION_DATE", "MODIFY_USER", "MODIFY_DATE")
TRACKING_LOWER = ("created_user", "created_date", "last_edited_user", "last_edited_date")

ARCPY_HINT = (
    "arcpy is required for this operation but is not importable.\n"
    "Run it with the ArcGIS Pro interpreter, typically:\n"
    r"  C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
    "\n(--self-test needs no arcpy and runs on any Python 3.8+.)"
)


# Every arcpy call that changes something. --self-test asserts none of these fire
# on a dry run, which is the only way the --apply gate can be tested without Esri.
WRITE_CALLS = frozenset((
    "CopyFeatures", "DefineProjection", "Project", "Append", "Delete",
    "FeatureClassToFeatureClass", "AddGlobalIDs", "EnableEditorTracking",
    "AddField", "CreateTable",
))

# Test seam. None means "import the real arcpy"; --self-test swaps in a stub and
# puts it back. Nothing else in the tool ever writes to it.
_ARCPY_OVERRIDE = None


# --------------------------------------------------------------------------- #
# arcpy access - the ONLY import site, deliberately inside a function
# --------------------------------------------------------------------------- #
def _arcpy():
    """Import arcpy lazily so the pure core and --self-test never need it."""
    if _ARCPY_OVERRIDE is not None:
        return _ARCPY_OVERRIDE
    try:
        import arcpy  # noqa: F401  (imported for its side effect of existing)
    except ModuleNotFoundError:
        raise SystemExit(ARCPY_HINT)
    return arcpy


# --------------------------------------------------------------------------- #
# Pure decision core - no arcpy, no I/O, no globals mutated
# --------------------------------------------------------------------------- #
def extent_in_degree_range(extent):
    """True when every corner of `extent` fits inside longitude/latitude bounds."""
    if not extent:
        return None
    xmin, ymin, xmax, ymax = (float(v) for v in extent)
    return (
        abs(xmin) <= DEGREE_X
        and abs(xmax) <= DEGREE_X
        and abs(ymin) <= DEGREE_Y
        and abs(ymax) <= DEGREE_Y
    )


def sr_verdict(sr_factory_code, sr_type, sr_name, extent, target_wkid, assume_sr=None):
    """Decide whether a source may be loaded, from plain data only.

    sr_factory_code: Describe(...).spatialReference.factoryCode (0 == undefined)
    sr_type        : "Geographic" | "Projected" | "" (unknown)
    sr_name        : .name, where "Unknown" also means undefined
    extent         : (xmin, ymin, xmax, ymax) in source units, or None
    target_wkid    : WKID of the destination, or None/0 when it has none
    assume_sr      : explicit operator override for an undefined source

    Returns (ACCEPT|REFUSE, [reason strings]).
    """
    reasons = []
    code = int(sr_factory_code or 0)
    name = (sr_name or "").strip()
    kind = (sr_type or "").strip().lower()
    target = int(target_wkid or 0)

    undefined = code == 0 or name.lower() in UNDEFINED_SR_NAMES

    if undefined:
        if assume_sr:
            assumed = int(assume_sr)
            reasons.append(
                "source spatial reference is UNDEFINED (factoryCode={0}, name={1!r}); "
                "OVERRIDE ACCEPTED: --assume-sr {2} supplied by the operator and "
                "recorded here".format(code, name or "Unknown", assumed)
            )
            # ponytail: the assumed SR is trusted as stated. Sanity-checking the extent
            # against it would need an EPSG table; --assume-sr is already an explicit,
            # logged human decision. Add the table if the override starts being abused.
            if target and assumed != target:
                reasons.append(
                    "assumed WKID {0} differs from target WKID {1}: will reproject "
                    "before loading".format(assumed, target)
                )
            else:
                reasons.append(
                    "assumed WKID {0} matches the target: no reprojection".format(assumed)
                )
            return ACCEPT, reasons

        reasons.append(
            "source spatial reference is UNDEFINED (factoryCode={0}, name={1!r})".format(
                code, name or "Unknown"
            )
        )
        reasons.append(
            "arcpy would import this at maxSeverity 0 with no warning and stamp the raw "
            "coordinates with the TARGET spatial reference, silently relocating every "
            "feature"
        )
        reasons.append(
            "supply --assume-sr WKID if you know what the coordinates really are"
        )
        return REFUSE, reasons

    in_degrees = extent_in_degree_range(extent)

    if in_degrees is None:
        reasons.append("extent unavailable: coordinate-range sanity check skipped")
    elif kind == "projected" and in_degrees:
        reasons.append(
            "spatial reference {0!r} (WKID {1}) is PROJECTED but the whole extent "
            "{2} fits inside degree bounds (|x|<={3:.0f}, |y|<={4:.0f})".format(
                name, code, tuple(extent), DEGREE_X, DEGREE_Y
            )
        )
        reasons.append(
            "the coordinates are latitude/longitude and the .prj is wrong; loading "
            "this would place the data near the origin of the projected grid"
        )
        # ponytail: false-positives on a local projected grid whose data genuinely sits
        # within 180 units of its origin. No override today - refusing a real dataset is
        # cheaper than importing a mislabelled one. Add --force if that case shows up.
        return REFUSE, reasons
    elif kind == "geographic" and in_degrees is False:
        reasons.append(
            "spatial reference {0!r} (WKID {1}) is GEOGRAPHIC but the extent {2} falls "
            "outside degree bounds (|x|<={3:.0f}, |y|<={4:.0f})".format(
                name, code, tuple(extent), DEGREE_X, DEGREE_Y
            )
        )
        reasons.append(
            "the coordinates are in projected units and the .prj is wrong; any "
            "reprojection from here produces garbage"
        )
        return REFUSE, reasons
    else:
        reasons.append(
            "spatial reference {0!r} (WKID {1}, {2}) is consistent with its "
            "extent".format(name, code, sr_type or "type unknown")
        )

    if not target:
        reasons.append("target has no spatial reference of its own: loading as-is")
    elif code == target:
        reasons.append("source WKID {0} matches the target: no reprojection".format(code))
    else:
        reasons.append(
            "source WKID {0} differs from target WKID {1}: this is a reprojection, "
            "not a corruption".format(code, target)
        )

    return ACCEPT, reasons


def _field_pairs(fields):
    """Normalise arcpy Field objects, dicts, or (name, type) tuples to [(name, type)]."""
    out = []
    for f in fields or []:
        if isinstance(f, dict):
            name, ftype = f.get("name"), f.get("type")
        elif isinstance(f, (tuple, list)):
            name = f[0]
            ftype = f[1] if len(f) > 1 else ""
        else:
            name, ftype = getattr(f, "name", None), getattr(f, "type", "")
        if not name:
            continue
        if str(name).lower() in SYSTEM_FIELDS:
            continue
        out.append((str(name), str(ftype or "")))
    return out


def field_diff(source_fields, target_fields):
    """Compare two field lists case-insensitively, ignoring geodatabase-managed fields.

    Returns (missing_in_target, missing_in_source, type_conflicts) where the first two
    are lists of (name, type) in their original casing and the third is a list of
    (name, source_type, target_type).
    """
    src = _field_pairs(source_fields)
    tgt = _field_pairs(target_fields)
    src_by_key = {n.lower(): (n, t) for n, t in src}
    tgt_by_key = {n.lower(): (n, t) for n, t in tgt}

    missing_in_target = [src_by_key[k] for k in (n.lower() for n, _ in src) if k not in tgt_by_key]
    missing_in_source = [tgt_by_key[k] for k in (n.lower() for n, _ in tgt) if k not in src_by_key]

    type_conflicts = []
    for key in src_by_key:
        if key not in tgt_by_key:
            continue
        s_name, s_type = src_by_key[key]
        t_name, t_type = tgt_by_key[key]
        if s_type.upper() != t_type.upper():
            type_conflicts.append((s_name, s_type, t_type))

    return missing_in_target, missing_in_source, sorted(type_conflicts)


def tracking_field_names(existing_fields):
    """Pick the four editor-tracking field names this dataset already uses.

    Enterprise geodatabases are not consistent: some use CREATION_USER/CREATION_DATE/
    MODIFY_USER/MODIFY_DATE, some use the Esri default created_user/created_date/
    last_edited_user/last_edited_date. Passing the wrong four to EnableEditorTracking
    adds a second, duplicate set of columns. Existing names win, exact casing included.
    """
    present = {}
    for f in existing_fields or []:
        if isinstance(f, str):
            name = f
        elif isinstance(f, dict):
            name = f.get("name", "")
        elif isinstance(f, (tuple, list)):
            name = f[0] if f else ""
        else:
            name = getattr(f, "name", "")
        if name:
            present[str(name).lower()] = str(name)

    upper_hits = sum(1 for n in TRACKING_UPPER if n.lower() in present)
    lower_hits = sum(1 for n in TRACKING_LOWER if n.lower() in present)

    style = TRACKING_UPPER if upper_hits > lower_hits else TRACKING_LOWER
    return tuple(present.get(n.lower(), n) for n in style)


def validate_args(ns):
    """Reject impossible flag combinations. Returns a list of error strings."""
    errors = []
    cmd = getattr(ns, "command", None)

    if cmd == "import":
        if not ns.source:
            errors.append("import requires --source")
        if not ns.workspace:
            errors.append("import requires --workspace")
    elif cmd == "chores":
        if not ns.workspace:
            errors.append("chores requires --workspace")
        if not ns.target:
            errors.append("chores requires --target (the feature class to work on)")
    elif cmd == "diff":
        if not ns.source:
            errors.append("diff requires --source")
        if not ns.workspace:
            errors.append("diff requires --workspace")
        if not ns.target:
            errors.append("diff requires --target (the feature class to compare against)")

    if ns.add_missing and cmd != "diff":
        errors.append("--add-missing applies to the 'diff' subcommand only")
    if ns.assume_sr is not None and cmd != "import":
        errors.append("--assume-sr applies to the 'import' subcommand only")
    if ns.assume_sr is not None and int(ns.assume_sr) <= 0:
        errors.append("--assume-sr must be a positive WKID (0 is the undefined case)")
    if ns.check_write and ns.apply:
        errors.append("--check-write is a probe and cannot be combined with --apply")

    return errors


def build_parser():
    p = argparse.ArgumentParser(
        prog="fcload",
        description="Load a dataset into a geodatabase, refusing the imports that corrupt silently.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("USAGE\n-----\n", 1)[-1],
    )
    p.add_argument("command", nargs="?", choices=("import", "chores", "diff"),
                   help="import | chores | diff")
    p.add_argument("--workspace", help="Target geodatabase (.gdb or .sde connection file)")
    p.add_argument("--source", help="Source feature class or shapefile")
    p.add_argument("--target", help="Target feature class name inside the workspace")
    p.add_argument("--dataset", help="Target feature dataset inside the workspace")
    p.add_argument("--name", help="Output feature class name (default: the source name)")
    p.add_argument("--assume-sr", type=int, default=None, metavar="WKID",
                   help="Explicit override for an UNDEFINED source SR; recorded in the log")
    p.add_argument("--add-missing", action="store_true",
                   help="diff: add fields present in the source but missing in the target")
    p.add_argument("--apply", action="store_true",
                   help="Actually perform the work. Everything is a dry run without it.")
    p.add_argument("--check-write", action="store_true",
                   help="Probe whether the workspace is writable, then stop")
    p.add_argument("--self-test", action="store_true",
                   help="Run the offline test suite (no arcpy, no network) and exit")
    return p


# --------------------------------------------------------------------------- #
# arcpy-backed description and actions
# --------------------------------------------------------------------------- #
def describe_source(path):
    """Return the plain-data description sr_verdict needs. Touches a geodatabase."""
    arcpy = _arcpy()
    if not arcpy.Exists(path):
        raise SystemExit("source not found: {0}".format(path))
    d = arcpy.Describe(path)
    sr = getattr(d, "spatialReference", None)
    ext = getattr(d, "extent", None)
    extent = None
    if ext is not None and ext.XMin is not None:
        extent = (ext.XMin, ext.YMin, ext.XMax, ext.YMax)
    return {
        "path": path,
        "name": getattr(d, "baseName", os.path.basename(path)),
        "shape_type": getattr(d, "shapeType", "Table"),
        "wkid": int(getattr(sr, "factoryCode", 0) or 0) if sr else 0,
        "sr_type": getattr(sr, "type", "") if sr else "",
        "sr_name": getattr(sr, "name", "Unknown") if sr else "Unknown",
        "extent": extent,
        "count": int(arcpy.management.GetCount(path)[0]),
        "fields": [(f.name, f.type, f.length) for f in arcpy.ListFields(path)],
    }


def target_wkid_of(workspace, dataset=None):
    """WKID of the destination feature dataset, or 0 when the destination is a root."""
    arcpy = _arcpy()
    path = os.path.join(workspace, dataset) if dataset else workspace
    if not arcpy.Exists(path):
        raise SystemExit("target location not found: {0}".format(path))
    if not dataset:
        return 0
    sr = getattr(arcpy.Describe(path), "spatialReference", None)
    return int(getattr(sr, "factoryCode", 0) or 0) if sr else 0


def check_write(workspace):
    """Create and delete a scratch table to prove the workspace accepts writes."""
    arcpy = _arcpy()
    probe = "fcload_write_probe"
    path = os.path.join(workspace, probe)
    try:
        if arcpy.Exists(path):
            arcpy.management.Delete(path)
        arcpy.management.CreateTable(workspace, probe)
        arcpy.management.Delete(path)
    except Exception as exc:  # arcpy raises ExecuteError, which we cannot name here
        print("  NOT WRITABLE: {0}".format(exc))
        return False
    print("  writable: {0}".format(workspace))
    return True


def cmd_import(ns):
    arcpy = _arcpy()
    src = describe_source(ns.source)
    target_wkid = target_wkid_of(ns.workspace, ns.dataset)
    out_name = ns.name or src["name"]
    out_path = os.path.join(ns.workspace, ns.dataset) if ns.dataset else ns.workspace

    print("fcload import")
    print("  source     : {0}".format(src["path"]))
    print("  geometry   : {0}   rows: {1}   fields: {2}".format(
        src["shape_type"], src["count"], len(src["fields"])))
    print("  source SR  : {0!r} (WKID {1}, {2})".format(
        src["sr_name"], src["wkid"], src["sr_type"] or "type unknown"))
    print("  extent     : {0}".format(src["extent"]))
    print("  target     : {0} -> {1}  (WKID {2})".format(out_path, out_name, target_wkid))

    verdict, reasons = sr_verdict(
        src["wkid"], src["sr_type"], src["sr_name"], src["extent"], target_wkid,
        assume_sr=ns.assume_sr,
    )
    print("  verdict    : {0}".format(verdict))
    for r in reasons:
        print("    - {0}".format(r))

    if verdict == REFUSE:
        return 2

    if not ns.apply:
        print("\n  DRY RUN. Nothing was written. Re-run with --apply to load.")
        return 0

    work = src["path"]
    scratch = []
    effective_wkid = int(ns.assume_sr) if ns.assume_sr else src["wkid"]

    if ns.assume_sr:
        copy = arcpy.CreateUniqueName("fcload_assumed", arcpy.env.scratchGDB)
        arcpy.management.CopyFeatures(work, copy)
        arcpy.management.DefineProjection(copy, arcpy.SpatialReference(int(ns.assume_sr)))
        scratch.append(copy)
        work = copy
        print("  defined source SR as WKID {0} on a scratch copy (source untouched)".format(
            int(ns.assume_sr)))

    if target_wkid and effective_wkid != target_wkid:
        proj = arcpy.CreateUniqueName("fcload_projected", arcpy.env.scratchGDB)
        arcpy.management.Project(work, proj, arcpy.SpatialReference(target_wkid))
        scratch.append(proj)
        work = proj
        print("  reprojected {0} -> {1}".format(effective_wkid, target_wkid))

    dest = os.path.join(out_path, out_name)
    existed = arcpy.Exists(dest)
    before = int(arcpy.management.GetCount(dest)[0]) if existed else 0

    if existed:
        print("  target exists: appending {0} rows with Append (NO_TEST)".format(src["count"]))
        arcpy.management.Append(inputs=work, target=dest, schema_type="NO_TEST")
    else:
        arcpy.conversion.FeatureClassToFeatureClass(
            in_features=work, out_path=out_path, out_name=out_name)

    for s in scratch:
        arcpy.management.Delete(s)

    if not arcpy.Exists(dest):
        print("  FAILED: {0} does not exist after the load".format(dest))
        return 1

    after = int(arcpy.management.GetCount(dest)[0])
    expected = before + src["count"]
    print("  rows: {0} before + {1} loaded = {2} expected, {3} found".format(
        before, src["count"], expected, after))
    if after != expected:
        print("  FAILED: row count mismatch. Treat this load as incomplete.")
        return 1

    d = arcpy.Describe(dest)
    print("  OK: {0} | {1} | SR {2!r} (WKID {3})".format(
        dest, d.shapeType, d.spatialReference.name, d.spatialReference.factoryCode))
    print("  next: fcload chores --workspace ... --target {0}".format(out_name))
    return 0


def cmd_chores(ns):
    arcpy = _arcpy()
    dest = os.path.join(ns.workspace, ns.dataset, ns.target) if ns.dataset \
        else os.path.join(ns.workspace, ns.target)
    if not arcpy.Exists(dest):
        raise SystemExit("target not found: {0}".format(dest))

    existing = [f.name for f in arcpy.ListFields(dest)]
    creator, created, editor, edited = tracking_field_names(existing)
    d = arcpy.Describe(dest)
    has_gid = bool(getattr(d, "hasGlobalID", False))
    tracking_on = bool(getattr(d, "editorTrackingEnabled", False))

    print("fcload chores")
    print("  target        : {0}".format(dest))
    print("  GlobalID      : {0}".format("present" if has_gid else "MISSING -> AddGlobalIDs"))
    print("  tracking      : {0}".format("enabled" if tracking_on else "off -> EnableEditorTracking"))
    print("  tracking names: {0}, {1}, {2}, {3}".format(creator, created, editor, edited))
    print("                  (chosen from the fields already on this dataset)")

    if not ns.apply:
        print("\n  DRY RUN. Nothing was written. Re-run with --apply.")
        _print_versioning_note()
        return 0

    if not has_gid:
        arcpy.management.AddGlobalIDs(dest)
        print("  AddGlobalIDs: done")
    if not tracking_on:
        arcpy.management.EnableEditorTracking(
            in_dataset=dest,
            creator_field=creator,
            creation_date_field=created,
            last_editor_field=editor,
            last_edit_date_field=edited,
            add_fields="ADD_FIELDS",
            record_dates_in="UTC",
        )
        print("  EnableEditorTracking: done")

    d2 = arcpy.Describe(dest)
    print("  verified: hasGlobalID={0} editorTrackingEnabled={1}".format(
        getattr(d2, "hasGlobalID", False), getattr(d2, "editorTrackingEnabled", False)))
    _print_versioning_note()
    return 0


def _print_versioning_note():
    print("")
    print("  VERSIONING (ERROR 001332):")
    print("    Versioning cannot be registered on a single feature class inside a feature")
    print("    dataset that is already registered as versioned - arcpy refuses with")
    print("    ERROR 001332. Re-register at the DATASET level in ArcGIS Pro instead:")
    print("      right-click the dataset > Manage > Register As Versioned")
    print("    That picks up the newly added feature class. fcload does not do this.")


def cmd_diff(ns):
    arcpy = _arcpy()
    dest = os.path.join(ns.workspace, ns.dataset, ns.target) if ns.dataset \
        else os.path.join(ns.workspace, ns.target)
    if not arcpy.Exists(dest):
        raise SystemExit("target not found: {0}".format(dest))
    if not arcpy.Exists(ns.source):
        raise SystemExit("source not found: {0}".format(ns.source))

    src_fields = [(f.name, f.type, f.length) for f in arcpy.ListFields(ns.source)]
    tgt_fields = [(f.name, f.type, f.length) for f in arcpy.ListFields(dest)]
    missing_t, missing_s, conflicts = field_diff(src_fields, tgt_fields)

    print("fcload diff")
    print("  source: {0}".format(ns.source))
    print("  target: {0}".format(dest))
    print("  in source, missing in target ({0}):".format(len(missing_t)))
    for n, t in missing_t:
        print("    + {0} ({1})".format(n, t))
    print("  in target, missing in source ({0}):".format(len(missing_s)))
    for n, t in missing_s:
        print("    - {0} ({1})".format(n, t))
    print("  type conflicts ({0}):".format(len(conflicts)))
    for n, st, tt in conflicts:
        print("    ! {0}: source {1} vs target {2}".format(n, st, tt))

    if not ns.add_missing:
        return 0
    if not missing_t:
        print("  nothing to add.")
        return 0
    if not ns.apply:
        print("\n  DRY RUN. Would AddField {0} field(s). Re-run with --apply.".format(len(missing_t)))
        return 0

    lengths = {n.lower(): ln for n, _, ln in src_fields}
    for n, t in missing_t:
        arcpy.management.AddField(dest, n, t, field_length=lengths.get(n.lower()))
        print("  AddField: {0} ({1})".format(n, t))

    after = {f.name.lower() for f in arcpy.ListFields(dest)}
    absent = [n for n, _ in missing_t if n.lower() not in after]
    if absent:
        print("  FAILED: still missing after AddField: {0}".format(absent))
        return 1
    print("  verified: {0} field(s) added.".format(len(missing_t)))
    return 0


# --------------------------------------------------------------------------- #
# Stub arcpy - used ONLY by --self-test, so the subcommands' write paths (the
# --apply gate and the post-load row-count check) are covered with no Esri
# software present. Implements only the calls fcload actually makes.
# --------------------------------------------------------------------------- #
class _Stub(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _StubGroup(object):
    """arcpy.management / arcpy.conversion: records the call, defers to the owner."""

    def __init__(self, owner):
        self._owner = owner

    def __getattr__(self, name):
        def call(*args, **kwargs):
            return self._owner._call(name, args, kwargs)
        return call


class _StubArcpy(object):
    SRC = "src.shp"
    WS = "w.gdb"
    DS = "D"

    def __init__(self, **kw):
        g = kw.get
        self.calls = []
        self.src_count = g("src_count", 3)
        self.dest_count = g("dest_before", 0)
        self.dest_exists = g("dest_exists", False)
        self.landed = g("landed", None)          # None -> the load lands correctly
        self.src_sr = _Stub(factoryCode=g("src_wkid", 4326),
                            type=g("src_type", "Geographic"),
                            name=g("src_name", "GCS_WGS_1984"))
        self.tgt_sr = _Stub(factoryCode=g("target_wkid", 4326),
                            type="Geographic", name="GCS_WGS_1984")
        self.src_extent = g("src_extent", (-82.5, 29.0, -82.0, 29.5))
        self.src_fields = list(g("src_fields", [("NOTES", "String", 50)]))
        self.dest_fields = list(g("dest_fields", []))
        self.has_gid = g("has_gid", False)
        self.tracking = g("tracking", False)
        self.add_field_works = g("add_field_works", True)
        self.management = _StubGroup(self)
        self.conversion = _StubGroup(self)
        self.env = _Stub(scratchGDB="scratch.gdb")
        self.dest_path = os.path.join(self.WS, self.DS, "out")

    # --- the arcpy surface fcload touches ----------------------------------- #
    def Exists(self, path):
        self.calls.append(("Exists", path, {}))
        return self.dest_exists if path == self.dest_path else True

    def Describe(self, path):
        if path == self.SRC:
            ext = None if self.src_extent is None else _Stub(
                XMin=self.src_extent[0], YMin=self.src_extent[1],
                XMax=self.src_extent[2], YMax=self.src_extent[3])
            return _Stub(spatialReference=self.src_sr, extent=ext,
                         baseName="out", shapeType="Point")
        return _Stub(spatialReference=self.tgt_sr, extent=None, baseName="out",
                     shapeType="Point", hasGlobalID=self.has_gid,
                     editorTrackingEnabled=self.tracking)

    def ListFields(self, path):
        rows = self.src_fields if path == self.SRC else self.dest_fields
        return [_Stub(name=r[0], type=r[1] if len(r) > 1 else "String",
                      length=r[2] if len(r) > 2 else 0) for r in rows]

    def SpatialReference(self, wkid):
        return _Stub(factoryCode=wkid, name="WKID {0}".format(wkid))

    def CreateUniqueName(self, base, workspace):
        return os.path.join(workspace, base)

    def _call(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))
        if name == "GetCount":
            path = args[0] if args else kwargs.get("in_rows")
            return [str(self.src_count if path == self.SRC else self.dest_count)]
        if name in ("FeatureClassToFeatureClass", "Append"):
            before = self.dest_count if self.dest_exists else 0
            self.dest_count = (before + self.src_count) if self.landed is None else self.landed
            self.dest_exists = True
        elif name == "AddGlobalIDs":
            self.has_gid = True
        elif name == "EnableEditorTracking":
            self.tracking = True
        elif name == "AddField" and self.add_field_works:
            self.dest_fields.append((args[1], args[2], 0))
        return None

    def writes(self):
        return [c[0] for c in self.calls if c[0] in WRITE_CALLS]


# --------------------------------------------------------------------------- #
# Offline self-test - no arcpy, no network, no geodatabase
# --------------------------------------------------------------------------- #
def self_test():
    checks = [0]

    def ok(cond, label):
        checks[0] += 1
        if not cond:
            raise AssertionError("FAILED [{0}]: {1}".format(checks[0], label))

    SP_FEET = (400000.0, 1500000.0, 700000.0, 2100000.0)      # US survey feet
    DEG = (-82.5, 29.0, -82.0, 29.5)                          # degrees
    WORLD_DEG = (-180.0, -90.0, 180.0, 90.0)

    # --- sr_verdict: the undefined case, the entire point of this tool ------- #
    v, r = sr_verdict(0, "Geographic", "Unknown", DEG, 2237)
    ok(v == REFUSE, "factoryCode 0 is refused")
    ok(any("UNDEFINED" in x for x in r), "the refusal names the undefined SR")
    ok(any("maxSeverity 0" in x for x in r), "the refusal explains arcpy's silence")
    ok(any("--assume-sr" in x for x in r), "the refusal names the override")

    v, r = sr_verdict(4326, "Geographic", "Unknown", DEG, 2237)
    ok(v == REFUSE, "SR name 'Unknown' is refused even with a non-zero factoryCode")

    v, r = sr_verdict(0, "", "", None, 2237)
    ok(v == REFUSE, "empty SR name with code 0 is refused")

    v, r = sr_verdict(0, "Projected", "NAD_1983_StatePlane", SP_FEET, 2237)
    ok(v == REFUSE, "factoryCode 0 is refused even when the SR has a plausible name")

    v, r = sr_verdict(None, None, None, None, None)
    ok(v == REFUSE, "all-None description is refused, never accepted by default")

    # --- sr_verdict: defined SRs that are fine ------------------------------- #
    v, r = sr_verdict(2237, "Projected", "NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet",
                      SP_FEET, 2237)
    ok(v == ACCEPT, "defined projected SR matching the target is accepted")
    ok(any("matches the target" in x for x in r), "the match is stated")
    ok(not any("differs" in x for x in r), "no spurious reprojection notice on a match")

    v, r = sr_verdict(6441, "Projected", "NAD_1983_2011_StatePlane_Florida_East",
                      SP_FEET, 2237)
    ok(v == ACCEPT, "defined projected SR differing from the target is accepted")
    ok(any("reprojection" in x for x in r), "the difference is called a reprojection")
    ok(any("not a corruption" in x for x in r), "a differing SR is not treated as corrupt")

    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", DEG, 2237)
    ok(v == ACCEPT, "geographic SR with a degree extent is accepted")
    ok(any("consistent with its" in x for x in r), "the consistency check is reported")

    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", WORLD_DEG, 4326)
    ok(v == ACCEPT, "the exact degree bounds are inside the box, not outside")

    v, r = sr_verdict(2237, "Projected", "StatePlane_Feet", SP_FEET, 0)
    ok(v == ACCEPT, "a target with no SR of its own still accepts a sane source")
    ok(any("no spatial reference of its own" in x for x in r), "the SR-less target is noted")

    v, r = sr_verdict(2237, "Projected", "StatePlane_Feet", None, 2237)
    ok(v == ACCEPT, "a missing extent does not by itself refuse")
    ok(any("sanity check skipped" in x for x in r), "the skipped check is disclosed")

    # --- sr_verdict: the wrong-.prj cases ------------------------------------ #
    v, r = sr_verdict(2237, "Projected", "NAD_1983_StatePlane_Florida_West", DEG, 2237)
    ok(v == REFUSE, "projected SR with a degree extent is refused")
    ok(any("PROJECTED" in x for x in r), "the projected/degree refusal names the SR type")
    ok(any("latitude/longitude" in x for x in r), "the evidence is spelled out")

    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", SP_FEET, 2237)
    ok(v == REFUSE, "geographic SR with a projected extent is refused")
    ok(any("GEOGRAPHIC" in x for x in r), "the geographic/projected refusal names the SR type")
    ok(any("outside degree bounds" in x for x in r), "the evidence is spelled out")

    v, _ = sr_verdict(4326, "Geographic", "GCS_WGS_1984", (-82.5, 29.0, -82.0, 1500000.0),
                      2237)
    ok(v == REFUSE, "one corner outside degree bounds is enough to refuse")

    v, _ = sr_verdict(2237, "Projected", "StatePlane_Feet", (0.0, 0.0, 0.0, 0.0), 2237)
    ok(v == REFUSE, "a degenerate origin extent under a projected SR is refused")

    # --- sr_verdict: the --assume-sr override -------------------------------- #
    v, r = sr_verdict(0, "", "Unknown", DEG, 2237, assume_sr=4326)
    ok(v == ACCEPT, "--assume-sr overrides the undefined refusal")
    ok(any("OVERRIDE ACCEPTED" in x for x in r), "the override is announced")
    ok(any("4326" in x for x in r), "the assumed WKID is recorded in the reasons")
    ok(any("will reproject" in x for x in r), "the assumed-vs-target reprojection is stated")

    v, r = sr_verdict(0, "", "Unknown", DEG, 2237, assume_sr=2237)
    ok(v == ACCEPT, "--assume-sr matching the target is accepted")
    ok(any("no reprojection" in x for x in r), "a matching assumption skips reprojection")

    v, _ = sr_verdict(0, "", "Unknown", DEG, 2237, assume_sr=0)
    ok(v == REFUSE, "--assume-sr 0 is not an override, it is the undefined value")

    v, _ = sr_verdict(2237, "Projected", "StatePlane_Feet", DEG, 2237, assume_sr=4326)
    ok(v == REFUSE, "--assume-sr does not excuse a defined-but-wrong .prj")

    ok(extent_in_degree_range(None) is None, "no extent yields no range answer")
    ok(extent_in_degree_range(DEG) is True, "degree extent is in range")
    ok(extent_in_degree_range(SP_FEET) is False, "state plane extent is out of range")

    # --- field_diff ---------------------------------------------------------- #
    src = [("OBJECTID", "OID", 4), ("Shape", "Geometry", 0), ("PARCEL_ID", "String", 20),
           ("ACRES", "Double", 8), ("OWNER", "String", 60)]
    tgt = [("OBJECTID", "OID", 4), ("Shape", "Geometry", 0), ("parcel_id", "String", 20),
           ("ACRES", "Text", 8), ("ZONING", "String", 10)]
    mt, ms, tc = field_diff(src, tgt)
    ok([n for n, _ in mt] == ["OWNER"], "only OWNER is missing in the target")
    ok([n for n, _ in ms] == ["ZONING"], "only ZONING is missing in the source")
    ok(tc == [("ACRES", "Double", "Text")], "ACRES is reported as a type conflict")
    ok(all(n.lower() != "objectid" for n, _ in mt + ms), "OBJECTID is never diffed")
    ok(all(n.lower() != "shape" for n, _ in mt + ms), "Shape is never diffed")
    ok(not any(n.lower() == "parcel_id" for n, _ in mt),
       "PARCEL_ID matches parcel_id case-insensitively")
    ok(not any(n.lower() == "parcel_id" for n, _, _ in tc),
       "a case-only name difference is not a type conflict")

    mt, ms, tc = field_diff([], [])
    ok((mt, ms, tc) == ([], [], []), "two empty field lists diff to nothing")
    mt, ms, tc = field_diff(None, None)
    ok((mt, ms, tc) == ([], [], []), "None field lists diff to nothing")
    mt, ms, tc = field_diff(src, [])
    ok(len(mt) == 3 and ms == [] and tc == [], "an empty target is missing every real field")
    mt, ms, tc = field_diff([], tgt)
    ok(mt == [] and len(ms) == 3, "an empty source lacks every real target field")

    mt, _, _ = field_diff([{"name": "NOTES", "type": "String"}], [])
    ok(mt == [("NOTES", "String")], "dict-shaped fields are accepted")

    class _F(object):
        def __init__(self, name, type_):
            self.name, self.type = name, type_

    mt, _, _ = field_diff([_F("NOTES", "String")], [])
    ok(mt == [("NOTES", "String")], "arcpy-style Field objects are accepted")

    _, _, tc = field_diff([("A", "double")], [("a", "DOUBLE")])
    ok(tc == [], "type comparison is case-insensitive")
    mt, _, _ = field_diff([("GlobalID", "GlobalID"), ("Shape_Area", "Double")], [])
    ok(mt == [], "GlobalID and Shape_Area are geodatabase-managed, never diffed")

    # --- tracking_field_names ------------------------------------------------ #
    up = tracking_field_names(["OBJECTID", "Shape", "CREATION_USER", "CREATION_DATE",
                               "MODIFY_USER", "MODIFY_DATE"])
    ok(up == TRACKING_UPPER, "an existing CREATION_USER set picks the upper style")

    lo = tracking_field_names(["OBJECTID", "Shape", "created_user", "created_date",
                               "last_edited_user", "last_edited_date"])
    ok(lo == TRACKING_LOWER, "an existing created_user set picks the lower style")

    ok(tracking_field_names([]) == TRACKING_LOWER,
       "with no evidence the Esri default names are used")
    ok(tracking_field_names(["OBJECTID", "Shape", "PARCEL_ID"]) == TRACKING_LOWER,
       "unrelated fields do not change the default")

    partial = tracking_field_names(["MODIFY_USER"])
    ok(partial == TRACKING_UPPER, "a single upper-style survivor is enough evidence")

    cased = tracking_field_names(["Creation_User", "Creation_Date", "Modify_User"])
    ok(cased[0] == "Creation_User", "the existing casing is preserved, not normalised")
    ok(cased[3] == "MODIFY_DATE", "a field absent from the dataset falls back to the style name")
    ok(len(cased) == 4, "exactly four tracking names are returned")

    class _NF(object):
        def __init__(self, name):
            self.name, self.type = name, "String"

    ok(tracking_field_names([_NF("CREATION_USER"), _NF("MODIFY_DATE")]) == TRACKING_UPPER,
       "Field objects work for tracking detection too")

    # --- argument parsing ---------------------------------------------------- #
    p = build_parser()

    ns = p.parse_args(["import", "--source", "a.shp", "--workspace", "w.gdb"])
    ok(ns.apply is False, "--apply is off by default")
    ok(ns.add_missing is False, "--add-missing is off by default")
    ok(ns.check_write is False, "--check-write is off by default")
    ok(ns.assume_sr is None, "--assume-sr is unset by default")
    ok(validate_args(ns) == [], "a minimal valid import parses clean")

    ns = p.parse_args(["import", "--source", "a.shp", "--workspace", "w.gdb", "--apply"])
    ok(ns.apply is True, "--apply turns on when asked")

    ok(validate_args(p.parse_args(["import", "--workspace", "w.gdb"])),
       "import without --source is rejected")
    ok(validate_args(p.parse_args(["import", "--source", "a.shp"])),
       "import without --workspace is rejected")
    ok(validate_args(p.parse_args(["chores", "--workspace", "w.gdb"])),
       "chores without --target is rejected")
    ok(validate_args(p.parse_args(["diff", "--source", "a.shp", "--workspace", "w.gdb"])),
       "diff without --target is rejected")

    errs = validate_args(p.parse_args(
        ["import", "--source", "a.shp", "--workspace", "w.gdb", "--add-missing"]))
    ok(any("--add-missing" in e for e in errs), "--add-missing on import is rejected")

    errs = validate_args(p.parse_args(
        ["chores", "--workspace", "w.gdb", "--target", "T", "--assume-sr", "2237"]))
    ok(any("--assume-sr" in e for e in errs), "--assume-sr on chores is rejected")

    errs = validate_args(p.parse_args(
        ["import", "--source", "a.shp", "--workspace", "w.gdb",
         "--assume-sr", "-1"]))
    ok(any("positive WKID" in e for e in errs), "a non-positive --assume-sr is rejected")

    errs = validate_args(p.parse_args(
        ["import", "--source", "a.shp", "--workspace", "w.gdb",
         "--check-write", "--apply"]))
    ok(any("--check-write" in e for e in errs), "--check-write with --apply is rejected")

    ok(validate_args(p.parse_args(
        ["diff", "--source", "a.shp", "--workspace", "w.gdb", "--target", "T",
         "--add-missing"])) == [], "--add-missing on diff is allowed")

    ok(p.parse_args(["--self-test"]).command is None,
       "--self-test needs no subcommand")

    ok(len([a for a in p._actions if a.option_strings and a.dest != "help"]) <= 10,
       "the tool exposes at most ten flags")

    # --- the write paths, driven against a stub arcpy ------------------------ #
    # Without this block the --apply gate and the post-load row-count check have
    # no coverage at all: both live in functions that need a geodatabase.
    import io
    from contextlib import redirect_stdout

    IMPORT = ["import", "--source", _StubArcpy.SRC, "--workspace", _StubArcpy.WS,
              "--dataset", _StubArcpy.DS]
    CHORES = ["chores", "--workspace", _StubArcpy.WS, "--target", "out"]
    DIFF = ["diff", "--source", _StubArcpy.SRC, "--workspace", _StubArcpy.WS,
            "--target", "out", "--add-missing"]

    def run(fn, argv, **stub_kw):
        global _ARCPY_OVERRIDE
        stub = _StubArcpy(**stub_kw)
        _ARCPY_OVERRIDE = stub
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = fn(p.parse_args(argv))
        finally:
            _ARCPY_OVERRIDE = None
        return rc, buf.getvalue(), stub

    rc, out, stub = run(cmd_import, IMPORT)
    ok(rc == 0, "a sane import dry-runs cleanly")
    ok(stub.writes() == [], "import without --apply performs NO write call")
    ok("DRY RUN" in out, "the dry run says so")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"])
    ok(rc == 0, "a sane import with --apply succeeds")
    ok("FeatureClassToFeatureClass" in stub.writes(), "--apply actually loads")
    ok("rows: 0 before + 3 loaded = 3 expected, 3 found" in out, "the row maths is reported")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], landed=2)
    ok(rc == 1, "a short row count after the load FAILS the import")
    ok("row count mismatch" in out, "the mismatch is named, not silently accepted")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], landed=4)
    ok(rc == 1, "an over-count after the load fails too, not just an under-count")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], dest_exists=True, dest_before=5)
    ok(rc == 0 and "Append" in stub.writes(), "an existing target is appended to")
    ok("5 before + 3 loaded = 8 expected, 8 found" in out, "append counts include the rows already there")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], dest_exists=True, dest_before=5,
                        landed=6)
    ok(rc == 1, "a partial append is caught by the same row-count check")

    rc, out, stub = run(cmd_import, IMPORT, src_wkid=0, src_name="Unknown")
    ok(rc == 2, "an undefined source is refused, not dry-run-accepted")
    ok(stub.writes() == [], "a refused import writes nothing")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_wkid=0, src_name="Unknown")
    ok(rc == 2, "--apply does not talk the refusal gate out of it")
    ok(stub.writes() == [], "a refused import writes nothing even with --apply")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply", "--assume-sr", "4326"],
                        src_wkid=0, src_name="Unknown")
    ok(rc == 0 and "DefineProjection" in stub.writes(),
       "--assume-sr defines the SR on a scratch copy before loading")
    ok("source untouched" in out, "the source is not modified in place")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], target_wkid=2237)
    ok(rc == 0 and "Project" in stub.writes(), "a differing target WKID triggers a reprojection")

    rc, out, stub = run(cmd_chores, CHORES)
    ok(rc == 0 and stub.writes() == [], "chores without --apply performs NO write call")
    ok("DRY RUN" in out and "ERROR 001332" in out, "the dry run still prints the versioning note")

    rc, out, stub = run(cmd_chores, CHORES + ["--apply"])
    ok("AddGlobalIDs" in stub.writes() and "EnableEditorTracking" in stub.writes(),
       "chores --apply does both chores")

    rc, out, stub = run(cmd_chores, CHORES + ["--apply"], has_gid=True, tracking=True)
    ok(stub.writes() == [], "chores skips work that is already done")

    rc, out, stub = run(cmd_diff, DIFF)
    ok(rc == 0 and stub.writes() == [], "diff --add-missing without --apply adds NO field")
    ok("DRY RUN" in out and "+ NOTES" in out, "the dry run names the field it would add")

    rc, out, stub = run(cmd_diff, DIFF + ["--apply"])
    ok(rc == 0 and "AddField" in stub.writes(), "diff --add-missing --apply adds the field")
    ok("verified: 1 field(s) added" in out, "the addition is verified by re-reading the fields")

    rc, out, stub = run(cmd_diff, DIFF + ["--apply"], add_field_works=False)
    ok(rc == 1 and "still missing" in out, "an AddField that did not take is reported as failure")

    rc, out, stub = run(cmd_diff, ["diff", "--source", _StubArcpy.SRC, "--workspace",
                                   _StubArcpy.WS, "--target", "out"])
    ok(rc == 0 and stub.writes() == [], "diff without --add-missing never writes")

    print("self-test: {0} assertions passed, 0 failed (offline, no arcpy).".format(checks[0]))
    return 0


# --------------------------------------------------------------------------- #
def main(argv=None):
    p = build_parser()
    ns = p.parse_args(argv)

    if ns.self_test:
        return self_test()
    if not ns.command:
        p.print_help()
        return 1

    errors = validate_args(ns)
    if errors:
        for e in errors:
            print("error: {0}".format(e), file=sys.stderr)
        return 1

    if ns.check_write:
        return 0 if check_write(ns.workspace) else 1

    return {"import": cmd_import, "chores": cmd_chores, "diff": cmd_diff}[ns.command](ns)


if __name__ == "__main__":
    sys.exit(main())
