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

Version 1.2.0 adds two more checks of the same kind. `import` now warns when a load drops
attachments. `loadscar` reads a shapefile that somebody delivered and looks for what the write
that made it ate. Both are below.

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
PASS  a world extent converted from Web Mercator, x one ULP past 180, is degrees  <-- pinned defect
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
PASS  a .prj whose first line is empty, as Pro reads it as Unknown  <-- pinned defect
...
PASS  a junk VERTCS after a valid root is not accepted unread  <-- pinned defect
...
PASS  a .prj with an NBSP after a comma is refused, as Pro reads it as Unknown  <-- pinned defect
...
PASS  a .prj with a number with an underscore is refused, as Pro reads it as Unknown  <-- pinned defect
...
PASS  a .prj with two root AUTHORITY nodes is refused, as Pro reads it as Unknown  <-- pinned defect
PASS  a .prj with a Foot_US UNIT in the GEOGCS of a PROJCS is refused, as Pro reads it as Unknown  <-- pinned defect
PASS  a .prj with a root AUTHORITY["EPSG"] with no code is refused, as Pro reads it as Unknown  <-- pinned defect
...
PASS  a .prj with an inverse flattening of 0.5 is refused, as Pro reads it as Unknown  <-- pinned defect
...
PASS  a .dbf that repeats a field name in another case is refused  <-- pinned defect
PASS  and in the other order, lower case first  <-- pinned defect
...
PASS  two non-Latin .dbf names are never merged by letter case  <-- pinned defect
...
PASS  a .dbf header that claims 0 records over 3 is refused, as Pro cannot open it  <-- pinned defect
...
PASS  a .dbf that claims 999 records of 0 bytes is refused: 0 bytes fit any count  <-- pinned defect
...
PASS  a .dbf or .shp error names the header bytes, never their values  <-- pinned defect
...
PASS  loadscar refuses the prefix --ap of a flag  <-- pinned defect
...
PASS  loadscar --check-write gives one error, never the advice to add --apply  <-- pinned defect
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
PASS  maintainAttachments False warns before the load that the attachments will be dropped  <-- pinned defect
...
PASS  a load that drops the attachments says so after, and the row count is still right  <-- pinned defect
...
PASS  a relationship class that is not an attachment one is not an attachment  <-- pinned defect
PASS  the attachment table itself, where the class is the destination, is not given attachments  <-- pinned defect
...
PASS  check refuses a shapefile with no .prj as UNDEFINED  <-- pinned defect
...
PASS  check refuses a target with no .prj as UNDEFINED, never 'loading as-is'  <-- pinned defect
...
PASS  check refuses a target with an empty .prj as UNDEFINED  <-- pinned defect
PASS  check refuses a target .prj named Unknown  <-- pinned defect
...
PASS  check refuses a name-only PROJCS that import reads as Unknown  <-- pinned defect
PASS  check refuses a pretty-printed .prj that import reads as Unknown  <-- pinned defect
PASS  check refuses a target .prj that Pro cannot parse  <-- pinned defect
PASS  an unreadable .prj stops with a message, not a traceback  <-- pinned defect
...
PASS  check refuses AUTHORITY["EPSG"], which Pro reads as Unknown  <-- pinned defect
...
PASS  check accepts a WGS84 world extent converted from Web Mercator  <-- pinned defect
...
PASS  check refuses a .dbf hard linked to a secret and prints no byte of it  <-- pinned defect
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
PASS  check refuses a source with no .dbf, as loadscar does, never ACCEPT on a file it could not read  <-- pinned defect
...
PASS  a .shx and a .dbf that disagree on the feature count are a scar, exit 2  <-- pinned defect
PASS  one text value that fills all 254 characters is a scar, singular  <-- pinned defect
...
PASS  loadscar refuses a .shx hard linked to a secret and prints no byte of it  <-- pinned defect
PASS  a full value in a later 4 MiB chunk is counted with one in the first  <-- pinned defect
...
PASS  a --target line that is not a field name is refused and never echoed  <-- pinned defect
PASS  a --target file hard linked to a secret is refused  <-- pinned defect
PASS  a --target file with no field name is refused, never ACCEPT: zero bytes  <-- pinned defect
PASS  a --target file saved UTF-8 with a BOM is read, not refused as 'not a field name'  <-- pinned defect
PASS  a hosted layer's Shape__Area and Shape__Length, cut by the writer, are system fields and no scar  <-- pinned defect
...
PASS  field_scars pairs the names exactly as Pro 3.6 cut and numbered them  <-- pinned defect
...
--------------------------------------------------------------------
551 assertions, 0 failed
```

The block is abridged: each `...` stands for PASS lines left out. Every PASS line quoted appears
verbatim in the live output, and the footer is the live footer.

## Requirements

One file, no dependencies, no config file, no credentials. Which modes need arcpy:

| Mode | arcpy | Runs on |
|---|---|---|
| `--self-test` | no | any Python 3.9+, Windows or Linux |
| `check` | no | any Python 3.9+, Windows or Linux |
| `loadscar` | no | any Python 3.9+, Windows or Linux |
| `import`, `chores`, `diff` | yes | the ArcGIS Pro interpreter |
| `--check-write --apply` | yes | the ArcGIS Pro interpreter |

The Pro interpreter is usually
`C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe`. `arcpy` is imported
inside `_arcpy()`, which only the geodatabase functions call. A missing arcpy prints that path.
The self-test asserts that importing `fcload` does not import arcpy, and it runs `check` with
arcpy made unimportable.

551 assertions pass on every host that ran them: Windows on Python 3.13.2, 3.12, 3.10.20 and
3.9.25, and Ubuntu on Python 3.12.3. The counts are equal because no assertion depends on the
host. The symbolic link rule is covered by patching `os.path.islink`, and hard links, which need
no admin rights, are made for real on both. Branch coverage under
`coverage run --branch fcload.py --self-test` is 100 percent on Windows: 1,921 statements and
496 branches, none missed. `pyflakes` is clean, and `python -W error` compiles the file on
both hosts. The shapefiles and geodatabases in the runs below were made by ArcGIS Pro 3.6 on
Windows. The Ubuntu host has no arcpy, so the geodatabase paths ran there against the stub only.
On the same Windows-made shapefiles, `loadscar` printed the same lines on Windows and on
Ubuntu. A `diff` of the two logs was empty.

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
   Each bound allows `1e-9` degrees of floating-point rounding. A WGS84 world layer converted
   from Web Mercator has `xmax = 180.00000000000003`, one step past 180. An earlier build
   refused that correct file as "in projected units", with no override. `check` and `import`
   share this test.
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
   A `.dbf` whose record length is not the total of its fields, or whose record count does
   not fit its file size, is corrupt too. A `.shp`, `.prj` or `.dbf` that is a symbolic or
   hard link is refused before it is read. A `.prj` that is not WKT is refused without
   echoing its text, and a header error names the bytes, not their values. A source with no
   `.dbf` is refused too: the specification requires one, and `check` cannot tell what the
   file holds. `loadscar` refuses the same file, so the two modes agree.

## What it warns about

A warning does not stop the load and does not change the exit code. Each one starts with
`WARNING`, so a search of the log finds it.

**An import that drops attachments.** A feature class with attachments goes through an
import, the row count is right, and every attachment is gone. Esri documents the control, the
geoprocessing environment `maintainAttachments`
([Maintain Attachments](https://doc.esri.com/en/arcgis-pro/latest/tool-reference/environment-settings/maintain-attachments.html)).
Its default is True. When it is False, the tools that honor it copy the features and leave
the attachments behind.

Measured on Pro 3.6, on a file geodatabase feature class with 3 points and 3 attachment rows.
Each cell is the attachment rows that the target held after one tool ran, in a fresh process:

| Tool | `maintainAttachments` True | `maintainAttachments` False |
|---|---|---|
| Feature Class To Feature Class | 3 | no attachment table |
| Copy Features | 3 | no attachment table |
| Project | 3 | no attachment table |
| Append, into a new feature class | 3 | no attachment table |
| Append, into one with attachments enabled | 3 | 0 |

With the setting False, Feature Class To Feature Class returned `maxSeverity 0`. Its only
messages were the start time and "Succeeded". A script, or a Pro project, that sets the
environment to False therefore loses the attachments without a sign.

fcload never sets the environment. It reads `arcpy.env.maintainAttachments`, finds the source's
attachment table, and says what the load will do. After `--apply` it counts the attachment rows
in the target. It warns when the count is below the rows the target held before, plus the
source's rows. These are runs under the Pro interpreter. A wrapper set the environment, then
called `fcload.main`. Paths are as typed, relative to the run folder.
With the setting False, a dry run:

```
fcload import
  source     : ea.gdb\DS\pts
  ...
  target     : ea.gdb\DS -> t2  (WKID 4326)
  WARNING    : 3 attachment rows in the source will be DROPPED: arcpy.env.maintainAttachments is False, and Pro 3.6 then copies the features at maxSeverity 0 with no warning
  WARNING    : set arcpy.env.maintainAttachments = True in the session or the project's environment settings, then run fcload again
  verdict    : ACCEPT
  ...

  DRY RUN. Nothing was written. Re-run with --apply to load.
(exit 0)
```

The same load with `--apply`. The row count is right and the attachment table does not exist:

```
fcload import
  source     : ea.gdb\DS\pts
  ...
  target     : ea.gdb\DS -> t4  (WKID 4326)
  WARNING    : 3 attachment rows in the source will be DROPPED: arcpy.env.maintainAttachments is False, and Pro 3.6 then copies the features at maxSeverity 0 with no warning
  WARNING    : set arcpy.env.maintainAttachments = True in the session or the project's environment settings, then run fcload again
  verdict    : ACCEPT
  ...
  rows: 0 before + 3 loaded = 3 expected, 3 found
  WARNING    : attachments dropped: the target should hold 3 attachment rows (0 already there + 3 from the source) and holds 0
  OK: ea.gdb\DS\t4 | Point | SR 'GCS_WGS_1984' (WKID 4326)
  next: fcload chores --workspace ... --target t4
(exit 0)
check by arcpy: attach table exists False None
```

An append into a target that already held 6 attachment rows, with the setting False:

```
fcload import
  source     : ea.gdb\DS\pts
  ...
  target     : ea.gdb\DS -> t3  (WKID 4326)
  WARNING    : 3 attachment rows in the source will be DROPPED: arcpy.env.maintainAttachments is False, and Pro 3.6 then copies the features at maxSeverity 0 with no warning
  WARNING    : set arcpy.env.maintainAttachments = True in the session or the project's environment settings, then run fcload again
  verdict    : ACCEPT
  ...
  target exists: appending 3 rows with Append (NO_TEST)
  rows: 6 before + 3 loaded = 9 expected, 9 found
  WARNING    : attachments dropped: the target should hold 9 attachment rows (6 already there + 3 from the source) and holds 6
  OK: ea.gdb\DS\t3 | Point | SR 'GCS_WGS_1984' (WKID 4326)
  next: fcload chores --workspace ... --target t3
(exit 0)
check by arcpy: attach table exists True 6
```

With the setting True, the same loads printed `3 attachment rows now in the target` for a new
target, and `6 attachment rows now in the target` for the append. `GetCount` on the attachment
tables gave 3 and 6.

How fcload finds the attachments: a feature class lists its `relationshipClassNames`. The one
that is an attachment relationship class (`isAttachmentRelationship`, see
[RelationshipClass properties](https://doc.esri.com/en/arcgis-pro/latest/arcpy/functions/relationshipclass-properties.html)) and has the feature class as its origin names the
attachment table as its destination. The public Describe pages that I read do not list
`relationshipClassNames`. Pro 3.6 ships it in its arcpy type stubs, as "the names of the
relationship classes that this table participates in". It returned the attachment class in
every run here. Pro keeps the class and the table of a feature class inside a feature dataset
at the workspace root, so the search climbs two levels. A shapefile source has no such
property, and `import` of one printed no attachment line.

## loadscar

A shapefile arrives from someone else. The write that made it has already run, and it left
little to see. Measured on Pro 3.6: a feature class with ten fields, exported to a shapefile
with Feature Class To Feature Class and again with Copy Features, gave `maxSeverity 0` and no
message but the start time and "Succeeded" both times. Both shapefiles held:

- Field names cut to 10 characters. `PARCEL_NUMBER`, `PARCEL_NUMBER_2` and `PARCEL_NUMBER_3`
  became `PARCEL_NUM`, `PARCEL_N_1` and `PARCEL_N_2`. `OWNER_NAME_FULL` became `OWNER_NA_1`.
- A 400-character text value, stored as 254 characters.
- A date with a time of 13:45:10, stored as the day.
- The Double 1.123456789012345, stored as `1.12345678901e+00`.
- A null Double and a null Long stored as 0, a null text as spaces, and a null date as blank.

Esri documents these limits: names of at most 10 characters, text fields 254 characters wide,
dates without a time, and no null values
([Geoprocessing considerations for shapefile output](https://pro.arcgis.com/en/pro-app/3.5/tool-reference/appendices/geoprocessing-considerations-for-shapefile-output.htm)).
The page also says that tools which must write a null may write a large sentinel number, and
that the limit on each shapefile component file is 2 GB.

`loadscar` runs fcload's rules backwards. `check` asks whether a source is safe to load.
`loadscar` asks what in a delivered file shows that a write lost data. It reads the file with
the standard library, opens every file read-only, and needs no arcpy.

A scar is evidence. `loadscar` exits 2 and prints REFUSE when it finds one:

1. The `.prj` is missing, is not WKT, is a WKT that Pro reads as `Unknown`, or contradicts the
   extent. These are rules 1, 3, 4 and 8 of `check`, with the same code.
2. The `.shx` and the `.dbf` disagree on the number of features. The
   [shapefile specification](https://www.esri.com/content/dam/esrisites/sitecore-archive/Files/Pdfs/library/whitepapers/pdfs/shapefile.pdf)
   says the index file is a 100-byte header and one 8-byte record per shape, and that the
   table must hold one record per shape feature.
3. A `.dbf` or `.shx` is missing. The specification says a shapefile consists of a main file,
   an index file and a dBASE table.
4. A text field 254 characters wide holds a value that fills all 254. That is the most a
   shapefile text field holds, so a longer original was cut. A value of exactly 254 characters
   is possible, so this is evidence and not proof.
5. The file holds no features.
6. With `--target FIELDS.txt`, a field of the original that was cut to 10 characters, or that
   has no counterpart in the delivered file. Pairing uses the two forms that Pro 3.6 wrote
   above: the first 10 characters, and a shorter stem with `_N` that makes 10.

Some losses leave nothing in the file. `loadscar` names them as notes, under the verdict, and
does not count them as scars: the time of a date, a null written as 0, digits of a Double past
the twelfth, and a name of exactly 10 characters when no `--target` was given.

`--target FIELDS.txt` is a text file of the original's field names, one per line. A blank line
and a line that starts with `#` are skipped. Any other line must be letters, digits and
underscores, starting with a letter. A line that is not is refused and never echoed, because
the operator may have pointed the flag at a password file. A symbolic link or a file with a
second hard link is refused for the same reason. The managed fields (`OBJECTID`, `Shape`,
`GlobalID` and the others in `diff`) are ignored on both sides. That includes the hosted-layer
pair `Shape__Area` and `Shape__Length`, which a shapefile writer cuts to `Shape__Are` and
`Shape__Len`
([Esri: ArcGIS Online does not display the Shape Area and Shape Length attributes](https://support.esri.com/en-us/knowledge-base/problem-arcgis-online-does-not-display-the-shape-area-a-000015423)
names the double-underscore fields).

A file with no field name at all, such as the zero-byte file that a failed `ListFields` export
leaves, is refused with exit 2. An empty list would compare nothing and print ACCEPT, a cleaner
verdict than giving no `--target`, and it would switch off the 10-character note. A UTF-8 file
with a byte order mark and a UTF-16 file with one (Notepad, and Windows PowerShell 5 redirection)
are read as text.

The four shapefiles below were written by Pro 3.6. `tidy.shp` is a clean export. `delivered.shp`
is the ten-field export above, with the text of its `NOTES` field cut. `lostrow.shp` is that
file with its second `.dbf` record cut and the header count patched by hand, to stage a loss.
`noprj.shp` is `delivered.shp` with its `.prj` deleted. `original_fields.txt` is the field list
that `ListFields` gave for the feature class.

```
$ python fcload.py loadscar --source tidy.shp
fcload loadscar (read-only, arcpy is not imported)
  delivered  : tidy.shp
  geometry   : Point   rows: 3   fields: 2
  .prj       : tidy.prj
  source SR  : 'GCS_WGS_1984' (no WKID stated, Geographic)
  extent     : (-82.29999999999995, 29.100000000000023, -82.09999999999997, 29.300000000000068)   (.shp header, bytes 36-67)
  shapes     : 3 in the .shx, 3 in the .dbf
  verdict    : ACCEPT
    - no scar found by these rules (see Limitations: absence is not proof)
  losses that leave no trace (named, not detected):
    - ACRES: a null in the original is written as 0 or a sentinel number, and as a space in a text field, so a 0 here may be a null
(exit 0)
```

The ten-field export, checked against the original's field names:

```
$ python fcload.py loadscar --source delivered.shp --target original_fields.txt
fcload loadscar (read-only, arcpy is not imported)
  delivered  : delivered.shp
  geometry   : Point   rows: 2   fields: 10
  .prj       : delivered.prj
  source SR  : 'GCS_WGS_1984' (no WKID stated, Geographic)
  extent     : (-82.19999999999999, 29.100000000000023, -82.09999999999997, 29.100000000000023)   (.shp header, bytes 36-67)
  shapes     : 2 in the .shx, 2 in the .dbf
  verdict    : REFUSE
    - 1 value of NOTES fills all 254 characters, the most a shapefile text field holds: a longer original was cut here
    - field PARCEL_NUMBER was cut to PARCEL_NUM by the 10-character limit
    - field PARCEL_NUMBER_2 was cut to PARCEL_N_1 by the 10-character limit
    - field PARCEL_NUMBER_3 was cut to PARCEL_N_2 by the 10-character limit
    - field OWNER_NAME_FULL was cut to OWNER_NA_1 by the 10-character limit
  losses that leave no trace (named, not detected):
    - WHEN_AT: a shapefile date holds the day only, so a time of day in the original is gone and leaves no trace
    - AMT, CNT, BIGN: a null in the original is written as 0 or a sentinel number, and as a space in a text field, so a 0 here may be a null
    - a Double is written with 12 significant digits by Pro 3.6, so digits past the twelfth are gone
(exit 2)
```

A lost record, then a lost `.prj`. The notes are the same as above and are shortened here:

```
$ python fcload.py loadscar --source lostrow.shp
fcload loadscar (read-only, arcpy is not imported)
  delivered  : lostrow.shp
  geometry   : Point   rows: 1   fields: 10
  .prj       : lostrow.prj
  source SR  : 'GCS_WGS_1984' (no WKID stated, Geographic)
  extent     : (-82.19999999999999, 29.100000000000023, -82.09999999999997, 29.100000000000023)   (.shp header, bytes 36-67)
  shapes     : 2 in the .shx, 1 in the .dbf
  verdict    : REFUSE
    - the .shx indexes 2 shapes and the .dbf holds 1 record: the two files disagree about how many features the file holds
    - 1 value of NOTES fills all 254 characters, the most a shapefile text field holds: a longer original was cut here
  losses that leave no trace (named, not detected):
    ...
(exit 2)
$ python fcload.py loadscar --source noprj.shp
fcload loadscar (read-only, arcpy is not imported)
  delivered  : noprj.shp
  geometry   : Point   rows: 2   fields: 10
  .prj       : MISSING - the spatial reference is UNDEFINED, never assumed geographic
  source SR  : 'Unknown' (no WKID stated, type unknown)
  extent     : (-82.19999999999999, 29.100000000000023, -82.09999999999997, 29.100000000000023)   (.shp header, bytes 36-67)
  shapes     : 2 in the .shx, 2 in the .dbf
  verdict    : REFUSE
    - 1 value of NOTES fills all 254 characters, the most a shapefile text field holds: a longer original was cut here
    - source spatial reference is UNDEFINED (factoryCode=0, name='Unknown')
    - arcpy would import this at maxSeverity 0 with no warning and stamp the raw coordinates with the TARGET spatial reference, silently relocating every feature
    - if you know what the coordinates really are, run `fcload import` with --assume-sr WKID
  losses that leave no trace (named, not detected):
    ...
(exit 2)
```

`pyogrio.read_info` (GDAL 3.12.4) read `lostrow.shp` as 2 features, and `delivered.shp` with
the cut names, and printed no complaint.

Four more Pro-made shapefiles covered scars 1, 3 and 5: `tidy.shp` with its `.shx` deleted, with
its `.dbf` deleted, with a StatePlane feet `.prj` that Pro wrote, and an empty export. Each
printed REFUSE and exit 2 with the reason for its scar. Windows and Ubuntu printed the same lines.

## Usage

```
python fcload.py check   --source PARCELS.shp
python fcload.py check   --source PARCELS.shp --target LOADED.shp
python fcload.py loadscar --source DELIVERED.shp
python fcload.py loadscar --source DELIVERED.shp --target FIELDS.txt
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
  record length (bytes 10-11) must be 1 plus the sum of the field lengths (byte 16 of each
  field descriptor). The record count must fit the file: the header length plus the count
  times the record length is the file size, with or without one `0x1A` end byte.

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
  and a named `SPHEROID`, with a semi-major axis above 0 and an inverse flattening of 0 (a
  sphere) or at least 1.
  The `PRIMEM` is from -180 to 180. The `UNIT` factor is above 0.
- A `PROJCS` has a name, one such `GEOGCS`, one `PROJECTION` and one `UNIT` with a factor above
  0. The `PROJECTION` is one of the 77 that Pro 3.6 writes. The `PARAMETER`s are the ones that
  Pro writes for that projection. None repeats, and each latitude is from -90 to 90.
- A `LINUNIT`, which Pro writes for a 3D system, is checked like a `UNIT`. A `PROJCS` has a
  `LINUNIT` only when its `GEOGCS` has one too, as Pro writes a 3D system. Pro 3.6 reads a
  2D StatePlane `PROJCS` with `LINUNIT["Meter",1.0]` added as `Unknown`.
- One `VERTCS` can follow the root, as Pro writes `PROJCS[...],VERTCS[...]`. It has a name, a
  `VDATUM` (or, for an ellipsoidal height, a `DATUM` with a `SPHEROID`), and a `UNIT` with a
  factor above 0. Its only parameters are `Vertical_Shift` and `Direction`.
- The WKT is on one line. Pro reads only the first line, so pretty-printed WKT is `Unknown` to
  it. A trailing line break, a lone CR, a tab and `, ` are whitespace to both. A `.prj` that
  starts with a line break (LF or CRLF) has an empty first line, and Pro 3.6 reads it as
  `Unknown`.
- The file does not start with a UTF-8 byte-order mark. Pro reads such a file as `Unknown`.
- Whitespace between tokens is a space, tab, VT, FF or CR, and nothing else. A number is
  ASCII: an optional sign, digits with an optional point, and an optional exponent.
- The `GEOGCS` `UNIT` is `Degree`, `Grad`, `Gon` or `Second`, in any letter case.
- Each node has at most one `AUTHORITY`. An `AUTHORITY` holds a quoted name and a code, and
  nothing else. Neither may be empty or blank. The code is quoted or a number.

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
- Pro `Describe` read 285 hand-made `.prj` files. `check` accepts none that Pro read as
  `Unknown`. It refuses 49 that Pro can read, for example an unknown node such as `FOO["bar"]`,
  a `GEOGCS` with no `PRIMEM`, a trailing comma, a lone `GEOGCS` in feet, or
  `AUTHORITY[EPSG,4326]` with a bare word. Those refusals are deliberate: fcload refuses what
  it cannot verify.
- A later sweep of 85 more hand-made files found 3 that Pro reads as `Unknown` and `check`
  accepted: the WGS84 `.prj` after a leading LF, the same after a leading CRLF, and a 2D
  `PROJCS` with a `LINUNIT`. `check` now refuses all 3. Of those 85 files, it refuses 8 that
  Pro can read.

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

The next count, 242 files, was false too. A later audit and a follow-up measurement found 18
more files that Pro reads as `Unknown` and `check` accepted, exit 0. Each is the valid WGS84 or
StatePlane WKT with one change:

- An `AUTHORITY` that is not one name and one code: `AUTHORITY["EPSG"]`, `AUTHORITY["ESRI"]`,
  `AUTHORITY[4326]`, an empty or blank name or code, on the root or inside a `DATUM`,
  `SPHEROID`, `UNIT`, `PROJECTION` or `PARAMETER`. `check` checked only how many `AUTHORITY`
  nodes the root held.
- Two `AUTHORITY` nodes inside the `DATUM`.
- An inverse flattening of 0.001, 0.5, 0.99 or 0.9999. Pro reads 0, 1 and 1.0001.

`check` now refuses all 18, and the self-test pins them. Under Pro, `import` refused four of
them as UNDEFINED, at least one of each kind. On Ubuntu, with `AUTHORITY["EPSG"]` on the root:

```
$ python3 fcload.py check --source bundle/auth.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source bundle/auth.shp: the .prj is not a spatial reference fcload can verify (an AUTHORITY in GEOGCS is not one name and one code). ArcGIS Pro 3.6 reads a .prj it cannot parse as UNDEFINED, 'Unknown' with factoryCode 0
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

The build before this one tested `os.path.islink` only, so a hard link passed it. GNU tar
1.35 restores a hard-link entry in an archive to a file that already exists in the folder. A bundle can carry
`roads.dbf` as a hard link to `.token`, and `tar xf` in a folder that holds `.token` makes
`roads.dbf` a second name for it. The previous build then read the token as a dBASE header. Its
size error printed the record count, header length and record length as decimal numbers,
`811808610`, `25649` and `25906`. Packed back into bytes, they are 8 bytes of the token,
`b7c01d2e`. `check` now also refuses a file with more than one hard link (`st_nlink > 1`).
A header error names the bytes that are wrong, not their values. The same Ubuntu run, with a
synthetic 34-byte `.token` and a tar file built with Python's `tarfile`:

```
$ cd inbox && tar xf ../bundle.tar && stat -c '%n %h' roads.dbf
roads.dbf 2
$ python3 fcload.py check --source inbox/roads.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source inbox/roads.shp: roads.dbf is a link, so it is not read: it could point at any file, such as ~/.pgpass
(exit 2)
```

`grep` found no byte of the password or the token in the four logs, as text or as those decimal
numbers. The self-test cannot make a symbolic link on Windows without admin rights, so it covers
that rule by patching `os.path.islink`. A hard link needs no admin rights, so the self-test
makes a real one with `os.link` on both hosts.

A `.dbf` whose header states the wrong record count is refused too. ArcGIS Pro 3.6 cannot open
one: `GetCount` on a Pro shapefile whose `.dbf` count was patched to 0, 2 or 5 over 3 records
raised `ERROR 000229: Cannot open`, and `import` stopped with that traceback. An earlier build of
`check` printed `rows: 0` and ACCEPT for the first. The next build checked the count against the
file size, but not the record length against the fields. A record length of 0 fits any count,
so a `.dbf` with no records whose header claimed 999 got `rows: 999` and ACCEPT. Now, on Ubuntu:

```
$ python3 fcload.py check --source bundle/counted.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source bundle/counted.shp: the .dbf record count and lengths at bytes 4-11 do not fit the 99 bytes the file holds: truncated or corrupt
(exit 2)
$ python3 fcload.py check --source bundle/ghost.shp
fcload check (read-only, arcpy is not imported)
  verdict    : REFUSE
    - source bundle/ghost.shp: the .dbf record length at bytes 10-11 is not 1 plus the sum of its field lengths: truncated or corrupt
(exit 2)
```

A `.dbf` that ArcGIS Pro 3.6 wrote, with text fields of 254 and 1 characters and Double, Long,
Short, Float and Date fields, passes the record-length rule.

The degree-bounds slack, on Ubuntu, for the WGS84 world layer from rule 4:

```
$ python3 fcload.py check --source bundle/world.shp
fcload check (read-only, arcpy is not imported)
  source     : bundle/world.shp
  geometry   : Point   rows: 3   fields: 1
  .prj       : bundle/world.prj
  source SR  : 'GCS_WGS_1984' (no WKID stated, Geographic)
  extent     : (-180.00000000000003, -85.0511287798066, 180.00000000000003, 85.0511287798066)   (.shp header, bytes 36-67)
  verdict    : ACCEPT
    - spatial reference 'GCS_WGS_1984' (no WKID stated, Geographic) is consistent with its extent
    - no target WKID to compare with: reprojection not assessed
(exit 0)
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
| `--source` | required for `import`, `diff`, `check`, `loadscar` | feature class or shapefile; a `.shp` for `check` and `loadscar` |
| `--target` | required for `chores`, `diff` | feature class name in the workspace; for `check`, an optional second `.shp`; for `loadscar`, an optional text file of the original's field names |
| `--dataset` | none, workspace root | target feature dataset |
| `--name` | the source name | output feature class name |
| `--assume-sr WKID` | unset | `import` only, must be positive |
| `--add-missing` | off | `diff` only |
| `--apply` | **off, everything is a dry run** | refused with `check` and `loadscar`, which are read-only |
| `--check-write` | off | probe the workspace and stop; needs `--apply` and `import`, `chores` or `diff` |
| `--self-test` | off | the one flag that takes no subcommand |

| Exit | Meaning |
|---|---|
| 0 | Accepted, or the dry run or probe succeeded. |
| 1 | Usage error (argparse's own errors included), a missing or unreadable source or target, a failed load, a failed probe, or a probe table left behind. |
| 2 | Refused. Nothing was written. For `loadscar`: a scar was found, the file is corrupt, or the `--target` file has no field name or a line that is not one. A `--target` that is missing or unreadable exits 1. |

## Configuration

None. No config file, no environment variables, no credentials, no hardcoded WKIDs, owners, or
connection strings. The target WKID is read from the destination feature dataset or the existing
feature class. The only SR you state by hand is `--assume-sr`, echoed in the verdict so it
survives in the run's log. `import` reads one arcpy environment setting, `maintainAttachments`, and
never sets it.

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

Attachments already have a control in Pro: the `maintainAttachments` setting, above. fcload
does not replace it. It reads the setting and checks that the load did what the setting says.

For `loadscar`, `gh search repos` on 2026-10-22 found these projects: `ncagle/Shapefile-Validator`
(a window that checks that a shapefile is valid with geopandas), `codybloemhard/shapefile-linter`
("linting, cleaning and compression for shapefiles"), `Karthik-Valmiki/GeoLint` and
`zhangchaosd/dbfview` (a DBF viewer and integrity validator). I did not run them. From their
descriptions, each checks a file on its own. I found none that compares a delivered file with the
schema it came from. GDAL, as above, reports `delivered.shp` and `lostrow.shp` as readable files
and leaves the judgement to the reader.

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
failing line and the number of assertions that failed, out of 455 in that build, are quoted. Windows and Ubuntu
gave the same lines. "1.0.0 --check-write rule back" restores both halves of the 1.0.0 rule:
`validate_args` refuses `--check-write` only together with `--apply`, and `check_write` probes
without asking. "well-bracketed .prj = defined" restores the earlier `check` rule, which took
the root's name and did not check its structure. "folder link rule back" restores the rule
that refused only a link out of the folder, and "islink-only link rule back" the rule that
missed hard links. "Unicode whitespace read" restores `\s` and `strip()`.

```
undefined treated as fine      -> FAIL  factoryCode 0 is refused  <-- pinned defect   29 failed
--apply gate removed           -> FAIL  import without --apply performs NO write call  <-- pinned defect   3 failed
row-count check removed        -> FAIL  a short row count after the load FAILS the import  <-- pinned defect   4 failed
1.0.0 --check-write rule back  -> FAIL  --check-write without --apply is rejected: the probe is a write  <-- pinned defect   12 failed
undefined target accepted      -> FAIL  so is an undefined target with an empty name   8 failed
missing .prj read as WGS84     -> FAIL  check refuses a shapefile with no .prj as UNDEFINED  <-- pinned defect   7 failed
nested AUTHORITY read as WKID  -> FAIL  an AUTHORITY on a nested UNIT is never taken for the root WKID  <-- pinned defect   2 failed
truncated .prj read anyway     -> FAIL  a truncated .prj is refused, not read from its prefix  <-- pinned defect (wrong exception IndexError('list index out of range')), then the self-test stops, exit 1
well-bracketed .prj = defined  -> FAIL  a name-only PROJCS is not defined  <-- pinned defect (no error raised)   56 failed
newline rule dropped           -> FAIL  WKT with a newline inside is not defined: Pro reads only its first line  <-- pinned defect   3 failed
junk VERTCS accepted unread    -> FAIL  a junk VERTCS after a valid root is not accepted unread  <-- pinned defect (no error raised)   6 failed
VERTCS tail refused again      -> self-test stops, exit 1: ValueError: the .prj WKT is truncated or corrupt: text follows its root that is not one VERTCS
duplicate .dbf names allowed   -> FAIL  a .dbf that repeats a field name in another case is refused  <-- pinned defect (no error raised)   2 failed
empty import source loaded     -> FAIL  import refuses an empty source, as check does, and writes nothing  <-- pinned defect   1 failed
output not escaped             -> self-test stops, exit 1: UnicodeEncodeError: 'charmap' codec can't encode characters in position 16-17: character maps to <undefined>
--assume-sr on a defined SR    -> FAIL  --assume-sr on a DEFINED, consistent source is refused, never ignored  <-- pinned defect   4 failed
root target WKID ignored       -> FAIL  an EXISTING root feature class supplies the target WKID to the verdict  <-- pinned defect   1 failed
.prj text echoed               -> FAIL  a .prj that is not WKT is never echoed: it may be ~/.pgpass  <-- pinned defect   4 failed
.prj link followed             -> FAIL  check refuses a .dbf hard linked to a secret and prints no byte of it  <-- pinned defect   5 failed
folder link rule back          -> FAIL  check refuses a .dbf hard linked to a secret and prints no byte of it  <-- pinned defect   5 failed
islink-only link rule back     -> FAIL  check refuses a .dbf hard linked to a secret and prints no byte of it  <-- pinned defect   1 failed
Unicode whitespace read        -> FAIL  a .prj with an NBSP after a comma is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   10 failed
float() reads any number       -> FAIL  a .prj with a number with an underscore is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   2 failed
two AUTHORITY nodes allowed    -> FAIL  a .prj with two root AUTHORITY nodes is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   2 failed
AUTHORITY structure unchecked  -> FAIL  the AUTHORITY refusal names the node that holds it   22 failed
flattening under 1 read        -> FAIL  a .prj with an inverse flattening of 0.5 is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   5 failed
world extent refused again     -> FAIL  each bound allows 1e-9 degrees of rounding, and not 1e-8   3 failed
record length unchecked        -> FAIL  a .dbf that claims 999 records of 0 bytes is refused: 0 bytes fit any count  <-- pinned defect (no error raised)   4 failed
GEOGCS unit not checked        -> FAIL  a .prj with a Foot_US UNIT in the GEOGCS of a PROJCS is refused, as Pro reads it as Unknown  <-- pinned defect (no error raised)   4 failed
.dbf size not checked          -> FAIL  a .dbf header that claims 0 records over 3 is refused, as Pro cannot open it  <-- pinned defect (no error raised)   6 failed
--check-write advice on check  -> FAIL  check --check-write gives one error, never the advice to add --apply  <-- pinned defect   1 failed
usage error exits 2 again      -> FAIL  an unknown subcommand is a usage error, exit 1, not the refusal code 2  <-- pinned defect   2 failed
```

An audit of an earlier build found refusal rules that no assertion could fail. Each of these
mutations left the self-test at `325 assertions, 0 failed`, and `check` then accepted a `.prj`
that Pro reads as `Unknown`: the `PROJCS` `UNIT` check removed, the `DATUM` name check removed,
the non-finite number filter removed, or `abs()` dropped from the `PRIMEM` or the latitude
rule. `DEGREE_X` widened from 180 to 199 also left 325 of 325, because the degree test built
its boxes from `DEGREE_X` itself. That test now uses the literals 180 and 90.

The next build claimed that each rule had an assertion that fails when the rule is deleted or
loosened, and that claim was false. A later audit loosened four rules, and the suite still
printed `396 assertions, 0 failed`. Each mutant then accepted a file that the shipped code
refused: `.lower()` dropped from the new name in the duplicate `.dbf` field test (`acres` then
`ACRES`), `.strip()` dropped from a `.dbf` field name (11 spaces), a blank node name such as
`DATUM["  "]`, and a `VERTCS` `PARAMETER` with no number. Two guards that stop a hostile `.prj`
from raising a traceback could fail no assertion either, nor could the upper-case `.SHP` test or
the `source SR` and `target SR` lines. The self-test now pins each of them.

Two sweeps now check the claim. The first applies 168 named one-line mutations to the
parsers and each `.prj` structure rule, the degree-bounds check, the target rule, the link
rules, the write gates, the flag checks and the tracking-name vote. It leaves three alive on
Ubuntu, and all three are equivalent. `pos + 32 >= end` in the `.dbf` loop refuses the same
headers one step earlier. `check` passing `prj_defined=True` changes nothing,
because a missing `.prj` also has an empty name, which is undefined on its own. `\s` in the
pattern for the space before a `[` changes nothing, because the word before it has already taken
every character that is not ASCII whitespace. On Windows a fourth survives: the upper-case
`.PRJ` lookup, because NTFS finds `upper.PRJ` under either case.

The second sweep, on Windows, swaps one operator or constant at a time on every line from
`extent_in_degree_range` to the end of `cmd_check`: `==` and `!=`, `<` and `<=`, `and` and
`or`, `not`, `in`, `0` and `1`, `.strip()`, `.lower()`, `abs()` and others. Of 486 mutants,
73 survive. 53 of them change a docstring. None of the other 20 lets a refused input through:

- 4 change `<=` to `<` at the edge of the `1e-9` slack, which differs for one float value.
- 2 drop one of two `strip()` calls on the same name, 1 drops a `.lower()` on names that are
  already lower case, and 1 widens a slice to the name string, which the number filter drops.
- 4 change how the `.dbf` header checks reach the same refusal.
- 2 change `_sibling`'s `return None` into a value that is just as false.
- 3 change only the wording of a message, such as `1 numbers`.
- 2 refuse more: a semi-major axis up to 1, and `--assume-sr 1`.
- 1 is the Windows-only `.PRJ` survivor above.

Some mutants stop the self-test with an exception instead of a `FAIL` line. That is still
exit 1.

Version 1.2.0 was checked the same way. Each of these 23 one-line mutations of the new code was
applied to a copy of `fcload.py` and fails the suite, which had 536 assertions in the build the mutants were run against.
The first failing line and the number of assertions that failed are quoted. The run was on
Windows, Python 3.13.2.

```
attachment warning gated on the wrong value   -> FAIL  maintainAttachments False warns before the load that the attachments will be dropped  <-- pinned defect   2 failed
import ignores the setting                    -> FAIL  maintainAttachments False warns before the load that the attachments will be dropped  <-- pinned defect   1 failed
post-load count removed                       -> FAIL  a load that copies the attachments counts them after   9 failed
loss needs one row less                       -> FAIL  a load that copies the attachments counts them after   4 failed
rows already in the target ignored            -> FAIL  an append that drops them is caught against what was already there   2 failed
no attachment table is fine                   -> FAIL  attachment_loss: one row short, no table, and a table short of its prior rows are all losses   1 failed
uncountable table called a success            -> FAIL  an uncountable target table is not called a success   2 failed
search climbs one level                       -> FAIL  a load that copies the attachments counts them after   5 failed
any relationship class is an attachment       -> FAIL  a relationship class that is not an attachment one is not an attachment  <-- pinned defect   1 failed
origin class not checked                      -> FAIL  the attachment table itself, where the class is the destination, is not given attachments  <-- pinned defect   1 failed
shape count not compared                      -> FAIL  a .shx and a .dbf that disagree on the feature count are a scar, exit 2  <-- pinned defect   3 failed
NUL counts as a value                         -> FAIL  text_cap_hits counts records whose byte at a column's end is not blank or NUL   1 failed
a later chunk not counted                     -> FAIL  a full value in a later 4 MiB chunk is counted with one in the first  <-- pinned defect   1 failed
any 10-character name is a cut                -> FAIL  a name that is not the 10-character form of the original is a lost field   1 failed
_N form of a cut dropped                      -> FAIL  field_scars pairs the names exactly as Pro 3.6 cut and numbered them  <-- pinned defect   1 failed
managed fields compared                       -> FAIL  the original's field names find a cut name and a lost field, and the 10-character note gives way to them   2 failed
missing .dbf or .shx replaces the other scars -> FAIL  a delivery with no .shx is a scar, and its .dbf is still read   1 failed
empty file judged by its .prj                 -> FAIL  an empty file's extent is not judged against its .prj   1 failed
.shx size not checked                         -> FAIL  a .shx that is not 100 bytes plus 8 a shape is refused as corrupt   1 failed
.shx link read                                -> FAIL  loadscar refuses a .shx hard linked to a secret and prints no byte of it  <-- pinned defect   1 failed
--target link read                            -> FAIL  a --target file hard linked to a secret is refused  <-- pinned defect   1 failed
--target line echoed                          -> FAIL  a --target line that is not a field name is refused and never echoed  <-- pinned defect   1 failed
loadscar always exits 0                       -> FAIL  a .shx and a .dbf that disagree on the feature count are a scar, exit 2  <-- pinned defect   15 failed
```

Four more mutations were applied after the 1.2.0 build, to controls that an operator-flip sweep
had shown the suite could not fail. Each now fails the suite, which has 551 assertions. The run
was on Windows, Python 3.13.2.

```
text-field test `and` flipped to `or`          -> FAIL  a full text field narrower than 254 is not a cut  <-- pinned defect   1 failed
last-byte offset one byte early                -> FAIL  a 253-character value in a 254-wide field is not a cut: only the last byte counts  <-- pinned defect   1 failed
attachment baseline `or 0` changed to `or 1`   -> FAIL  an append into a target with no attachment table starts from 0 attachment rows  <-- pinned defect   2 failed
check reads the .shx by default                -> FAIL  check does not read the .shx, so a corrupt one beside the .shp is not its business  <-- pinned defect   1 failed
```

The subcommands that need a geodatabase run against a recording stub arcpy in the self-test.

## What it wraps

Everything fcload does to a geodatabase is arcpy: `FeatureClassToFeatureClass`, `Append`,
`AddField`, `ListFields`, `AddGlobalIDs`, `EnableEditorTracking`, `Describe`, plus
`CopyFeatures`, `DefineProjection`, `Project`, `GetCount`, `CreateTable` and `Delete`. arcpy does
all of the work. fcload adds the refusal gate in front of it, the attachment warning and
`loadscar`. The attachment warning also calls `Describe(...).relationshipClassNames`, the
`isAttachmentRelationship`, `originClassNames` and `destinationClassNames` of a relationship
class, and `GetCount` on the attachment table.

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
   `.shx` or the `.cpg`. The `.dbf` record count is checked against the `.dbf` file size and
   the record length against the fields, not against the number of shapes in the `.shp`. A `.prj` is decoded as UTF-8, and a UTF-16
   `.prj` is refused as not WKT. A `.dbf` field name keeps each byte above `0x7F` as an escape such as `\xc4`, because
   without the `.cpg` its letter case is unknown. Letter case is ignored for ASCII letters only.
   Two names that differ only in a non-ASCII letter's case are reported as two fields, which
   is a false alarm, never a false match. Non-ASCII characters print as backslash escapes.
9. `check` names `.dbf` types from the dBASE letter and width, as Pro 3.6 `ListFields` named
   them for widths 1 to 20: `C` String, `D` Date, `L` Logical. `N` with no decimals is
   SmallInteger up to width 4, Integer up to 10, else Double. `N` with decimals is Single up to
   width 8, else Double. `F` is Single up to width 13, else Double. Text field lengths are not
   compared, in `check` or in `diff`. Other writers or Pro versions can name a field
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
14. `check` refuses a `.shp`, `.prj` or `.dbf` with more than one hard link, even one linked
    on purpose, for example by a backup tool that saves space with hard links. Copy the
    shapefile to a new folder first. The link test and the read are two steps, so a file
    swapped for a link between them is read.
15. `check` reads a `.dbf` field length from byte 16 only. Some writers store a text field
    longer than 255 bytes with byte 17 as a high byte. Such a `.dbf` fails the record-length
    rule and is refused as corrupt. `loadscar` does the same, so a valid delivered file with such
    a field gets REFUSE for that reason.
16. `check` reads the first 64 KiB of a `.prj` and ignores the rest. A `.prj` padded past that
    size with whitespace can hide a trailing node that makes Pro read it as `Unknown`.
17. `check` takes the root `AUTHORITY` code as the WKID without comparing it with the root
    keyword. A `GEOGCS` that states a projected code, such as 2237, is reported with that code,
    and `check --target` then prints `matches the target: no reprojection` for a target in 2237.
    `check` also reads a code in non-ASCII digits, such as Arabic-Indic digits, as a number.
18. The attachment warning was run for real on a file geodatabase only. An enterprise geodatabase
    was not tried. There the attachment table may sit under a qualified name, and fcload finds it
    through the relationship class, not by name. The origin class is matched bare or owner-
    qualified (`DBO.PTS`), and that match ran against the stub only. The count compares rows,
    not file contents.
19. `maintainAttachments` is read from `arcpy.env`. A fresh Pro process printed True. A Pro project
    can set it to False, and a run from that project then warns. A tool that fcload does not
    call is out of scope.
20. A warning exits 0. A scheduled job that must stop on a dropped attachment has to search its
    log for `WARNING`.
21. `loadscar` scar 4 is evidence, not proof, and covers only text fields exactly 254 characters
    wide. It reads the last byte of that field in every record, in 4 MiB chunks. The largest
    file in the self-test is 20,000 records of 255 bytes. No 2 GB file was timed.
22. `loadscar` pairs a cut name with its original by the two forms that Pro 3.6 wrote. A writer
    that cuts or numbers names in another way gets "no counterpart" for each of them, which is
    true and less helpful. The order of the fields is not compared.
23. `loadscar` does not read the `.cpg` or the language byte of the `.dbf`. A text value that
    was written in the wrong code page is not found. It does not read geometry, the offsets in
    the `.shx`, or the 2 GB size limit of each component file.
24. `loadscar` compares the `.shx` count with the `.dbf` count. If both files were cut to the same
    length, scar 2 finds nothing.
25. `loadscar --target` reads UTF-8, with or without a byte order mark, and UTF-16 with one. UTF-16
    without a byte order mark and other code pages are refused as "not a field name".
26. After a `WARNING` line the import still ends with `OK:` and exits 0. Treat the load as
    incomplete when the log holds `WARNING`, whatever the last line says.
27. With `maintainAttachments` False and an empty source attachment table, the warning says
    "0 attachment rows ... will be DROPPED", although no row can be lost. The target then has no
    attachment table, and fcload prints "0 attachment rows now in the target" without naming
    that loss.
28. fcload counts every source attachment row as one that should copy. A source with orphaned
    attachment rows (the parent feature is deleted) can print "attachments dropped" after a
    correct load.
29. `import` finds attachments only through `Describe(path).relationshipClassNames` of a
    geodatabase path. A hosted feature service URL, a map layer name, or a path without that
    property prints no attachment line. A silent import does not prove the source has none.
30. When the source count is unknown and the target table is found, `import` prints "N attachment
    rows now in the target" with no comparison. An uncountable target table counts as 0 before
    the load, so a real drop can go unreported when the target already held enough rows.
31. `check` and `loadscar` refuse a `.dbf` with trailing bytes other than one `0x1A` end byte,
    such as padding that a copy tool added, as truncated or corrupt.
32. Sibling files are found as all lower-case or all upper-case extensions only. On a
    case-sensitive filesystem, `Parcels.shp` beside `Parcels.Dbf` is reported as having no
    `.dbf`, a false scar. Windows is not affected.
33. `loadscar` counts a deleted `.dbf` record (flag `*`) as a record, and a non-blank last byte in
    it as a cut value.
34. `loadscar` reads the first 1 MiB of the `--target` file. A longer list is cut without a
    message, and a name cut at the boundary can be reported as having no counterpart.
35. `loadscar --target` echoes any line that looks like a field name. A file that holds one bare
    token, such as a `.token` file, is printed in "field X has no counterpart". Point it at a
    field list only.
36. Esri's Maintain Attachments page lists Append, Copy, Copy Features, Copy Rows, Export
    Features, Export Table, Merge and Project as the tools that transfer attachments, and says
    the setting does not create attachments on the output. The measured table above adds Feature
    Class To Feature Class, which Esri does not list. An Append into a target without
    attachments carried them across in the Pro 3.6 run, which that page does not predict.
37. The `loadscar` note "a Double is written with 12 significant digits by Pro 3.6" is also
    printed for files that other tools (GDAL) wrote, so the writer it names may not have made the
    file.
38. The `loadscar` verdict ACCEPT means no scar was found by these rules. The line says that
    absence is not proof, but the word reads as clean. Do not read it as a clean bill.
39. `check` refuses GDAL's EPSG:3857 `.prj`, which carries a `Mercator_1SP` projection and
    `AXIS` and `EXTENSION` nodes. The refusal follows the documented rules, and which node
    decides it was not isolated. Pro's reading of `EXTENSION` was not verified.
40. `check` and `loadscar` print the names of a valid WKT `.prj` and the `.dbf` field names as
    they are. Only a `.prj` that is not WKT is withheld. A credential file would have to parse
    as WKT to reach the output.
41. An exception that arcpy raises in `import`, such as a workspace error from the write probe,
    is printed in arcpy's words. An enterprise connection error can include a server or user
    name. The text goes to the operator's own log.

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
