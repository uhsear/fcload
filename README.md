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

## Requirements

One file, no dependencies, no config file, no credentials. `--self-test` runs on any Python
3.8+ with no arcpy and no network; `import`, `chores`, and `diff` need the Pro interpreter:
`C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe`. `arcpy` is imported
inside `_arcpy()`, called only from the functions that touch a geodatabase; that is also the
path a `ModuleNotFoundError` prints.

## Quick start

```
git clone <this repo>
cd fcload
python fcload.py --self-test
self-test: 110 assertions passed, 0 failed (offline, no arcpy).
```

## What it refuses

1. Source SR **undefined**: `factoryCode 0`, or a name of `Unknown`, `<undefined>`,
   `undefined`, `none`, or empty. Override with `--assume-sr WKID`, printed in the verdict.
2. Source SR **projected** but its whole extent fits inside degree bounds (`|x| <= 180`,
   `|y| <= 90`): the coordinates are lat/long and the `.prj` is wrong.
3. Source SR **geographic** but its extent falls outside them: the coordinates are projected.
4. Row count after the load is not `rows before + rows loaded`. Exit 1, not a quiet success.
5. Anything that writes, without `--apply`. An SR that merely *differs* from the target is not
   a refusal; that is a reprojection, and fcload performs it.

## Usage

```
python fcload.py import  --source PARCELS.shp --workspace t.gdb --dataset DS
python fcload.py import  --source PARCELS.shp --workspace t.gdb --dataset DS --apply
python fcload.py import  --source noprj.shp   --workspace t.gdb --assume-sr 4326 --apply
python fcload.py chores  --workspace t.gdb --target PARCELS --apply
python fcload.py diff    --source PARCELS.shp --workspace t.gdb --target PARCELS --add-missing
```

**import** describes the source, runs the refusal gate, prints the verdict and its evidence,
and stops. With `--apply` it creates (`FeatureClassToFeatureClass`) or appends (`Append`,
`NO_TEST`), then verifies the row count. `--assume-sr` runs `DefineProjection` on a scratch
copy; the source on disk is never modified.

**chores** runs `AddGlobalIDs` and `EnableEditorTracking`, each skipped when already present.
Tracking field names differ between geodatabases (`CREATION_USER / CREATION_DATE /
MODIFY_USER / MODIFY_DATE` against Esri's `created_user / created_date / last_edited_user /
last_edited_date`) and the wrong four add a second, duplicate set of columns, so fcload reads
the dataset's own fields and keeps their convention and casing.

**diff** lists fields in the source but not the target, the reverse, and type conflicts, case
insensitively, never reporting geodatabase-managed fields (`OBJECTID`, `Shape`, `GlobalID`,
`Shape_Area`). `--add-missing --apply` adds them with `AddField` and re-reads the field list.

| Flag | Default | Notes |
|---|---|---|
| `--workspace` | required | target `.gdb` or `.sde` connection file |
| `--source` | required for `import`, `diff` | feature class or shapefile |
| `--target` | required for `chores`, `diff` | feature class name inside the workspace |
| `--dataset` | none, workspace root | target feature dataset |
| `--name` | the source name | output feature class name |
| `--assume-sr WKID` | unset | `import` only, must be positive |
| `--add-missing` | off | `diff` only |
| `--apply` | **off, everything is a dry run** | |
| `--check-write` | off | probe the workspace and stop; needs a subcommand, refuses `--apply` |
| `--self-test` | off | the one flag that takes no subcommand |

## Configuration

None. No config file, no environment variables, no credentials, no hardcoded WKIDs, owners, or
connection strings. The target WKID is read from the destination feature dataset; the only SR
you state by hand is `--assume-sr`, echoed in the verdict so it survives in the run's log.

## Why the obvious version is wrong

The guard hand-rolled import scripts usually carry:

```python
if src_wkid != TARGET_WKID and src_wkid != 0:   # reproject, else assume it is fine
```

`wkid == 0` **is** the undefined case, the one measured above, so the guard excludes precisely
the input that corrupts: the author read `0` as "nothing to do" rather than "nobody knows where
this data is", and arcpy reinforces it by reporting `maxSeverity 0`, leaving no error to notice
and no warning to grep for. fcload inverts that. Undefined is the loudest failure, and the only
way past is an explicit, recorded `--assume-sr`. Swapping in the naive guard, or deleting either
impure refusal, fails the suite:

```
naive != 0 guard      -> FAILED [1]:  factoryCode 0 is refused
--apply gate removed  -> FAILED [83]: import without --apply performs NO write call
row-count check gone  -> FAILED [88]: a short row count after the load FAILS the import
```

The last two need a geodatabase, so the subcommands run against a recording stub arcpy.

## What it wraps

Everything fcload does is arcpy: `FeatureClassToFeatureClass`, `Append`, `AddField`,
`ListFields`, `AddGlobalIDs`, `EnableEditorTracking`, `Describe`, plus `CopyFeatures`,
`DefineProjection`, `Project`, and `GetCount`. arcpy does all of the work; the only thing
claimed as new here is the refusal gate in front of it.

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

## Contributing

Issues and pull requests welcome. `python fcload.py --self-test` must stay green on a stock
Python 3.8+ with no Esri software installed, and new decision logic belongs in a pure function
so the suite reaches it. Nothing here has ever touched a production geodatabase or AGOL.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.
