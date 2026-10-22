#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fcload - load a dataset into a geodatabase, refusing the imports that corrupt silently.

WHAT IS NEW HERE (one claim only)
---------------------------------
Every step this tool performs is arcpy: FeatureClassToFeatureClass, Append, AddField,
ListFields, AddGlobalIDs, EnableEditorTracking, Describe. The novelty is the refusal
gate in front of them. Version 1.2.0 adds a warning when an import drops attachments,
and `loadscar`, which runs the same rules on a shapefile somebody delivered.

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
1. UNDEFINED source spatial reference (factoryCode 0 or name "Unknown", or for
   `check` a missing or empty .prj, or a .prj without the structure ArcGIS Pro needs
   to read it, which Pro reads as "Unknown"). Override for import: --assume-sr WKID.
   --assume-sr on a DEFINED source is refused: it would stamp one SR over another.
   An UNDEFINED target is refused too: a `check --target` shapefile, or for import the
   feature dataset or existing feature class the rows land in. There is no override.
2. Projected source spatial reference whose extent lies entirely inside degree bounds
   (|x| <= 180, |y| <= 90, plus 1e-9 for rounding). The .prj is wrong; the coordinates
   are lat/long.
3. Geographic source spatial reference whose extent falls outside degree bounds.
   The .prj is wrong; the coordinates are projected units.
4. Row count after the load does not match the row count expected. The load is
   reported as failed rather than silently accepted.
5. Anything that writes without --apply. Dry run is the default for every subcommand,
   and --check-write is a write: its probe creates and deletes a table.
6. A source that holds no features. Its extent says nothing about where data is.
7. `check` only: a .shp, .prj or .dbf header that is corrupt or truncated (a .prj
   whose WKT is not well formed, or not on one line, or lacks a node Pro needs, see
   parse_prj; a .dbf that repeats a field name in another case, whose record length is
   not its fields' total, or whose record count does not fit its file size), and a
   target whose own .prj contradicts its bounding box (refusals 2 and 3 applied to the
   target). Also a .shp, .prj or .dbf that is a symbolic or hard link. A .prj that is
   not WKT is refused without echoing its text, and a header error names bytes, not
   their values.

WARNINGS (fcload loads, and says what it saw)
---------------------------------------------
A. `import` of a source with attachments. With arcpy.env.maintainAttachments False the
   tools copy the features and drop every attachment at maxSeverity 0, with no warning
   (measured on Pro 3.6). fcload prints WARNING before the load when the setting is False,
   and after --apply it counts the attachment rows in the target and warns when they are
   fewer than the source's plus what the target already held. A warning does not change
   the exit code: a load that dropped attachments still exits 0.

LOADSCAR (the refusal rules run backwards on a delivered file)
--------------------------------------------------------------
`loadscar --source D.shp [--target FIELDS.txt]` reads a shapefile somebody delivered and
looks for what a write leaves behind when it eats data: no .prj, a .prj that
contradicts the extent, a .shx and .dbf that disagree about the number of features, a
text field with values that fill its 254 characters, and, given a text file of the
original's field names, names cut to 10 characters and fields that are gone. Losses that
leave no trace (the time of a date, a null written as 0, digits past the twelfth of a
Double) are named as notes. It is read-only and needs no arcpy. It exits 2 on a scar.

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
    python fcload.py check   --source S.shp [--target T.shp]          # no arcpy, read-only
    python fcload.py loadscar --source D.shp [--target FIELDS.txt]    # no arcpy, read-only
    python fcload.py import  --source S --workspace W [--dataset D] [--name N] [--apply]
    python fcload.py chores  --workspace W --target T [--apply]
    python fcload.py diff    --source S --workspace W --target T [--add-missing] [--apply]
    python fcload.py import|chores|diff ... --check-write --apply     # write probe, then stop

`check` reads a shapefile's .shp header, .prj and .dbf header with the standard
library and feeds the same verdict. It needs no arcpy, runs on Linux, and opens
files read-only. With --target T.shp it also compares the two .dbf field lists.
import, chores, diff and --check-write need arcpy. arcpy is imported only inside the
functions that touch a geodatabase. The decision logic - sr_verdict, field_diff,
tracking_field_names, validate_args and the three header parsers - is pure and is
exercised by --self-test on any Python 3.9+ with no Esri software installed.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import struct
import sys

__version__ = "1.2.0"

ACCEPT = "ACCEPT"
REFUSE = "REFUSE"

# Longitude/latitude bounds. A coordinate pair inside this box is almost certainly
# degrees; a pair outside it is almost certainly projected units.
DEGREE_X = 180.0
DEGREE_Y = 90.0
# Floating-point slack on those bounds. A full-world extent converted from Web Mercator
# reaches 180.00000000000003, one ULP past 180; 1e-9 degrees is about 0.1 mm on the ground.
DEGREE_SLACK = 1e-9

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
    "\n(--self-test and `check` need no arcpy and run on any Python 3.9+.)"
)

# Every arcpy call that changes something. --self-test asserts none of these fire
# on a dry run, which is the only way the --apply gate can be tested without Esri.
WRITE_CALLS = frozenset((
    "CopyFeatures", "DefineProjection", "Project", "Append", "Delete",
    "FeatureClassToFeatureClass", "AddGlobalIDs", "EnableEditorTracking",
    "AddField", "CreateTable",
))

PROBE_NAME = "fcload_write_probe"
CHECK_WRITE_REFUSAL = (
    "--check-write creates and then deletes a scratch table (%s) in the workspace. "
    "That is a write, so it runs only with --apply. Nothing was written." % PROBE_NAME
)

# ESRI Shapefile Technical Description (1998): the 100-byte main-file header.
SHP_HEADER = 100
SHP_FILE_CODE = 9994      # bytes 0-3, big-endian
SHP_VERSION = 1000        # bytes 28-31, little-endian
SHAPE_TYPES = {
    0: "Null", 1: "Point", 3: "Polyline", 5: "Polygon", 8: "MultiPoint",
    11: "PointZ", 13: "PolylineZ", 15: "PolygonZ", 18: "MultiPointZ",
    21: "PointM", 23: "PolylineM", 25: "PolygonM", 28: "MultiPointM", 31: "MultiPatch",
}
DBF_TYPES = {"C": "String", "D": "Date", "L": "Logical"}
SHX_HEADER = 100
SHX_RECORD = 8            # an index record: offset and content length, 4 bytes each
NAME_CAP = 10             # a shapefile field name holds 10 characters
TEXT_CAP = 254            # a shapefile text field holds 254 characters
UNKNOWN_ROWS = -1         # attachments exist but their table could not be found
_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")

# The leading keyword of a .prj, and one WKT token: a bracket or comma, a quoted string,
# or a bare word (a keyword, a number, or an AXIS direction such as NORTH).
# Whitespace is these five ASCII characters only, never Python's Unicode \s. Measured on
# ArcGIS Pro 3.6: a space, tab, VT, FF or lone CR between tokens is whitespace, but an NBSP,
# U+2003, U+3000, U+2028, U+0085, 0x1C or 0x1F there makes the whole .prj 'Unknown'.
_WS = " \t\r\x0b\x0c"
_PRJ_KEYWORD = re.compile(r'[%s]*([A-Za-z_][A-Za-z0-9_]{0,15})[%s]*\[' % (_WS, _WS))
_WKT_TOKEN = re.compile(r'[%s]*(?:([\[\],])|"([^"]*)"|([^%s\[\],"]+))' % (_WS, _WS))
_WKT_OPEN = re.compile(r'[%s]*\[' % _WS)
# A WKT number is ASCII. float() alone also takes '0.017_45', fullwidth and Arabic-Indic
# digits, 'nan' and 'Infinity', and Pro 3.6 read a .prj with any of the first four as
# 'Unknown'. A bare word that does not match stays a word, so the node lacks its number.
_WKT_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
# Measured on Pro 3.6: a PROJCS whose GEOGCS UNIT is Foot_US, Meter or Radian is 'Unknown';
# Degree, Grad, Gon and Second are read. Pro writes Degree or Grad in all 7,455 .prj files.
_ANGULAR_UNITS = ("degree", "grad", "gon", "second")
# The nodes fcload verifies inside each WKT1 node it reads. Any other node is refused.
_PRJ_ALLOWED = {
    "GEOGCS": ("DATUM", "PRIMEM", "UNIT", "LINUNIT", "AUTHORITY", "AXIS"),
    "DATUM": ("SPHEROID", "TOWGS84", "AUTHORITY"),
    "PROJCS": ("GEOGCS", "PROJECTION", "PARAMETER", "UNIT", "LINUNIT", "AUTHORITY", "AXIS"),
    "VERTCS": ("VDATUM", "DATUM", "PARAMETER", "UNIT", "AUTHORITY", "AXIS"),
}
# Measured on ArcGIS Pro 3.6: every PROJECTION in the .prj that Pro writes for each of
# its 6,316 projected systems (arcpy.ListSpatialReferences), grouped by the PARAMETERs
# Pro writes for all of them. exportToString gives WKT2 for about 1,200 systems, but the
# .prj Pro writes for them is WKT1, so the .prj files are the source of this table.
# False_Easting and False_Northing are written for every one and are left out below.
# Pro reads a .prj with an unknown PROJECTION or PARAMETER name, or with no
# Central_Meridian on a Transverse_Mercator, as UNDEFINED.
_PRJ_PROJECTIONS = (
    "Option = Fuller;"
    "Central_Meridian = Aitoff Behrmann Compact_Miller Craster_Parabolic Eckert_I Eckert_II"
    " Eckert_III Eckert_IV Eckert_V Eckert_VI Equal_Earth Flat_Polar_Quartic"
    " Gall_Stereographic Hammer_Aitoff Miller_Cylindrical Mollweide Natural_Earth"
    " Natural_Earth_II Patterson Plate_Carree Quartic_Authalic Robinson Sinusoidal Times"
    " Tobler_Cylindrical_I Tobler_Cylindrical_II Van_der_Grinten_I Wagner_V;"
    "Central_Meridian Option = Cube Goode_Homolosine;"
    "Central_Meridian Central_Parallel = Loximuthal;"
    "Height Longitude_Of_Center Option = Geostationary_Satellite;"
    "Central_Meridian Latitude_Of_Origin = Azimuthal_Equidistant Cassini"
    " Lambert_Azimuthal_Equal_Area Polyconic Wagner_IV Wagner_VII;"
    "Central_Meridian Standard_Parallel_1 = Bonne Cylindrical_Equal_Area"
    " Equidistant_Cylindrical Equidistant_Cylindrical_Ellipsoidal Mercator"
    " Stereographic_North_Pole Stereographic_South_Pole Winkel_I Winkel_II Winkel_Tripel;"
    "Latitude_Of_Center Longitude_Of_Center = Gnomonic Orthographic;"
    "Latitude_Of_Origin Longitude_Of_Origin = New_Zealand_Map_Grid;"
    "Longitude_Of_Origin Standard_Parallel_1 = Polar_Stereographic_Variant_B;"
    "Height Latitude_Of_Center Longitude_Of_Center = IGAC_Plano_Cartesiano"
    " Vertical_Near_Side_Perspective;"
    "Central_Meridian Latitude_Of_Origin Scale_Factor = Double_Stereographic Gauss_Kruger"
    " Lambert_Conformal_Conic_1SP Stereographic Transverse_Mercator"
    " Transverse_Mercator_Complex;"
    "Latitude_Of_Origin Longitude_Of_Origin Scale_Factor = Polar_Stereographic_Variant_A;"
    "Central_Meridian Latitude_Of_Origin XY_Plane_Rotation = Berghaus_Star;"
    "Central_Meridian Latitude_Of_Origin Option Scale_Factor = Peirce_Quincuncial;"
    "Central_Meridian Latitude_Of_Origin Standard_Parallel_1 = Lambert_Conformal_Conic;"
    "Auxiliary_Sphere_Type Central_Meridian Standard_Parallel_1 = Mercator_Auxiliary_Sphere;"
    "Azimuth Latitude_Of_Center Longitude_Of_Center Scale_Factor ="
    " Hotine_Oblique_Mercator_Azimuth_Center Hotine_Oblique_Mercator_Azimuth_Natural_Origin"
    " Laborde_Oblique_Mercator Local;"
    "Central_Meridian Latitude_Of_Origin Standard_Parallel_1 Standard_Parallel_2 ="
    " Albers Equidistant_Conic;"
    "Azimuth Latitude_Of_Center Longitude_Of_Center Scale_Factor XY_Plane_Rotation ="
    " Adams_Square_II Rectified_Skew_Orthomorphic_Natural_Origin;"
    "Latitude_Of_1st_Point Latitude_Of_2nd_Point Longitude_Of_1st_Point"
    " Longitude_Of_2nd_Point = Two_Point_Equidistant;"
    "Azimuth Latitude_Of_Center Longitude_Of_Center Pseudo_Standard_Parallel_1 Scale_Factor"
    " XY_Plane_Rotation X_Scale Y_Scale = Krovak;"
    "Latitude_Of_1st_Point Latitude_Of_2nd_Point Latitude_Of_Center Longitude_Of_1st_Point"
    " Longitude_Of_2nd_Point Scale_Factor = Hotine_Oblique_Mercator_Two_Point_Natural_Origin"
)
# {lower-case projection: (its name, the PARAMETERs it needs)}, and every PARAMETER name.
_PRJ_REQUIRED = dict(
    (name.lower(), (name, tuple(("False_Easting False_Northing " + params).split())))
    for params, names in (group.split("=") for group in _PRJ_PROJECTIONS.split(";"))
    for name in names.split())
_PRJ_PARAMETERS = frozenset(p.lower() for _, req in _PRJ_REQUIRED.values() for p in req)

# Test seam. None means "import the real arcpy"; --self-test swaps in a stub and
# puts it back. Nothing else in the tool ever writes to it.
_ARCPY_OVERRIDE = None


# --------------------------------------------------------------------------- #
# arcpy access - the ONLY import site, deliberately inside a function
# --------------------------------------------------------------------------- #
def _arcpy():
    """Import arcpy lazily so the pure core, `check` and --self-test never need it."""
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
    """True when every corner of `extent` fits inside longitude/latitude bounds.

    None when there is no extent to judge: none given, or a non-finite one. ArcGIS Pro
    3.6 describes an empty shapefile's extent as (nan, nan, nan, nan), which says
    nothing about units, so it must not be read as "outside degree bounds".
    """
    if not extent:
        return None
    xmin, ymin, xmax, ymax = (float(v) for v in extent)
    if not all(map(math.isfinite, (xmin, ymin, xmax, ymax))):
        return None
    return (
        abs(xmin) <= DEGREE_X + DEGREE_SLACK
        and abs(xmax) <= DEGREE_X + DEGREE_SLACK
        and abs(ymin) <= DEGREE_Y + DEGREE_SLACK
        and abs(ymax) <= DEGREE_Y + DEGREE_SLACK
    )


def _wkid_text(code):
    """'WKID 2237', or a plain statement that the source names no WKID."""
    return "WKID %d" % code if code else "no WKID stated"


def sr_undefined(code, name, prj_defined):
    """True when a spatial reference is UNDEFINED. One rule for a source and a target.

    factoryCode 0 is undefined unless a .prj names a WKT root (prj_defined); a name such
    as "Unknown" is undefined whatever the code says.
    """
    return ((int(code or 0) == 0 and not prj_defined)
            or (name or "").strip().lower() in UNDEFINED_SR_NAMES)


def sr_verdict(sr_factory_code, sr_type, sr_name, extent, target_wkid, assume_sr=None,
               prj_defined=False):
    """Decide whether a source may be loaded, from plain data only.

    sr_factory_code: Describe(...).spatialReference.factoryCode (0 == undefined)
    sr_type        : "Geographic" | "Projected" | "" (unknown)
    sr_name        : .name, where "Unknown" also means undefined
    extent         : (xmin, ymin, xmax, ymax) in source units, or None
    target_wkid    : WKID of the destination, 0 when it has none, None when there is
                     no target WKID to compare with (`check` without one)
    assume_sr      : explicit operator override for an undefined source
    prj_defined    : True when a .prj holds a named WKT root but no WKID. Then a code
                     of 0 means "not stated", not "undefined". arcpy callers leave it
                     False, because Describe reports 0 only for an undefined SR.

    Returns (ACCEPT|REFUSE, [reason strings]).
    """
    reasons = []
    code = int(sr_factory_code or 0)
    name = (sr_name or "").strip()
    kind = (sr_type or "").strip().lower()
    target = int(target_wkid or 0)

    undefined = sr_undefined(code, name, prj_defined)

    if assume_sr and not undefined:
        # --apply defines the assumed SR over the source's coordinates. On a DEFINED
        # source that stamps degrees as feet, or feet as degrees: the headline disaster.
        return REFUSE, [
            "source spatial reference is DEFINED (%s, %r); --assume-sr applies only to "
            "an undefined source" % (_wkid_text(code), name),
            "--assume-sr %d would be stamped over coordinates that are already in "
            "another system, silently relocating every feature" % int(assume_sr),
            "run again without --assume-sr; a wrong .prj is fixed with Define Projection "
            "in ArcGIS Pro, not here",
        ]

    if undefined:
        if assume_sr:
            assumed = int(assume_sr)
            reasons.append(
                "source spatial reference is UNDEFINED (factoryCode=%d, name=%r); "
                "OVERRIDE ACCEPTED: --assume-sr %d supplied by the operator and "
                "recorded here" % (code, name or "Unknown", assumed)
            )
            # ponytail: the assumed SR is trusted as stated. Sanity-checking the extent
            # against it would need an EPSG table; --assume-sr is already an explicit,
            # logged human decision. Add the table if the override starts being abused.
            if not target:
                reasons.append(
                    "target has no spatial reference of its own: loading as-is, "
                    "no reprojection"
                )
            elif assumed != target:
                reasons.append(
                    "assumed WKID %d differs from target WKID %d: will reproject "
                    "before loading" % (assumed, target)
                )
            else:
                reasons.append(
                    "assumed WKID %d matches the target: no reprojection" % assumed
                )
            return ACCEPT, reasons

        reasons.append(
            "source spatial reference is UNDEFINED (factoryCode=%d, name=%r)"
            % (code, name or "Unknown")
        )
        reasons.append(
            "arcpy would import this at maxSeverity 0 with no warning and stamp the raw "
            "coordinates with the TARGET spatial reference, silently relocating every "
            "feature"
        )
        reasons.append(
            "if you know what the coordinates really are, run `fcload import` with "
            "--assume-sr WKID"
        )
        return REFUSE, reasons

    in_degrees = extent_in_degree_range(extent)

    if in_degrees is None:
        reasons.append("extent unavailable: coordinate-range sanity check skipped")
    elif kind == "projected" and in_degrees:
        reasons.append(
            "spatial reference %r (%s) is PROJECTED but the whole extent "
            "%s fits inside degree bounds (|x|<=%.0f, |y|<=%.0f)"
            % (name, _wkid_text(code), tuple(extent), DEGREE_X, DEGREE_Y)
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
            "spatial reference %r (%s) is GEOGRAPHIC but the extent %s falls "
            "outside degree bounds (|x|<=%.0f, |y|<=%.0f)"
            % (name, _wkid_text(code), tuple(extent), DEGREE_X, DEGREE_Y)
        )
        reasons.append(
            "the coordinates are in projected units and the .prj is wrong; any "
            "reprojection from here produces garbage"
        )
        return REFUSE, reasons
    else:
        reasons.append(
            "spatial reference %r (%s, %s) is consistent with its "
            "extent" % (name, _wkid_text(code), sr_type or "type unknown")
        )

    if target_wkid is None:
        reasons.append("no target WKID to compare with: reprojection not assessed")
    elif not target:
        reasons.append("target has no spatial reference of its own: loading as-is")
    elif not code:
        reasons.append(
            "the source .prj states no WKID, so the file cannot say whether loading "
            "into target WKID %d reprojects" % target
        )
    elif code == target:
        reasons.append("source WKID %d matches the target: no reprojection" % code)
    else:
        reasons.append(
            "source WKID %d differs from target WKID %d: this is a reprojection, "
            "not a corruption" % (code, target)
        )

    return ACCEPT, reasons


def target_verdict(label, code, sr_type, name, extent, prj_defined=False):
    """Refuse a destination whose own spatial reference cannot be trusted. Pure.

    An UNDEFINED target is the headline disaster in reverse. Measured on ArcGIS Pro 3.6:
    Append of WGS84 points into a shapefile with no .prj returned maxSeverity 0 with no
    message and stored the raw degrees beside the target's rows in feet. A defined target
    whose extent contradicts its SR (refusals 2 and 3) has a wrong .prj, and a
    reprojection into it is garbage. extent None skips that second test.
    Returns (ACCEPT|REFUSE, [reasons]); every reason names `label`.
    """
    if sr_undefined(code, name, prj_defined):
        return REFUSE, [
            "%s spatial reference is UNDEFINED (factoryCode=%d, name=%r): its coordinates "
            "are in a system nobody recorded" % (label, int(code or 0),
                                                 (name or "").strip() or "Unknown"),
            "arcpy would load the source beside them unconverted, at maxSeverity 0 with "
            "no warning; --assume-sr describes the source and cannot fix the target",
            "define the %s spatial reference first, then run fcload again" % label,
        ]
    verdict, reasons = sr_verdict(code, sr_type, name, extent, None, prj_defined=prj_defined)
    if verdict == REFUSE:
        return REFUSE, ["%s: %s" % (label, r) for r in reasons]
    return ACCEPT, []


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


def parse_shp_header(head, size):
    """Read the 100-byte .shp main-file header. Pure: the header bytes and file size in.

    Returns (shape_type, (xmin, ymin, xmax, ymax), empty). Raises ValueError for any
    header that is not well formed, so a corrupt file is refused, never read as an
    extent. The bounding box of an empty file is not checked: it has no meaning.
    """
    if len(head) < SHP_HEADER:
        raise ValueError("the .shp is %d bytes; its header alone is %d"
                         % (len(head), SHP_HEADER))
    # The messages below name the bytes, never their values: a file that is not a
    # shapefile, such as a secret under a .shp name, must not reach the log.
    if struct.unpack(">i", head[0:4])[0] != SHP_FILE_CODE:
        raise ValueError("the .shp file code at byte 0 is not %d (big-endian): "
                         "this is not a shapefile" % SHP_FILE_CODE)
    length = struct.unpack(">i", head[24:28])[0] * 2
    version, shape_type = struct.unpack("<2i", head[28:36])
    if version != SHP_VERSION:
        raise ValueError("the .shp version at byte 28 is not %d (little-endian)" % SHP_VERSION)
    if shape_type not in SHAPE_TYPES:
        raise ValueError("the .shp shape type at byte 32 is one that no shapefile uses")
    if length < SHP_HEADER or length > size:
        raise ValueError("the .shp file length at byte 24 is under %d bytes or over the %d "
                         "the file holds: truncated or corrupt" % (SHP_HEADER, size))
    bbox = struct.unpack("<4d", head[36:68])
    empty = length == SHP_HEADER
    if not empty and not (all(map(math.isfinite, bbox))
                          and bbox[0] <= bbox[2] and bbox[1] <= bbox[3]):
        raise ValueError("the .shp bounding box (bytes 36-67) is not a real extent")
    return shape_type, bbox, empty


def _wkt_nodes(text):
    """Parse WKT1 text into its top-level nodes. Pure, and never echoes the text.

    A node is (KEYWORD, [items]). An item is a node, a quoted str, a float, or
    (WORD, None) for a bare word that is not a number. Raises ValueError unless the
    brackets, quotes and commas are well formed. A stack, not recursion, so a .prj of
    60,000 open brackets is refused, not a RecursionError.
    """
    top = []
    stack = [top]
    want_item = True
    pos = 0
    while pos < len(text):
        m = _WKT_TOKEN.match(text, pos)
        if not m:
            break                               # an unclosed quote
        pos = m.end()
        punct, quoted, word = m.groups()
        opening = _WKT_OPEN.match(text, pos)
        if want_item and word is not None and opening:
            node = (word.upper(), [])
            stack[-1].append(node)
            stack.append(node[1])
            pos = opening.end()                 # the "[" is part of the node
        elif want_item and quoted is not None:
            stack[-1].append(quoted)
            want_item = False
        elif want_item and word is not None:
            stack[-1].append(float(word) if _WKT_NUMBER.match(word) else (word, None))
            want_item = False
        elif not want_item and punct == ",":
            want_item = True
        elif not want_item and punct == "]" and len(stack) > 1:
            stack.pop()
        else:
            break                               # a token out of place
    else:
        if not want_item and len(stack) == 1:
            return top
    raise ValueError("the .prj WKT is truncated or corrupt: its brackets, quotes or "
                     "commas are not well formed")


def _prj_fail(what):
    """The refusal for a .prj whose structure fcload cannot verify. Names no file text."""
    return ValueError(
        "the .prj is not a spatial reference fcload can verify (%s). ArcGIS Pro 3.6 reads "
        "a .prj it cannot parse as UNDEFINED, 'Unknown' with factoryCode 0" % what)


def _is_node(item):
    return isinstance(item, tuple) and isinstance(item[1], list)


def _prj_nodes(node, key):
    return [c for c in node[1] if _is_node(c) and c[0] == key]


def _prj_one(node, key):
    hits = _prj_nodes(node, key)
    if len(hits) != 1:
        raise _prj_fail("%s has %s %s" % (node[0], "more than one" if hits else "no", key))
    return hits[0]


def _prj_named(node, count=0):
    """(name, [first `count` numbers]) of a node. Refuses an empty name, a missing or
    non-finite number, or a child node fcload does not verify."""
    items = node[1]
    name = items[0] if isinstance(items[0], str) else ""
    nums = [v for v in items[1:1 + count] if isinstance(v, float) and math.isfinite(v)]
    if not name.strip() or len(nums) < count:
        raise _prj_fail("%s needs a name%s" % (
            node[0], " and %d number%s" % (count, "s" if count > 1 else "") if count else ""))
    for c in items:
        if _is_node(c) and c[0] not in _PRJ_ALLOWED.get(node[0], ("AUTHORITY",)):
            raise _prj_fail("%s holds a node that fcload does not verify" % node[0])
    return name, nums


def _prj_unit(node, key="UNIT"):
    """The name of the one UNIT (or LINUNIT) of a node. Refuses a factor that is not positive."""
    name, (factor,) = _prj_named(_prj_one(node, key), 1)
    if factor <= 0:
        raise _prj_fail("the %s %s factor is not positive" % (node[0], key))
    return name


def _prj_linunit(node):
    """Pro writes a LINUNIT into a 3D system. Measured: Pro reads one that is repeated,
    unnamed, or without a positive factor as UNDEFINED, so it is checked like UNIT."""
    if _prj_nodes(node, "LINUNIT"):
        _prj_unit(node, "LINUNIT")


def _prj_datum(node):
    """Refuse a node without one named DATUM holding a SPHEROID with sane numbers."""
    datum = _prj_one(node, "DATUM")
    _prj_named(datum)
    a, invf = _prj_named(_prj_one(datum, "SPHEROID"), 2)[1]
    # Measured on Pro 3.6: an inverse flattening of 0 (a sphere), 1 or 1.0001 is read, and
    # 0.001, 0.5, 0.99 or 0.9999 is 'Unknown'. Below 1 the minor axis would be negative.
    if a <= 0 or not (invf == 0 or invf >= 1):
        raise _prj_fail("SPHEROID needs a positive semi-major axis and an inverse "
                        "flattening of 0 or at least 1")


def _prj_geogcs(g):
    """Refuse a GEOGCS without DATUM[SPHEROID], PRIMEM and an angular UNIT with a positive
    factor. Pro reads a lone GEOGCS in feet; fcload refuses it, as it is not in degrees."""
    name = _prj_named(g)[0]
    _prj_datum(g)
    if abs(_prj_named(_prj_one(g, "PRIMEM"), 1)[1][0]) > 180:
        raise _prj_fail("the PRIMEM longitude is outside -180 to 180")
    if _prj_unit(g).lower() not in _ANGULAR_UNITS:
        raise _prj_fail("the GEOGCS UNIT is not Degree, Grad, Gon or Second")
    _prj_linunit(g)
    return name


def _prj_projcs(p):
    """Refuse a PROJCS without a valid GEOGCS, a known PROJECTION with the PARAMETERs
    Pro writes for it, and a UNIT with a positive factor."""
    name = _prj_named(p)[0]
    geog = _prj_one(p, "GEOGCS")
    _prj_geogcs(geog)
    # Pro writes a PROJCS LINUNIT only over a 3D GEOGCS that has one too (WKID 9895).
    # Measured: a 2D StatePlane PROJCS with LINUNIT["Meter",1.0] added is 'Unknown'.
    if _prj_nodes(p, "LINUNIT") and not _prj_nodes(geog, "LINUNIT"):
        raise _prj_fail("a PROJCS LINUNIT needs a GEOGCS with a LINUNIT, as in a 3D system")
    known = _PRJ_REQUIRED.get(_prj_named(_prj_one(p, "PROJECTION"))[0].lower())
    if not known:
        raise _prj_fail("its PROJECTION is none of the %d that ArcGIS Pro 3.6 writes"
                        % len(_PRJ_REQUIRED))
    seen = set()
    for par in _prj_nodes(p, "PARAMETER"):
        pname, (value,) = _prj_named(par, 1)
        key = pname.lower()
        if key not in _PRJ_PARAMETERS or key in seen:
            raise _prj_fail("a PARAMETER is named twice, or with a name ArcGIS Pro 3.6 "
                            "never writes")
        if ("latitude" in key or "parallel" in key) and abs(value) > 90:
            raise _prj_fail("a latitude PARAMETER is outside -90 to 90")
        seen.add(key)
    missing = [r for r in known[1] if r.lower() not in seen]
    if missing:
        raise _prj_fail("%s needs PARAMETER %s" % (known[0], ", ".join(missing)))
    _prj_unit(p)
    _prj_linunit(p)
    return name


def _prj_vertcs(v):
    """Refuse a VERTCS without a name, a named VDATUM (or, for an ellipsoidal height, a
    DATUM[SPHEROID]) and a UNIT with a positive factor."""
    _prj_named(v)
    if _prj_nodes(v, "DATUM"):
        _prj_datum(v)
    else:
        _prj_named(_prj_one(v, "VDATUM"))
    names = [_prj_named(par, 1)[0].lower() for par in _prj_nodes(v, "PARAMETER")]
    if len(set(names)) < len(names) or set(names) - {"vertical_shift", "direction"}:
        # Measured: Pro reads a VERTCS PARAMETER["Bogus",0.0] as UNDEFINED, horizontal too.
        raise _prj_fail("a VERTCS PARAMETER is named twice, or is neither Vertical_Shift "
                        "nor Direction")
    _prj_unit(v)


def _prj_authorities(nodes):
    """Refuse an AUTHORITY that is not one name and one code, anywhere in the tree.

    Measured on Pro 3.6: AUTHORITY["EPSG"], AUTHORITY[4326], AUTHORITY["EPSG",""] or
    AUTHORITY["  ","4326"], on the root or inside a DATUM, SPHEROID, UNIT, PROJECTION or
    PARAMETER, and two AUTHORITY nodes in one node, each make the whole .prj 'Unknown'.
    Pro also read a few forms that are refused here, such as AUTHORITY[EPSG,4326].
    """
    stack = list(nodes)
    while stack:
        node = stack.pop()
        auths = _prj_nodes(node, "AUTHORITY")
        if len(auths) > 1:
            raise _prj_fail("%s has more than one AUTHORITY" % node[0])
        for auth in auths:
            org, code = auth[1] if len(auth[1]) == 2 else ("", "")
            if not (isinstance(org, str) and org.strip() and (
                    isinstance(code, float) or isinstance(code, str) and code.strip())):
                raise _prj_fail("an AUTHORITY in %s is not one name and one code" % node[0])
        stack.extend(c for c in node[1] if _is_node(c))


def parse_prj(text):
    """Read what sr_verdict needs from .prj text. Pure.

    Returns (wkid, sr_type, name, defined). An empty .prj is UNDEFINED, the same class
    as a missing one, and is never assumed geographic. wkid is 0 when the WKT names no
    root AUTHORITY, which ESRI-dialect .prj files usually do not.

    defined is True only for a .prj with the structure ArcGIS Pro 3.6 needs to read it
    (see _prj_geogcs and _prj_projcs). Anything less raises ValueError: Pro reads a
    PROJCS["name"] with nothing inside as 'Unknown', and a load stamps the target SR
    on the raw coordinates. Every rule below was measured against Pro 3.6 Describe. Some
    refuse more than Pro does, such as a node fcload does not know: refused, not guessed.
    """
    # Leading whitespace goes, but not a leading line break: Pro reads only the first
    # line, so a .prj that starts "\n" or "\r\n" is 'Unknown' to Pro 3.6 (measured).
    text = (text or "").rstrip(_WS + "\n").lstrip(_WS)
    if not text:
        return 0, "", "", False
    if text.startswith("\n"):
        raise _prj_fail("its first line is empty, and Pro reads only the first line")
    if text.startswith(u"\ufeff"):
        raise _prj_fail("it starts with a UTF-8 byte-order mark")
    kw = _PRJ_KEYWORD.match(text)
    root_kw = kw.group(1).upper() if kw else ""
    if root_kw not in ("PROJCS", "GEOGCS"):
        # ponytail: WKT1 PROJCS and GEOGCS only, the dialect shapefile writers emit.
        # COMPD_CS, LOCAL_CS and WKT2 are refused, not guessed. Add them if one shows up.
        # The text itself is never echoed: a .prj unzipped as a link to ~/.pgpass would
        # print a password into the run's log. A leading WKT keyword is safe to name.
        raise ValueError("the .prj does not start with a WKT1 PROJCS or GEOGCS: %s"
                         % ("it starts with %s[" % kw.group(1) if kw else
                            "its %d characters are not echoed" % len(text)))
    if "\n" in text:
        # Pro reads only the first line: pretty-printed WKT, one node per line, is
        # UNDEFINED to it. A lone CR, a tab and ", " are whitespace to Pro and here.
        raise _prj_fail("its WKT is split across lines")
    nodes = _wkt_nodes(text)
    # Pro writes a horizontal SR with a vertical CS as PROJCS[...],VERTCS[...]. Nothing
    # else may follow the root.
    if len(nodes) > 2 or (len(nodes) == 2 and not (
            _is_node(nodes[1]) and nodes[1][0] == "VERTCS")):
        raise ValueError("the .prj WKT is truncated or corrupt: text follows its root "
                         "that is not one VERTCS")
    root = nodes[0]
    name = _prj_projcs(root) if root_kw == "PROJCS" else _prj_geogcs(root)
    if len(nodes) == 2:
        _prj_vertcs(nodes[1])
    _prj_authorities(nodes)
    wkid = 0
    for auth in _prj_nodes(root, "AUTHORITY"):          # at most one, checked above
        org, code = auth[1]
        if isinstance(code, float) and code.is_integer():
            code = "%.0f" % code
        if org.upper() in ("EPSG", "ESRI") and isinstance(code, str) and code.isdecimal():
            wkid = int(code)
    kind = "Projected" if root_kw == "PROJCS" else "Geographic"
    return wkid, kind, name, True


def _dbf_type(letter, width, decimals):
    """A dBASE field as Pro 3.6 ListFields names its type, measured on widths 1-20."""
    if letter == "N" and not decimals:
        return "SmallInteger" if width <= 4 else "Integer" if width <= 10 else "Double"
    if letter in ("N", "F"):
        return "Single" if width <= (8 if letter == "N" else 13) else "Double"
    return DBF_TYPES.get(letter, letter)


def parse_dbf_fields(data, size):
    """Read the record count and field descriptors of a dBASE III header. Pure: the
    header bytes and the file size in.

    Returns (record_count, [(name, type)]). Raises ValueError for a header that is
    too short, has no 0x0D terminator inside its declared length, has a field with
    no name, or repeats a name case-insensitively. ArcGIS field names are unique
    without regard to case, and field_diff would silently keep only the last one.
    Also raises when the record length is not 1 plus the sum of the field lengths
    (byte 16 of each descriptor), or when the header length plus record count times
    record length is not the file size, give or take the 0x1A end byte. Measured on Pro
    3.6: GetCount on a .dbf whose count was patched to 0, 2 or 5 over 3 records fails,
    ERROR 000229. A record length of 0 fits any count, so both rules are needed.
    The messages name the header bytes, never their values (see parse_shp_header).
    """
    if len(data) < 32:
        raise ValueError("the .dbf is %d bytes; its header alone is at least 32"
                         % len(data))
    count, header_len, record_len = struct.unpack("<IHH", data[4:12])
    end = min(header_len, len(data))
    fields = []
    width = 1                                   # the deletion flag byte
    pos = 32
    while data[pos:pos + 1] != b"\r" or pos >= end:
        if pos + 32 > end:
            raise ValueError("the .dbf has no field terminator (0x0D) inside the header "
                             "length that bytes 8-9 declare")
        desc = data[pos:pos + 32]
        # A byte above 0x7F stays a \xNN escape. The .cpg is not read, so the letter case
        # of such a byte is unknown, and lower() on a Latin-1 guess merges distinct names.
        name = desc[:11].split(b"\0", 1)[0].decode("ascii", "backslashreplace").strip()
        if not name:
            raise ValueError("the .dbf field descriptor at byte %d has no name" % pos)
        if name.lower() in (n.lower() for n, _ in fields):
            raise ValueError("the .dbf names the field %r twice (field names are "
                             "unique without regard to case)" % name)
        fields.append((name, _dbf_type(desc[11:12].decode("latin-1"), desc[16], desc[17])))
        width += desc[16]
        pos += 32
    # ponytail: byte 17 is not read as a high length byte for text fields over 255 bytes,
    # which shapelib writes but no longer reads. Such a .dbf is refused, not guessed.
    if record_len != width:
        raise ValueError("the .dbf record length at bytes 10-11 is not 1 plus the sum of "
                         "its field lengths: truncated or corrupt")
    if size - header_len - count * record_len not in (0, 1):
        raise ValueError("the .dbf record count and lengths at bytes 4-11 do not fit the "
                         "%d bytes the file holds: truncated or corrupt" % size)
    return count, fields


def attachment_plan(rows, maintain):
    """What to tell the operator before a load, when the source has attachments.

    rows: the rows in the source's attachment table, or UNKNOWN_ROWS when that table
    could not be found. maintain: arcpy.env.maintainAttachments. Returns a list of
    (level, text), level "info" or "warning". Pure.
    """
    what = ("an unknown number of attachment rows" if rows < 0
            else "%d attachment row%s" % (rows, "" if rows == 1 else "s"))
    if maintain:
        return [("info", "%s in the source; arcpy.env.maintainAttachments is True, so the "
                         "load copies them (checked after --apply)" % what)]
    return [("warning", "%s in the source will be DROPPED: arcpy.env.maintainAttachments is "
                        "False, and Pro 3.6 then copies the features at maxSeverity 0 with "
                        "no warning" % what),
            ("warning", "set arcpy.env.maintainAttachments = True in the session or the "
                        "project's environment settings, then run fcload again")]


def attachment_loss(source_rows, before_rows, after_rows):
    """The message for a load that left attachments behind, or None when none were lost.

    source_rows: the source's attachment rows, UNKNOWN_ROWS when not countable.
    before_rows: attachment rows the target already held (0 for a new target).
    after_rows : attachment rows the target holds now, None when it has no attachment
    table at all. Pure.
    """
    if source_rows == 0:
        return None
    if source_rows < 0:
        if after_rows is None:
            return "the source has attachments and the target has no attachment table"
        return None
    expected = before_rows + source_rows
    if after_rows is None or after_rows < expected:
        return ("attachments dropped: the target should hold %d attachment rows (%d already "
                "there + %d from the source) and holds %d"
                % (expected, before_rows, source_rows, after_rows or 0))
    return None


def dbf_layout(data):
    """[(name, letter, width, offset)] of a .dbf header that parse_dbf_fields accepted,
    with `offset` the column's first byte inside a record (the deletion flag is byte 0),
    and the header length and record length. Pure."""
    header_len, record_len = struct.unpack("<HH", data[8:12])
    columns = []
    offset = 1
    pos = 32
    while data[pos:pos + 1] != b"\r":
        desc = data[pos:pos + 32]
        name = desc[:11].split(b"\0", 1)[0].decode("ascii", "backslashreplace").strip()
        columns.append((name, desc[11:12].decode("latin-1"), desc[16], offset))
        offset += desc[16]
        pos += 32
    return columns, header_len, record_len


def text_cap_hits(chunk, record_len, last_bytes):
    """Per column, how many whole records in `chunk` have a byte that is not blank at the
    column's last byte (`last_bytes`, offsets inside a record). A text value that fills a
    field to its last byte is a value that fits exactly or one that was cut. Pure."""
    hits = []
    for at in last_bytes:
        column = chunk[at::record_len]
        hits.append(len(column) - column.count(b" ") - column.count(b"\0"))
    return hits


def _cut_form(original, delivered):
    """True when `delivered` is how a shapefile writer spells `original` under the
    10-character limit: the first 10 characters, or a shorter stem and _N that make 10.
    Measured on Pro 3.6: PARCEL_NUMBER_2 became PARCEL_N_1 beside PARCEL_NUM."""
    o, d = original.lower(), delivered.lower()
    if len(o) <= NAME_CAP or len(d) != NAME_CAP:
        return False
    stem, sep, num = d.rpartition("_")
    return o.startswith(d) or bool(sep and num.isdigit() and o.startswith(stem))


def field_scars(original, delivered):
    """Fields the write ate, from the original's names and the delivered file's names.

    Both are lists of names. Geodatabase-managed fields are ignored on both sides. A name
    the delivered file lacks is paired with a delivered name nobody else claimed when that
    name is the 10-character form of it, else it is reported as having no counterpart.
    Returns a list of messages. Pure.
    """
    orig = [n for n in original if n.lower() not in SYSTEM_FIELDS]
    deliv = [n for n in delivered if n.lower() not in SYSTEM_FIELDS]
    have = set(n.lower() for n in orig)
    left = [d for d in deliv if d.lower() not in have]
    got = set(d.lower() for d in deliv)
    out = []
    for o in orig:
        if o.lower() in got:
            continue
        cut = next((d for d in left if _cut_form(o, d)), None)
        if cut is None:
            out.append("field %s has no counterpart in the delivered file" % o)
        else:
            left.remove(cut)
            out.append("field %s was cut to %s by the %d-character limit"
                       % (o, cut, NAME_CAP))
    return out


def scar_report(fields, shapes, rows, full, original):
    """The scars and notes for a delivered shapefile. Pure: plain data in, text out.

    fields  : [(name, type)] read from the .dbf, types as parse_dbf_fields names them
    shapes  : shapes the .shx indexes, None when there is no .shx
    rows    : records the .dbf holds
    full    : {text field name: values that fill its last byte}, 254-wide fields only
    original: the original's field names, or None when the operator gave none

    A scar is evidence that the write lost data. A note is a loss that leaves no trace in
    the file, so it can only be named. Returns (scars, notes).
    """
    scars = []
    notes = []
    if shapes is not None and shapes != rows:
        scars.append("the .shx indexes %d shape%s and the .dbf holds %d record%s: the two "
                     "files disagree about how many features the file holds"
                     % (shapes, "" if shapes == 1 else "s", rows, "" if rows == 1 else "s"))
    for name, n in sorted(full.items()):
        scars.append("%d value%s of %s fill%s all %d characters, the most a shapefile text "
                     "field holds: a longer original was cut here"
                     % (n, "" if n == 1 else "s", name, "s" if n == 1 else "", TEXT_CAP))
    names = [n for n, _ in fields]
    if original is not None:
        scars.extend(field_scars(original, names))
    else:
        tens = [n for n in names if len(n) == NAME_CAP]
        if tens:
            notes.append("%s %s exactly %d characters, the longest name a shapefile holds: "
                         "a longer name in the original would be cut to this. Compare with "
                         "--target FIELDS.txt" % (", ".join(tens), "is" if len(tens) == 1
                                                 else "are", NAME_CAP))
    dates = [n for n, t in fields if t == "Date"]
    if dates:
        notes.append("%s: a shapefile date holds the day only, so a time of day in the "
                     "original is gone and leaves no trace" % ", ".join(dates))
    numbers = [n for n, t in fields if t in ("Double", "Single", "Integer", "SmallInteger")]
    if numbers:
        notes.append("%s: a null in the original is written as 0 or a sentinel number, and "
                     "as a space in a text field, so a 0 here may be a null"
                     % ", ".join(numbers))
    if any(t == "Double" for _, t in fields):
        notes.append("a Double is written with 12 significant digits by Pro 3.6, so digits "
                     "past the twelfth are gone")
    return scars, notes


def read_original_fields(path):
    """The field names in a loadscar --target file: one per line, blank lines and lines
    that start with # ignored. Raises ValueError for a line that is not a field name, and
    does not echo it: the operator may have pointed this at a password file."""
    try:
        if os.path.isfile(path):
            _refuse_link(path)
        text = _read(path, 1 << 20).decode("utf-8", "replace")
    except OSError as exc:
        raise SystemExit("--target %s: cannot be read: %s" % (path, exc))
    names = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not _FIELD_NAME.match(line):
            raise ValueError("--target %s: line %d is not a field name (letters, "
                             "digits and underscores, starting with a letter): not echoed"
                             % (path, number))
        names.append(line)
    return names


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
    elif cmd in ("check", "loadscar"):
        if not ns.source:
            errors.append("%s requires --source (a .shp file)" % cmd)
        if ns.apply:
            errors.append("%s is read-only: --apply has nothing to apply" % cmd)
        if ns.check_write:
            errors.append("--check-write probes a workspace, and %s has none" % cmd)

    if ns.add_missing and cmd != "diff":
        errors.append("--add-missing applies to the 'diff' subcommand only")
    if ns.assume_sr is not None and cmd != "import":
        errors.append("--assume-sr applies to the 'import' subcommand only")
    if ns.assume_sr is not None and int(ns.assume_sr) <= 0:
        errors.append("--assume-sr must be a positive WKID (0 is the undefined case)")
    if ns.check_write and not ns.apply and cmd not in ("check", "loadscar"):
        # check and loadscar have their own refusal above; --apply would not cure it.
        errors.append(CHECK_WRITE_REFUSAL)

    return errors


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error. Here 2 means REFUSED, so a typo exits 1."""

    def error(self, message):
        self.print_usage(sys.stderr)
        raise SystemExit("%s: error: %s" % (self.prog, message))


def build_parser():
    p = _Parser(
        prog="fcload",
        description="Load a dataset into a geodatabase, refusing the imports that corrupt silently.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("USAGE\n-----\n", 1)[-1],
        allow_abbrev=False,
    )
    p.add_argument("command", nargs="?",
                   choices=("import", "chores", "diff", "check", "loadscar"),
                   help="import | chores | diff | check | loadscar (check and loadscar "
                        "need no arcpy)")
    p.add_argument("--workspace", help="Target geodatabase (.gdb or .sde connection file)")
    p.add_argument("--source", help="Source feature class or shapefile (check: a .shp; loadscar: "
                                     "the delivered .shp)")
    p.add_argument("--target", help="Target feature class name inside the workspace "
                                    "(check: a second .shp to compare with; loadscar: "
                                    "a text file of the original's field names)")
    p.add_argument("--dataset", help="Target feature dataset inside the workspace")
    p.add_argument("--name", help="Output feature class name (default: the source name)")
    p.add_argument("--assume-sr", type=int, default=None, metavar="WKID",
                   help="Explicit override for an UNDEFINED source SR, recorded in the "
                        "log. Refused for a source whose SR is defined.")
    p.add_argument("--add-missing", action="store_true",
                   help="diff: add fields present in the source but missing in the target")
    p.add_argument("--apply", action="store_true",
                   help="Actually perform the work. Everything is a dry run without it.")
    p.add_argument("--check-write", action="store_true",
                   help="Probe whether the workspace is writable, then stop. The probe "
                        "creates and deletes a table, so it needs --apply.")
    p.add_argument("--self-test", action="store_true",
                   help="Run the offline test suite (no arcpy, no network) and exit")
    return p


# --------------------------------------------------------------------------- #
# stdlib shapefile reading for `check` - read-only, never imports arcpy
# --------------------------------------------------------------------------- #
def _sibling(base, ext):
    """The .prj or .dbf beside a shapefile, in either letter case, or None."""
    for candidate in (base + ext, base + ext.upper()):
        if os.path.isfile(candidate):
            return candidate
    return None


def _read(path, limit):
    with open(path, "rb") as f:
        return f.read(limit)


def _refuse_link(f):
    """Raise ValueError when `f` is a symbolic link or has a second hard link.

    unzip restores a symbolic link from an archive, and tar a hard link to a file already
    in the folder. A linked file is not read, wherever it points: `parcels.dbf -> .token`
    in the same folder would print parts of the token. A hard link is a second name with
    st_nlink > 1; os.link needs no admin on Windows.
    """
    if os.path.islink(f) or os.stat(f).st_nlink > 1:
        raise ValueError("%s is a link, so it is not read: it could point at any file, "
                         "such as ~/.pgpass" % os.path.basename(f))


def read_shapefile(path, label, want_shx=False):
    """The .shp header, .prj and .dbf header of one shapefile. Opens files 'rb' only.

    With want_shx the .shx is read too, for the shape count it indexes (loadscar)."""
    base, ext = os.path.splitext(path)
    if ext.lower() != ".shp" or not os.path.isfile(path):
        raise SystemExit("%s not found or not a .shp file: %s" % (label, path))
    prj = _sibling(base, ".prj")
    dbf = _sibling(base, ".dbf")
    shx = _sibling(base, ".shx") if want_shx else None
    try:
        for f in (path, prj, dbf, shx):
            if f:
                _refuse_link(f)
        shape_type, bbox, empty = parse_shp_header(_read(path, SHP_HEADER),
                                                   os.path.getsize(path))
        prj_text = _read(prj, 65536).decode("utf-8", "replace") if prj else ""
        wkid, kind, name, defined = parse_prj(prj_text)
        dbf_head = _read(dbf, 65536) if dbf else None
        rows, fields = (parse_dbf_fields(dbf_head, os.path.getsize(dbf)) if dbf
                        else (None, None))
        shapes = None
        if shx:
            size = os.path.getsize(shx)
            parse_shp_header(_read(shx, SHX_HEADER), size)
            if (size - SHX_HEADER) % SHX_RECORD:
                raise ValueError("the .shx is not %d bytes plus %d for each shape: "
                                 "truncated or corrupt" % (SHX_HEADER, SHX_RECORD))
            shapes = (size - SHX_HEADER) // SHX_RECORD
    except ValueError as exc:
        raise ValueError("%s %s: %s" % (label, path, exc))
    except OSError as exc:
        raise SystemExit("%s %s: cannot be read: %s" % (label, path, exc))
    return {"path": path, "shape_type": shape_type, "bbox": bbox, "empty": empty,
            "prj": prj, "wkid": wkid, "sr_type": kind, "sr_name": name,
            "defined": defined, "dbf": dbf, "rows": rows, "fields": fields,
            "dbf_head": dbf_head, "shx": shx, "shapes": shapes}


def _print_field_diff(missing_t, missing_s, conflicts):
    print("  in source, missing in target (%d):" % len(missing_t))
    for n, t in missing_t:
        print("    + %s (%s)" % (n, t))
    print("  in target, missing in source (%d):" % len(missing_s))
    for n, t in missing_s:
        print("    - %s (%s)" % (n, t))
    print("  type conflicts (%d):" % len(conflicts))
    for n, st, tt in conflicts:
        print("    ! %s: source %s vs target %s" % (n, st, tt))


def _print_summary(src, label):
    """The lines `check` and `loadscar` share. `label` is 9 characters wide."""
    print("  %s  : %s" % (label, src["path"]))
    print("  geometry   : %s   rows: %s   fields: %s" % (
        SHAPE_TYPES[src["shape_type"]],
        "unknown (no .dbf)" if src["rows"] is None else src["rows"],
        "unknown (no .dbf)" if src["fields"] is None else len(src["fields"])))
    print("  .prj       : %s" % (src["prj"] or "MISSING - the spatial reference is "
                                  "UNDEFINED, never assumed geographic"))
    print("  source SR  : %r (%s, %s)" % (src["sr_name"] or "Unknown",
                                          _wkid_text(src["wkid"]),
                                          src["sr_type"] or "type unknown"))
    print("  extent     : %s   (.shp header, bytes 36-67)" % (src["bbox"],))


def cmd_check(ns):
    print("fcload check (read-only, arcpy is not imported)")
    try:
        src = read_shapefile(ns.source, "source")
        tgt = read_shapefile(ns.target, "target") if ns.target else None
    except ValueError as exc:
        print("  verdict    : REFUSE")
        print("    - %s" % exc)
        return 2

    _print_summary(src, "source   ")

    if src["empty"] or src["shape_type"] == 0 or src["rows"] == 0:
        print("  verdict    : REFUSE")
        print("    - the source holds no features (%s): its bounding box says nothing "
              "about where the data is" % ("the .shp is its 100-byte header alone"
                                            if src["empty"] else "shape type 0, Null"
                                            if src["shape_type"] == 0 else
                                            "the .dbf holds 0 records"))
        return 2

    target_wkid = None
    if tgt:
        target_wkid = tgt["wkid"] or None
        print("  target     : %s   (.prj %s)" % (tgt["path"], tgt["prj"] or "MISSING"))
        print("  target SR  : %r (%s, %s)   extent %s" % (
            tgt["sr_name"] or "Unknown", _wkid_text(tgt["wkid"]),
            tgt["sr_type"] or "type unknown", tgt["bbox"]))

    verdict, reasons = sr_verdict(src["wkid"], src["sr_type"], src["sr_name"], src["bbox"],
                                  target_wkid, prj_defined=src["defined"])
    if tgt:
        # An empty target is a fresh schema and a Null one has no location: their boxes
        # say nothing, so only the undefined rule applies to them.
        blank = tgt["empty"] or tgt["shape_type"] == 0
        tv, tr = target_verdict("target", tgt["wkid"], tgt["sr_type"], tgt["sr_name"],
                                None if blank else tgt["bbox"], prj_defined=tgt["defined"])
        if tv == REFUSE:
            verdict, reasons = REFUSE, tr + (reasons if verdict == REFUSE else [])
    print("  verdict    : %s" % verdict)
    for r in reasons:
        print("    - %s" % r)

    if tgt:
        if src["fields"] is None or tgt["fields"] is None:
            print("  fields     : not compared, a .dbf is missing")
        else:
            _print_field_diff(*field_diff(src["fields"], tgt["fields"]))

    return 2 if verdict == REFUSE else 0


def scan_text_caps(path, header_len, record_len, count, last_bytes):
    """text_cap_hits over every record of a .dbf, read in chunks. Read-only."""
    totals = [0] * len(last_bytes)
    step = max(1, (1 << 22) // record_len)
    with open(path, "rb") as f:
        f.seek(header_len)
        left = count
        while left:
            take = min(step, left)
            chunk = f.read(take * record_len)
            totals = [a + b for a, b in zip(totals, text_cap_hits(chunk, record_len,
                                                                  last_bytes))]
            left -= take
    return totals


def cmd_loadscar(ns):
    """fcload's refusal rules run backwards on a file somebody delivered: what in the file
    shows that the write which made it lost data. Read-only, no arcpy."""
    print("fcload loadscar (read-only, arcpy is not imported)")
    try:
        src = read_shapefile(ns.source, "delivered", want_shx=True)
        original = read_original_fields(ns.target) if ns.target else None
    except ValueError as exc:
        print("  verdict    : REFUSE")
        print("    - %s" % exc)
        return 2

    _print_summary(src, "delivered")
    scars = []
    notes = []
    for ext, found in ((".dbf", src["dbf"]), (".shx", src["shx"])):
        if not found:
            scars.append("no %s beside the .shp: the shapefile specification requires it, so "
                         "part of the delivery is missing" % ext)
    blank = src["empty"] or src["shape_type"] == 0 or src["rows"] == 0
    if blank:
        scars.append("the delivered file holds no features (%s)" % (
            "the .shp is its 100-byte header alone" if src["empty"] else
            "shape type 0, Null" if src["shape_type"] == 0 else "the .dbf holds 0 records"))
    elif src["dbf"]:
        columns, header_len, record_len = dbf_layout(src["dbf_head"])
        wide = [c for c in columns if c[1] == "C" and c[2] == TEXT_CAP]
        hits = []
        if wide:
            try:
                hits = scan_text_caps(src["dbf"], header_len, record_len, src["rows"],
                                      [off + width - 1 for _, _, width, off in wide])
            except OSError as exc:
                raise SystemExit("delivered %s: cannot be read: %s" % (src["path"], exc))
        found, notes = scar_report(src["fields"], src["shapes"], src["rows"],
                                   dict((c[0], n) for c, n in zip(wide, hits) if n), original)
        scars.extend(found)
    if not blank:
        # An empty file's box says nothing, so its SR is not judged against it.
        verdict, reasons = sr_verdict(src["wkid"], src["sr_type"], src["sr_name"],
                                      src["bbox"], None, prj_defined=src["defined"])
        if verdict == REFUSE:
            scars.extend(reasons)
    print("  shapes     : %s in the .shx, %s in the .dbf" % (
        "unknown" if src["shapes"] is None else src["shapes"],
        "unknown" if src["rows"] is None else src["rows"]))
    print("  verdict    : %s" % (REFUSE if scars else ACCEPT))
    for x in scars or ["no scar found by these rules (see Limitations: absence is not proof)"]:
        print("    - %s" % x)
    if notes:
        print("  losses that leave no trace (named, not detected):")
        for x in notes:
            print("    - %s" % x)
    return 2 if scars else 0


# --------------------------------------------------------------------------- #
# arcpy-backed description and actions
# --------------------------------------------------------------------------- #
def _sr_fields(sr):
    """(factoryCode, type, name) of an arcpy SpatialReference. None reads as undefined."""
    if sr is None:
        return 0, "", "Unknown"
    return (int(getattr(sr, "factoryCode", 0) or 0), getattr(sr, "type", ""),
            getattr(sr, "name", "Unknown"))


def attachment_rows(arcpy, path):
    """None when `path` has no attachments, else the rows in its attachment table, or
    UNKNOWN_ROWS when that table cannot be found. Reads only.

    A feature class lists its relationship classes; the one whose isAttachmentRelationship
    is True names the attachment table as its destination. Measured on Pro 3.6, a feature
    class inside a feature dataset keeps that class and table at the workspace root, so the
    search climbs from the path to its two nearest parents.
    """
    base = os.path.basename(path).lower()
    here = path
    for rel in getattr(arcpy.Describe(path), "relationshipClassNames", None) or []:
        for _ in range(2):
            here = os.path.dirname(here)
            rel_path = os.path.join(here, rel)
            if not arcpy.Exists(rel_path):
                continue
            rd = arcpy.Describe(rel_path)
            if (getattr(rd, "isAttachmentRelationship", False)
                    and base in [n.lower() for n in rd.originClassNames]):
                table = os.path.join(here, rd.destinationClassNames[0])
                return (int(arcpy.management.GetCount(table)[0]) if arcpy.Exists(table)
                        else UNKNOWN_ROWS)
        here = path
    return None


def describe_source(path):
    """Return the plain-data description sr_verdict needs. Touches a geodatabase."""
    arcpy = _arcpy()
    if not arcpy.Exists(path):
        raise SystemExit("source not found: %s" % path)
    d = arcpy.Describe(path)
    code, kind, name = _sr_fields(getattr(d, "spatialReference", None))
    ext = getattr(d, "extent", None)
    extent = None
    if ext is not None and ext.XMin is not None:
        extent = (ext.XMin, ext.YMin, ext.XMax, ext.YMax)
    return {
        "path": path,
        "name": getattr(d, "baseName", os.path.basename(path)),
        "shape_type": getattr(d, "shapeType", "Table"),
        "wkid": code,
        "sr_type": kind,
        "sr_name": name,
        "extent": extent,
        "count": int(arcpy.management.GetCount(path)[0]),
        "fields": [(f.name, f.type, f.length) for f in arcpy.ListFields(path)],
        "attach": attachment_rows(arcpy, path),
    }


def target_sr_of(workspace, dataset=None):
    """_sr_fields of the destination feature dataset, or None when it is a root."""
    arcpy = _arcpy()
    path = os.path.join(workspace, dataset) if dataset else workspace
    if not arcpy.Exists(path):
        raise SystemExit("target location not found: %s" % path)
    if not dataset:
        return None
    return _sr_fields(getattr(arcpy.Describe(path), "spatialReference", None))


def check_write(workspace, apply=False):
    """Create and delete a scratch table to prove the workspace accepts writes.

    That probe is a write, so without apply=True it touches nothing and says why.
    Returns True (writable, probe removed), False (the write failed, or it worked but
    the probe table could not be removed) or None (not probed).
    """
    if not apply:
        print("  NOT PROBED: %s" % CHECK_WRITE_REFUSAL)
        return None
    arcpy = _arcpy()
    path = os.path.join(workspace, PROBE_NAME)
    try:
        if arcpy.Exists(path):
            arcpy.management.Delete(path)
        arcpy.management.CreateTable(workspace, PROBE_NAME)
    except Exception as exc:  # arcpy raises ExecuteError, which we cannot name here
        print("  NOT WRITABLE: %s" % exc)
        return False
    print("  writable: %s" % workspace)
    try:
        arcpy.management.Delete(path)
    except Exception as exc:
        # The write worked. Say so, and name what the probe left behind.
        print("  LEFT BEHIND: the probe table %s could not be removed: %s" % (path, exc))
        print("  Delete it by hand. The workspace accepted the write.")
        return False
    return True


def cmd_import(ns):
    arcpy = _arcpy()
    src = describe_source(ns.source)
    dataset_sr = target_sr_of(ns.workspace, ns.dataset)
    out_name = ns.name or src["name"]
    out_path = os.path.join(ns.workspace, ns.dataset) if ns.dataset else ns.workspace
    dest = os.path.join(out_path, out_name)

    # The rows land in the feature dataset's SR, or in an existing feature class's.
    landing = [("target dataset", dataset_sr)] if dataset_sr else []
    if arcpy.Exists(dest):
        landing.append(("existing target feature class",
                        _sr_fields(getattr(arcpy.Describe(dest), "spatialReference", None))))
    # The last landing SR is the one the rows are stored in. A new feature class at the
    # workspace root has none and takes the source's SR.
    target_wkid = landing[-1][1][0] if landing else 0

    print("fcload import")
    print("  source     : %s" % src["path"])
    print("  geometry   : %s   rows: %d   fields: %d" % (
        src["shape_type"], src["count"], len(src["fields"])))
    print("  source SR  : %r (WKID %d, %s)" % (
        src["sr_name"], src["wkid"], src["sr_type"] or "type unknown"))
    print("  extent     : %s" % (src["extent"],))
    print("  target     : %s -> %s  (WKID %d)" % (out_path, out_name, target_wkid))
    if src["attach"] is not None:
        maintain = bool(getattr(arcpy.env, "maintainAttachments", True))
        for level, text in attachment_plan(src["attach"], maintain):
            print("  %s: %s" % ("attachments" if level == "info" else "WARNING    ", text))

    if src["count"] == 0:
        print("  verdict    : REFUSE")
        print("    - the source holds no features (GetCount 0): its extent says nothing "
              "about where the data is")
        return 2

    verdict, reasons = sr_verdict(
        src["wkid"], src["sr_type"], src["sr_name"], src["extent"], target_wkid,
        assume_sr=ns.assume_sr,
    )
    for label, (code, kind, name) in landing:
        tv, tr = target_verdict(label, code, kind, name, None)
        if tv == REFUSE:
            verdict, reasons = REFUSE, tr + (reasons if verdict == REFUSE else [])
    print("  verdict    : %s" % verdict)
    for r in reasons:
        print("    - %s" % r)

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
        print("  defined source SR as WKID %d on a scratch copy (source untouched)"
              % int(ns.assume_sr))

    # Only a feature dataset load is projected here, as in 1.0.0. Append into a root
    # feature class projects on the fly. Measured on Pro 3.6, WGS84 into WKID 2237: Append
    # stored (611496.05, 1765403.33), Project first stored (611497.11, 1765401.34).
    if ns.dataset and target_wkid and effective_wkid != target_wkid:
        proj = arcpy.CreateUniqueName("fcload_projected", arcpy.env.scratchGDB)
        arcpy.management.Project(work, proj, arcpy.SpatialReference(target_wkid))
        scratch.append(proj)
        work = proj
        print("  reprojected %d -> %d" % (effective_wkid, target_wkid))

    existed = arcpy.Exists(dest)
    before = int(arcpy.management.GetCount(dest)[0]) if existed else 0
    before_att = max(attachment_rows(arcpy, dest) or 0, 0) if existed else 0

    if existed:
        print("  target exists: appending %d rows with Append (NO_TEST)" % src["count"])
        arcpy.management.Append(inputs=work, target=dest, schema_type="NO_TEST")
    else:
        arcpy.conversion.FeatureClassToFeatureClass(
            in_features=work, out_path=out_path, out_name=out_name)

    for s in scratch:
        arcpy.management.Delete(s)

    if not arcpy.Exists(dest):
        print("  FAILED: %s does not exist after the load" % dest)
        return 1

    after = int(arcpy.management.GetCount(dest)[0])
    expected = before + src["count"]
    print("  rows: %d before + %d loaded = %d expected, %d found" % (
        before, src["count"], expected, after))
    if after != expected:
        print("  FAILED: row count mismatch. Treat this load as incomplete.")
        return 1

    if src["attach"] is not None:
        after_att = attachment_rows(arcpy, dest)
        if after_att == UNKNOWN_ROWS:
            lost = ("attachments not verified: the target's attachment table could not be "
                    "found to count")
        else:
            lost = attachment_loss(src["attach"], before_att, after_att)
        if lost:
            print("  WARNING    : %s" % lost)
        else:
            print("  attachments: %d attachment rows now in the target" % (after_att or 0))

    d = arcpy.Describe(dest)
    print("  OK: %s | %s | SR %r (WKID %s)" % (
        dest, d.shapeType, d.spatialReference.name, d.spatialReference.factoryCode))
    print("  next: fcload chores --workspace ... --target %s" % out_name)
    return 0


def cmd_chores(ns):
    arcpy = _arcpy()
    dest = os.path.join(ns.workspace, ns.dataset, ns.target) if ns.dataset \
        else os.path.join(ns.workspace, ns.target)
    if not arcpy.Exists(dest):
        raise SystemExit("target not found: %s" % dest)

    existing = [f.name for f in arcpy.ListFields(dest)]
    creator, created, editor, edited = tracking_field_names(existing)
    d = arcpy.Describe(dest)
    has_gid = bool(getattr(d, "hasGlobalID", False))
    tracking_on = bool(getattr(d, "editorTrackingEnabled", False))

    print("fcload chores")
    print("  target        : %s" % dest)
    print("  GlobalID      : %s" % ("present" if has_gid else "MISSING -> AddGlobalIDs"))
    print("  tracking      : %s" % ("enabled" if tracking_on else "off -> EnableEditorTracking"))
    print("  tracking names: %s, %s, %s, %s" % (creator, created, editor, edited))
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
    print("  verified: hasGlobalID=%s editorTrackingEnabled=%s" % (
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
        raise SystemExit("target not found: %s" % dest)
    if not arcpy.Exists(ns.source):
        raise SystemExit("source not found: %s" % ns.source)

    src_fields = [(f.name, f.type, f.length) for f in arcpy.ListFields(ns.source)]
    tgt_fields = [(f.name, f.type, f.length) for f in arcpy.ListFields(dest)]
    missing_t, missing_s, conflicts = field_diff(src_fields, tgt_fields)

    print("fcload diff")
    print("  source: %s" % ns.source)
    print("  target: %s" % dest)
    _print_field_diff(missing_t, missing_s, conflicts)

    if not ns.add_missing:
        return 0
    if not missing_t:
        print("  nothing to add.")
        return 0
    if not ns.apply:
        print("\n  DRY RUN. Would AddField %d field(s). Re-run with --apply." % len(missing_t))
        return 0

    lengths = {n.lower(): ln for n, _, ln in src_fields}
    for n, t in missing_t:
        arcpy.management.AddField(dest, n, t, field_length=lengths.get(n.lower()))
        print("  AddField: %s (%s)" % (n, t))

    after = {f.name.lower() for f in arcpy.ListFields(dest)}
    absent = [n for n, _ in missing_t if n.lower() not in after]
    if absent:
        print("  FAILED: still missing after AddField: %s" % (absent,))
        return 1
    print("  verified: %d field(s) added." % len(missing_t))
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
        self.missing = set(g("missing", ()))     # paths Exists always reports absent
        self.create_fails = g("create_fails", False)
        self.delete_fails = g("delete_fails", False)
        self.src_sr = None if g("no_sr") else _Stub(factoryCode=g("src_wkid", 4326),
                                                   type=g("src_type", "Geographic"),
                                                   name=g("src_name", "GCS_WGS_1984"))
        self.tgt_sr = None if g("no_sr") else _Stub(factoryCode=g("target_wkid", 4326),
                                                   type="Geographic", name="GCS_WGS_1984")
        self.src_extent = g("src_extent", (-82.5, 29.0, -82.0, 29.5))
        self.src_fields = list(g("src_fields", [("NOTES", "String", 50)]))
        self.dest_fields = list(g("dest_fields", []))
        self.has_gid = g("has_gid", False)
        self.tracking = g("tracking", False)
        self.add_field_works = g("add_field_works", True)
        self.management = _StubGroup(self)
        self.conversion = _StubGroup(self)
        self.maintain = g("maintain", True)
        self.env = _Stub(scratchGDB="scratch.gdb", maintainAttachments=self.maintain)
        self.dest_path = g("dest_path", os.path.join(self.WS, self.DS, "out"))
        # Attachments: None is "none", a number is the rows in the attachment table.
        self.src_attach = g("src_attach")
        self.dest_attach = g("dest_attach")
        self.attach_copies = g("attach_copies", self.maintain)
        self.table_missing = g("table_missing", False)
        self.rel_attach = g("rel_attach", True)         # False: a plain relationship class
        self.rel_origin = g("rel_origin")               # override the origin class name
        self.extra_rel = g("extra_rel", False)          # a first relationship class that is not there
        self.rels = {
            "src__ATTACHREL": _Stub(isAttachmentRelationship=self.rel_attach,
                                    originClassNames=[self.rel_origin or self.SRC],
                                    destinationClassNames=["src__ATTACH"]),
            os.path.join(self.WS, "out__ATTACHREL"): _Stub(
                isAttachmentRelationship=self.rel_attach,
                originClassNames=[self.rel_origin or "OUT"],
                destinationClassNames=["out__ATTACH"]),
        }

    # --- the arcpy surface fcload touches ----------------------------------- #
    def Exists(self, path):
        self.calls.append(("Exists", path, {}))
        if path in self.missing:
            return False
        if path.endswith("REL"):
            return path in self.rels
        if path.endswith("__ATTACH"):
            return not self.table_missing
        return self.dest_exists if path == self.dest_path else True

    def Describe(self, path):
        if path in self.rels:
            return self.rels[path]
        extra = ["gone__REL"] if self.extra_rel else []
        if path == self.SRC:
            ext = None if self.src_extent is None else _Stub(
                XMin=self.src_extent[0], YMin=self.src_extent[1],
                XMax=self.src_extent[2], YMax=self.src_extent[3])
            d = _Stub(spatialReference=self.src_sr, extent=ext,
                      baseName="out", shapeType="Point")
            if self.src_attach is not None:     # a shapefile has no such property at all
                d.relationshipClassNames = extra + ["src__ATTACHREL"]
            return d
        return _Stub(spatialReference=self.tgt_sr, extent=None, baseName="out",
                     shapeType="Point", hasGlobalID=self.has_gid,
                     editorTrackingEnabled=self.tracking,
                     relationshipClassNames=extra + (
                         [] if self.dest_attach is None else ["out__ATTACHREL"]))

    def ListFields(self, path):
        rows = self.src_fields if path == self.SRC else self.dest_fields
        return [_Stub(name=r[0], type=r[1] if len(r) > 1 else "String",
                      length=r[2] if len(r) > 2 else 0) for r in rows]

    def SpatialReference(self, wkid):
        return _Stub(factoryCode=wkid, name="WKID %d" % wkid)

    def CreateUniqueName(self, base, workspace):
        return os.path.join(workspace, base)

    def _call(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))
        if name == "GetCount":
            path = args[0] if args else kwargs.get("in_rows")
            if path.endswith("__ATTACH"):
                return [str(self.src_attach if path == "src__ATTACH" else self.dest_attach)]
            return [str(self.src_count if path == self.SRC else self.dest_count)]
        if name in ("FeatureClassToFeatureClass", "Append"):
            before = self.dest_count if self.dest_exists else 0
            self.dest_count = (before + self.src_count) if self.landed is None else self.landed
            self.dest_exists = True
            if self.src_attach is not None and self.attach_copies:
                self.dest_attach = (self.dest_attach or 0) + max(self.src_attach, 0)
        elif name == "AddGlobalIDs":
            self.has_gid = True
        elif name == "EnableEditorTracking":
            self.tracking = True
        elif name == "AddField" and self.add_field_works:
            self.dest_fields.append((args[1], args[2], 0))
        elif name == "CreateTable" and self.create_fails:
            raise RuntimeError("stub: the workspace refused CreateTable")
        elif name == "Delete" and self.delete_fails:
            raise RuntimeError("stub: cannot get an exclusive schema lock")
        return None

    def writes(self):
        return [c[0] for c in self.calls if c[0] in WRITE_CALLS]


# --------------------------------------------------------------------------- #
# Offline self-test - no arcpy, no network, no geodatabase
# --------------------------------------------------------------------------- #
def self_test():
    import importlib.util
    import io
    import shutil
    import tempfile
    import types
    from contextlib import contextmanager, redirect_stderr, redirect_stdout

    passed = [0]
    failed = []

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def raises(fn, label, exc_type=ValueError, needle=""):
        try:
            fn()
        except exc_type as exc:
            check(needle in str(exc), label)
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)

    @contextmanager
    def arcpy_module(value):
        """Put `value` in sys.modules['arcpy'] for the block, then restore what was there."""
        prev = sys.modules.get("arcpy")
        sys.modules["arcpy"] = value
        try:
            yield
        finally:
            del sys.modules["arcpy"]
            sys.modules.update({} if prev is None else {"arcpy": prev})

    print("fcload self-test: no arcpy, no geodatabase, no network")
    print("-" * 68)

    # --- the harness itself: a failure is recorded, never swallowed ---------- #
    with redirect_stdout(io.StringIO()) as probe_out:
        check(False, "probe")
        raises(lambda: None, "probe")
        raises(lambda: [][1], "probe")
        raises(lambda: int("x"), "probe", needle="never in the message")
    probes = failed[-4:]
    del failed[-4:]
    check(len(probes) == 4 and probe_out.getvalue().count("FAIL  ") == 4,
          "the harness records a false check, a missing raise, a wrong exception "
          "and a wrong message")

    SP_FEET = (400000.0, 1500000.0, 700000.0, 2100000.0)      # US survey feet
    DEG = (-82.5, 29.0, -82.0, 29.5)                          # degrees
    WORLD_DEG = (-180.0, -90.0, 180.0, 90.0)

    # --- sr_verdict: the undefined case, the entire point of this tool ------- #
    v, r = sr_verdict(0, "Geographic", "Unknown", DEG, 2237)
    check(v == REFUSE, "factoryCode 0 is refused  <-- pinned defect")
    check(any("UNDEFINED" in x for x in r), "the refusal names the undefined SR")
    _, r = sr_verdict(0, "", "", DEG, 2237)
    check(any("(factoryCode=0, name='Unknown')" in x for x in r),
          "an undefined SR with an empty name is called 'Unknown', never ''")
    _, r = target_verdict("target", 0, "", "", None)
    check(any("target spatial reference is UNDEFINED (factoryCode=0, name='Unknown')" in x
              for x in r), "so is an undefined target with an empty name")
    _, r = target_verdict("target", 0, "", "  GCS_Mine ", None)
    check(any("(factoryCode=0, name='GCS_Mine')" in x for x in r),
          "an undefined target keeps its own name in the refusal, trimmed")
    check(any("maxSeverity 0" in x for x in r), "the refusal explains arcpy's silence")
    check(any("--assume-sr" in x for x in r), "the refusal names the override")

    v, r = sr_verdict(4326, "Geographic", "Unknown", DEG, 2237)
    check(v == REFUSE, "an SR name of 'Unknown' is refused even with a non-zero factoryCode")

    v, r = sr_verdict(0, "", "", None, 2237)
    check(v == REFUSE, "empty SR name with code 0 is refused")

    v, r = sr_verdict(0, "Projected", "NAD_1983_StatePlane", SP_FEET, 2237)
    check(v == REFUSE, "factoryCode 0 is refused even when the SR has a plausible name")

    v, r = sr_verdict(None, None, None, None, None)
    check(v == REFUSE, "all-None description is refused, never accepted by default")

    # --- sr_verdict: defined SRs that are fine ------------------------------- #
    v, r = sr_verdict(2237, "Projected", "NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet",
                      SP_FEET, 2237)
    check(v == ACCEPT, "defined projected SR matching the target is accepted")
    check(any("matches the target" in x for x in r), "the match is stated")
    check(not any("differs" in x for x in r), "no spurious reprojection notice on a match")

    v, r = sr_verdict(6441, "Projected", "NAD_1983_2011_StatePlane_Florida_East",
                      SP_FEET, 2237)
    check(v == ACCEPT, "defined projected SR differing from the target is accepted")
    check(any("reprojection" in x for x in r), "the difference is called a reprojection")
    check(any("not a corruption" in x for x in r), "a differing SR is not treated as corrupt")

    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", DEG, 2237)
    check(v == ACCEPT, "geographic SR with a degree extent is accepted")
    check(any("consistent with its" in x for x in r), "the consistency check is reported")
    check(any("(WKID 4326, Geographic)" in x for x in r),
          "a stated WKID is printed exactly as before")

    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", WORLD_DEG, 4326)
    check(v == ACCEPT, "the exact degree bounds are inside the box, not outside")

    v, r = sr_verdict(2237, "Projected", "StatePlane_Feet", SP_FEET, 0)
    check(v == ACCEPT, "a target with no SR of its own still accepts a sane source")
    check(any("no spatial reference of its own" in x for x in r), "the SR-less target is noted")

    v, r = sr_verdict(2237, "Projected", "StatePlane_Feet", None, 2237)
    check(v == ACCEPT, "a missing extent does not by itself refuse")
    check(any("sanity check skipped" in x for x in r), "the skipped check is disclosed")

    # --- sr_verdict: the wrong-.prj cases ------------------------------------ #
    v, r = sr_verdict(2237, "Projected", "NAD_1983_StatePlane_Florida_West", DEG, 2237)
    check(v == REFUSE, "projected SR with a degree extent is refused")
    check(any("PROJECTED" in x for x in r), "the projected/degree refusal names the SR type")
    check(any("latitude/longitude" in x for x in r), "the evidence is spelled out")

    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", SP_FEET, 2237)
    check(v == REFUSE, "geographic SR with a projected extent is refused")
    check(any("GEOGRAPHIC" in x for x in r), "the geographic/projected refusal names the SR type")
    check(any("outside degree bounds" in x for x in r), "the evidence is spelled out")

    v, _ = sr_verdict(4326, "Geographic", "GCS_WGS_1984", (-82.5, 29.0, -82.0, 1500000.0),
                      2237)
    check(v == REFUSE, "one corner outside degree bounds is enough to refuse")

    v, _ = sr_verdict(2237, "Projected", "StatePlane_Feet", (0.0, 0.0, 0.0, 0.0), 2237)
    check(v == REFUSE, "a degenerate origin extent under a projected SR is refused")

    # --- sr_verdict: the --assume-sr override -------------------------------- #
    v, r = sr_verdict(0, "", "Unknown", DEG, 2237, assume_sr=4326)
    check(v == ACCEPT, "--assume-sr overrides the undefined refusal")
    check(any("OVERRIDE ACCEPTED" in x for x in r), "the override is announced")
    check(any("4326" in x for x in r), "the assumed WKID is recorded in the reasons")
    check(any("will reproject" in x for x in r), "the assumed-vs-target reprojection is stated")

    v, r = sr_verdict(0, "", "Unknown", DEG, 2237, assume_sr=2237)
    check(v == ACCEPT, "--assume-sr matching the target is accepted")
    check(any("no reprojection" in x for x in r), "a matching assumption skips reprojection")

    v, r = sr_verdict(0, "", "Unknown", DEG, 0, assume_sr=4326)
    check(v == ACCEPT and not any("matches the target" in x for x in r),
          "--assume-sr into an SR-less target never claims it matches the target  "
          "<-- pinned defect")
    check(any("no spatial reference of its own" in x for x in r),
          "and says the target has no SR of its own")

    v, _ = sr_verdict(0, "", "Unknown", DEG, 2237, assume_sr=0)
    check(v == REFUSE, "--assume-sr 0 is not an override, it is the undefined value")

    v, _ = sr_verdict(2237, "Projected", "StatePlane_Feet", DEG, 2237, assume_sr=4326)
    check(v == REFUSE, "--assume-sr does not excuse a defined-but-wrong .prj")
    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", DEG, 2237, assume_sr=2237)
    check(v == REFUSE and any("applies only to an undefined source" in x for x in r)
          and not any("reprojection" in x for x in r),
          "--assume-sr on a DEFINED, consistent source is refused, never ignored  "
          "<-- pinned defect")
    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", DEG, 4326, assume_sr=2237)
    check(v == REFUSE and not any("no reprojection" in x for x in r),
          "--assume-sr 2237 on a WGS84 source into a WGS84 target is refused too  "
          "<-- pinned defect")
    v, r = sr_verdict(0, "Geographic", "GCS_WGS_1984", DEG, None, assume_sr=4326,
                      prj_defined=True)
    check(v == REFUSE and any("DEFINED (no WKID stated" in x for x in r),
          "a named .prj with no WKID is defined, so --assume-sr is refused on it")

    check(extent_in_degree_range(None) is None, "no extent yields no range answer")
    check(extent_in_degree_range(DEG) is True, "degree extent is in range")
    check(extent_in_degree_range(SP_FEET) is False, "state plane extent is out of range")
    bounds = (180.0, 90.0, 180.0, 90.0)     # literals: the test must not move with DEGREE_X
    corner = []
    for i in range(4):
        for sign in (-1.0, 1.0):
            for past, want in ((0.0, True), (1.0, False)):
                box = [0.0, 0.0, 0.0, 0.0]
                box[i] = sign * (bounds[i] + past)
                corner.append(extent_in_degree_range(box) is want)
    check(len(corner) == 16 and all(corner),
          "each corner, either sign, is in range on its bound and out one unit past it  "
          "<-- pinned defect")
    check(extent_in_degree_range((-200.0, 10.0, 20.0, 20.0)) is False,
          "an extent with xmin -200 is not in degree range")
    slack = []
    for i in range(4):
        for sign in (-1.0, 1.0):
            for past, want in ((1e-10, True), (1e-8, False)):
                box = [0.0, 0.0, 0.0, 0.0]
                box[i] = sign * (bounds[i] + past)
                slack.append(extent_in_degree_range(box) is want)
    check(len(slack) == 16 and all(slack),
          "each bound allows 1e-9 degrees of rounding, and not 1e-8")
    WORLD_3857 = (-180.00000000000003, -85.0511287798066, 180.00000000000003, 85.0511287798066)
    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", WORLD_3857, None)
    check(v == ACCEPT and any("consistent with its" in x for x in r),
          "a world extent converted from Web Mercator, x one ULP past 180, is degrees  "
          "<-- pinned defect")
    v, _ = sr_verdict(4326, "Geographic", "GCS_WGS_1984",
                      (-6.0e6, -3.0e6, -5.9e6, -2.9e6), 2237)
    check(v == REFUSE, "a geographic .prj over all-negative Web Mercator metres is refused  "
          "<-- pinned defect")
    v, _ = sr_verdict(4326, "Geographic", "x", (10.0, 100.0, 20.0, 120.0), None)
    check(v == REFUSE, "a geographic .prj over latitude 100 to 120 is refused")
    nan = float("nan")
    check(extent_in_degree_range((nan, nan, nan, nan)) is None,
          "a NaN extent, as Pro 3.6 describes an empty shapefile, has no range answer")
    v, r = sr_verdict(4326, "Geographic", "GCS_WGS_1984", (nan, nan, nan, nan), 2237)
    check(v == ACCEPT and not any("GEOGRAPHIC" in x for x in r)
          and any("sanity check skipped" in x for x in r),
          "a NaN extent is unavailable, never 'projected units under a wrong .prj'  "
          "<-- pinned defect")

    # --- sr_verdict: a .prj with a named root but no WKID (check mode) ------- #
    v, r = sr_verdict(0, "Geographic", "GCS_WGS_1984", DEG, None, prj_defined=True)
    check(v == ACCEPT, "a named .prj without a WKID is defined, not undefined")
    check(any("no WKID stated" in x for x in r), "and the missing WKID is said in words")
    check(any("reprojection not assessed" in x for x in r),
          "with no target WKID the reprojection is not assessed, not assumed")
    v, r = sr_verdict(0, "Projected", "StatePlane_Feet", SP_FEET, 2237, prj_defined=True)
    check(v == ACCEPT and any("cannot say whether" in x for x in r),
          "a source with no WKID is never called a match for the target")
    v, r = sr_verdict(0, "Projected", "StatePlane_Feet", DEG, None, prj_defined=True)
    check(v == REFUSE, "a WKID-less projected .prj over degrees is still refused")
    check(any("(no WKID stated)" in x for x in r), "and the refusal does not print WKID 0")
    v, _ = sr_verdict(0, "Geographic", "Unknown", DEG, None, prj_defined=True)
    check(v == REFUSE, "a .prj named 'Unknown' is undefined even when the file exists")
    v, _ = sr_verdict(0, "Geographic", "", DEG, None, prj_defined=True)
    check(v == REFUSE, "a .prj root with an empty name is undefined")
    check(all(sr_verdict(4326, "Geographic", n, DEG, None)[0] == REFUSE
              for n in ("<undefined>", " Undefined", "NONE", "unknown")),
          "each name that means undefined is refused, whatever the code says")
    v, _ = sr_verdict(2237, " Projected ", "StatePlane_Feet", DEG, 2237)
    check(v == REFUSE, "an SR type padded with spaces is still read as projected")

    # --- field_diff ---------------------------------------------------------- #
    src = [("OBJECTID", "OID", 4), ("Shape", "Geometry", 0), ("PARCEL_ID", "String", 20),
           ("ACRES", "Double", 8), ("OWNER", "String", 60)]
    tgt = [("OBJECTID", "OID", 4), ("Shape", "Geometry", 0), ("parcel_id", "String", 20),
           ("ACRES", "Text", 8), ("ZONING", "String", 10)]
    mt, ms, tc = field_diff(src, tgt)
    check([n for n, _ in mt] == ["OWNER"], "only OWNER is missing in the target")
    check([n for n, _ in ms] == ["ZONING"], "only ZONING is missing in the source")
    check(tc == [("ACRES", "Double", "Text")], "the ACRES field is reported as a type conflict")
    check(all(n.lower() != "objectid" for n, _ in mt + ms), "the OBJECTID field is never diffed")
    check(all(n.lower() != "shape" for n, _ in mt + ms), "the Shape field is never diffed")
    check(not any(n.lower() == "parcel_id" for n, _ in mt),
          "the PARCEL_ID field matches parcel_id case-insensitively")
    check(not any(n.lower() == "parcel_id" for n, _, _ in tc),
          "a case-only name difference is not a type conflict")

    mt, ms, tc = field_diff([], [])
    check((mt, ms, tc) == ([], [], []), "two empty field lists diff to nothing")
    mt, ms, tc = field_diff(None, None)
    check((mt, ms, tc) == ([], [], []), "None field lists diff to nothing")
    mt, ms, tc = field_diff(src, [])
    check(len(mt) == 3 and ms == [] and tc == [], "an empty target is missing every real field")
    mt, ms, tc = field_diff([], tgt)
    check(mt == [] and len(ms) == 3, "an empty source lacks every real target field")

    mt, _, _ = field_diff([{"name": "NOTES", "type": "String"}], [])
    check(mt == [("NOTES", "String")], "dict-shaped fields are accepted")

    class _F(object):
        def __init__(self, name, type_):
            self.name, self.type = name, type_

    mt, _, _ = field_diff([_F("NOTES", "String")], [])
    check(mt == [("NOTES", "String")], "arcpy-style Field objects are accepted")

    _, _, tc = field_diff([("A", "double")], [("a", "DOUBLE")])
    check(tc == [], "type comparison is case-insensitive")
    mt, _, _ = field_diff([("GlobalID", "GlobalID"), ("Shape_Area", "Double")], [])
    check(mt == [], "the GlobalID and Shape_Area fields are geodatabase-managed, never diffed")
    mt, _, _ = field_diff([("", "String"), ("NOTES",)], [])
    check(mt == [("NOTES", "")], "a nameless field is skipped and a typeless one kept")

    # --- tracking_field_names ------------------------------------------------ #
    up = tracking_field_names(["OBJECTID", "Shape", "CREATION_USER", "CREATION_DATE",
                               "MODIFY_USER", "MODIFY_DATE"])
    check(up == TRACKING_UPPER, "an existing CREATION_USER set picks the upper style")

    lo = tracking_field_names(["OBJECTID", "Shape", "created_user", "created_date",
                               "last_edited_user", "last_edited_date"])
    check(lo == TRACKING_LOWER, "an existing created_user set picks the lower style")

    check(tracking_field_names([]) == TRACKING_LOWER,
          "with no evidence the Esri default names are used")
    check(tracking_field_names(["OBJECTID", "Shape", "PARCEL_ID"]) == TRACKING_LOWER,
          "unrelated fields do not change the default")

    partial = tracking_field_names(["MODIFY_USER"])
    check(partial == TRACKING_UPPER, "a single upper-style survivor is enough evidence")

    cased = tracking_field_names(["Creation_User", "Creation_Date", "Modify_User"])
    check(cased[0] == "Creation_User", "the existing casing is preserved, not normalised")
    check(cased[3] == "MODIFY_DATE", "a field absent from the dataset falls back to the style name")
    check(len(cased) == 4, "exactly four tracking names are returned")

    class _NF(object):
        def __init__(self, name):
            self.name, self.type = name, "String"

    check(tracking_field_names([_NF("CREATION_USER"), _NF("MODIFY_DATE")]) == TRACKING_UPPER,
          "field objects work for tracking detection too")
    check(tracking_field_names([{"name": "CREATION_USER"}, ("MODIFY_USER", "String"), (), ""])
          == TRACKING_UPPER, "dicts, tuples, an empty tuple and an empty name are all read")
    check(tracking_field_names([{"name": "CREATION_USER"}]) == TRACKING_UPPER,
          "a dict alone is read")
    check(tracking_field_names([("MODIFY_USER", "String")]) == TRACKING_UPPER,
          "a tuple alone is read")
    check(tracking_field_names(["CREATION_USER", "created_user"]) == TRACKING_LOWER,
          "one name of each style is a tie, and a tie keeps the Esri default")
    check(tracking_field_names(["CREATION_USER", "MODIFY_USER", "created_user"])
          == ("CREATION_USER", "CREATION_DATE", "MODIFY_USER", "MODIFY_DATE"),
          "two upper-style names outvote one lower-style name")

    # --- the three header parsers `check` relies on -------------------------- #
    def shp_bytes(shape_type=1, bbox=DEG, records=3, code=SHP_FILE_CODE,
                  version=SHP_VERSION, declare_extra=0, record_size=28):
        # 28: an 8-byte record header + a 20-byte point, unread. A .shx record is 8.
        body = b"\0" * (record_size * records)
        words = (SHP_HEADER + len(body) + declare_extra) // 2
        head = struct.pack(">7i", code, 0, 0, 0, 0, 0, words)
        head += struct.pack("<2i8d", version, shape_type, bbox[0], bbox[1], bbox[2],
                            bbox[3], 0.0, 0.0, 0.0, 0.0)
        return head + body

    def dbf_bytes(fields, records=3, terminator=b"\r", rows=None):
        desc = b"".join(struct.pack("<11sc4xBB14x",
                                    n if isinstance(n, bytes) else n.encode("ascii"),
                                    t.encode("ascii"), ln, dec) for n, t, ln, dec in fields)
        head_len = 32 + len(desc) + len(terminator)
        rec_len = 1 + sum(f[2] for f in fields)
        head = struct.pack("<4BIHH20x", 3, 126, 9, 26, records, head_len, rec_len)
        if rows is not None:        # rows: one list of byte values per record
            body = b"".join(b" " + b"".join(v.ljust(f[2]) for v, f in zip(row, fields))
                            for row in rows)
        else:
            body = b" " * (rec_len * records)
        return head + desc + terminator + body + b"\x1a"

    def dbf_parse(data):
        return parse_dbf_fields(data, len(data))

    good = shp_bytes()
    stype, bbox, empty = parse_shp_header(good[:SHP_HEADER], len(good))
    check((stype, bbox, empty) == (1, DEG, False),
          "a point .shp header yields shape type 1 and its bbox from bytes 36-67")
    check(parse_shp_header(shp_bytes(records=0), SHP_HEADER)[2] is True,
          "a .shp that is only its 100-byte header is empty")
    raises(lambda: parse_shp_header(good[:99], 99), "a .shp shorter than its header is refused",
           needle="99 bytes")
    raises(lambda: parse_shp_header(shp_bytes(code=9995), 184),
           "a file code other than 9994 is refused  <-- pinned defect", needle="9994")
    swapped = struct.pack("<i", SHP_FILE_CODE) + good[4:SHP_HEADER]
    raises(lambda: parse_shp_header(swapped, 184),
           "a little-endian file code is refused: byte 0 is big-endian", needle="9994")
    raises(lambda: parse_shp_header(shp_bytes(version=999), 184),
           "a version other than 1000 is refused", needle="1000")
    raises(lambda: parse_shp_header(shp_bytes(shape_type=2), 184),
           "shape type 2, which no shapefile uses, is refused", needle="shape type")
    raises(lambda: parse_shp_header(shp_bytes(declare_extra=28), 184),
           "a header declaring more bytes than the file holds is refused as truncated",
           needle="truncated")
    raises(lambda: parse_shp_header(shp_bytes(records=0, declare_extra=-2), 184),
           "a header declaring less than 100 bytes is refused", needle="under 100 bytes")
    raises(lambda: parse_shp_header(shp_bytes(bbox=(0.0, float("nan"), 1.0, 1.0)), 184),
           "a NaN in the bounding box is refused", needle="not a real extent")
    inf = float("inf")
    raises(lambda: parse_shp_header(shp_bytes(bbox=(-inf, 0.0, inf, 1.0)), 184),
           "an infinite bounding box is refused, although xmin <= xmax holds  <-- pinned defect",
           needle="not a real extent")
    one = (-82.1, 29.1, -82.1, 29.1)
    check(parse_shp_header(shp_bytes(bbox=one), 184)[1:] == (one, False),
          "a one-point .shp, xmin == xmax and ymin == ymax, is a real extent  <-- pinned defect")
    raises(lambda: parse_shp_header(shp_bytes(bbox=(5.0, 0.0, 1.0, 10.0)), 184),
           "a bounding box with xmin > xmax is refused, ymax apart  <-- pinned defect",
           needle="not a real extent")
    raises(lambda: parse_shp_header(shp_bytes(bbox=(0.0, 5.0, 10.0, 1.0)), 184),
           "a bounding box with ymin > ymax is refused, xmax apart  <-- pinned defect",
           needle="not a real extent")
    check(parse_shp_header(shp_bytes(records=0, bbox=(0.0, 0.0, -1.0, -1.0)),
                           SHP_HEADER)[2] is True,
          "an empty .shp as ArcGIS Pro 3.6 writes it, bbox (0, 0, -1, -1), is empty, "
          "not corrupt  <-- pinned defect")
    check(parse_shp_header(shp_bytes(records=0, bbox=(0.0, float("nan"), 0.0, 0.0)),
                           SHP_HEADER)[2] is True,
          "the bbox of an empty .shp is not read as an extent, so NaN there is no error")

    WGS84 = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,'
             '298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
    SPF = ('PROJCS["NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet",'
           'GEOGCS["GCS_North_American_1983",DATUM["D_North_American_1983",'
           'SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],'
           'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
           'PARAMETER["False_Easting",656166.6666666665],PARAMETER["False_Northing",0.0],'
           'PARAMETER["Central_Meridian",-82.0],PARAMETER["Scale_Factor",0.9999411764705882],'
           'PARAMETER["Latitude_Of_Origin",24.33333333333333],UNIT["Foot_US",0.3048006096012192]]')
    SPF_EPSG = SPF[:-1] + ',AUTHORITY["EPSG","2237"]]'

    check(parse_prj(WGS84) == (0, "Geographic", "GCS_WGS_1984", True),
          "an ESRI-dialect GEOGCS .prj is geographic, named, and states no WKID")
    check(parse_prj(SPF) == (0, "Projected",
                             "NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet", True),
          "an ESRI-dialect PROJCS .prj is projected and named")
    check(parse_prj(SPF_EPSG + "\r\n")[0] == 2237,
          "a root AUTHORITY[\"EPSG\",\"2237\"] gives WKID 2237, trailing newline and all")
    check(parse_prj(WGS84[:-1] + ',AUTHORITY["EPSG",4326]]')[0] == 4326,
          "an unquoted AUTHORITY code is read too")
    nested = SPF[:-2] + ',AUTHORITY["EPSG","9003"]]]'
    check(parse_prj(nested)[0] == 0,
          "an AUTHORITY on a nested UNIT is never taken for the root WKID  <-- pinned defect")
    check(parse_prj("  " + WGS84.replace("GEOGCS", "geogcs").replace("DATUM", "datum"))
          == (0, "Geographic", "GCS_WGS_1984", True),
          "keywords are read case-insensitively, as Pro 3.6 reads them")
    check(parse_prj("") == (0, "", "", False),
          "an empty .prj is UNDEFINED, never assumed geographic  <-- pinned defect")
    check(parse_prj(" \r\n ")[3] is False, "a whitespace-only .prj is undefined too")
    check(parse_prj(None)[3] is False, "no .prj text at all is undefined")
    raises(lambda: parse_prj("hello"), "a .prj that is not WKT is refused", needle="PROJCS")
    SECRET = "dbhost:5432:gis:loader:SyntheticPassw0rd!\n"
    raises(lambda: parse_prj(SECRET),
           "a .prj that is not WKT is never echoed: it may be ~/.pgpass  <-- pinned defect",
           needle="PROJCS or GEOGCS: its 41 characters are not echoed")
    raises(lambda: parse_prj('LOCAL_CS["grid"]'),
           "a LOCAL_CS .prj is refused, not guessed", needle="LOCAL_CS")
    raises(lambda: parse_prj('GEOGCS["GCS_WGS_1984",DATUM['),
           "a truncated .prj is refused, not read from its prefix  <-- pinned defect",
           needle="truncated or corrupt")
    raises(lambda: parse_prj('GEOGCS["GCS_WGS_1984"]]]]garbage'),
           "a .prj that closes early and trails text is refused  <-- pinned defect",
           needle="truncated or corrupt")
    check(parse_prj(WGS84.replace("GCS_WGS_1984", "a]b[,"))[2] == "a]b[,",
          "a bracket inside a quoted name does not count toward the balance")
    WEBM = ('PROJCS["WGS_1984_Web_Mercator_Auxiliary_Sphere",' + WGS84 +
            ',PROJECTION["Mercator_Auxiliary_Sphere"],PARAMETER["False_Easting",0.0],'
            'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",0.0],'
            'PARAMETER["Standard_Parallel_1",0.0],PARAMETER["Auxiliary_Sphere_Type",0.0],'
            'UNIT["Meter",1.0],AUTHORITY["ESRI","102100"]]')
    check(parse_prj(WEBM)[0] == 102100,
          "a root AUTHORITY[\"ESRI\",\"102100\"] gives WKID 102100  <-- pinned defect")
    # Exactly as ArcGIS Pro 3.6 writes SpatialReference(2237, 6360) into a .prj.
    PRO_VCS = (
        'PROJCS["NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet",GEOGCS["GCS_North_'
        'American_1983",DATUM["D_North_American_1983",SPHEROID["GRS_1980",6378137.0,'
        '298.257222101]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.017453292519943295]],'
        'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",656166.66666666651],'
        'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",-82.0],'
        'PARAMETER["Scale_Factor",0.99994117647058822],PARAMETER["Latitude_Of_Origin",'
        '24.333333333333332],UNIT["Foot_US",0.30480060960121924]],VERTCS["NAVD88_height_'
        '(ftUS)",VDATUM["North_American_Vertical_Datum_1988"],PARAMETER["Vertical_Shift",'
        '0.0],PARAMETER["Direction",1.0],UNIT["Foot_US",0.30480060960121924]]')
    check(parse_prj(PRO_VCS) == (0, "Projected",
                                 "NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet", True),
          "a PROJCS followed by a VERTCS, as Pro writes it, is read by its root  "
          "<-- pinned defect")
    raises(lambda: parse_prj(PRO_VCS[:-1]), "a truncated VERTCS is refused",
           needle="truncated or corrupt")
    raises(lambda: parse_prj(PRO_VCS + "x"), "text after the VERTCS is refused",
           needle="truncated or corrupt")
    raises(lambda: parse_prj(WGS84 + ',DATUM["d"]'), "a trailing node that is not a VERTCS "
           "is refused", needle="truncated or corrupt")
    VCS_EPSG = ',VERTCS["v",VDATUM["d"],UNIT["Meter",1.0],AUTHORITY["EPSG","6360"]]'
    check(parse_prj(SPF + VCS_EPSG)[0] == 0,
          "the VERTCS AUTHORITY is never taken for the horizontal WKID")
    check(parse_prj(SPF_EPSG + VCS_EPSG)[0] == 2237,
          "the root AUTHORITY is still read when a VERTCS follows")
    check(parse_prj(WGS84[:-1] + ',AUTHORITY["IGNF","4326"]]')[0] == 0,
          "an AUTHORITY that is neither EPSG nor ESRI gives no WKID")

    # Structure. Each text below is refused, and ArcGIS Pro 3.6 Describe read each one as
    # 'Unknown', factoryCode 0, so a plain arcpy load stamps the target SR over it.
    WGS_TM = SPF.replace('PROJECTION["Transverse_Mercator"],', "")
    # Two .prj files exactly as ArcGIS Pro 3.6 writes them, for WKID 3823 and 23313.
    GEO3D = ('GEOGCS["TWD_1997_3D",DATUM["D_TWD_1997",SPHEROID["GRS_1980",6378137.0,'
             '298.257222101]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.017453292519943295],'
             'LINUNIT["Meter",1.0]]')
    LCC1 = ('PROJCS["NAD_1983_(2011)_ICS_Bloomington_(US_Feet)",GEOGCS["GCS_NAD_1983_2011",'
            'DATUM["D_NAD_1983_2011",SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM['
            '"Greenwich",0.0],UNIT["Degree",0.017453292519943295]],PROJECTION['
            '"Lambert_Conformal_Conic_1SP"],PARAMETER["False_Easting",0.0],PARAMETER['
            '"False_Northing",0.0],PARAMETER["Central_Meridian",-89.0],PARAMETER['
            '"Scale_Factor",1.0],PARAMETER["Latitude_Of_Origin",40.5],UNIT["Foot_US",'
            '0.3048006096012192]]')
    check(parse_prj(GEO3D)[3] is True,
          "a 3D GEOGCS with the LINUNIT Pro writes is defined  <-- pinned defect")
    check(parse_prj(LCC1)[3] is True,
          "a Lambert_Conformal_Conic_1SP .prj as Pro writes it is defined  <-- pinned defect")
    for text, label, needle in (
            ('PROJCS["NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet"]',
             "a name-only PROJCS is not defined  <-- pinned defect", "PROJCS has no GEOGCS"),
            (WGS84.replace("],", "],\n"),
             "WKT with a newline inside is not defined: Pro reads only its first line  "
             "<-- pinned defect", "split across lines"),
            (WGS84.replace("],", "],\r\n"), "nor is WKT split by CRLF", "split across lines"),
            ("\n" + WGS84, "a .prj whose first line is empty, as Pro reads it as Unknown  "
             "<-- pinned defect", "first line is empty"),
            ("\r\n" + WGS84, "a .prj that starts with CRLF  <-- pinned defect",
             "first line is empty"),
            (WGS84 + ',VERTCS["v"]',
             "a junk VERTCS after a valid root is not accepted unread  <-- pinned defect",
             "VERTCS has no VDATUM"),
            ('GEOGCS["GCS_WGS_1984"]', "a name-only GEOGCS is not defined",
             "GEOGCS has no DATUM"),
            ('GEOGCS["GCS_WGS_1984",DATUM["garbage"]]', "a DATUM with no SPHEROID is not defined",
             "DATUM has no SPHEROID"),
            (WGS_TM, "a PROJCS with no PROJECTION is not defined", "PROJCS has no PROJECTION"),
            (SPF.replace("Transverse_Mercator", "Nope_Mercator"),
             "a PROJECTION Pro never writes is not defined", "none of the 77"),
            ('PROJCS["X",PROJECTION["Transverse_Mercator"],UNIT["Foot_US",0.3048006096012192]]',
             "a PROJCS with no GEOGCS is not defined", "PROJCS has no GEOGCS"),
            (WGS84.replace("0.0174532925199433", "0.0"), "a UNIT factor of 0 is not defined",
             "UNIT factor is not positive"),
            (WGS84.replace('"GCS_WGS_1984"', '""'), "a GEOGCS with an empty name is not defined",
             "GEOGCS needs a name"),
            (WGS84.replace("6378137.0", "-6378137.0"), "a negative semi-major axis is not defined",
             "positive semi-major axis"),
            (WGS84.replace('"Greenwich",0.0', '"Greenwich",400.0'),
             "a PRIMEM at 400 degrees is not defined", "PRIMEM longitude"),
            (SPF.replace("PARAMETER[", 'PARAMETER["Bogus",30.0],PARAMETER[', 1),
             "a PARAMETER Pro never writes is not defined", "PARAMETER is named twice"),
            (SPF.replace("24.33333333333333", "100.0"),
             "a latitude PARAMETER of 100 is not defined", "latitude PARAMETER"),
            (SPF.replace('PARAMETER["Central_Meridian",-82.0],', ""),
             "a Transverse_Mercator with no Central_Meridian is not defined",
             "Transverse_Mercator needs PARAMETER Central_Meridian"),
            (SPF + PRO_VCS[PRO_VCS.index(",VERTCS"):].replace("Vertical_Shift", "Bogus"),
             "a VERTCS PARAMETER Pro never writes is not defined", "VERTCS PARAMETER"),
            (WGS84[:-1] + ',UNIT["Degree",0.0174532925199433]]',
             "a GEOGCS with two UNITs is not defined", "more than one UNIT"),
            (SPF.replace("PARAMETER[", 'PARAMETER["False_Easting",1.0],PARAMETER[', 1),
             "a PARAMETER given twice is not defined", "PARAMETER is named twice"),
            (WGS84.replace("6378137.0", "0.0"), "a semi-major axis of 0 is not defined",
             "positive semi-major axis"),
            (WGS84.replace("298.257223563", "-5.0"),
             "a negative inverse flattening is not defined", "flattening of 0 or at least 1"),
            (SPF + PRO_VCS[PRO_VCS.index(",VERTCS"):].replace(
                'PARAMETER["Direction",1.0]', 'PARAMETER["Direction",1.0],'
                'PARAMETER["Direction",1.0]'),
             "a VERTCS PARAMETER given twice is not defined", "VERTCS PARAMETER"),
            (GEO3D.replace('LINUNIT["Meter",1.0]', 'LINUNIT["Meter",0.0]'),
             "a 3D GEOGCS whose LINUNIT factor is 0 is not defined", "LINUNIT factor"),
            (u"\ufeff" + WGS84, "a .prj with a UTF-8 byte-order mark is not defined",
             "byte-order mark"),
            ('GEOGCS["GCS_WGS_1984', "an unclosed quote is refused", "truncated or corrupt")):
        raises(lambda t=text: parse_prj(t), label, needle=needle)
    # Pro read these as defined. fcload accepts them too.
    check(parse_prj(WGS84.replace(",", ", ").replace("],", "],\t").replace("]],", "]],\r"))
          == (0, "Geographic", "GCS_WGS_1984", True),
          "', ', a tab and a lone CR inside the WKT are whitespace, as they are to Pro")
    check(parse_prj(WGS84 + ',VERTCS["WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",'
                    '6378137.0,298.257223563]],PARAMETER["Vertical_Shift",0.0],'
                    'PARAMETER["Direction",1.0],UNIT["Meter",1.0]]')[3] is True,
          "an ellipsoidal-height VERTCS with a DATUM[SPHEROID] is defined")
    OGC_UTM = ('PROJCS["WGS 84 / UTM zone 17N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID['
               '"WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG",'
               '"6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",'
               '0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]],'
               'PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],'
               'PARAMETER["central_meridian",-81],PARAMETER["scale_factor",0.9996],'
               'PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",'
               '1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],'
               'AUTHORITY["EPSG","32617"]]')
    check(parse_prj(OGC_UTM) == (32617, "Projected", "WGS 84 / UTM zone 17N", True),
          "a GDAL-style .prj with AXIS[...,EAST] is defined and gives WKID 32617")
    # ponytail: conservative, not measured danger. Pro reads past a node it does not know;
    # fcload refuses what it cannot verify. Loosen per node if a real .prj trips it.
    raises(lambda: parse_prj(WGS84[:-1] + ',FOO["bar"]]'),
           "a node fcload does not verify is refused, though Pro read this one",
           needle="(GEOGCS holds a node that fcload does not verify)")
    raises(lambda: parse_prj(WGS84[:-1] + ',AUTHORITY["EPSG"]]'),
           "the AUTHORITY refusal names the node that holds it",
           needle="(an AUTHORITY in GEOGCS is not one name and one code)")
    check(parse_prj(WGS84[:-1] + ',AUTHORITY["epsg","4326"]]')[0] == 4326,
          "a lower-case authority name gives the WKID too")

    # ArcGIS Pro 3.6 Describe read each text below as 'Unknown', factoryCode 0, and an
    # earlier build of check printed ACCEPT for every one: the headline disaster.
    GU = 'UNIT["Degree",0.0174532925199433]'
    SPF3D = SPF.replace(GU + "]", GU + ',LINUNIT["Meter",1.0]]')
    check(parse_prj(SPF3D[:-1] + ',LINUNIT["Meter",1.0]]')[3] is True,
          "a 3D PROJCS with a LINUNIT in its GEOGCS and its root, as Pro writes it, is defined")
    raises(lambda: parse_prj(SPF[:-1] + ',LINUNIT["Meter",1.0]]'),
           "a 2D PROJCS with a LINUNIT added is refused, as Pro reads it as Unknown  "
           "<-- pinned defect", needle="PROJCS LINUNIT needs a GEOGCS")
    for text, label, needle in (
            (WGS84.replace(",DATUM", u",\u00a0DATUM"), "an NBSP after a comma", "verify"),
            (WGS84.replace("DATUM[", u"DATUM\u00a0["), "an NBSP before a bracket", "verify"),
            (u"\u00a0" + WGS84, "a leading NBSP", "not echoed"),
            (u"\u3000" + WGS84, "a leading ideographic space", "not echoed"),
            (WGS84.replace(",DATUM", u",\u2003DATUM"), "an em space", "verify"),
            (WGS84.replace(",DATUM", u",\u2028DATUM"), "a U+2028 line separator", "verify"),
            (WGS84.replace(",DATUM", u",\x85DATUM"), "a U+0085 next line", "verify"),
            (WGS84.replace(",DATUM", u",\x1fDATUM"), "a 0x1F unit separator", "verify"),
            (WGS84.replace(",DATUM", u",\x1cDATUM"), "a 0x1C file separator", "verify"),
            (WGS84.replace("0.0174532925199433", "0.017_4532925199433"),
             "a number with an underscore", "UNIT needs a name and 1 number"),
            (WGS84.replace("298.257223563", u"\uff12\uff19\uff18.257223563"),
             "fullwidth digits", "SPHEROID needs a name and 2 numbers"),
            (WGS84.replace('"Greenwich",0.0', u'"Greenwich",\u0660.\u0660'),
             "Arabic-Indic digits", "PRIMEM needs a name and 1 number"),
            (SPF_EPSG[:-1] + ',AUTHORITY["EPSG","2236"]]', "two root AUTHORITY nodes",
             "more than one AUTHORITY"),
            (SPF.replace(GU, 'UNIT["Foot_US",0.3048006096012192]'),
             "a Foot_US UNIT in the GEOGCS of a PROJCS", "GEOGCS UNIT is not Degree")):
        raises(lambda t=text: parse_prj(t), "a .prj with %s is refused, as Pro reads it as Unknown  "
               "<-- pinned defect" % label, needle=needle)
    ONE = "not one name and one code"
    RA = WGS84[:-1] + ",%s]"
    SPH = "298.257223563]"
    # An earlier build of check printed ACCEPT, exit 0, for each text below, and Pro 3.6
    # Describe read each one as 'Unknown', factoryCode 0.
    for text, label, needle in (
            (RA % 'AUTHORITY["EPSG"]', 'a root AUTHORITY["EPSG"] with no code', ONE),
            (RA % 'AUTHORITY["ESRI"]', 'a root AUTHORITY["ESRI"] with no code', ONE),
            (RA % "AUTHORITY[4326]", "a root AUTHORITY[4326] with no name", ONE),
            (RA % 'AUTHORITY["EPSG",""]', "a root AUTHORITY with an empty code", ONE),
            (RA % 'AUTHORITY["",4326]', "a root AUTHORITY with an empty name", ONE),
            (RA % 'AUTHORITY["  ","4326"]', "a root AUTHORITY with a blank name", ONE),
            (RA % 'AUTHORITY["EPSG","  "]', "a root AUTHORITY with a blank code", ONE),
            (WGS84.replace(SPH + "]", SPH + ',AUTHORITY["EPSG"]]'),
             'AUTHORITY["EPSG"] inside the DATUM', ONE),
            (WGS84.replace(SPH, '298.257223563,AUTHORITY["EPSG"]]'),
             'AUTHORITY["EPSG"] inside the SPHEROID', ONE),
            (WGS84.replace("0.0174532925199433]", '0.0174532925199433,AUTHORITY["EPSG"]]'),
             'AUTHORITY["EPSG"] inside the UNIT', ONE),
            (SPF[:-1] + ',AUTHORITY["EPSG"]]', 'AUTHORITY["EPSG"] on a PROJCS root', ONE),
            (SPF.replace('"Transverse_Mercator"]', '"Transverse_Mercator",AUTHORITY["EPSG"]]'),
             'AUTHORITY["EPSG"] inside the PROJECTION', ONE),
            (SPF.replace('"False_Northing",0.0]', '"False_Northing",0.0,AUTHORITY["EPSG"]]'),
             'AUTHORITY["EPSG"] inside a PARAMETER', ONE),
            (WGS84.replace(SPH + "]", SPH + ',AUTHORITY["EPSG","6326"],'
                                            'AUTHORITY["EPSG","6326"]]'),
             "two AUTHORITY nodes in the DATUM", "DATUM has more than one AUTHORITY"),
            (WGS84.replace("298.257223563", "0.5"), "an inverse flattening of 0.5",
             "flattening of 0 or at least 1"),
            (WGS84.replace("298.257223563", "0.9999"), "an inverse flattening of 0.9999",
             "flattening of 0 or at least 1"),
            (WGS84.replace("298.257223563", "0.99"), "an inverse flattening of 0.99",
             "flattening of 0 or at least 1"),
            (WGS84.replace("298.257223563", "0.001"), "an inverse flattening of 0.001",
             "flattening of 0 or at least 1"),
            (SPF.replace("298.257222101", "0.5"),
             "an inverse flattening of 0.5 in the GEOGCS of a PROJCS",
             "flattening of 0 or at least 1"),
            (WGS84 + ',VERTCS["v",VDATUM["d"],PARAMETER["Vertical_Shift"],UNIT["Meter",1.0]]',
             "a VERTCS PARAMETER with no number", "PARAMETER needs a name and 1 number")):
        raises(lambda t=text: parse_prj(t), "a .prj with %s is refused, as Pro reads it as "
               "Unknown  <-- pinned defect" % label, needle=needle)
    ALB_SP100 = ('PROJCS["x",' + WGS84 + ',PROJECTION["Albers"],PARAMETER["False_Easting",'
                 '0.0],PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",-84.0],'
                 'PARAMETER["Standard_Parallel_1",100.0],PARAMETER["Standard_Parallel_2",29.5],'
                 'PARAMETER["Latitude_Of_Origin",24.0],UNIT["Meter",1.0]]')
    VCS = (',VERTCS["v",VDATUM["d"],PARAMETER["Vertical_Shift",0.0],PARAMETER["Direction",'
           '1.0],UNIT["Meter",1.0]]')
    VCS2 = ',VERTCS["v",VDATUM["d"],UNIT["Meter",1.0]]'
    # Each rule below had no assertion that failed when it was deleted. Pro read every text
    # in this loop as 'Unknown' too.
    for text, label, needle in (
            (SPF.replace(',UNIT["Foot_US",0.3048006096012192]]', "]"),
             "a PROJCS with no UNIT of its own", "PROJCS has no UNIT"),
            (SPF.replace('UNIT["Foot_US",0.3048006096012192]', 'UNIT["Foot_US",0.0]'),
             "a PROJCS UNIT factor of 0", "PROJCS UNIT factor is not positive"),
            (SPF3D[:-1] + ',LINUNIT["Meter",0.0]]', "a PROJCS LINUNIT factor of 0",
             "PROJCS LINUNIT factor"),
            (WGS84.replace('DATUM["D_WGS_1984",', "DATUM["), "a DATUM with no name",
             "DATUM needs a name"),
            (SPF.replace("656166.6666666665", "1e999"), "a PARAMETER of 1e999, infinite as a "
             "float", "PARAMETER needs a name and 1 number"),
            (SPF.replace('"False_Northing",0.0', '"False_Northing",nan'), "a PARAMETER of nan",
             "PARAMETER needs a name and 1 number"),
            (WGS84.replace('"Greenwich",0.0', '"Greenwich",-400.0'), "a PRIMEM at -400",
             "PRIMEM longitude"),
            (WGS84.replace('"Greenwich",0.0', '"Greenwich",180.000001'),
             "a PRIMEM just past 180", "PRIMEM longitude"),
            (SPF.replace("24.33333333333333", "-100.0"), "a Latitude_Of_Origin of -100",
             "latitude PARAMETER"),
            (SPF.replace("24.33333333333333", "90.000001"), "a Latitude_Of_Origin just past 90",
             "latitude PARAMETER"),
            (ALB_SP100, "a Standard_Parallel_1 of 100", "latitude PARAMETER"),
            (WGS84.replace("6378137.0,298", "6378137.0 298"),
             "a SPHEROID with no comma between its numbers", "truncated or corrupt"),
            (WGS84.replace('",DATUM', '"DATUM'), "a node with no comma before it",
             "truncated or corrupt"),
            (WGS84.replace(GU, 'UNIT["Degree"]'), "a UNIT with no factor",
             "UNIT needs a name and 1 number"),
            (SPF.replace(GU, 'UNIT["Meter",1.0]'), "a PROJCS whose GEOGCS UNIT is Meter",
             "GEOGCS UNIT is not Degree"),
            (SPF.replace(GU, 'UNIT["Radian",1.0]'), "a PROJCS whose GEOGCS UNIT is Radian",
             "GEOGCS UNIT is not Degree"),
            (WGS84 + VCS.replace('VDATUM["d"]', 'DATUM["d"]'), "a VERTCS DATUM with no SPHEROID",
             "DATUM has no SPHEROID")):
        raises(lambda t=text: parse_prj(t), "%s is refused" % label, needle=needle)
    # Pro read these as defined. fcload refuses them: it cannot verify them. Each pins a
    # rule of the parser that no Pro-Unknown text above reaches.
    for text, label, needle in (
            (WGS84 + VCS.replace(',UNIT["Meter",1.0]', ""), "a VERTCS with no UNIT",
             "VERTCS has no UNIT"),
            (WGS84 + VCS2 + VCS2, "a second VERTCS", "not one VERTCS"),
            (WGS84.replace('"WGS_1984",', '"WGS_1984""x",'), "a string straight after a string",
             "truncated or corrupt"),
            (WGS84.replace('"GCS_WGS_1984",', '"GCS_WGS_1984",,'), "a doubled comma",
             "truncated or corrupt"),
            (WGS84 + ",", "a trailing comma", "truncated or corrupt"),
            (WGS84 + u"\u00a0", "a trailing NBSP", "truncated or corrupt"),
            (WGS84.replace(GU, 'UNIT["Foot_US",0.3048006096012192]'),
             "a lone GEOGCS in Foot_US", "GEOGCS UNIT is not Degree"),
            (WGS84.replace('"Greenwich",0.0', '"Greenwich",0x0'), "a hexadecimal number",
             "PRIMEM needs a name and 1 number"),
            (WGS84.replace('"D_WGS_1984"', '"  "'), "a DATUM named with two spaces",
             "DATUM needs a name"),
            (SPF.replace('UNIT["Foot_US"', 'UNIT["  "'), "a PROJCS UNIT named with two spaces",
             "UNIT needs a name and 1 number"),
            (RA % "AUTHORITY[EPSG,4326]", "an AUTHORITY name that is a bare word", ONE),
            (RA % 'AUTHORITY[4326,"EPSG"]', "an AUTHORITY with its items swapped", ONE),
            (RA % 'AUTHORITY["EPSG","4326","x"]', "an AUTHORITY with three items", ONE),
            (RA % 'AUTHORITY["EPSG","4326",UNIT["x",1.0]]', "a node inside an AUTHORITY", ONE)):
        raises(lambda t=text: parse_prj(t), "%s is refused, though Pro read it" % label,
               needle=needle)
    # Not measured under Pro. Each pins one parser rule that nothing above reaches.
    for text, label, needle in (
            (WGS84[:-1] + 'AUTHORITY[]]', "an empty node with no comma before it",
             "truncated or corrupt"),
            (WGS84.replace("6378137.0", "6_378_137.0"), "an underscore before the point",
             "SPHEROID needs a name and 2 numbers"),
            (WGS84 + "],x", "a stray ] and a word after the root", "truncated or corrupt"),
            (WGS84 + ",5", "a number after the root", "not one VERTCS"),
            (WGS84 + ",VERTCS", "a bare word VERTCS after the root", "not one VERTCS")):
        raises(lambda t=text: parse_prj(t), "%s is refused" % label, needle=needle)
    # Pro read these as defined, and fcload does too: the rules above stop at the boundary.
    for text, label in (
            (WGS84.replace(",DATUM", ",\x0bDATUM"), "a VT between items"),
            (WGS84.replace(",DATUM", ",\x0cDATUM"), "an FF between items"),
            (WGS84.replace("DATUM[", "DATUM ["), "a space before a bracket"),
            (WGS84.replace("DATUM[", "DATUM\t["), "a tab before a bracket"),
            (WGS84 + "\r\n", "a trailing CRLF"),
            (WGS84.replace('"Greenwich",0.0', '"Greenwich",+0.0'), "a number '+0.0'"),
            (WGS84.replace('"Greenwich",0.0', '"Greenwich",0.'), "a number '0.'"),
            (WGS84.replace("0.0174532925199433", ".0174532925199433"), "a number '.0174...'"),
            (WGS84.replace("0.0174532925199433", "1.74532925199433E-2"), "a number '1.7...E-2'"),
            (WGS84.replace('"Greenwich",0.0', '"Greenwich",180.0'), "a PRIMEM of 180"),
            (WGS84.replace('"Greenwich",0.0', '"Greenwich",-180.0'), "a PRIMEM of -180"),
            (SPF.replace("24.33333333333333", "90.0"), "a Latitude_Of_Origin of 90"),
            (SPF.replace("24.33333333333333", "-90.0"), "a Latitude_Of_Origin of -90"),
            (WGS84.replace("298.257223563", "0.0"), "an inverse flattening of 0, a sphere"),
            (SPF.replace(GU, 'UNIT["degree",0.0174532925199433]'), "a GEOGCS UNIT 'degree'"),
            (SPF.replace(GU, 'UNIT["Grad",0.015707963267948967]'), "a GEOGCS UNIT 'Grad'"),
            (SPF.replace(GU, 'UNIT["Gon",0.015707963267948967]'), "a GEOGCS UNIT 'Gon'"),
            (SPF.replace(GU, 'UNIT["Second",4.84813681109536e-06]'), "a GEOGCS UNIT 'Second'"),
            (SPF_EPSG, "one root AUTHORITY"),
            (RA % 'AUTHORITY["IGNF","RGF93G"]', "an AUTHORITY code that is not a number"),
            (RA % 'AUTHORITY["EPSG",-1]', "an AUTHORITY code of -1"),
            (RA % 'AUTHORITY["EPSG",4326.5]', "an AUTHORITY code of 4326.5"),
            (WGS84.replace(SPH + "]", SPH + ',AUTHORITY["EPSG","6326"]]'),
             "one AUTHORITY in the DATUM"),
            (WGS84.replace("298.257223563", "1.0"), "an inverse flattening of 1"),
            (WGS84.replace("298.257223563", "1.0001"), "an inverse flattening of 1.0001")):
        check(parse_prj(text)[3] is True, "%s: defined, as Pro read it" % label)

    FIELDS = [("PARCEL_ID", "C", 20, 0), ("ACRES", "N", 12, 3), ("UNITS", "N", 5, 0),
              ("SURVEYED", "D", 8, 0), ("OK", "L", 1, 0), ("AREA", "F", 19, 11),
              ("BLOB", "M", 10, 0)]
    rows, flds = dbf_parse(dbf_bytes(FIELDS, records=7))
    check(rows == 7, "the .dbf record count is read from bytes 4-7")
    check(flds == [("PARCEL_ID", "String"), ("ACRES", "Double"), ("UNITS", "Integer"),
                   ("SURVEYED", "Date"), ("OK", "Logical"), ("AREA", "Double"),
                   ("BLOB", "M")],
          "dBASE C, N, D, L and F types are named; an unknown letter is kept as-is")
    check(dbf_parse(dbf_bytes([]))[1] == [], "a .dbf with no fields reads as none")
    check([t for _, t in dbf_parse(dbf_bytes(
        [("A", "N", 4, 0), ("B", "N", 10, 0), ("C", "N", 11, 0), ("D", "N", 8, 3),
         ("E", "N", 9, 1), ("F", "F", 13, 3), ("G", "F", 14, 0)]))[1]] ==
          ["SmallInteger", "Integer", "Double", "Single", "Double", "Single", "Double"],
          "a numeric .dbf field is named by its width, as Pro 3.6 ListFields names it  "
          "<-- pinned defect")
    raises(lambda: dbf_parse(b"\x03" * 31), "a .dbf shorter than 32 bytes is refused",
           needle="31 bytes")
    raises(lambda: dbf_parse(dbf_bytes(FIELDS[:1], terminator=b"")),
           "a .dbf header with no 0x0D terminator is refused", needle="terminator")
    raises(lambda: dbf_parse(dbf_bytes([("", "C", 5, 0)])),
           "a .dbf field descriptor with no name is refused", needle="no name")
    short = bytearray(dbf_bytes(FIELDS[:1]))
    short[8:10] = struct.pack("<H", 33)
    raises(lambda: dbf_parse(bytes(short)),
           "a terminator beyond the declared header length is not accepted",
           needle="terminator")
    at_end = bytearray(dbf_bytes(FIELDS[:1]))
    at_end[8:10] = struct.pack("<H", 64)
    raises(lambda: dbf_parse(bytes(at_end)),
           "a terminator at the declared header length, not inside it, is refused  "
           "<-- pinned defect", needle="terminator")
    raises(lambda: dbf_parse(dbf_bytes([("ACRES", "N", 10, 3), ("acres", "C", 10, 0)])),
           "a .dbf that repeats a field name in another case is refused  <-- pinned defect",
           needle="'acres' twice")
    raises(lambda: dbf_parse(dbf_bytes([("acres", "N", 10, 3), ("ACRES", "C", 10, 0)])),
           "and in the other order, lower case first  <-- pinned defect",
           needle="'ACRES' twice")
    raises(lambda: dbf_parse(dbf_bytes([(b" " * 11, "C", 10, 0)])),
           "a .dbf field name of 11 spaces has no name  <-- pinned defect", needle="no name")
    # Two GBK names whose lead bytes differ by 0x20, U+4F60 and U+6EB7: a Latin-1 lower()
    # folds them into one, and field_diff then reports nothing.
    gbk_a = dbf_parse(dbf_bytes([(b"\xc4\xe3", "C", 10, 0)]))[1]
    gbk_b = dbf_parse(dbf_bytes([(b"\xe4\xe3", "C", 10, 0)]))[1]
    mt, ms, _ = field_diff(gbk_a, gbk_b)
    check(len(mt) == 1 and len(ms) == 1,
          "two non-Latin .dbf names are never merged by letter case  <-- pinned defect")
    check(dbf_parse(dbf_bytes([(b"\xc4\xe3", "C", 10, 0), (b"\xe4\xe3", "C", 10, 0)]))[1]
          == [("\\xc4\\xe3", "String"), ("\\xe4\\xe3", "String")],
          "and one .dbf may hold both, each kept as its byte escapes")
    three = dbf_bytes(FIELDS[:1])                    # 65-byte header, 3 records of 21 bytes
    check(dbf_parse(three[:-1])[0] == 3, "a .dbf without the optional 0x1A end byte is read")
    for claimed in (0, 2, 5):
        lie = bytearray(three)
        lie[4:8] = struct.pack("<I", claimed)
        raises(lambda b=bytes(lie): dbf_parse(b),
               "a .dbf header that claims %d records over 3 is refused, as Pro cannot open "
               "it  <-- pinned defect" % claimed, needle="truncated or corrupt")
    raises(lambda: dbf_parse(three[:65 + 21]),
           "a .dbf cut after its first record is refused  <-- pinned defect",
           needle="bytes 4-11 do not fit the 86 bytes the file holds")
    raises(lambda: dbf_parse(three + b"\x1a"), "two bytes past the last record are refused: "
           "only one end byte is allowed", needle="fit the 130 bytes")
    zero = bytearray(dbf_bytes([("NAME", "C", 10, 0)], records=0))
    zero[4:8] = struct.pack("<I", 999)
    zero[10:12] = struct.pack("<H", 0)
    raises(lambda: dbf_parse(bytes(zero)),
           "a .dbf that claims 999 records of 0 bytes is refused: 0 bytes fit any count  "
           "<-- pinned defect", needle="not 1 plus the sum of its field lengths")
    for count, length, label in ((9, 7, "shorter"), (1, 63, "longer")):
        fits = bytearray(three)                      # 3 x 21 bytes == 9 x 7 == 1 x 63
        fits[4:12] = struct.pack("<IHH", count, 65, length)
        raises(lambda b=bytes(fits): dbf_parse(b),
               "a record length %s than the fields is refused, though it fits the file"
               % label, needle="bytes 10-11")
    # A secret read as a header. Bytes 4-11 of this token are b"b7c01d2e".
    TOKEN = b"9f3ab7c01d2e4f5a6b7c8d9e0f1a2b3c\r\n"
    said = []
    for fn, data in ((parse_dbf_fields, TOKEN), (parse_shp_header, TOKEN * 3)):
        try:
            fn(data, len(data))
        except ValueError as exc:
            said.append(str(exc))
    values = struct.unpack("<IHH", TOKEN[4:12]) + struct.unpack(">i", TOKEN[:4])
    check(len(said) == 2 and not any(str(n) in m for n in values for m in said),
          "a .dbf or .shp error names the header bytes, never their values  <-- pinned defect")

    # --- argument parsing ---------------------------------------------------- #
    p = build_parser()

    ns = p.parse_args(["import", "--source", "a.shp", "--workspace", "w.gdb"])
    check(ns.apply is False, "--apply is off by default")
    check(ns.add_missing is False, "--add-missing is off by default")
    check(ns.check_write is False, "--check-write is off by default")
    check(ns.assume_sr is None, "--assume-sr is unset by default")
    check(validate_args(ns) == [], "a minimal valid import parses clean")
    check(validate_args(p.parse_args(["import", "--source", "a.shp", "--workspace", "w.gdb",
                                      "--assume-sr", "4326"])) == [],
          "import --assume-sr 4326 validates clean: the override is reachable")
    check(validate_args(p.parse_args(["chores", "--workspace", "w.gdb", "--target", "T"])) == [],
          "a complete chores command validates clean")

    ns = p.parse_args(["import", "--source", "a.shp", "--workspace", "w.gdb", "--apply"])
    check(ns.apply is True, "--apply turns on when asked")
    refused = False
    try:
        with redirect_stderr(io.StringIO()):
            p.parse_args(["import", "--source", "a.shp", "--workspace", "w.gdb", "--ap"])
    except SystemExit:
        refused = True
    check(refused, "a unique prefix of --apply (--ap) is refused, never read as --apply  "
          "<-- pinned defect")

    for prefix in ("--ap", "--app", "--tar"):
        refused = False
        try:
            with redirect_stderr(io.StringIO()):
                p.parse_args(["loadscar", "--source", "a.shp", prefix])
        except SystemExit:
            refused = True
        check(refused, "loadscar refuses the prefix %s of a flag  <-- pinned defect" % prefix)
    check(validate_args(p.parse_args(["loadscar", "--source", "d.shp"])) == []
          and validate_args(p.parse_args(["loadscar", "--source", "d.shp", "--target",
                                          "f.txt"])) == [],
          "loadscar validates clean with a delivered file, and with a file of names")
    check(validate_args(p.parse_args(["loadscar"])) ==
          ["loadscar requires --source (a .shp file)"], "loadscar without --source is rejected")
    check(validate_args(p.parse_args(["loadscar", "--source", "d.shp", "--apply"])) ==
          ["loadscar is read-only: --apply has nothing to apply"],
          "loadscar --apply is rejected: it only reads")
    check(validate_args(p.parse_args(["loadscar", "--source", "d.shp", "--check-write"])) ==
          ["--check-write probes a workspace, and loadscar has none"],
          "loadscar --check-write gives one error, never the advice to add --apply  "
          "<-- pinned defect")

    check(validate_args(p.parse_args(["import", "--workspace", "w.gdb"])),
          "import without --source is rejected")
    check(validate_args(p.parse_args(["import", "--source", "a.shp"])),
          "import without --workspace is rejected")
    check(validate_args(p.parse_args(["chores", "--workspace", "w.gdb"])),
          "chores without --target is rejected")
    check(validate_args(p.parse_args(["chores", "--target", "T"])),
          "chores without --workspace is rejected")
    check(validate_args(p.parse_args(["diff", "--source", "a.shp", "--workspace", "w.gdb"])),
          "diff without --target is rejected")
    check(len(validate_args(p.parse_args(["diff", "--target", "T"]))) == 2,
          "diff without --source and --workspace names both")
    check(validate_args(p.parse_args([])) == [], "no subcommand and no flags is not an error")

    errs = validate_args(p.parse_args(
        ["import", "--source", "a.shp", "--workspace", "w.gdb", "--add-missing"]))
    check(any("--add-missing" in e for e in errs), "--add-missing on import is rejected")

    errs = validate_args(p.parse_args(
        ["chores", "--workspace", "w.gdb", "--target", "T", "--assume-sr", "2237"]))
    check(any("--assume-sr" in e for e in errs), "--assume-sr on chores is rejected")

    errs = validate_args(p.parse_args(
        ["import", "--source", "a.shp", "--workspace", "w.gdb",
         "--assume-sr", "-1"]))
    check(any("positive WKID" in e for e in errs), "a non-positive --assume-sr is rejected")
    errs = validate_args(p.parse_args(
        ["import", "--source", "a.shp", "--workspace", "w.gdb", "--assume-sr", "0"]))
    check(any("positive WKID" in e for e in errs),
          "--assume-sr 0 is rejected at the flag  <-- pinned defect")

    errs = validate_args(p.parse_args(
        ["import", "--source", "a.shp", "--workspace", "w.gdb", "--check-write"]))
    check(any("That is a write" in e for e in errs),
          "--check-write without --apply is rejected: the probe is a write  <-- pinned defect")
    check(any("Nothing was written" in e for e in errs), "and the rejection says nothing was written")

    check(validate_args(p.parse_args(
        ["import", "--source", "a.shp", "--workspace", "w.gdb",
         "--check-write", "--apply"])) == [],
        "--check-write with --apply is accepted: the write is authorised")

    check(validate_args(p.parse_args(["check", "--source", "a.shp"])) == [],
          "check needs only --source")
    check(any("check requires --source" in e for e in validate_args(p.parse_args(["check"]))),
          "check without --source is rejected")
    check(any("read-only" in e for e in validate_args(
        p.parse_args(["check", "--source", "a.shp", "--apply"]))),
        "check with --apply is rejected: it is read-only")
    check(any("check has none" in e for e in validate_args(
        p.parse_args(["check", "--source", "a.shp", "--check-write", "--apply"]))),
        "check with --check-write is rejected: there is no workspace to probe")
    errs = validate_args(p.parse_args(["check", "--source", "a.shp", "--check-write"]))
    check(len(errs) == 1 and "check has none" in errs[0],
          "check --check-write gives one error, never the advice to add --apply  "
          "<-- pinned defect")
    check(any("--assume-sr" in e for e in validate_args(
        p.parse_args(["check", "--source", "a.shp", "--assume-sr", "4326"]))),
        "--assume-sr on check is rejected")

    check(validate_args(p.parse_args(
        ["diff", "--source", "a.shp", "--workspace", "w.gdb", "--target", "T",
         "--add-missing"])) == [], "--add-missing on diff is allowed")

    check(p.parse_args(["--self-test"]).command is None,
          "--self-test needs no subcommand")

    check(len([a for a in p._actions if a.option_strings and a.dest != "help"]) <= 10,
          "the tool exposes at most ten flags")
    help_text = " ".join(p.format_help().split())
    check("so it needs --apply" in help_text, "--help says the write probe needs --apply")
    check("# no arcpy, read-only" in help_text, "--help says check needs no arcpy")
    check("WHAT IS NEW HERE" not in help_text and "USAGE" not in help_text,
          "--help ends with the usage lines, not the whole module docstring")

    # --- the write paths, driven against a stub arcpy ------------------------ #
    # Without this block the --apply gate and the post-load row-count check have
    # no coverage at all: both live in functions that need a geodatabase.
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
            with redirect_stdout(buf), redirect_stderr(buf):
                rc = fn(argv) if fn is main else fn(p.parse_args(argv))
        finally:
            _ARCPY_OVERRIDE = None
        return rc, buf.getvalue(), stub

    rc, out, stub = run(cmd_import, IMPORT)
    check(rc == 0, "a sane import dry-runs cleanly")
    check(stub.writes() == [], "import without --apply performs NO write call  <-- pinned defect")
    check("DRY RUN" in out, "the dry run says so")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"])
    check(rc == 0, "a sane import with --apply succeeds")
    check("FeatureClassToFeatureClass" in stub.writes(), "--apply actually loads")
    check("rows: 0 before + 3 loaded = 3 expected, 3 found" in out, "the row maths is reported")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], landed=2)
    check(rc == 1, "a short row count after the load FAILS the import  <-- pinned defect")
    check("row count mismatch" in out, "the mismatch is named, not silently accepted")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], landed=4)
    check(rc == 1, "an over-count after the load fails too, not just an under-count")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], dest_exists=True, dest_before=5)
    check(rc == 0 and "Append" in stub.writes(), "an existing target is appended to")
    check("5 before + 3 loaded = 8 expected, 8 found" in out,
          "append counts include the rows already there")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], dest_exists=True, dest_before=5,
                        landed=6)
    check(rc == 1, "a partial append is caught by the same row-count check")

    rc, out, stub = run(cmd_import, IMPORT, src_wkid=0, src_name="Unknown")
    check(rc == 2, "an undefined source is refused, not dry-run-accepted")
    check(stub.writes() == [], "a refused import writes nothing")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_wkid=0, src_name="Unknown")
    check(rc == 2, "--apply does not talk the refusal gate out of it")
    check(stub.writes() == [], "a refused import writes nothing even with --apply")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply", "--assume-sr", "4326"],
                        src_wkid=0, src_name="Unknown")
    check(rc == 0 and "DefineProjection" in stub.writes(),
          "--assume-sr defines the SR on a scratch copy before loading")
    check("source untouched" in out, "the source is not modified in place")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], target_wkid=2237)
    check(rc == 0 and "Project" in stub.writes(),
          "a differing target WKID triggers a reprojection")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply", "--assume-sr", "2237"],
                        target_wkid=2237)
    check(rc == 2 and "applies only to an undefined source" in out and stub.writes() == [],
          "import --apply --assume-sr on a DEFINED source is refused and writes nothing  "
          "<-- pinned defect")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], missing=[stub.dest_path])
    check(rc == 1 and "does not exist after the load" in out,
          "a target that is absent after the load FAILS the import")

    rc, out, stub = run(cmd_import, IMPORT, no_sr=True)
    check(rc == 2 and "'Unknown' (WKID 0" in out,
          "a source Describe with no spatialReference is read as UNDEFINED")

    rc, out, stub = run(cmd_import, IMPORT, src_extent=(nan, nan, nan, nan))
    check(rc == 0 and "sanity check skipped" in out and "GEOGRAPHIC" not in out,
          "import of a source whose extent is NaN is not misdiagnosed")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_wkid=2237, src_type="Projected",
                        src_name="StatePlane_Feet", target_wkid=2237)
    check(rc == 2 and "PROJECTED" in out and stub.writes() == [],
          "import --apply refuses a projected SR over a degree extent, read through "
          "Describe, and writes nothing  <-- pinned defect")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_extent=SP_FEET)
    check(rc == 2 and "GEOGRAPHIC" in out and stub.writes() == [],
          "import --apply refuses a geographic SR over an extent in feet, and writes nothing")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_count=0)
    check(rc == 2 and "holds no features (GetCount 0)" in out and stub.writes() == [],
          "import refuses an empty source, as check does, and writes nothing  "
          "<-- pinned defect")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], target_wkid=0)
    check(rc == 2 and "target dataset spatial reference is UNDEFINED" in out
          and stub.writes() == [],
          "import refuses a feature dataset whose SR is undefined  <-- pinned defect")
    ROOT = ["import", "--source", _StubArcpy.SRC, "--workspace", _StubArcpy.WS, "--apply"]
    rc, out, stub = run(cmd_import, ROOT, target_wkid=0)
    check(rc == 2 and "existing target feature class spatial reference is UNDEFINED" in out
          and stub.writes() == [],
          "import refuses to append into a feature class whose SR is undefined  "
          "<-- pinned defect")
    rc, out, stub = run(cmd_import, ROOT[:-1], target_wkid=0,
                        missing=[os.path.join(_StubArcpy.WS, "out")])
    check(rc == 0 and "DRY RUN" in out and "UNDEFINED" not in out,
          "a new feature class at the workspace root takes the source SR and is accepted")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_wkid=0, src_name="Unknown",
                        target_wkid=0)
    check(rc == 2 and "target dataset spatial reference" in out
          and "source spatial reference is UNDEFINED" in out,
          "an undefined source and an undefined target are both named")

    rc, out, stub = run(cmd_import, IMPORT + ["--name", "PARCELS_NEW", "--apply"],
                        missing=[os.path.join(_StubArcpy.WS, _StubArcpy.DS, "PARCELS_NEW")])
    check("-> PARCELS_NEW" in out and [c[2].get("out_name") for c in stub.calls
                                       if c[0] == "FeatureClassToFeatureClass"] == ["PARCELS_NEW"],
          "--name names the feature class that is written")

    rc, out, stub = run(cmd_import, IMPORT, src_extent=(None, None, None, None))
    check(rc == 0 and "sanity check skipped" in out,
          "an extent whose XMin is None is treated as unavailable")

    rc, out, stub = run(cmd_import, ["import", "--source", _StubArcpy.SRC,
                                     "--workspace", _StubArcpy.WS, "--apply"],
                        dest_path=os.path.join(_StubArcpy.WS, "out"), target_wkid=2237)
    check(rc == 0 and "(WKID 0)" in out and "Project" not in stub.writes(),
          "a NEW feature class at the workspace root has no target WKID and is not "
          "reprojected")
    rc, out, stub = run(cmd_import, ["import", "--source", _StubArcpy.SRC,
                                     "--workspace", _StubArcpy.WS, "--apply"],
                        target_wkid=2237)
    check(rc == 0 and "(WKID 2237)" in out and "no spatial reference of its own" not in out
          and "differs from target WKID 2237" in out,
          "an EXISTING root feature class supplies the target WKID to the verdict  "
          "<-- pinned defect")
    check(stub.writes() == ["Append"],
          "and Append projects into it on the fly, as in 1.0.0, with no Project first")

    def exits(fn, argv, needle, label, **stub_kw):
        raises(lambda: run(fn, argv, **stub_kw), label, exc_type=SystemExit, needle=needle)

    exits(cmd_import, IMPORT, "source not found", "a missing import source stops with its name",
          missing=[_StubArcpy.SRC])
    exits(cmd_import, IMPORT, "target location not found",
          "a missing target dataset stops with its name",
          missing=[os.path.join(_StubArcpy.WS, _StubArcpy.DS)])
    exits(cmd_chores, CHORES, "target not found", "chores on a missing target stops",
          missing=[os.path.join(_StubArcpy.WS, "out")])
    exits(cmd_diff, DIFF, "target not found", "diff on a missing target stops",
          missing=[os.path.join(_StubArcpy.WS, "out")])
    exits(cmd_diff, DIFF, "source not found", "diff on a missing source stops",
          missing=[_StubArcpy.SRC])

    rc, out, stub = run(cmd_chores, CHORES)
    check(rc == 0 and stub.writes() == [], "chores without --apply performs NO write call")
    check("DRY RUN" in out and "ERROR 001332" in out, "the dry run still prints the versioning note")

    rc, out, stub = run(cmd_chores, CHORES + ["--apply"])
    check("AddGlobalIDs" in stub.writes() and "EnableEditorTracking" in stub.writes(),
          "chores --apply does both chores")

    rc, out, stub = run(cmd_chores, CHORES + ["--apply", "--dataset", _StubArcpy.DS],
                        has_gid=True, tracking=True, dest_exists=True)
    check(stub.writes() == [], "chores skips work that is already done")

    rc, out, stub = run(cmd_diff, DIFF)
    check(rc == 0 and stub.writes() == [], "diff --add-missing without --apply adds NO field")
    check("DRY RUN" in out and "+ NOTES" in out, "the dry run names the field it would add")

    rc, out, stub = run(cmd_diff, DIFF + ["--apply"])
    check(rc == 0 and "AddField" in stub.writes(), "diff --add-missing --apply adds the field")
    check("verified: 1 field(s) added" in out, "the addition is verified by re-reading the fields")

    rc, out, stub = run(cmd_diff, DIFF + ["--apply"], add_field_works=False)
    check(rc == 1 and "still missing" in out, "an AddField that did not take is reported as failure")

    rc, out, stub = run(cmd_diff, ["diff", "--source", _StubArcpy.SRC, "--workspace",
                                   _StubArcpy.WS, "--target", "out"])
    check(rc == 0 and stub.writes() == [], "diff without --add-missing never writes")
    rc, out, stub = run(cmd_diff, ["diff", "--source", _StubArcpy.SRC, "--workspace",
                                   _StubArcpy.WS, "--target", "out", "--apply"])
    check(rc == 0 and stub.writes() == [] and "DRY RUN" not in out,
          "diff --apply without --add-missing never writes either  <-- pinned defect")

    rc, out, stub = run(cmd_diff, DIFF + ["--apply", "--dataset", _StubArcpy.DS],
                        dest_exists=True, dest_fields=[("notes", "String", 50), ("ZONING", "String", 10),
                                     ("NOTES2", "Double", 8)],
                        src_fields=[("NOTES", "String", 50), ("NOTES2", "String", 8)])
    check(rc == 0 and "nothing to add" in out and stub.writes() == [],
          "diff with nothing missing adds nothing even with --apply")
    check("- ZONING (String)" in out and "! NOTES2: source String vs target Double" in out,
          "diff prints the reverse gap and the type conflict")

    # --- --check-write: the probe is a write, so it is behind --apply ------- #
    rc, out, stub = run(main, IMPORT + ["--check-write"])
    check(rc == 1 and stub.writes() == [] and stub.calls == [],
          "--check-write alone makes NO arcpy call at all  <-- pinned defect")
    check("That is a write" in out and "Nothing was written" in out,
          "--check-write alone says why it did not probe")

    rc, out, stub = run(main, IMPORT + ["--check-write", "--apply"])
    check(rc == 0 and stub.writes() == ["Delete", "CreateTable", "Delete"],
          "--check-write --apply creates and deletes the probe table, then stops")
    check("writable: w.gdb" in out and "FeatureClassToFeatureClass" not in stub.writes(),
          "the probe reports writable and does not go on to import")

    rc, out, stub = run(main, IMPORT + ["--check-write", "--apply"],
                        missing=[os.path.join(_StubArcpy.WS, PROBE_NAME)])
    check(stub.writes() == ["CreateTable", "Delete"],
          "a probe table that is not already there is not deleted first")

    rc, out, stub = run(main, IMPORT + ["--check-write", "--apply"], create_fails=True)
    check(rc == 1 and "NOT WRITABLE" in out, "a refused CreateTable reports NOT WRITABLE, exit 1")

    rc, out, stub = run(main, IMPORT + ["--check-write", "--apply"], delete_fails=True,
                        missing=[os.path.join(_StubArcpy.WS, PROBE_NAME)])
    check(rc == 1 and "writable: w.gdb" in out and "NOT WRITABLE" not in out,
          "a probe whose cleanup Delete fails still reports the write it made  <-- pinned defect")
    check("LEFT BEHIND" in out and os.path.join(_StubArcpy.WS, PROBE_NAME) in out,
          "and names the probe table it left in the workspace")

    probed, out, stub = run(lambda _ns: check_write(_StubArcpy.WS), [])
    check(probed is None and "NOT PROBED" in out and stub.calls == [],
          "check_write itself refuses without apply=True and calls no arcpy, a second layer")

    # --- main --------------------------------------------------------------- #
    rc, out, _ = run(main, [])
    check(rc == 1 and "usage: fcload" in out, "no subcommand prints help and exits 1")
    rc, out, _ = run(main, ["import"])
    check(rc == 1 and "error: import requires --source" in out,
          "a rejected flag set exits 1 and names the problem")
    rc, out, stub = run(main, IMPORT)
    check(rc == 0 and "fcload import" in out, "main dispatches import")
    exits(main, ["bogus"], "invalid choice",
          "an unknown subcommand is a usage error, exit 1, not the refusal code 2  "
          "<-- pinned defect")
    exits(main, IMPORT + ["--frobnicate"], "unrecognized arguments",
          "an unknown flag is a usage error with a message, exit 1")

    # --- the arcpy import is lazy and explains itself ------------------------ #
    with arcpy_module(None):
        raises(_arcpy, "a missing arcpy stops with the Pro interpreter path",
               exc_type=SystemExit, needle="arcgispro-py3")
    fake = types.ModuleType("arcpy")
    with arcpy_module(fake):
        check(_arcpy() is fake, "an importable arcpy is the one returned")
    check(sys.modules.get("arcpy") is not fake, "the arcpy test seam is put back afterwards")

    with arcpy_module(None):
        spec = importlib.util.spec_from_file_location("fcload_import_probe",
                                                      os.path.abspath(__file__))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    check(mod.__version__ == __version__ and mod.__name__ != "__main__",
          "importing fcload does not import arcpy and does not run the CLI")

    # --- attachments: an import that would drop them says so ----------------- #
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"])
    check(rc == 0 and "attachments" not in out and "WARNING" not in out,
          "a source with no attachments prints no attachment line and no warning")

    rc, out, stub = run(cmd_import, IMPORT, src_attach=3)
    check(rc == 0 and "\n  attachments: 3 attachment rows in the source;" in out
          and "maintainAttachments is True" in out and "WARNING" not in out,
          "a source with attachments reports them, and the setting that copies them")
    check(stub.writes() == [], "reporting attachments in a dry run writes nothing")
    rc, out, stub = run(cmd_import, IMPORT, src_attach=1)
    check("1 attachment row in the source" in out, "one attachment row is not '1 rows'")
    rc, out, stub = run(cmd_import, IMPORT, src_attach=0)
    check("0 attachment rows in the source" in out and "WARNING" not in out,
          "attachments enabled with no rows is reported and is not a warning")
    rc, out, stub = run(cmd_import, IMPORT, src_attach=3, table_missing=True)
    check("an unknown number of attachment rows in the source" in out,
          "an attachment table that cannot be found is an unknown count, never 0")

    rc, out, stub = run(cmd_import, IMPORT, src_attach=3, maintain=False)
    check(rc == 0 and "\n  WARNING    : 3 attachment rows in the source will be DROPPED:" in out
          and "maxSeverity 0" in out and "= True in the session" in out,
          "maintainAttachments False warns before the load that the attachments will be "
          "dropped  <-- pinned defect")
    check(stub.writes() == [], "and the warning dry run still writes nothing")

    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_attach=3)
    check(rc == 0 and "\n  attachments: 3 attachment rows now in the target" in out
          and "WARNING" not in out, "a load that copies the attachments counts them after")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_attach=3, maintain=False)
    check(rc == 0 and "\n  WARNING    : attachments dropped: the target should hold 3 "
          "attachment rows (0 already there + 3 from the source) and holds 0" in out,
          "a load that drops the attachments says so after, and the row count is still "
          "right  <-- pinned defect")
    check("rows: 0 before + 3 loaded = 3 expected, 3 found" in out,
          "the attachment warning follows the row check, never replaces it")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_attach=3, attach_copies=False)
    check(rc == 0 and "attachments dropped" in out and "holds 0" in out,
          "a load that drops them although the setting is True is caught by the count  "
          "<-- pinned defect")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_attach=3, dest_exists=True,
                        dest_before=5, dest_attach=2)
    check("5 attachment rows now in the target" in out and "WARNING" not in out,
          "an append counts the attachment rows the target already held")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_attach=3, dest_exists=True,
                        dest_before=5, dest_attach=2, attach_copies=False)
    check("should hold 5 attachment rows (2 already there + 3 from the source) and holds 2"
          in out, "an append that drops them is caught against what was already there")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_attach=3, table_missing=True)
    check(rc == 0 and "attachments not verified: the target's attachment table could not "
          "be found to count" in out, "an uncountable target table is not called a success")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_attach=3, dest_exists=True,
                        dest_before=5, dest_attach=2, table_missing=True)
    check("attachments not verified" in out, "and not when the target already held some")
    rc, out, stub = run(cmd_import, IMPORT + ["--apply"], src_attach=0)
    check(rc == 0 and "0 attachment rows now in the target" in out and "WARNING" not in out,
          "an attachment table with no rows loses nothing")
    rc, out, stub = run(cmd_import, ["import", "--source", _StubArcpy.SRC, "--workspace",
                                     _StubArcpy.WS, "--apply"], src_attach=2,
                        dest_path=os.path.join(_StubArcpy.WS, "out"))
    check(rc == 0 and "2 attachment rows now in the target" in out,
          "a target at the workspace root finds its attachment table one level up")
    rc, out, stub = run(cmd_import, IMPORT, src_attach=3, extra_rel=True)
    check("3 attachment rows in the source" in out,
          "an attachment relationship class is found among others")
    rc, out, stub = run(cmd_import, IMPORT, src_attach=3, rel_attach=False)
    check("attachment" not in out,
          "a relationship class that is not an attachment one is not an attachment  "
          "<-- pinned defect")
    rc, out, stub = run(cmd_import, IMPORT, src_attach=3, rel_origin="some_other_class")
    check("attachment" not in out,
          "the attachment table itself, where the class is the destination, is not "
          "given attachments  <-- pinned defect")
    check(attachment_plan(2, True)[0][0] == "info" and len(attachment_plan(2, False)) == 2
          and all(lv == "warning" for lv, _ in attachment_plan(2, False)),
          "attachment_plan: True is information, False is two warnings")
    check([attachment_loss(*a) for a in ((0, 0, None), (3, 0, 3), (3, 2, 5), (-1, 0, 4))]
          == [None] * 4, "attachment_loss: nothing lost when the target holds what it should")
    check(all(attachment_loss(*a) for a in ((3, 0, 2), (3, 0, None), (3, 2, 4), (-1, 0, None))),
          "attachment_loss: one row short, no table, and a table short of its prior rows "
          "are all losses")

    # --- check: stdlib shapefile reading, end to end ------------------------- #
    tmp = tempfile.mkdtemp(prefix="fcload-selftest-")
    try:
        def put(name, data):
            path = os.path.join(tmp, name)
            with open(path, "w" if isinstance(data, str) else "wb") as f:
                f.write(data)
            return path

        def shapefile(stem, prj=WGS84, fields=FIELDS[:2], **shp_kw):
            path = put(stem + ".shp", shp_bytes(**shp_kw))
            if prj is not None:
                put(stem + ".prj", prj)
            if fields is not None:
                put(stem + ".dbf", dbf_bytes(fields))
            return path

        def run_check(*argv):
            # stdout is a cp1252 byte stream, as when a Windows run is piped to a log.
            raw = io.BytesIO()
            buf = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")
            with arcpy_module(None), redirect_stdout(buf):
                rc = main(["check"] + list(argv))
            buf.flush()
            return rc, raw.getvalue().decode("ascii")

        def snapshot():
            out = {}
            for n in sorted(os.listdir(tmp)):
                with open(os.path.join(tmp, n), "rb") as f:
                    out[n] = (f.read(), os.path.getmtime(os.path.join(tmp, n)))
            return out

        wgs = shapefile("wgs")
        noprj = shapefile("noprj", prj=None)
        before = snapshot()

        rc, out = run_check("--source", wgs)
        check(rc == 0 and "verdict    : ACCEPT" in out,
              "check accepts a geographic .prj over a degree bbox")
        check("\n  source SR  : 'GCS_WGS_1984' (no WKID stated, Geographic)\n" in out,
              "check prints the .prj name and says no WKID was stated")
        check("rows: 3" in out and "fields: 2" in out, "check reads rows and fields from the .dbf")
        check("reprojection not assessed" in out, "with no --target nothing is said about reprojection")

        rc, out = run_check("--source", noprj)
        check(rc == 2 and "verdict    : REFUSE" in out and "UNDEFINED" in out,
              "check refuses a shapefile with no .prj as UNDEFINED  <-- pinned defect")
        check("never assumed geographic" in out and "Geographic)" not in out,
              "a missing .prj is its own class and is never assumed geographic  <-- pinned defect")
        check("run `fcload import` with --assume-sr WKID" in out,
              "check points the override at import, the one mode that takes it  <-- pinned defect")
        check("\n  source SR  : 'Unknown' (no WKID stated, type unknown)\n" in out,
              "the source SR line of a missing .prj says Unknown and type unknown")

        check(snapshot() == before and sorted(os.listdir(tmp)) == sorted(before),
              "check changes no byte and no timestamp and adds no file")
        check("arcpy is not imported" in out,
              "check ran with arcpy made unimportable, so it never imports arcpy")

        rc, out = run_check("--source", shapefile("empty_prj", prj=""))
        check(rc == 2 and "UNDEFINED" in out, "check refuses an empty .prj as UNDEFINED")
        rc, out = run_check("--source", shapefile("unknown", prj=WGS84.replace(
            "GCS_WGS_1984", "Unknown", 1)))
        check(rc == 2 and "UNDEFINED" in out, "check refuses a .prj named Unknown")
        rc, out = run_check("--source", shapefile("wrongprj", prj=SPF))
        check(rc == 2 and "PROJECTED" in out,
              "check refuses a projected .prj over a degree bbox: the .prj is wrong")
        rc, out = run_check("--source", shapefile("feetgeo", bbox=SP_FEET))
        check(rc == 2 and "GEOGRAPHIC" in out,
              "check refuses a geographic .prj over a bbox in feet")

        spf = shapefile("spf", prj=SPF, bbox=SP_FEET)
        spf_epsg = shapefile("spf_epsg", prj=SPF_EPSG, bbox=SP_FEET,
                             fields=[("parcel_id", "C", 20, 0), ("ACRES", "C", 12, 0),
                                     ("ZONING", "C", 10, 0)])
        rc, out = run_check("--source", spf, "--target", spf_epsg)
        check(rc == 0 and "cannot say whether" in out,
              "a WKID-less source against a WKID target is accepted, reprojection left open")
        check("in source, missing in target (0)" in out and "- ZONING (String)" in out and
              "! ACRES: source Double vs target String" in out,
              "check --target compares the two .dbf field lists")
        check("\n  target SR  : 'NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet' (WKID 2237, "
              "Projected)   extent %s\n" % (SP_FEET,) in out,
              "the target SR line names the target .prj, its WKID and its type")
        rc, out = run_check("--source", spf_epsg, "--target", spf_epsg)
        check(rc == 0 and "source WKID 2237 matches the target" in out,
              "two .prj files with the same root AUTHORITY match")
        rc, out = run_check("--source", spf_epsg, "--target", spf)
        check(rc == 0 and "no target WKID to compare with" in out,
              "a target .prj with no WKID is not compared, not guessed")
        rc, out = run_check("--source", wgs,
                            "--target", shapefile("tgt_noprj", prj=None, bbox=SP_FEET))
        check(rc == 2 and "verdict    : REFUSE" in out and ".prj MISSING" in out
              and "target spatial reference is UNDEFINED" in out and "as-is" not in out,
              "check refuses a target with no .prj as UNDEFINED, never 'loading as-is'  "
              "<-- pinned defect")
        check("\n  target SR  : 'Unknown' (no WKID stated, type unknown)   extent" in out,
              "the target SR line of a missing .prj says Unknown and type unknown")
        rc, out = run_check("--source", wgs,
                            "--target", shapefile("tgt_empty_prj", prj="", bbox=SP_FEET))
        check(rc == 2 and "target spatial reference is UNDEFINED" in out,
              "check refuses a target with an empty .prj as UNDEFINED  <-- pinned defect")
        rc, out = run_check("--source", wgs, "--target", shapefile(
            "tgt_unknown", prj=WGS84.replace("GCS_WGS_1984", "Unknown", 1), bbox=SP_FEET))
        check(rc == 2 and "target spatial reference is UNDEFINED" in out
              and "not assessed" not in out,
              "check refuses a target .prj named Unknown  <-- pinned defect")
        rc, out = run_check("--source", noprj, "--target", shapefile("tgt_noprj2", prj=None))
        check(rc == 2 and "target spatial reference is UNDEFINED" in out
              and "source spatial reference is UNDEFINED" in out,
              "an undefined source and an undefined target are both named by check")
        rc, out = run_check("--source", wgs, "--target", shapefile("tgt_feetgeo", bbox=SP_FEET))
        check(rc == 2 and "target: spatial reference 'GCS_WGS_1984'" in out
              and "GEOGRAPHIC" in out,
              "check refuses a target whose geographic .prj sits over a bbox in feet")
        rc, out = run_check("--source", spf, "--target", shapefile(
            "tgt_fresh", prj=SPF, records=0, bbox=(0.0, 0.0, -1.0, -1.0)))
        check(rc == 0, "an empty target's bbox, as Pro writes it, is not tested against its .prj")
        rc, out = run_check("--source", spf, "--target", shapefile(
            "tgt_nulls", prj=SPF, shape_type=0, bbox=(0.0, 0.0, 0.0, 0.0)))
        check(rc == 0, "nor is the bbox of a target of Null shapes")
        rc, out = run_check("--source", shapefile("nodbf_src", fields=None), "--target", wgs)
        check("not compared, a .dbf is missing" in out and "missing in source" not in out,
              "a source with no .dbf is not diffed as if it had no fields")
        vcs = shapefile("vcs", prj=PRO_VCS, bbox=SP_FEET)
        rc, out = run_check("--source", vcs)
        check(rc == 0 and "verdict    : ACCEPT" in out,
              "check accepts a Pro .prj with a VERTCS, as import does  <-- pinned defect")
        SPF_NAME_ONLY = 'PROJCS["NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet"]'
        rc, out = run_check("--source", shapefile("nameonly", prj=SPF_NAME_ONLY, bbox=SP_FEET))
        check(rc == 2 and "verdict    : REFUSE" in out and "PROJCS has no GEOGCS" in out
              and "as UNDEFINED" in out,
              "check refuses a name-only PROJCS that import reads as Unknown  <-- pinned defect")
        rc, out = run_check("--source", shapefile("pretty", prj=WGS84.replace("],", "],\n")))
        check(rc == 2 and "split across lines" in out,
              "check refuses a pretty-printed .prj that import reads as Unknown  "
              "<-- pinned defect")
        rc, out = run_check("--source", spf, "--target",
                            shapefile("tgt_nameonly", prj=SPF_NAME_ONLY, bbox=SP_FEET))
        check(rc == 2 and "target " in out and "tgt_nameonly.shp" in out
              and "PROJCS has no GEOGCS" in out,
              "check refuses a target .prj that Pro cannot parse  <-- pinned defect")

        real_read = _read

        def denied(path, limit):
            if path.endswith(".prj"):
                raise PermissionError(13, "Permission denied", path)
            return real_read(path, limit)

        globals()["_read"] = denied
        try:
            raises(lambda: run_check("--source", wgs),
                   "an unreadable .prj stops with a message, not a traceback  <-- pinned defect",
                   exc_type=SystemExit, needle="wgs.shp: cannot be read: [Errno 13] Permission")
        finally:
            globals()["_read"] = real_read
        rc, out = run_check("--source", spf, "--target",
                            shapefile("tgt_nodbf", prj=SPF, fields=None, bbox=SP_FEET))
        check("not compared, a .dbf is missing" in out,
              "fields are not compared when a .dbf is missing")
        rc, out = run_check("--source", shapefile("nodbf", fields=None))
        check(rc == 0 and "rows: unknown (no .dbf)" in out,
              "a source with no .dbf is still checked, its rows reported unknown")

        up = shapefile("upper", prj=None)
        put("upper.PRJ", WGS84)
        rc, out = run_check("--source", up)
        check(rc == 0 and "upper.PRJ" in out.replace("upper.prj", "upper.PRJ"),
              "an upper-case .PRJ beside the .shp is found")

        upshp = put("UPSHP.SHP", shp_bytes())
        put("UPSHP.prj", WGS84)
        put("UPSHP.dbf", dbf_bytes(FIELDS[:2]))
        rc, out = run_check("--source", upshp)
        check(rc == 0 and "verdict    : ACCEPT" in out,
              "an upper-case .SHP, as old ArcView and DOS exports name it, is read")
        AUTH1 = SPF[:-1] + ',AUTHORITY["EPSG"]]'
        rc, out = run_check("--source", shapefile("auth1", prj=AUTH1, bbox=SP_FEET))
        check(rc == 2 and ONE in out,
              'check refuses AUTHORITY["EPSG"], which Pro reads as Unknown  <-- pinned defect')
        rc, out = run_check("--source", spf, "--target",
                            shapefile("tgt_auth1", prj=AUTH1, bbox=SP_FEET))
        check(rc == 2 and "target " in out and "tgt_auth1.shp" in out and ONE in out,
              'and refuses it as a target too  <-- pinned defect')
        rc, out = run_check("--source", shapefile("world", bbox=WORLD_3857))
        check(rc == 0 and "verdict    : ACCEPT" in out,
              "check accepts a WGS84 world extent converted from Web Mercator  <-- pinned defect")
        put("rec0.dbf", bytes(zero))
        rc, out = run_check("--source", shapefile("rec0", fields=None))
        check(rc == 2 and "rows: 999" not in out and "bytes 10-11" in out,
              "check refuses a .dbf of 999 records of 0 bytes, never 'rows: 999' and ACCEPT  "
              "<-- pinned defect")
        # tar restores a hard link to a file already in the folder, and os.link needs no
        # admin on Windows, so this link is real on both hosts.
        hard = shapefile("hard", fields=None)
        os.link(put(".token", TOKEN), os.path.join(tmp, "hard.dbf"))
        rc, out = run_check("--source", hard)
        check(rc == 2 and "hard.dbf is a link, so it is not read" in out
              and not any(str(n) in out for n in values) and "b7c01d2e" not in out,
              "check refuses a .dbf hard linked to a secret and prints no byte of it  "
              "<-- pinned defect")
        rc, out = run_check("--source", shapefile("empty", records=0))
        check(rc == 2 and "holds no features" in out and "100-byte header" in out,
              "check refuses an empty shapefile  <-- pinned defect")
        shapefile("norows", fields=None)
        put("norows.dbf", dbf_bytes(FIELDS[:2], records=0))
        rc, out = run_check("--source", os.path.join(tmp, "norows.shp"))
        check(rc == 2 and "the .dbf holds 0 records" in out,
              "check refuses 3 shapes over a .dbf of 0 records, as import refuses GetCount 0  "
              "<-- pinned defect")
        rc, out = run_check("--source", shapefile("wide", fields=[("N1", "N", 18, 0)]),
                            "--target", shapefile("narrow", fields=[("N1", "N", 4, 0)]))
        check(rc == 0 and "! N1: source Double vs target SmallInteger" in out,
              "check --target reports N(18,0) against N(4,0) as a type conflict  "
              "<-- pinned defect")
        rc, out = run_check("--source", shapefile("nulls", shape_type=0))
        check(rc == 2 and "shape type 0, Null" in out,
              "check refuses a shapefile of Null shapes")
        rc, out = run_check("--source", shapefile("badcode", code=1234))
        check(rc == 2 and "9994" in out and "badcode.shp" in out,
              "check refuses a corrupt .shp header and names the file")
        rc, out = run_check("--source", shapefile("trunc", declare_extra=56))
        check(rc == 2 and "truncated" in out, "check refuses a truncated .shp")
        rc, out = run_check("--source", shapefile("badprj", prj="not wkt"))
        check(rc == 2 and "does not start with a WKT1" in out, "check refuses a .prj that is not WKT")
        rc, out = run_check("--source", shapefile("pgpass", prj=SECRET))
        check(rc == 2 and "not echoed" in out and "Passw0rd" not in out,
              "check prints no byte of a .prj that is not WKT  <-- pinned defect")
        # unzip restores symbolic links. islink is patched because a symbolic link needs
        # admin on Windows; the Linux run in the README uses real ones. samedir.dbf stands
        # for a link to .token in its own folder, which a rule on the resolved folder let
        # through.
        real_islink = os.path.islink
        os.path.islink = lambda path: os.path.basename(path) in (
            "lnkprj.prj", "lnkdbf.dbf", "lnkshp.shp", "samedir.dbf") or real_islink(path)
        try:
            rc, out = run_check("--source", shapefile("lnkprj"))
            check(rc == 2 and "lnkprj.prj is a link, so it is not read" in out,
                  "check refuses a .prj that is a link  <-- pinned defect")
            rc, out = run_check("--source", shapefile("lnkdbf"))
            check(rc == 2 and "lnkdbf.dbf is a link" in out and "rows:" not in out,
                  "check refuses a .dbf that is a link, before reading it")
            rc, out = run_check("--source", shapefile("lnkshp"))
            check(rc == 2 and "lnkshp.shp is a link" in out,
                  "and a .shp that is a link: its header numbers are its bytes")
            rc, out = run_check("--source", shapefile("samedir"), "--target", wgs)
            check(rc == 2 and "samedir.dbf is a link" in out and "missing in target" not in out,
                  "check refuses a .dbf linked to another file in its own folder  "
                  "<-- pinned defect")
        finally:
            os.path.islink = real_islink
        rc, out = run_check("--source", shapefile("truncprj", prj='GEOGCS["GCS_WGS_1984",DATUM['))
        check(rc == 2 and "truncated or corrupt" in out, "check refuses a truncated .prj")
        rc, out = run_check("--source", shapefile("cjk", prj=WGS84.replace(
            "GCS_WGS_1984", u"\u5317\u4eac1954").encode("utf-8")))
        check(rc == 0 and "'\\u5317\\u4eac1954'" in out and "verdict    : ACCEPT" in out,
              "a non-ASCII .prj name is printed escaped on a cp1252 pipe, no crash  "
              "<-- pinned defect")
        rc, out = run_check("--source", shapefile("c1src", fields=[(b"CAF\x81", "N", 10, 0)]),
                            "--target", shapefile("c1tgt", fields=[(b"CAF\x81", "C", 10, 0)]))
        check(rc == 0 and "! CAF\\x81: source Integer vs target String" in out,
              "a .dbf field name with a C1 byte is printed escaped, and the diff completes  "
              "<-- pinned defect")
        rc, out = run_check("--source", shapefile("baddbf", fields=[("", "C", 1, 0)]))
        check(rc == 2 and "no name" in out, "check refuses a corrupt .dbf header")
        lie = bytearray(dbf_bytes(FIELDS[:2]))
        lie[4:8] = struct.pack("<I", 0)
        put("dbflie.dbf", bytes(lie))
        rc, out = run_check("--source", shapefile("dbflie", fields=None))
        check(rc == 2 and "truncated or corrupt" in out and "rows: 0" not in out,
              "check refuses a .dbf that claims 0 records over 3, never 'rows: 0' and ACCEPT  "
              "<-- pinned defect")
        put("bigdbf.dbf", dbf_bytes(FIELDS[:1], records=3200))       # 67,266 bytes
        rc, out = run_check("--source", shapefile("bigdbf", fields=None))
        check(rc == 0 and "rows: 3200" in out,
              "a .dbf larger than the 64 KiB header read is sized from the file, not the read")
        rc, out = run_check("--source", wgs, "--target", shapefile("tgtbad", code=1))
        check(rc == 2 and "target" in out and "tgtbad.shp" in out,
              "a corrupt target is refused and named as the target")
        rc, out = run_check("--source", shapefile("tgt_empty_src", records=3),
                            "--target", shapefile("tgt_empty", records=0))
        check(rc == 0, "an empty TARGET is fine: a fresh schema holds no rows yet")

        # --- loadscar: the rules run backwards on a delivered file ------------- #
        SCAR_FIELDS = [("PARCEL_NUM", "C", 20, 0), ("NOTES", "C", 254, 0),
                       ("WHEN_AT", "D", 8, 0), ("AMT", "F", 19, 11), ("CNT", "N", 10, 0)]
        ROW = [b"A1", b"short", b"20260102", b"1.5", b"5"]

        def delivered(stem, fields=SCAR_FIELDS, records=3, values=None, shapes=None,
                      prj=WGS84, dbf=True, shx=True, shp_records=None, **shp_kw):
            n = records if values is None else len(values)
            path = put(stem + ".shp", shp_bytes(records=n if shp_records is None
                                                else shp_records, **shp_kw))
            if shx:
                put(stem + ".shx", shp_bytes(records=n if shapes is None else shapes,
                                             record_size=8, **shp_kw))
            if prj is not None:
                put(stem + ".prj", prj)
            if dbf:
                put(stem + ".dbf", dbf_bytes(fields, records=n, rows=values))
            return path

        def run_scar(*argv):
            raw = io.BytesIO()
            buf = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")
            with arcpy_module(None), redirect_stdout(buf):
                rc = main(["loadscar"] + list(argv))
            buf.flush()
            return rc, raw.getvalue().decode("ascii")

        sc_clean = delivered("sc_clean", values=[ROW, ROW, ROW])
        sc_idx = delivered("sc_idx", shapes=2)
        sc_cut = delivered("sc_cut", values=[ROW, [b"A2", b"x" * 254] + ROW[2:], ROW])
        sc_cuts = delivered("sc_cuts", values=[[b"A", b"x" * 254] + ROW[2:]] * 2)
        sc_noprj = delivered("sc_noprj", prj=None)
        sc_wrong = delivered("sc_wrong", prj=SPF)
        sc_names = put("sc_names.txt", "# the original schema\nOBJECTID\n\nPARCEL_NUMBER\n"
                                       "NOTES\nWHEN_AT\nAMT\nCNT\nDROPPED_FIELD\n")
        secret = put("sc_secret.bin", b"S3cr3tT0ken\x00\x01 host:5432:db:user:S3cr3tPass\n")
        before_scar = snapshot()

        rc, out = run_scar("--source", sc_clean)
        check(rc == 0 and "verdict    : ACCEPT" in out and "no scar found by these rules" in out
              and out.startswith("fcload loadscar (read-only, arcpy is not imported)\n"),
              "loadscar accepts a delivered file with no scar, and says what that is worth")
        check("\n  delivered  : " in out and "\n  shapes     : 3 in the .shx, 3 in the .dbf\n"
              in out, "loadscar prints the delivered file and both counts")
        check("PARCEL_NUM is exactly 10 characters, the longest name a shapefile holds" in out
              and "--target FIELDS.txt" in out,
              "a name of exactly 10 characters is named as possibly cut, with the way to check")
        check("WHEN_AT: a shapefile date holds the day only" in out
              and "AMT, CNT: a null in the original is written as 0" in out
              and "a Double is written with 12 significant digits" in out
              and "losses that leave no trace (named, not detected):" in out,
              "the losses that leave no trace are named as notes, not as scars")

        rc, out = run_scar("--source", sc_idx)
        check(rc == 2 and "the .shx indexes 2 shapes and the .dbf holds 3 records" in out
              and "verdict    : REFUSE" in out,
              "a .shx and a .dbf that disagree on the feature count are a scar, exit 2  "
              "<-- pinned defect")
        rc, out = run_scar("--source", sc_cut)
        check(rc == 2 and "1 value of NOTES fills all 254 characters" in out,
              "one text value that fills all 254 characters is a scar, singular  "
              "<-- pinned defect")
        rc, out = run_scar("--source", sc_cuts)
        check(rc == 2 and "2 values of NOTES fill all 254 characters" in out,
              "two of them are counted, plural")
        rc, out = run_scar("--source", sc_noprj)
        check(rc == 2 and "source spatial reference is UNDEFINED" in out,
              "a delivered file with no .prj is a scar: the undefined rule run backwards")
        rc, out = run_scar("--source", sc_wrong)
        check(rc == 2 and "PROJECTED" in out and "latitude/longitude" in out,
              "a delivered .prj that contradicts the extent is a scar: rule 3 run backwards")

        rc, out = run_scar("--source", sc_clean, "--target", sc_names)
        check(rc == 2 and "field PARCEL_NUMBER was cut to PARCEL_NUM by the 10-character limit"
              in out and "field DROPPED_FIELD has no counterpart in the delivered file" in out
              and "exactly 10 characters" not in out and "OBJECTID" not in out,
              "the original's field names find a cut name and a lost field, and the 10-"
              "character note gives way to them")

        check(snapshot() == before_scar and sorted(os.listdir(tmp)) == sorted(before_scar),
              "loadscar changes no byte and no timestamp and adds no file")

        rc, out = run_scar("--source", delivered("sc_idx1", values=[ROW], shapes=2))
        check(rc == 2 and "indexes 2 shapes and the .dbf holds 1 record:" in out,
              "one record is not '1 records'")
        rc, out = run_scar("--source", delivered("sc_idx2", shapes=1))
        check(rc == 2 and "indexes 1 shape and the .dbf holds 3 records:" in out,
              "one shape is not '1 shapes'")

        for label, kw, text in (
                ("empty", dict(records=0), "the .shp is its 100-byte header alone"),
                ("null", dict(shape_type=0), "shape type 0, Null"),
                ("zero", dict(records=0, shp_records=3, shapes=3), "the .dbf holds 0 records")):
            rc, out = run_scar("--source", delivered("sc_" + label, **kw))
            check(rc == 2 and "the delivered file holds no features (%s)" % text in out,
                  "loadscar refuses a delivered file with no features: %s" % label)
        rc, out = run_scar("--source", delivered("sc_empty_np", records=0, prj=None))
        check(rc == 2 and "source spatial reference is UNDEFINED" not in out,
              "an empty file's extent is not judged against its .prj")

        rc, out = run_scar("--source", delivered("sc_nodbf", dbf=False))
        check(rc == 2 and "no .dbf beside the .shp: the shapefile specification requires it"
              in out and "rows: unknown (no .dbf)" in out,
              "a delivery with no .dbf is a scar")
        rc, out = run_scar("--source", delivered("sc_noshx", shx=False))
        check(rc == 2 and "no .shx beside the .shp" in out
              and "shapes     : unknown in the .shx, 3 in the .dbf" in out
              and "WHEN_AT: a shapefile date" in out and "indexes" not in out,
              "a delivery with no .shx is a scar, and its .dbf is still read")

        put("sc_badshx.shx", shp_bytes(records=3, record_size=8) + b"\0\0\0")
        delivered("sc_badshx", shx=False)
        rc, out = run_scar("--source", os.path.join(tmp, "sc_badshx.shp"))
        check(rc == 2 and "the .shx is not 100 bytes plus 8 for each shape" in out,
              "a .shx that is not 100 bytes plus 8 a shape is refused as corrupt")
        delivered("sc_codeshx", shx=False)
        put("sc_codeshx.shx", shp_bytes(records=3, record_size=8, code=1))
        rc, out = run_scar("--source", os.path.join(tmp, "sc_codeshx.shp"))
        check(rc == 2 and "9994" in out, "a .shx with the wrong file code is refused")
        delivered("sc_lnkshx", shx=False)
        os.link(secret, os.path.join(tmp, "sc_lnkshx.shx"))
        rc, out = run_scar("--source", os.path.join(tmp, "sc_lnkshx.shp"))
        check(rc == 2 and "sc_lnkshx.shx is a link, so it is not read" in out
              and "S3cr3t" not in out,
              "loadscar refuses a .shx hard linked to a secret and prints no byte of it  "
              "<-- pinned defect")

        big = [[b"a"]] * 20000
        big[5] = big[17500] = [b"x" * 254]
        rc, out = run_scar("--source", delivered("sc_big", fields=[("NOTES", "C", 254, 0)],
                                                  values=big))
        check(rc == 2 and "2 values of NOTES fill all 254 characters" in out,
              "a full value in a later 4 MiB chunk is counted with one in the first  "
              "<-- pinned defect")

        rc, out = run_scar("--source", delivered("sc_c1", fields=[(b"CAF\x81", "N", 10, 0)],
                                                  values=[[b"1"]] * 3))
        check(rc == 0 and "CAF\\x81: a null in the original is written as 0 or a sentinel" in out,
              "loadscar prints a .dbf name with a C1 byte escaped on a cp1252 pipe")

        real_scan = scan_text_caps

        def broken_scan(*args):
            raise OSError("stub: the disk went away")

        globals()["scan_text_caps"] = broken_scan
        try:
            raises(lambda: run_scar("--source", sc_clean), "a .dbf that cannot be scanned "
                   "stops with its name", exc_type=SystemExit, needle="cannot be read")
        finally:
            globals()["scan_text_caps"] = real_scan
        rc, out = run_scar("--source", delivered("sc_nowide", fields=SCAR_FIELDS[:1]))
        check(rc == 0, "a .dbf with no 254-character text field is not scanned at all")

        # --- loadscar: the --target file of original names ----------------------- #
        rc, out = run_scar("--source", sc_clean, "--target", put("sc_bad.txt", "NOTES\nhost:5432:"
                                                                  "db:user:S3cr3tPass\n"))
        check(rc == 2 and "line 2 is not a field name" in out and "S3cr3t" not in out
              and "NOTES" not in out.split("verdict")[1], "a --target line that is not a field "
              "name is refused and never echoed  <-- pinned defect")
        os.link(secret, os.path.join(tmp, "sc_link.txt"))
        rc, out = run_scar("--source", sc_clean, "--target", os.path.join(tmp, "sc_link.txt"))
        check(rc == 2 and "sc_link.txt is a link, so it is not read" in out
              and "S3cr3t" not in out, "a --target file hard linked to a secret is refused  "
              "<-- pinned defect")
        raises(lambda: run_scar("--source", sc_clean, "--target", os.path.join(tmp, "nope.txt")),
               "a --target file that is absent stops with its name", exc_type=SystemExit,
               needle="nope.txt: cannot be read")
        raises(lambda: run_scar("--source", sc_clean, "--target", tmp),
               "a --target that is a folder stops, never a traceback", exc_type=SystemExit,
               needle="cannot be read")
        check(read_original_fields(put("sc_ok.txt", "A\r\n  b_2  \n#c\n")) == ["A", "b_2"],
              "read_original_fields trims, skips blank and # lines, and reads CRLF")

        # --- loadscar: the pure parts --------------------------------------------- #
        cols, hl, rl = dbf_layout(dbf_bytes(SCAR_FIELDS))
        check(cols == [("PARCEL_NUM", "C", 20, 1), ("NOTES", "C", 254, 21),
                       ("WHEN_AT", "D", 8, 275), ("AMT", "F", 19, 283), ("CNT", "N", 10, 302)]
              and hl == 32 + 32 * 5 + 1 and rl == 1 + 20 + 254 + 8 + 19 + 10,
              "dbf_layout gives each column's offset inside a record, after the flag byte")
        check(text_cap_hits(b" ab ac a\0 ad", 4, [3, 1]) == [2, 2]
              and text_cap_hits(b" ab ac a\0 ad", 4, [0]) == [1]
              and text_cap_hits(b"", 4, [1]) == [0],
              "text_cap_hits counts records whose byte at a column's end is not blank or NUL")
        PRO_ORIG = ["PARCEL_NUMBER", "PARCEL_NUMBER_2", "PARCEL_NUMBER_3", "OWNER_NAME",
                    "OWNER_NAME_FULL", "NOTES", "WHEN_AT", "AMT", "CNT", "BIGN"]
        PRO_OUT = ["PARCEL_NUM", "PARCEL_N_1", "PARCEL_N_2", "OWNER_NAME", "OWNER_NA_1",
                   "NOTES", "WHEN_AT", "AMT", "CNT", "BIGN"]
        check(field_scars(PRO_ORIG, PRO_OUT) == [
            "field PARCEL_NUMBER was cut to PARCEL_NUM by the 10-character limit",
            "field PARCEL_NUMBER_2 was cut to PARCEL_N_1 by the 10-character limit",
            "field PARCEL_NUMBER_3 was cut to PARCEL_N_2 by the 10-character limit",
            "field OWNER_NAME_FULL was cut to OWNER_NA_1 by the 10-character limit"],
            "field_scars pairs the names exactly as Pro 3.6 cut and numbered them  "
            "<-- pinned defect")
        check(field_scars(PRO_OUT, PRO_OUT) == [] and field_scars(["OBJECTID", "Shape", "A"],
                                                                  ["a"]) == [],
              "field_scars finds nothing when every name is there, in any letter case, and "
              "ignores the managed fields")
        check(field_scars(["LONG_FIELD_NAME"], ["LONG"]) == [
            "field LONG_FIELD_NAME has no counterpart in the delivered file"]
              and field_scars(["ABCDEFGHIJKL"], ["WXYZABC_12"]) == [
            "field ABCDEFGHIJKL has no counterpart in the delivered file"]
              and field_scars(["ACRES"], ["ACRES_1234"]) != [],
              "a name that is not the 10-character form of the original is a lost field")
        check(field_scars(["ABCDEFGH_XYZ"], ["ABCDEFGH_X", "NEW"]) == [
            "field ABCDEFGH_XYZ was cut to ABCDEFGH_X by the 10-character limit"],
              "an extra delivered field is not a scar, and a cut that ends in _X is a cut")
        check(scar_report([("A", "String")], 3, 3, {}, None) == ([], []),
              "scar_report: a plain file has no scar and no note")
        check(scar_report([("A", "String")], None, 3, {}, []) == ([], []),
              "scar_report: no .shx means no shape count to compare")
        check(scar_report([("ABCDEFGHIJ", "String"), ("KLMNOPQRST", "String")], 3, 3, {}, None)
              [1][0].startswith("ABCDEFGHIJ, KLMNOPQRST are exactly 10 characters"),
              "scar_report: two 10-character names read 'are'")
        check(scar_report([("A", "Single")], 3, 3, {}, [])[1] == [
            "A: a null in the original is written as 0 or a sentinel number, and as a space "
            "in a text field, so a 0 here may be a null"], "a Single gets the null note and no Double note")

        raises(lambda: run_scar("--source", os.path.join(tmp, "absent.shp")),
               "loadscar on a missing .shp stops with its name", exc_type=SystemExit,
               needle="absent.shp")

        raises(lambda: run_check("--source", os.path.join(tmp, "absent.shp")),
               "a missing .shp stops with its name", exc_type=SystemExit, needle="absent.shp")
        raises(lambda: run_check("--source", put("x.txt", "x")),
               "a path that is not a .shp stops", exc_type=SystemExit, needle="not a .shp")
    finally:
        shutil.rmtree(tmp)

    print("-" * 68)
    print("%d assertions, %d failed" % (passed[0] + len(failed), len(failed)))
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
def main(argv=None):
    # A .prj or .dbf can hold any byte, and a Windows pipe is cp1252. Printed as-is,
    # one CJK name or C1 byte crashes the run mid-verdict. Escape every non-ASCII
    # character instead, so each host prints the same ASCII lines.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="ascii", errors="backslashreplace")
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
            print("error: %s" % e, file=sys.stderr)
        return 1

    if ns.check_write:
        return 0 if check_write(ns.workspace, ns.apply) else 1

    return {"import": cmd_import, "chores": cmd_chores, "diff": cmd_diff,
            "check": cmd_check, "loadscar": cmd_loadscar}[ns.command](ns)


if __name__ == "__main__":
    sys.exit(main())
