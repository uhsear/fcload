# fcload

Load a dataset into a geodatabase, refusing the imports that corrupt silently.

arcpy will import a source whose spatial reference is undefined without a single warning. It
copies the raw coordinates unchanged, stamps them with the target dataset's spatial reference,
and every check afterwards passes. fcload refuses that import instead.

Measured on ArcGIS Pro 3.6: one point at `(-82.14, 29.19)` in WGS84 degrees, exported into a
file geodatabase feature dataset in WKID 2237 (NAD83 StatePlane Florida West, US ft).

```
BEFORE  .prj present
  source SR   : 'GCS_WGS_1984' (WKID 4326, Geographic)
  stored      : X = 611496.0477   Y = 1765403.3285
AFTER   .prj deleted, fresh process, identical export
  source SR   : 'Unknown' (WKID 0, type 'Unknown')
  maxSeverity : 0     warnings: (none)   errors: (none)   Exists: True   GetCount: 1
  Describe SR : 'NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet' (2237)
  stored      : X = -82.1399      Y = 29.1899
```

That is 1,868,308 feet, or 354 miles, from where the point belongs, reported by nothing.
fcload on the same pair, no `--apply`:

```
  verdict    : REFUSE
    - source spatial reference is UNDEFINED (factoryCode=0, name='Unknown')
    - arcpy would import this at maxSeverity 0 with no warning and stamp the raw
      coordinates with the TARGET spatial reference, silently relocating every feature
```

```
$ python fcload.py --self-test
fcload self-test: no arcpy, no geodatabase, no network
--------------------------------------------------------------------
PASS  the harness records a false check, a missing raise, a wrong exception and a wrong message
PASS  factoryCode 0 is refused  <-- pinned defect
...
PASS  --assume-sr on a DEFINED, consistent source is refused, never ignored  <-- pinned defect
PASS  --assume-sr 2237 on a WGS84 source into a WGS84 target is refused too  <-- pinned defect
...
PASS  an AUTHORITY on a nested UNIT is never taken for the root WKID  <-- pinned defect
...
PASS  a .prj that is not WKT is never echoed: it may be ~/.pgpass  <-- pinned defect
...
PASS  a PROJCS followed by a VERTCS, as Pro writes it, is read by its root  <-- pinned defect
...
PASS  a 3D GEOGCS with the LINUNIT Pro writes is defined  <-- pinned defect
PASS  a Lambert_Conformal_Conic_1SP .prj as Pro writes it is defined  <-- pinned defect
PASS  a name-only PROJCS is not defined  <-- pinned defect
PASS  WKT with a newline inside is not defined: Pro reads only its first line  <-- pinned defect
...
PASS  a junk VERTCS after a valid root is not accepted unread  <-- pinned defect
...
PASS  a .prj with an NBSP after a comma is refused, as Pro reads it as Unknown  <-- pinned defect
...
PASS  a .prj with a number with an underscore is refused, as Pro reads it as Unknown  <-- pinned defect
...
PASS  a .prj with two root AUTHORITY nodes is refused, as Pro reads it as Unknown  <-- pinned defect
PASS  a .prj with a Foot_US UNIT in the GEOGCS of a PROJCS is refused, as Pro reads it as Unknown  <-- pinned defect
...
PASS  two non-Latin .dbf names are never merged by letter case  <-- pinned defect
...
PASS  a .dbf header that claims 0 records over 3 is refused, as Pro cannot open it  <-- pinned defect
...
PASS  --check-write without --apply is rejected: the probe is a write  <-- pinned defect
...
PASS  check --check-write gives one error, never the advice to add --apply  <-- pinned defect
...
PASS  import --apply --assume-sr on a DEFINED source is refused and writes nothing  <-- pinned defect
...
PASS  import --apply refuses a projected SR over a degree extent, read through Describe, and writes nothing  <-- pinned defect
PASS  import --apply refuses a geographic SR over an extent in feet, and writes nothing
PASS  import refuses an empty source, as check does, and writes nothing  <-- pinned defect
PASS  import refuses a feature dataset whose SR is undefined  <-- pinned defect
PASS  import refuses to append into a feature class whose SR is undefined  <-- pinned defect
...
PASS  an EXISTING root feature class supplies the target WKID to the verdict  <-- pinned defect
PASS  and Append projects into it on the fly, as in 1.0.0, with no Project first
...
PASS  diff --apply without --add-missing never writes either  <-- pinned defect
...
PASS  --check-write alone makes NO arcpy call at all  <-- pinned defect
...
PASS  check refuses a shapefile with no .prj as UNDEFINED  <-- pinned defect
...
PASS  check refuses a target with no .prj as UNDEFINED, never 'loading as-is'  <-- pinned defect
PASS  check refuses a target with an empty .prj as UNDEFINED  <-- pinned defect
PASS  check refuses a target .prj named Unknown  <-- pinned defect
...
PASS  check refuses a name-only PROJCS that import reads as Unknown  <-- pinned defect
PASS  check refuses a pretty-printed .prj that import reads as Unknown  <-- pinned defect
PASS  check refuses a target .prj that Pro cannot parse  <-- pinned defect
PASS  an unreadable .prj stops with a message, not a traceback  <-- pinned defect
...
PASS  check prints no byte of a .prj that is not WKT  <-- pinned defect
PASS  check refuses a .prj that is a link  <-- pinned defect
...
PASS  check refuses a .dbf linked to another file in its own folder  <-- pinned defect
...
PASS  a non-ASCII .prj name is printed escaped on a cp1252 pipe, no crash  <-- pinned defect
...
PASS  check refuses a .dbf that claims 0 records over 3, never 'rows: 0' and ACCEPT  <-- pinned defect
...
--------------------------------------------------------------------
396 assertions, 0 failed
```

## Requirements

One file, no dependencies, no config file, no credentials. Which modes need arcpy:

| Mode | arcpy | Runs on |
|---|---|---|
| `--self-test` | no | any Python 3.9+, Windows or Linux |
| `check` | no | any Python 3.9+, Windows or Linux |
| `import`, `chores`, `diff` | yes | the ArcGIS Pro interpreter |
| `--check-write --apply` | yes | the ArcGIS Pro interpreter |

The Pro interpreter is usually
`C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe`. `arcpy` is imported
inside `_arcpy()`, which only the geodatabase functions call. A missing arcpy prints that path.
The self-test asserts that importing `fcload` does not import arcpy, and it runs `check` with
arcpy made unimportable.

The same 396 assertions pass on Windows (Python 3.13.2 and 3.9.25) and on Ubuntu (Python 3.12.3).
The Windows and Ubuntu outputs are identical line for line, apart from Windows line endings.

fcload writes only ASCII to stdout. A `.prj` or `.dbf` can hold any byte, and a Windows pipe or
log file is cp1252, so a raw CJK name or a C1 byte would crash the run halfway through its
verdict. fcload prints each non-ASCII character as a backslash escape instead, for example
`CAF\x81`. The output is then the same on both hosts for hostile input too. The self-test runs
`check` into a cp1252 byte stream to prove it.

## Quick start

```
git clone https://github.com/uhsear/fcload.git
cd fcload
python fcload.py --self-test
```

## What it refuses

1. Source SR **undefined**: `factoryCode 0`, or a name of `Unknown`, `<undefined>`,
   `undefined`, `none`, or empty. For `check`, a missing or empty `.prj` is the same class,
   and so is a `.prj` that ArcGIS Pro cannot parse (rule 8). It is never assumed to be
   geographic. Override `import` with `--assume-sr WKID`, which is
   printed in the verdict. `--assume-sr` on a source whose SR is **defined** is refused, not
   ignored: `--apply` would define the assumed SR over coordinates already in another system.
2. Target SR **undefined**, by the same rule: the `check --target` shapefile, or for `import`
   the feature dataset or the existing feature class that the rows land in. There is no
   override. `--assume-sr` describes the source and cannot fix the target.
3. Source SR **projected** but its whole extent fits inside degree bounds (`|x| <= 180`,
   `|y| <= 90`): the coordinates are lat/long and the `.prj` is wrong.
4. Source SR **geographic** but its extent falls outside them: the coordinates are projected.
5. A source that holds **no features**, in both modes. An empty file's extent says nothing
   about its data.
6. Row count after the load is not `rows before + rows loaded`. Exit 1, not a quiet success.
7. Anything that writes, without `--apply`. That includes `--check-write`, because its probe
   creates and deletes a table. An SR that merely *differs* from the target is not a refusal;
   that is a reprojection, and fcload performs it.
8. `check` only: a `.shp`, `.prj` or `.dbf` header that is corrupt or truncated, and a `.prj`
   without the structure that ArcGIS Pro needs to read it. Pro reads such a `.prj` as `Unknown`,
   factoryCode 0, which is rule 1. The structure is listed under **check** below. A `.dbf` that
   repeats a field name in another case is corrupt, because ArcGIS field names are unique
   without regard to case. With `--target`, these rules apply to the target's files too, and
   rules 3 and 4 apply to the target's own `.prj` and bounding box, unless the target is empty.
   A `.dbf` whose record count does not fit its file size is corrupt too. A `.shp`, `.prj` or
   `.dbf` that is a link is refused before it is read. A `.prj` that is not WKT is refused
   without echoing its text.

## Usage

```
python fcload.py check   --source PARCELS.shp
python fcload.py check   --source PARCELS.shp --target LOADED.shp
python fcload.py import  --source PARCELS.shp --workspace t.gdb --dataset DS
python fcload.py import  --source PARCELS.shp --workspace t.gdb --dataset DS --apply
python fcload.py import  --source noprj.shp   --workspace t.gdb --assume-sr 4326 --apply
python fcload.py import  --source PARCELS.shp --workspace t.gdb --check-write --apply
python fcload.py chores  --workspace t.gdb --target PARCELS --apply
python fcload.py diff    --source PARCELS.shp --workspace t.gdb --target PARCELS --add-missing
```

**check** reads one shapefile with the standard library and feeds the same verdict function
(`sr_verdict`) that `import` uses.
It opens every file read-only and writes nothing. It reads three things:

- The `.shp` main-file header: file code 9994 (big-endian, byte 0), version 1000 and the shape
  type (little-endian, bytes 28-35), and the bounding box as four little-endian doubles at
  bytes 36-67. The declared file length must fit inside the file.
- The `.prj` WKT: the root keyword (`PROJCS` or `GEOGCS`), its name, and a WKID only when one
  `AUTHORITY` (`EPSG` or `ESRI`) closes the root. An `AUTHORITY` on a nested `UNIT` or `DATUM`
  is not the WKID. The `.prj` must also have the structure that ArcGIS Pro needs, below.
- The `.dbf` header (dBASE III): the record count and the field descriptors. With `--target`,
  the two field lists go through the same `field_diff` that `diff` uses. A field name that
  repeats in another case is refused, because `field_diff` would keep only the last one. The
  record count must fit the file: the header length plus the count times the record length
  (bytes 10-11) is the file size, with or without one `0x1A` end byte.

**A `.prj` is defined only when ArcGIS Pro can read it.** Pro 3.6 reads a `.prj` that it
cannot parse as `Unknown`, factoryCode 0. That is the undefined case, and a plain arcpy load
relocates the data. An earlier build of `check` counted a `.prj` as defined when its root had a
name and its brackets closed. Under Pro 3.6, `check` then printed ACCEPT for each of these
files, and `import` printed REFUSE:

- `PROJCS["NAD_1983_StatePlane_Florida_West_FIPS_0902_Feet"]`, a name with nothing inside, over
  a bounding box in feet.
- The valid WGS84 WKT with a line break after each `],`.
- The valid WGS84 WKT followed by `,VERTCS["v"]`.

A user who ran `check` on Linux and then loaded with plain arcpy got the disaster at the top of
this page. `check` now counts a `.prj` as defined only when it has this structure:

- A `GEOGCS` has a name and one each of `DATUM`, `PRIMEM` and `UNIT`. The `DATUM` has a name
  and a named `SPHEROID`, with a semi-major axis above 0 and an inverse flattening of 0 or more.
  The `PRIMEM` is from -180 to 180. The `UNIT` factor is above 0.
- A `PROJCS` has a name, one such `GEOGCS`, one `PROJECTION` and one `UNIT` with a factor above
  0. The `PROJECTION` is one of the 77 that Pro 3.6 writes. The `PARAMETER`s are the ones that
  Pro writes for that projection. None repeats, and each latitude is from -90 to 90.
- A `LINUNIT`, which Pro writes for a 3D system, is checked like a `UNIT`.
- One `VERTCS` can follow the root, as Pro writes `PROJCS[...],VERTCS[...]`. It has a name, a
  `VDATUM` (or, for an ellipsoidal height, a `DATUM` with a `SPHEROID`), and a `UNIT` with a
  factor above 0. Its only parameters are `Vertical_Shift` and `Direction`.
- The WKT is on one line. Pro reads only the first line, so pretty-printed WKT is `Unknown` to
  it. A trailing line break, a lone CR, a tab and `, ` are whitespace to both.
- The file does not start with a UTF-8 byte-order mark. Pro reads such a file as `Unknown`.
- Whitespace between tokens is a space, tab, VT, FF or CR, and nothing else. A number is
  ASCII: an optional sign, digits with an optional point, and an optional exponent.
- The `GEOGCS` `UNIT` is `Degree`, `Grad`, `Gon` or `Second`, in any letter case.
- The root has at most one `AUTHORITY`.

A `.prj` that breaks a rule is refused, and the message names the rule. The first file above,
under Pro and then on Ubuntu:

```
$ python fcload.py import --source spf_minimal.shp --workspace t.gdb --dataset DS
  ...
  source SR  : 'Unknown' (WKID 0, Unknown)
  ...
  verdict    : REFUSE
    - source spatial reference is UNDEFINED (factoryCode=0, name='Unknown')
$ python3 fcload.py check --source spf_minimal.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source spf_minimal.shp: the .prj is not a spatial reference fcload can verify (PROJCS has no GEOGCS). ArcGIS Pro 3.6 reads a .prj it cannot parse as UNDEFINED, 'Unknown' with factoryCode 0
$ python3 fcload.py check --source spf_ok.shp --target spf_minimal.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - target spf_minimal.shp: the .prj is not a spatial reference fcload can verify (PROJCS has no GEOGCS). ArcGIS Pro 3.6 reads a .prj it cannot parse as UNDEFINED, 'Unknown' with factoryCode 0
```

`check` refused the other two files for `its WKT is split across lines` and `VERTCS has no
VDATUM`, and `import` refused both as UNDEFINED. Both modes accepted `spf_ok.shp`, which has
the full StatePlane `.prj`. Windows printed the same `check` lines as Ubuntu.

The rules were measured with ArcGIS Pro 3.6 in two ways:

- Pro wrote a shapefile for each of 7,425 coordinate systems, one for each WKID that
  `arcpy.ListSpatialReferences` lists, and for 30 horizontal and vertical pairs. `check`
  reads all 7,455 of those `.prj` files as defined.
- Pro `Describe` read 242 hand-made `.prj` files. `check` accepts none that Pro read as
  `Unknown`. It refuses 43 that Pro can read, for example an unknown node such as `FOO["bar"]`,
  a `GEOGCS` with no `PRIMEM`, a trailing comma, or a lone `GEOGCS` in feet. Those refusals are
  deliberate: fcload refuses what it cannot verify.

An earlier version of that second claim was false. It counted 146 files and no false ACCEPT.
An audit then found 14 files that Pro reads as `Unknown` and `check` accepted, exit 0. Each is
the valid WGS84 or StatePlane WKT with one change:

- A no-break space, U+2003, U+3000, U+2028, U+0085, `0x1C` or `0x1F` where whitespace goes.
  Python's `\s` and `strip()` count each one as whitespace. Pro does not.
- A number written `0.017_4532925199433`, or in fullwidth or Arabic-Indic digits. Python's
  `float()` reads all three. Pro does not.
- Two `AUTHORITY` nodes on the root. `check` took the last one as the WKID.
- A `Foot_US` `UNIT` in the `GEOGCS` of a `PROJCS`. Pro reads `Meter` and `Radian` there as
  `Unknown` too, and reads `Degree`, `Grad`, `Gon` and `Second`.

`check --target` used the same parser, so a target like these passed too. That is the
undefined-target disaster below. `check` now refuses all 14, and the self-test pins each one.
On Ubuntu, the first of them:

```
$ python3 fcload.py check --source bundle/nbsp.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source bundle/nbsp.shp: the .prj is not a spatial reference fcload can verify (GEOGCS holds a node that fcload does not verify). ArcGIS Pro 3.6 reads a .prj it cannot parse as UNDEFINED, 'Unknown' with factoryCode 0
```

An unreadable `.shp`, `.prj` or `.dbf` stops `check` with exit 1 and names the file. On Ubuntu,
with `chmod 000` on the `.prj`:

```
source src_wgs.shp: cannot be read: [Errno 13] Permission denied: 'src_wgs.prj'
```

`check` is for incoming bundles, and `unzip` on Linux restores a symbolic link from an archive.
An earlier build echoed the first 40 characters of a `.prj` that was not WKT. A `.prj` linked
to a password file therefore printed the password into the run's log. `check` now echoes no
byte of such a file.

The next build refused only a file that resolved outside its own folder, and that was not
enough. A bundle unzipped into a folder that already holds a secret can carry
`roads.dbf -> .token`. That link stays in the folder, so `check` read the token as a dBASE
header. With `--target`, it printed the first 11 bytes of each 32 as a field name, and a
synthetic `S3cr3tT0ken` reached the log. `check` now reads no `.shp`, `.prj` or `.dbf` that is
a link, wherever it points. On Ubuntu, with a synthetic password file in `home/pgpass`, a link
to it as `parcels.prj`, a plain copy of it as `plain.prj`, and `roads.dbf` linked to a
synthetic `.token` beside it:

```
$ ln -sf "$PWD/home/pgpass" bundle/parcels.prj
$ ln -sf .token bundle/roads.dbf
$ python3 fcload.py check --source bundle/parcels.shp --target bundle/pts.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source bundle/parcels.shp: parcels.prj is a link, so it is not read: it could point at any file, such as ~/.pgpass
(exit 2)
$ python3 fcload.py check --source bundle/plain.shp --target bundle/pts.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source bundle/plain.shp: the .prj does not start with a WKT1 PROJCS or GEOGCS: its 41 characters are not echoed
(exit 2)
$ python3 fcload.py check --source bundle/roads.shp --target bundle/pts.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source bundle/roads.shp: roads.dbf is a link, so it is not read: it could point at any file, such as ~/.pgpass
(exit 2)
```

`grep` found no byte of the password or the token in the three logs. The self-test cannot make
a link on Windows without admin rights, so it covers the link rule by patching
`os.path.islink`.

A `.dbf` whose header states the wrong record count is refused too. ArcGIS Pro 3.6 cannot open
one: `GetCount` on a Pro shapefile whose `.dbf` count was patched to 0, 2 or 5 over 3 records
raised `ERROR 000229: Cannot open`, and `import` stopped with that traceback. An earlier build of
`check` printed `rows: 0` and ACCEPT for the first. Now, on Ubuntu:

```
$ python3 fcload.py check --source bundle/counted.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source bundle/counted.shp: the .dbf header declares 0 records of 11 bytes after 65 bytes of header, but the file holds 99 bytes: truncated or corrupt
```

A real run on shapefiles that ArcGIS Pro 3.6 wrote, then the same file with its `.prj` deleted.
This output is from Ubuntu with no arcpy. Windows printed the same lines.

```
$ python3 fcload.py check --source pts.shp
fcload check (read-only, arcpy is not imported)
  source     : pts.shp
  geometry   : Point   rows: 3   fields: 3
  .prj       : pts.prj
  source SR  : 'GCS_WGS_1984' (no WKID stated, Geographic)
  extent     : (-82.4, 29.1, -82.1, 29.4)   (.shp header, bytes 36-67)
  verdict    : ACCEPT
    - spatial reference 'GCS_WGS_1984' (no WKID stated, Geographic) is consistent with its extent
    - no target WKID to compare with: reprojection not assessed
$ python3 fcload.py check --source noprj.shp
fcload check (read-only, arcpy is not imported)
  source     : noprj.shp
  geometry   : Point   rows: 3   fields: 3
  .prj       : MISSING - the spatial reference is UNDEFINED, never assumed geographic
  source SR  : 'Unknown' (no WKID stated, type unknown)
  extent     : (-82.4, 29.1, -82.1, 29.4)   (.shp header, bytes 36-67)
  verdict    : REFUSE
    - source spatial reference is UNDEFINED (factoryCode=0, name='Unknown')
    - arcpy would import this at maxSeverity 0 with no warning and stamp the raw coordinates with the TARGET spatial reference, silently relocating every feature
    - if you know what the coordinates really are, run `fcload import` with --assume-sr WKID
```

On the same files, `import` under the Pro interpreter gave the same two verdicts. A copy of
`pts.shp` with a StatePlane feet `.prj` was refused by both `check` and `import` as PROJECTED
over degree bounds. A Pro shapefile in WKID 2237 with a NAVD88 vertical CS was accepted by
`check`, and `import` read it as WKID 2237.

An empty shapefile from Pro is refused by both modes. Pro writes its bounding box as
`(0.0, 0.0, -1.0, -1.0)`, so `check` reads the length field, not the box, to find it empty.
`import` reads `GetCount 0`. Version 1.0.0 behaved differently. It refused an empty geographic
source for the wrong reason, because `Describe` gives the extent as `(nan, nan, nan, nan)`, and it
accepted an empty projected source. Both are now refused with the same reason:

```
$ python fcload.py import --source empty.shp --workspace x.gdb --dataset DS --apply
fcload import
  source     : empty.shp
  geometry   : Point   rows: 0   fields: 3
  source SR  : 'GCS_WGS_1984' (WKID 4326, Geographic)
  extent     : (nan, nan, nan, nan)
  target     : x.gdb\DS -> empty  (WKID 2237)
  verdict    : REFUSE
    - the source holds no features (GetCount 0): its extent says nothing about where the data is
```

**An undefined target is the same disaster in reverse.** Under Pro 3.6, `Append` of the three
WGS84 points in `pts.shp` into a shapefile in feet whose `.prj` was lost returned
`maxSeverity 0`, no warning and no error. The target then held its own row and the raw degrees:

```
target SR 'Unknown' 0
Append maxSeverity 0 warnings '' errors ''
  row ((500000.0, 1600000.0), 'feet')
  row ((-82.4, 29.1), 'r0')
  row ((-82.1, 29.4), 'r1')
  row ((-82.2, 29.2), 'r2')
```

fcload refuses that pair, and the same load into a geodatabase:

```
$ python fcload.py check --source pts.shp --target tgt_feet.shp
  ...
  target     : tgt_feet.shp   (.prj MISSING)
  target SR  : 'Unknown' (no WKID stated, type unknown)   extent (500000.0, 1600000.0, 500000.0, 1600000.0)
  verdict    : REFUSE
    - target spatial reference is UNDEFINED (factoryCode=0, name='Unknown'): its coordinates are in a system nobody recorded
    - arcpy would load the source beside them unconverted, at maxSeverity 0 with no warning; --assume-sr describes the source and cannot fix the target
    - define the target spatial reference first, then run fcload again
$ python fcload.py import --source pts.shp --workspace x.gdb --name T --apply
  ...
  verdict    : REFUSE
    - existing target feature class spatial reference is UNDEFINED (factoryCode=0, name='Unknown'): its coordinates are in a system nobody recorded
```

Pro was also given a feature dataset created with no spatial reference. `import --dataset` into
it was refused as `target dataset spatial reference is UNDEFINED`, and nothing was written.
A load into the workspace root under a new name is not affected: the new feature class takes
the source's spatial reference.

**import** describes the source, runs the refusal gate, prints the verdict and its evidence,
and stops. With `--apply` it creates (`FeatureClassToFeatureClass`) or appends (`Append`,
`NO_TEST`), then verifies the row count. `--assume-sr` runs `DefineProjection` on a scratch
copy; the source on disk is never modified.

The target WKID comes from the feature dataset, or from the existing feature class that the rows
are appended to. A new feature class at the workspace root has none and takes the source's SR.
An earlier build read only the feature dataset, so an append into a root feature class in WKID
2237 printed `(WKID 0)` and `loading as-is`. The data still landed correctly, because `Append`
projects on the fly. Under Pro 3.6 the same append now prints:

```
  target     : t.gdb -> ROOTFC  (WKID 2237)
  verdict    : ACCEPT
    - spatial reference 'GCS_WGS_1984' (WKID 4326, Geographic) is consistent with its extent
    - source WKID 4326 differs from target WKID 2237: this is a reprojection, not a corruption
  target exists: appending 1 rows with Append (NO_TEST)
```

It stored `(611496.05, 1765403.33)`, the same point as the BEFORE row above.

`--assume-sr` is for an undefined source only. An earlier build ignored it in the verdict for a
defined source, then applied it on `--apply`. Given the WGS84 point and `--assume-sr 2237`, it
printed ACCEPT and stored the raw degrees in a feet dataset, the disaster at the top of this
page. Under Pro 3.6 the same command now stops, exit 2, and writes nothing:

```
$ python fcload.py import --source pts.shp --workspace t.gdb --dataset DS --name A1 --assume-sr 2237 --apply
  ...
  verdict    : REFUSE
    - source spatial reference is DEFINED (WKID 4326, 'GCS_WGS_1984'); --assume-sr applies only to an undefined source
    - --assume-sr 2237 would be stamped over coordinates that are already in another system, silently relocating every feature
    - run again without --assume-sr; a wrong .prj is fixed with Define Projection in ArcGIS Pro, not here
```

**chores** runs `AddGlobalIDs` and `EnableEditorTracking`, each skipped when already present.
Tracking field names differ between geodatabases (`CREATION_USER / CREATION_DATE /
MODIFY_USER / MODIFY_DATE` against Esri's `created_user / created_date / last_edited_user /
last_edited_date`) and the wrong four add a second, duplicate set of columns, so fcload reads
the dataset's own fields and keeps their convention and casing.

**diff** lists fields in the source but not the target, the reverse, and type conflicts, case
insensitively, never reporting geodatabase-managed fields (`OBJECTID`, `Shape`, `GlobalID`,
`Shape_Area`). `--add-missing --apply` adds them with `AddField` and re-reads the field list.

**--check-write --apply** creates a table named `fcload_write_probe` in the workspace, deletes
it, reports `writable` or `NOT WRITABLE`, and stops. If `CreateTable` works but the cleanup
`Delete` fails, for example on a schema lock, fcload prints `writable`, then `LEFT BEHIND` with
the path of the probe table, and exits 1. Version 1.0.0 ran this probe without
`--apply`. A probe that creates a table is a write, so it now needs `--apply`. Without it,
fcload exits 1, makes no arcpy call, and prints:

```
error: --check-write creates and then deletes a scratch table (fcload_write_probe) in the workspace. That is a write, so it runs only with --apply. Nothing was written.
```

That run was made under the Pro interpreter against a scratch file geodatabase. A directory
listing of the `.gdb` was identical before and after. With `--apply`, the same run printed
`writable: x.gdb`, and `ListTables` found no table left behind.

| Flag | Default | Notes |
|---|---|---|
| `--workspace` | required for `import`, `chores`, `diff` | target `.gdb` or `.sde` connection file |
| `--source` | required for `import`, `diff`, `check` | feature class or shapefile; a `.shp` for `check` |
| `--target` | required for `chores`, `diff` | feature class name in the workspace; for `check`, an optional second `.shp` |
| `--dataset` | none, workspace root | target feature dataset |
| `--name` | the source name | output feature class name |
| `--assume-sr WKID` | unset | `import` only, must be positive |
| `--add-missing` | off | `diff` only |
| `--apply` | **off, everything is a dry run** | refused with `check`, which is read-only |
| `--check-write` | off | probe the workspace and stop; needs `--apply` and `import`, `chores` or `diff` |
| `--self-test` | off | the one flag that takes no subcommand |

| Exit | Meaning |
|---|---|
| 0 | Accepted, or the dry run or probe succeeded. |
| 1 | Usage error (argparse's own errors included), a missing or unreadable source or target, a failed load, a failed probe, or a probe table left behind. |
| 2 | Refused. Nothing was written. |

## Configuration

None. No config file, no environment variables, no credentials, no hardcoded WKIDs, owners, or
connection strings. The target WKID is read from the destination feature dataset or the existing
feature class. The only SR you state by hand is `--assume-sr`, echoed in the verdict so it
survives in the run's log.

## What already exists, and what it does well

GDAL reads the same shapefile header and `.prj`, in far more formats than this tool, and it
matches ESRI-dialect WKT to an EPSG code, which `check` cannot. `pyogrio.read_info` (GDAL 3.12.4)
on the files above, abridged:

```
pts.shp       crs='EPSG:4326'           bounds=(-82.4, 29.1, -82.1, 29.4)  features=3
noprj.shp     crs=None                  bounds=(-82.4, 29.1, -82.1, 29.4)  features=3
wrongprj.shp  crs='PROJCS["NAD83 / Florida West (ftUS)",...'
                                        bounds=(-82.4, 29.1, -82.1, 29.4)  features=3
empty.shp     crs='EPSG:4326'           bounds=(0.0, 0.0, -1.0, -1.0)      features=0
```

GDAL reports the facts. It does not refuse. `crs=None` next to degree bounds, and a feet
projection next to degree bounds, are printed without comment. fcload's job is the refusal.

## Why the obvious version is wrong

The guard hand-rolled import scripts usually carry:

```python
if src_wkid != TARGET_WKID and src_wkid != 0:   # reproject, else assume it is fine
```

`wkid == 0` **is** the undefined case, the one measured above, so the guard excludes precisely
the input that corrupts: the author read `0` as "nothing to do" rather than "nobody knows where
this data is", and arcpy reinforces it by reporting `maxSeverity 0`, leaving no error to notice
and no warning to grep for. fcload inverts that. Undefined is the loudest failure, and the only
way past is an explicit, recorded `--assume-sr`.

Each of these mutations was applied to a copy of `fcload.py` and fails the suite. The first
failing line and the number of the 396 assertions that failed are quoted. Windows and Ubuntu
gave the same lines. "1.0.0 --check-write rule back" restores both halves of the 1.0.0 rule:
`validate_args` refuses `--check-write` only together with `--apply`, and `check_write` probes
without asking. "well-bracketed .prj = defined" restores the earlier `check` rule, which took
the root's name and did not check its structure. "folder link rule back" restores the rule
that refused only a link out of the folder. "Unicode whitespace read" restores `\s` and
`strip()`.

```
undefined treated as fine      -> FAIL  factoryCode 0 is refused  <-- pinned defect   30 failed
--apply gate removed           -> FAIL  import without --apply performs NO write call  <-- pinned defect   3 failed
row-count check removed        -> FAIL  a short row count after the load FAILS the import  <-- pinned defect   4 failed
1.0.0 --check-write rule back  -> FAIL  --check-write without --apply is rejected: the probe is a write  <-- pinned defect   12 failed
undefined target accepted      -> FAIL  import refuses a feature dataset whose SR is undefined  <-- pinned defect   7 failed
missing .prj read as WGS84     -> FAIL  check refuses a shapefile with no .prj as UNDEFINED  <-- pinned defect   5 failed
nested AUTHORITY read as WKID  -> FAIL  an AUTHORITY on a nested UNIT is never taken for the root WKID  <-- pinned defect, then the self-test stops, exit 1
truncated .prj read anyway     -> FAIL  a truncated .prj is refused, not read from its prefix  <-- pinned defect (wrong exception IndexError('list index out of range')), then the self-test stops, exit 1
well-bracketed .prj = defined  -> FAIL  a name-only PROJCS is not defined  <-- pinned defect (no error raised)   49 failed
newline rule dropped           -> FAIL  WKT with a newline inside is not defined: Pro reads only its first line  <-- pinned defect   3 failed
junk VERTCS accepted unread    -> FAIL  a junk VERTCS after a valid root is not accepted unread  <-- pinned defect (no error raised)   5 failed
VERTCS tail refused again      -> self-test stops, exit 1: ValueError: the .prj WKT is truncated or corrupt: text follows its root that is not one VERTCS
duplicate .dbf names allowed   -> FAIL  a .dbf that repeats a field name in another case is refused  <-- pinned defect (no error raised)   1 failed
empty import source loaded     -> FAIL  import refuses an empty source, as check does, and writes nothing  <-- pinned defect   1 failed
output not escaped             -> self-test stops, exit 1: UnicodeEncodeError: 'charmap' codec can't encode characters in position 16-17: character maps to <undefined>
--assume-sr on a defined SR    -> FAIL  --assume-sr on a DEFINED, consistent source is refused, never ignored  <-- pinned defect   4 failed
root target WKID ignored       -> FAIL  an EXISTING root feature class supplies the target WKID to the verdict  <-- pinned defect   1 failed
.prj text echoed               -> FAIL  a .prj that is not WKT is never echoed: it may be ~/.pgpass  <-- pinned defect   4 failed
.prj link followed             -> FAIL  check refuses a .prj that is a link  <-- pinned defect   4 failed
folder link rule back          -> FAIL  check refuses a .prj that is a link  <-- pinned defect   4 failed
Unicode whitespace read        -> FAIL  a .prj with an NBSP after a comma is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   10 failed
float() reads any number       -> FAIL  a .prj with a number with an underscore is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   2 failed
two root AUTHORITY allowed     -> FAIL  a .prj with two root AUTHORITY nodes is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   1 failed
GEOGCS unit not checked        -> FAIL  a .prj with a Foot_US UNIT in the GEOGCS of a PROJCS is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   4 failed
.dbf size not checked          -> FAIL  a .dbf header that claims 0 records over 3 is refused, as Pro cannot open it  <-- pinned defect (no error raised)   6 failed
--check-write advice on check  -> FAIL  check --check-write gives one error, never the advice to add --apply  <-- pinned defect   1 failed
usage error exits 2 again      -> FAIL  an unknown subcommand is a usage error, exit 1, not the refusal code 2  <-- pinned defect   2 failed
```

The same audit found refusal rules that no assertion could fail. In the earlier build, each of
these mutations left the self-test at `325 assertions, 0 failed`, and `check` then accepted a
`.prj` that Pro reads as `Unknown`: the `PROJCS` `UNIT` check removed, the `DATUM` name check
removed, the non-finite number filter removed, or `abs()` dropped from the `PRIMEM` or the
latitude rule. With the rule that a node needs its numbers removed, `check` crashed with
`IndexError` instead. `DEGREE_X` widened from 180 to 199 also left 325 of 325, because the
degree test built its boxes from `DEGREE_X` itself. That test now uses the literals 180 and 90,
and each rule has an assertion that fails when the rule is deleted or loosened.

A wider sweep of 139 one-line mutations covers the parsers and each `.prj` structure rule, the
degree-bounds check, the target rule, the link rule, the write gates, the flag checks and the
tracking-name vote. It leaves three alive on Ubuntu, and all three are equivalent.
`pos + 32 >= end` in the `.dbf` loop refuses the same headers one step earlier. `check` passing
`prj_defined=True` changes nothing, because a missing `.prj` also has an empty name, which is
undefined on its own. `\s` in the pattern for the space before a `[` changes nothing, because
the word before it has already taken every character that is not ASCII whitespace. On Windows a
fourth survives: the upper-case `.PRJ` lookup, because NTFS finds `upper.PRJ` under either case.
Some mutants stop the self-test with an exception instead of a `FAIL` line. That is still
exit 1.

The subcommands that need a geodatabase run against a recording stub arcpy in the self-test.

## What it wraps

Everything fcload does to a geodatabase is arcpy: `FeatureClassToFeatureClass`, `Append`,
`AddField`, `ListFields`, `AddGlobalIDs`, `EnableEditorTracking`, `Describe`, plus
`CopyFeatures`, `DefineProjection`, `Project`, `GetCount`, `CreateTable` and `Delete`. arcpy does
all of the work; the only thing claimed as new here is the refusal gate in front of it.

## Limitations

1. It does not validate attribute values. Nulls, bad domain codes, and duplicate keys pass.
2. It cannot tell you the **correct** spatial reference for a source with a missing `.prj`,
   only that one is missing. `--assume-sr` is trusted as stated, never checked against extent.
3. The degree-bounds heuristic false-positives on a local projected grid whose data genuinely
   sits within 180 units of its own origin, and there is no override for that.
4. `Append` runs `NO_TEST`, so a schema mismatch drops fields instead of failing. Run `diff`
   first.
5. It does not register versioning. arcpy raises **ERROR 001332** for a single feature class
   inside an already-versioned dataset; re-register at the dataset level in ArcGIS Pro instead
   (right-click > Manage > Register As Versioned), which `chores` prints rather than faking.
6. `check` reads a WKID only from a root `AUTHORITY`. The `.prj` files ArcGIS Pro writes have
   none, so for them `check` prints `no WKID stated` and does not assess reprojection. The
   degree-bounds refusals still apply. GDAL, above, can name the EPSG code. Pro itself ignores
   the `AUTHORITY` and finds the WKID from the definition. Under Pro 3.6, the StatePlane `.prj`
   with `AUTHORITY["EPSG","4326"]` or `AUTHORITY["EPSG","32617"]` read as WKID 2237, and
   `check` printed the stated code. That changes the reprojection note only, not the verdict.
7. `check` reads WKT1 `PROJCS` and `GEOGCS` only. `COMPD_CS`, `LOCAL_CS` and WKT2 are refused,
   not guessed. Pro 3.6 writes WKT1 into every `.prj` measured above, including the systems
   whose `exportToString` is WKT2. A trailing `VERTCS` is checked for structure but not read.
   `check` refuses some `.prj` files that Pro can read (see **check**). `check` accepts a
   complete custom `.prj` that states no WKID. `import` refuses the same file as UNDEFINED,
   because `Describe` gives it factoryCode 0 (limitation 12). Under Pro 3.6, a `GEOGCS`
   renamed to `GCS_Mine` got ACCEPT from `check` and REFUSE from `import`.
8. `check` reads headers, not records. It trusts the header bounding box and does not read the
   `.shx` or the `.cpg`. The `.dbf` record count is checked against the `.dbf` file size, not
   against the number of shapes in the `.shp`. A `.prj` is decoded as UTF-8, and a UTF-16
   `.prj` is refused as not WKT. A `.dbf` field name keeps each byte above `0x7F` as an escape such as `\xc4`, because
   without the `.cpg` its letter case is unknown. Letter case is ignored for ASCII letters only.
   Two names that differ only in a non-ASCII letter's case are reported as two fields, which
   is a false alarm, never a false match. Non-ASCII characters print as backslash escapes.
9. `check` names `.dbf` types from the dBASE letter: `C` String, `D` Date, `L` Logical, and `N`
   or `F` Integer when it has no decimals, else Double. arcpy can name the same field
   differently, so compare `check` output with `check` output. arcpy also counts `FID` and
   `Shape`, so `import` showed `fields: 5` where `check` showed `fields: 3`.
10. `--check-write --apply` deletes a table already named `fcload_write_probe` before it
    probes. The `NOT WRITABLE` and `LEFT BEHIND` paths are covered by the stub arcpy only. No
    read-only or locked workspace was probed for real.
11. An empty source is refused, so fcload cannot create an empty feature class to hold a
    schema. Use ArcGIS Pro for that.
12. The target rule for `import` reads only the target's spatial reference, not its extent.
    A target feature class with a defined but wrong spatial reference is not detected there.
    `factoryCode 0` counts as undefined for a target as for a source, so a custom spatial
    reference with no WKID is refused too.
13. `import --dataset` with a source in another SR runs `Project` with no geographic
    transformation, as 1.0.0 did. Under Pro 3.6, the WGS84 point loaded into a WKID 2237
    dataset was stored at `(611497.11, 1765401.34)`. `Append` into a root feature class in
    WKID 2237 stored `(611496.05, 1765403.33)`, about 2.3 ft away. For a datum change that
    matters, project the source in ArcGIS Pro with the transformation you choose, then load it.
14. The link rule sees symbolic links only, so a hard link passes it. A `.dbf` hard linked
    to a file elsewhere is read, and a field name from it can print. A hard-linked `.prj` is
    read too, but not echoed. On Ubuntu, `zip` then `unzip` of a hard-linked pair restored two
    separate files, each with a link count of 1.

## Contributing

Issues and pull requests welcome. `python fcload.py --self-test` must stay green on a stock
Python 3.9+ with no Esri software installed, and new decision logic belongs in a pure function
so the suite reaches it. Branch coverage of `fcload.py` under `--self-test` is 100 percent.
Nothing here has ever touched a production geodatabase or AGOL.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.

## Related

Other single-file tools in this portfolio that pair with this one:

- [safe-republish](https://github.com/uhsear/safe-republish) - the refusal that should sit in front of a truncate and append
- [gdbxray](https://github.com/uhsear/gdbxray) - what the destination geodatabase actually holds, subtypes and attribute rules included
