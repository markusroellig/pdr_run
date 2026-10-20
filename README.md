# PDR Framework Installation and Testing Guide

This document provides instructions for installing, configuring, and testing the PDR (Photo-Dissociation Region) framework.

Changes per version are listed in [CHANGELOG.md](CHANGELOG.md).

## Table of Contents
- [Installation](#installation)
- [Preflight Check](#preflight-check)
- [Grid Status Snapshot](#grid-status-snapshot-pdr_run-status)
  - [Surveying a finished grid](#surveying-a-finished-grid)
- [Querying the configuration of a job](#querying-the-configuration-of-a-job)
- [Run Status and Exit Handling](#run-status-and-exit-handling)
- [Reacting to KOSMA-tau code flags](#reacting-to-kosma-tau-code-flags)
  - [Healing with a second binary](#healing-with-a-second-binary)
- [Configuration](#configuration)
  - [Configuration Precedence](#configuration-precedence)
  - [Database Configuration](#database-configuration)
  - [Storage Configuration](#storage-configuration)
    - [Local copy of remote results](#local-copy-of-remote-results-use_local_copy)
- [MySQL Setup](#mysql-setup)
- [SFTP Storage Setup](#sftp-storage-setup)
- [Running a Test Model](#running-a-test-model)
- [Running with JSON Only](#running-with-json-only)
- [Resource Management](#resource-management)
- [Comparing with Example Script](#comparing-with-example-script)
- [Advanced Usage](#advanced-usage)
- [Run Status and Exit Handling](#run-status-and-exit-handling)
  - [Job states](#job-states)
  - [Database columns](#database-columns)
- [Wall-Time Cap](#wall-time-cap)
  - [First-iteration stall watchdog](#first-iteration-stall-watchdog)
- [Stale-Job Recovery](#stale-job-recovery)
- [Storage Retries](#storage-retries)
  - [Whole-file compression of stored results](#whole-file-compression-of-stored-results)
  - [RClone storage on S3](#rclone-storage-on-s3)
  - [Placeholder checksums](#placeholder-checksums)
- [Post-Processing Steps](#post-processing-steps)
  - [UV continuum](#uv-continuum)
  - [ONION](#onion)
  - [SIMLINE](#simline)
- [UQ campaigns](#uq-campaigns)
- [Production Grid Run](#production-grid-run)
  - [Grid-1 template](#grid-1-template)
- [Troubleshooting](#troubleshooting)
  - [Known Limitations](#known-limitations)

## Installation

Install the PDR framework in development mode:

```bash
# Navigate to the package directory
cd pdr_run/

# Install in development mode
pip install -e .
```

This makes the `pdr_run` command available in your path while allowing you to modify the code.

## Installation Validation

After installation, verify everything is working:

```bash
# Check if pdr_run command is available
which pdr_run

# Test basic functionality
pdr_run --help

# Verify package installation
python -c "import pdr_run; print(f'PDR Framework version: {pdr_run.__version__ if hasattr(pdr_run, \"__version__\") else \"installed\"}')"

# Test imports
python -c "
from pdr_run.database.db_manager import get_db_manager
from pdr_run.storage.base import get_storage_backend
print('All core modules imported successfully')
"
```

## Quick Start Verification

Test the complete workflow:

```bash
# 1. Install and setup
make dev-install
make setup-sandbox
make start-services

# 2. Run tests
make test-all

# 3. Test a simple model (if PDR executables are available)
pdr_run --model-name test_install --dry-run --single --dens 3.0 --chi 1.0

# 4. Check logs
ls -la logs/
```

## Preflight Check

`pdr_run --check` verifies the whole set-up of a production run in a few seconds and prints one
line per check. It is the compact alternative to `--dry-run`, which dumps the full configuration
and log.

```bash
pdr_run --check --config my_config.yaml                    # human-readable report
pdr_run --check-json --config my_config.yaml               # same results as JSON (for scripts)
pdr_run --check --config my_config.yaml --min-free-gb 50   # disk-space WARN threshold (default 20)
pdr_run --check --config my_config.yaml --check-timeout 10 # per network check (default 5 s)
```

`python -m pdr_run.cli.runner --check ...` is equivalent (`pdr_run` is the console script).
`--workers`, `--cpus`, `--species`, `--json-template` and `--force-simline` (and `--rerun`, shown in the report) are honoured so that
the check sees what the real run would see.

What is checked (`name` as shown in the report):

| Group | Checks |
|---|---|
| Configuration | `config.file` (loaded? no `pdr:` section means the runner discards the file), `config.sections` (unknown sections abort a run), `config.model_params` (names the grid-axes key: `model_params` canonical, `model_parameters` accepted as alias, both = FAIL), `config.env` (which `PDR_*` variables override the file, and which the code silently IGNORES because the file defines that section), `config.secrets` (password set/not set; values are never printed), `python.imports` |
| KOSMA-tau install | `kt.base_dir`, `kt.rundir_write` (probe file), `kt.exe.pdr/onion/getctrlind/mrt` (exist + executable), `kt.pdr_version` (`pdrexe --version`, `-dirty` is a WARN), `kt.input_dirs` (symlinks resolve) |
| Templates and inputs | `tpl.json` (found, placeholders substituted as in a real job, parsed with json-fortran-style comments), `tpl.provenance` (config provenance is recorded; template sha256), `tpl.chem_network`, `tpl.binding_energies`, `tpl.fuv_file` (only if `ifuvtype` 5/6) |
| Scratch and disk | `tmp.dir` (writable), `disk.free` (per filesystem, WARN below `--min-free-gb`) |
| Storage | `storage` (local, sftp or rclone: write probe, read back, compare, delete; latency), `storage.local_copy` (rclone/sftp: root of the local copy, free space, example path) |
| Database | `db.connect` (+ server version), `db.tables`, `db.columns`, `db.additive_columns` (the 13 run-status / UV-continuum / config-provenance columns), `db.rows`, `db.stale_jobs` (running/stale rows, and pending/running rows of this `pdr.model_name`), `db.write_rollback` (INSERT rolled back) |
| Post-processing | `post.onion` (`ONION3.INP.<species>` for every species), `post.uv_continuum` (`kosma_h2` importable, data files), `post.simline` (driver, binary, molecules, config) |
| Resources | `run.walltime` (WARN if `pdr.max_walltime_s` is unset), `run.workers` (workers, CPUs, RAM, MySQL `max_connections`) |

`--check` is read-only: it never calls `create_tables()` or `ensure_additive_columns()` and never
creates a missing database file or storage directory. A missing column is a FAIL with the hint
that a normal run would add it. The only side effects are probe files / one rolled-back INSERT,
which are removed even when a check fails. Each check is isolated (an exception becomes a FAIL
line for that check only) and every network check has a timeout. During the check the console log
is suppressed; the detailed log still goes to `logs/pdr_run.log` (the footer prints the path).

Example (sandbox-like set-up with an old database that lacks a column):

```
[PASS] config.file           /tmp/ex/cfg.yaml (4 sections)
[PASS] config.env            no PDR_* environment overrides set
[PASS] config.secrets        DB password NOT set (values never printed); storage password not set
[PASS] kt.exe.pdr            mockpdr
[PASS] kt.pdr_version        Mock PDR executable running...
[PASS] tpl.json              /tmp/ex/rundir/templates/pdr_config.json.template (placeholders substituted…
[PASS] storage               local /tmp/ex/store: write/read/compare/delete OK (0 ms)
[PASS] disk.free             run dir+temp dir+storage+sqlite db+log dir 80 GB free (min 20 GB)
[PASS] db.connect            sqlite 3.49.1 at /tmp/ex/pdr.db (1 ms)
[PASS] db.tables             all 9 expected tables present
[FAIL] db.additive_columns   1/11 additive column(s) missing on pdr_model_jobs: uvcont_error - a normal run adds them (ensure_additive_columns); --check does not
[PASS] db.write_rollback     INSERT + rollback OK, no residue (1 ms)
[SKIP] post.uv_continuum     uv_continuum.enabled is false
[PASS] run.walltime          max_walltime_s=21600 s (6.0 h); stale threshold 9.0 h
  ... (about 32 lines in total)

Summary: 27 PASS, 1 WARN, 1 FAIL, 3 SKIP in 2.1 s -> NOT READY
Config: /tmp/ex/cfg.yaml | detailed log: /tmp/ex/logs/pdr_run.log | 0 WARNING+ log message(s) suppressed on screen
```

Exit codes: `0` nothing FAILed (WARN and SKIP are fine), `1` at least one FAIL. With `--check-json`
the object has `ok`, `summary` (counts), `checks` (`name`, `status`, `detail`, `elapsed_s`),
`config_file`, `log_files`.

## Grid Status Snapshot (`pdr_run status`)

Read-only JSON snapshot of one grid tier (one model name), the data source of the live
grid dashboard. It reads the job database only (SQLite `mode=ro` + `query_only`; MySQL
`SET SESSION TRANSACTION READ ONLY`, `SELECT` only; tables are reflected, nothing is created
or altered, `ensure_additive_columns` is never called), so it is safe every few minutes while
jobs run (0.05 s for 392 nodes, 0.1 s for 2535 nodes on SQLite). The only files it writes are
`--out` and, with `--with-physics`, the physics cache.

```bash
export PDR_DB_TYPE=mysql PDR_DB_HOST=... PDR_DB_DATABASE=... PDR_DB_USERNAME=pdr_dash PDR_DB_PASSWORD=...
pdr_run status --json --config grid1.yaml --model-name grid1_tier0 --out snapshot.json
pdr_run status --json --config grid1.yaml --since 2026-10-03T14:00:00Z --with-physics --with-local
pdr_run status --config grid1.yaml          # short human table
```

Database credentials come from `PDR_DB_*` exactly as for a run (environment > file >
defaults), so a read-only MySQL user (`GRANT SELECT`) is sufficient. The payload never
contains passwords, user names, DB host, or `config_json`; any configured secret that
appears in a text field (for example a `postproc_error`) is replaced by `***`.
`pdr_run status` is dispatched before the run CLI is imported (no `logs/` directory is
created). After updating an existing installation, re-run `pip install -e .` so that the
`pdr_run` console script points to `pdr_run.cli.entry`; `python -m pdr_run.cli.status` always works.

| Option | Meaning |
|---|---|
| `--config FILE` | pdr_run YAML (database section, `pdr.model_name`, `pdr.max_walltime_s`) |
| `--model-name NAME` | grid tier; default `pdr.model_name` |
| `--registry FILE` | optional grid registry YAML (default `<config stem>.grid.yaml` if present): `id`, `tier`, `facet_axis`, `workers`, `cores_per_job`, `notebook_entry`, `axes` (override of `key/param/label/unit/log/values`), `status_classes`, `struct_dir`, `physics.molfrac_source` |
| `--since TS` | delta mode: nodes, events and physics changed since TS (ISO; `Z`/offset allowed); `summary` stays complete |
| `--with-physics` | per-node physics from the stored `pdrstruct<node>.hdf5[.gz]` (needs h5py; skipped and reported otherwise) |
| `--struct-dir DIR` | where those files are (default `<model_path>/pdrgrid` from the database, if it exists locally) |
| `--physics-cache FILE` | cache keyed by (path, size, mtime); default `~/.cache/pdr_run/status_physics.json` |
| `--physics-budget S` | stop opening new HDF5 files after S seconds (default 20); the next call continues |
| `--with-local` | process/TEXTOUT progress of running jobs, load, memory, disk (Linux, optional) |
| `--out FILE` | write there (atomically) instead of stdout |

Node state is the latest non-`skipped` job row of the node (the `--rerun` rule); a node
with only skipped rows keeps its skipped row. Classes: `ok` (finished, skipped), `warn`
(finished_relaxed, flagged, or a post-processing error), `bad` (not_converged, aborted,
timeout, stalled, failed_storage, ...), `run`, `pending`. A node with `postproc_error` carries `warn_reason`
(if its class is `warn`; `"post-processing error"` or `"SIMLINE partial: <species> missing"`) and, for a
partial SIMLINE run, `simline_failed_species`. Axes are read from the parameter columns
(log10 of `xnsur`, `mass`, `sint`; `zmetal` only if it varies) and each node carries its
index into the axis values. `eta_inputs` holds the run-time samples (timeouts censored at
the cap), the queue and the worker count for the dashboard's own ETA; `summary.eta` is a
naive median/quantile estimate. Physics quantities per finished node (`q`):
`log_Tsurf`, `log_Tdeep` (gas T of the first/last filled zone), `log_H2IR_tot` (sum of
column 5 of `Spectrum IR small`), `op_col` (column o/p of H2), `AV_HH2` (A_V where
2n(H2)/(n(H)+2n(H2)) first reaches 0.5); an unavailable quantity is `null`. Dataset paths
and rules: docstring of `pdr_run/cli/status_physics.py`.

Schema: `pdr_run/schemas/status_v1.schema.json` (`"schema": "pdr_run.status/1"`, JSON Schema
draft 7; the tests validate every payload against it). Size (compact JSON, 30 % finished-type
mixture): 392 nodes about 130 KB (16 KB gzipped), 2535 nodes about 720 KB (78 KB gzipped);
a 30-minute delta is typically 10 to 60 KB.

Example (abridged; `nodes`, `events` and `physics` shortened):

```json
{"schema":"pdr_run.status/1","generated_at":"2026-10-01T13:58:26Z",
 "collector":{"version":"pdr_run 0.1.0+gabc1234","host":"halley","elapsed_s":0.03,"utc_offset_s":7200},
 "grid":{"id":"grid1","database":"pdr_grid1","model_name":"grid1_tier0","tier":"t0",
   "axes":[{"key":"n","param":"xnsur","label":"n_s","unit":"cm^-3","log":true,"values":[2.0,3.0,4.0]},
           {"key":"M","param":"mass","label":"M","unit":"Msun","log":true,"values":[0.0]},
           {"key":"chi","param":"sint","label":"chi","unit":"Draine","log":true,"values":[4.0]}],
   "facet_axis":"M","max_walltime_s":14400,"workers":6},
 "summary":{"total":3,"by_status":{"finished":1,"pending":1,"running":1},
   "by_class":{"ok":1,"warn":0,"bad":0,"run":1,"pending":1},"core_hours":3.0,
   "eta":{"p10":null,"p50":null,"p90":null,"model":"median-quantile-naive","n_fit":1}},
 "delta":false,
 "nodes":[{"id":"100_20_0_40_00","idx":[0,0,0],"job_id":1,"status":"finished","class":"ok","reruns":0,
           "t_start":"2026-10-01T10:58:26","t_finish":"2026-10-01T13:58:26","exec_s":10800.0,
           "converged":true,"global_it":7,"eps":0.002,"tsearch_flagged":0,"chem_relaxed":0}],
 "running":[{"job_id":2,"node":"100_30_0_40_00","elapsed_s":3601,"cap_s":14400,"frac_cap":0.25}],
 "eta_inputs":{"axes":["n","M","chi"],"samples":[{"idx":[0,0,0],"exec_s":10800.0,"censored":false}],
               "queue":[[2,0,0]],"workers":6,"cap_s":14400},
 "events":[{"t":"2026-10-01T13:58:26","kind":"job_finished","node":"100_20_0_40_00","sev":"info",
            "text":"job 1 finished, 3.00 h, 7 it"}],
 "storage":{"struct_dir_local":true,"stored_nodes":1,"backlog_nodes":0,"failed_storage":0},
 "urgent":[],
 "physics":[{"node":"100_20_0_40_00","job_id":1,
             "q":{"log_Tsurf":2.9013,"log_Tdeep":1.8237,"log_H2IR_tot":-1.9183,"op_col":1.0508,"AV_HH2":0.6694},
             "flags":{"zones":179,"AV_HH2":"ok","molfrac_source":"densities"}}],
 "physics_meta":{"h5py":true,"computed":1,"cached":0,"no_file":0,"failed":0,"deferred":0}}
```

Times are the naive local time of the collector host (`collector.utc_offset_s`), as stored by
pdr_run; only `generated_at` is UTC.

### Surveying a finished grid

`status --json` is the primary survey of a tier: `summary.by_status` / `by_class` (counts), `summary.core_hours`,
`nodes[]` with `idx` (position on the registry axes), `status`, `class`, `reruns`, `t_start`, `t_finish`, `exec_s`,
`converged`, `global_it`, `eps`, `tsearch_flagged`, `chem_relaxed`, `uvcont.applied` / `closure_ok`; `urgent[]`
(running jobs older than 1.5 x the cap), `storage` (stored, backlog and failed nodes), `events[]` (last 24 h by
default, `--since` for a window). With `--with-physics` the per-node physics of the stored model files is added
(surface/centre temperatures, columns, molecular fraction; cached in `--physics-cache`).

Grid-level checks that the snapshot does not do, with the tools used for grid-1 tier 0:

| Question | Source | Tool |
|---|---|---|
| Where do the flags cluster, and what do they mean? | `nodes[].tsearch_flagged`, `Solver health` dataset of every stored `pdrstruct<model>.hdf5` | read-only extraction over the store (h5py); classification A/B/C by residual, T continuity, column share and status bits: lab-notebook entry `2026-10-18_grid1-t0-flagged-quality` (`data/extract_health.py`, `analysis.py`, `data/classes.csv`) |
| Are the nodes smooth across the grid? | per-node observables (T_surface, T_deep, H2 line totals, columns, front positions) from the stored files | second differences along each axis against the layer envelope (same entry; `2026-10-17_grid1-tier0-healing` for the healed nodes vs their neighbours) |
| Reproducibility floor of a node | repeated runs with the same binary | noise-floor ensembles (`2026-10-12_noise-floor-tier0`): +-4.4 % per IR line, 2.4 % in the IR total at (5,0,4) |
| Wall time and iteration budget per layer | `nodes[].exec_s`, `global_it` | `status --json` (ETA model `eta.*`); calibrate `max_walltime_s` and `stall_first_iteration_s` from the finished nodes of the previous tier |
| Which binary computed a node? | `kosmatau_executables` row of the job, `/Parameters/Informations` of the file | `pdr_run status --json` does not list it yet; query the job table or the HDF5 attributes |

Export the snapshot to a file for the record of a tier (`--out status_<tier>_<date>.json`); the
grid-1 tier-0 survey of 2026-10-18 is the example referenced in the KOSMA-tau wiki page on run status.

## Querying the configuration of a job

The `kosmatau_parameters` columns are only the old PDRNEW.INP parameters; everything else (h2.*,
radiative_transfer.*, numerical_params.*, dust.*, the network file, species, abundances) is fixed in
the JSON template. To make it queryable, every job stores, when its `pdr_config.json` is rendered,

* `pdr_model_jobs.config_json` - the complete resolved config the job ran with (placeholders
  substituted), parsed to a normalized JSON object (comments and trailing commas, which json-fortran
  accepts, are removed; `JSON` on MySQL, text with the JSON functions on SQLite);
* `pdr_model_jobs.template_sha256` - sha256 of the template file it was rendered from.

The record belongs to the job (not to `json_files`, whose rows are deduplicated by file hash and
shared between jobs). A job that is skipped because its result already exists is rendered all the
same, so it carries the config it would have run with (a `--rerun` job gets the new one). If the
config does not parse, `config_json` is NULL and a WARNING is logged; the run is not affected.
Old databases get the columns automatically (`ensure_additive_columns`); jobs created before have NULL.

```sql
-- jobs with h2.gas_seed_h3p_shape = 1
SELECT id, model_job_name FROM pdr_model_jobs
 WHERE config_json->>'$.h2.gas_seed_h3p_shape' = '1';

-- the network file of a job
SELECT config_json->>'$.chemical_network_file' FROM pdr_model_jobs WHERE id = 123;

-- jobs whose species list contains "HCO18O+"
SELECT id FROM pdr_model_jobs
 WHERE JSON_CONTAINS(config_json->'$.species', '"HCO18O+"');

-- jobs rendered from a given template
SELECT COUNT(*) FROM pdr_model_jobs WHERE template_sha256 = '<sha256 from pdr_run --check>';
```

For a frequently used key, a generated column plus an index (MySQL 5.7+):

```sql
ALTER TABLE pdr_model_jobs
  ADD COLUMN h3p_shape INT GENERATED ALWAYS AS (config_json->>'$.h2.gas_seed_h3p_shape') VIRTUAL,
  ADD INDEX idx_h3p_shape (h3p_shape);
```

SQLite supports `json_extract(config_json, '$.h2.gas_seed_h3p_shape')` (and `json_each` for lists);
in SQLAlchemy use `PDRModelJob.config_json['h2']['gas_seed_h3p_shape'].as_integer()`.

## Configuration

The PDR framework supports multiple configuration methods with a clear precedence hierarchy. Configuration can be provided through environment variables, YAML configuration files, or a combination of both.

### Configuration Precedence

The framework follows this precedence order (highest to lowest priority):

1. **Environment Variables** (highest priority)
2. **Configuration File Settings** (YAML file)
3. **Default Configuration** (lowest priority)

This means:
- Environment variables always override config file settings
- Config file settings override default values
- If a setting is not specified in any location, the default value is used

### Database Configuration

#### SQLite (Default)
```bash
# Environment variables
export PDR_DB_TYPE=sqlite
export PDR_DB_FILE=/path/to/kosma_tau.db  # or ":memory:" for in-memory database
```

Or in your `config.yaml`:
```yaml
database:
  type: sqlite
  path: /path/to/kosma_tau.db  # or ":memory:" for in-memory database
```

#### MySQL Configuration
```bash
# Environment variables (recommended for passwords)
export PDR_DB_TYPE=mysql
export PDR_DB_HOST=localhost
export PDR_DB_PORT=3306
export PDR_DB_DATABASE=pdr_test
export PDR_DB_USERNAME=pdr_user
export PDR_DB_PASSWORD=your_secure_password  # Always use environment variable for passwords
```

Or in your `config.yaml`:
```yaml
database:
  type: mysql
  host: localhost
  port: 3306
  database: pdr_test
  username: pdr_user
  password: null  # Leave null - use PDR_DB_PASSWORD environment variable
  pool_recycle: 3600
  pool_pre_ping: true
  connect_args: {}
```

#### PostgreSQL Configuration
```bash
# Environment variables
export PDR_DB_TYPE=postgresql
export PDR_DB_HOST=localhost
export PDR_DB_PORT=5432
export PDR_DB_DATABASE=pdr_test
export PDR_DB_USERNAME=pdr_user
export PDR_DB_PASSWORD=your_secure_password
```

### Storage Configuration

#### Local Storage (Default)
```bash
export PDR_STORAGE_TYPE=local
export PDR_STORAGE_DIR=/path/to/storage/directory
```

Or in `config.yaml`:
```yaml
storage:
  type: local
  base_dir: /path/to/storage/directory
```

#### SFTP Storage
```bash
# Environment variables
export PDR_STORAGE_TYPE=sftp
export PDR_STORAGE_HOST=your-sftp-server.com
export PDR_STORAGE_USER=your_username
export PDR_STORAGE_PASSWORD=your_password  # Use environment variable for security
export PDR_STORAGE_DIR=/remote/path/to/storage
```

Or in `config.yaml`:
```yaml
storage:
  type: sftp
  host: your-sftp-server.com
  username: your_username
  password: null  # Leave null - use PDR_STORAGE_PASSWORD environment variable
  base_dir: /remote/path/to/storage
```

#### RClone Storage
```bash
export PDR_STORAGE_TYPE=rclone
export PDR_STORAGE_RCLONE_REMOTE=your_remote_name
export PDR_STORAGE_DIR=/path/to/local/mount/point
# Optional: Set a prefix to trim from remote paths to create cleaner directory structures.
export PDR_STORAGE_REMOTE_PATH_PREFIX=/path/to/trim
```

Or in `config.yaml`:
```yaml
storage:
  type: rclone
  rclone_remote: your_remote_name
  base_dir: /path/to/mount/point
  use_mount: false
  # Optional: Specify a path prefix to remove from the remote destination path.
  # This is useful for creating cleaner remote directory structures by removing
  # absolute local path components.
  remote_path_prefix: /path/to/trim/from/remote/destination
```

#### Local copy of remote results (`use_local_copy`)

For `rclone` and `sftp` storage, `storage.use_local_copy: true` (the default) also keeps every file after
its upload succeeded (and was verified) under a local root, with the same relative key as on the remote:

```yaml
storage:
  type: rclone
  base_dir: /data/grid1_store              # model_path = base_dir/<model_name>
  remote_path_prefix: /data/grid1_store    # stripped from the remote key
  use_local_copy: true
  local_copy_dir: null                     # default: base_dir (rclone); REQUIRED for sftp (base_dir is on the server)
```

- Relative key = the stored path below `remote_path_prefix` (else below `base_dir`). With `base_dir` equal to
  `remote_path_prefix`, a file lands exactly at its model path, e.g.
  `/data/grid1_store/<model_name>/pdrgrid/pdrstruct<model>.hdf5` for key `<model_name>/pdrgrid/...`.
- Files are copied exactly as stored: a compressed `TEXTOUT_x.gz` is kept as `.gz`.
- Written atomically (`<name>.part`, then rename). A failed copy logs a WARNING and never fails the job.
- A recomputed node (`--rerun`) uploads again and replaces its local copy. A node that is skipped because it
  exists remotely uploads nothing and leaves the local copy alone; `--rerun` is the way to refresh it.
- `--check` shows the root, the free space and an example path (`storage.local_copy`).

For an S3 server such as the Cologne one use `rclone_remote: kosmatau:<bucket>/<prefix>` (the bucket must
already exist) and `compress_files: ["TEXTOUT*", "pdrchem*.hdf5", "chemchk*.out"]`; see
[RClone storage on S3](#rclone-storage-on-s3).

## MySQL Setup

### Prerequisites

1. **Install MySQL connector**:
   ```bash
   pip install mysql-connector-python
   ```

2. **Start MySQL service** (using Docker for development):
   ```bash
   make start-services
   # or manually: cd sandbox && docker compose up -d mysql
   ```

### Database Setup

#### Using Sandbox (Recommended for Development)

The sandbox environment **automatically creates** the database and user when MySQL starts:

```bash
# 1. Setup sandbox directories
make setup-sandbox

# 2. Start services (MySQL auto-creates database and user)
make start-services

# 3. Test connection
python sandbox/test_db_connections.py
```

**Note:** No manual database creation required! Docker automatically creates:
- Database: `pdr_test`
- User: `pdr_user` with password `pdr_password`

#### Manual Setup (Production/Non-Docker)

If not using the sandbox, create the database and user manually:

```sql
CREATE DATABASE pdr_test;
CREATE USER 'pdr_user'@'%' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON pdr_test.* TO 'pdr_user'@'%';
FLUSH PRIVILEGES;
```

#### Configure Environment

```bash
export PDR_DB_TYPE=mysql
export PDR_DB_HOST=localhost
export PDR_DB_PORT=3306
export PDR_DB_DATABASE=pdr_test
export PDR_DB_USERNAME=pdr_user
export PDR_DB_PASSWORD=your_password
```

### Running MySQL Integration Tests

```bash
# Automated setup and testing
python pdr_run/tests/integration/run_mysql_tests.py

# Or manually
cd sandbox && docker compose up -d mysql
python pdr_run/tests/integration/test_mysql_integration.py
```

## SFTP Storage Setup

### Prerequisites

1. **Install paramiko** (usually included):
   ```bash
   pip install paramiko
   ```

2. **SFTP Server Access**: Ensure you have:
   - Hostname/IP address
   - Username and password (or SSH key)
   - Remote directory path with write permissions

### Configuration

1. **Environment variables** (recommended):
   ```bash
   export PDR_STORAGE_TYPE=sftp
   export PDR_STORAGE_HOST=hera.ph1.uni-koeln.de
   export PDR_STORAGE_USER=your_username
   export PDR_STORAGE_PASSWORD=your_password
   export PDR_STORAGE_DIR=/remote/path/to/pdr/storage
   ```

2. **Configuration file** (for non-sensitive settings):
   ```yaml
   storage:
     type: sftp
     host: hera.ph1.uni-koeln.de
     username: your_username
     password: null  # Use PDR_STORAGE_PASSWORD environment variable
     base_dir: /remote/path/to/pdr/storage
   ```

### Testing SFTP Connection

```bash
# Test storage functionality (if file exists)
python sandbox/test_storage.py

# Alternative: use integration tests
python pdr_run/tests/integration/test_storage.py

# Check configuration
python -c "
from pdr_run.storage.base import get_storage_backend
storage = get_storage_backend()
print(f'Storage type: {type(storage).__name__}')
print('Connection test passed!' if hasattr(storage, 'host') else 'Using local storage')
"
```

### SSH Key Authentication

For key-based authentication, ensure your SSH key is available:

```bash
# Add your key to ssh-agent
ssh-add ~/.ssh/your_private_key

# Or use environment variable for key path
export PDR_STORAGE_SSH_KEY=/path/to/your/private/key
```

## Configuration File Examples

### Complete MySQL + SFTP Configuration

Create `my_config.yaml`:
```yaml
# Database Configuration
database:
  type: mysql
  host: localhost
  port: 3306
  database: pdr_test
  username: pdr_user
  password: null  # Use PDR_DB_PASSWORD environment variable

# Storage Configuration
storage:
  type: sftp
  host: your-server.com
  username: your_username
  password: null  # Use PDR_STORAGE_PASSWORD environment variable
  base_dir: /remote/pdr/storage

# Model Configuration
pdr:
  model_name: production_run
  base_dir: /home/user/pdr/production

# Model Parameters
model_params:
  metal: ["100"]
  dens: ["30", "40", "50"]
  mass: ["5", "6", "7"]
  chi: ["1", "10", "100"]
  species:
    - CO
    - C+
    - C
    - O
```

### Environment Variables for Production

```bash
#!/bin/bash
# production_env.sh - Source this file for production environment

# Database (MySQL)
export PDR_DB_TYPE=mysql
export PDR_DB_HOST=production-db.company.com
export PDR_DB_PORT=3306
export PDR_DB_DATABASE=pdr_production
export PDR_DB_USERNAME=pdr_service
export PDR_DB_PASSWORD="$(cat /etc/pdr/db_password)"  # Read from secure file

# Storage (SFTP)
export PDR_STORAGE_TYPE=sftp
export PDR_STORAGE_HOST=storage.company.com
export PDR_STORAGE_USER=pdr_service
export PDR_STORAGE_PASSWORD="$(cat /etc/pdr/storage_password)"
export PDR_STORAGE_DIR=/data/pdr/models

# Optional: Additional settings
export PDR_EXEC_PATH=/opt/pdr/bin
```

Usage:
```bash
source production_env.sh
pdr_run --config production_config.yaml --grid
```

## Running a Test Model

Run a simple PDR model:

```bash
# Basic single-point model
pdr_run --model-name test_model --single --dens 3.0 --chi 1.0

# With specific configuration
pdr_run --config my_config.yaml --model-name test_model --single --dens 3.0 --chi 1.0
```

## Running with JSON Only

You can run the PDR model using only a JSON parameter file. The PDRNEW.INP template is optional. If it is missing, the workflow will proceed as long as a valid JSON template is available.

```bash
# Run with JSON only (no PDRNEW.INP.template required)
pdr_run --model-name test_model --json-template my_config.json.template --dens 3.0 --chi 1.0
```

If `PDRNEW.INP.template` is not found, a warning will be logged, but the model will run using the JSON configuration.

## Resource Management

The PDR framework manages resources from several locations:

### 1. Package Data Directory
The framework includes essential files within the installed package:
- Templates: `pdr_run/templates/`
- Reference data: `pdr_run/reference_data/`

### 2. Environment-Configured Locations
Resources are accessed via environment variables:
- `PDR_STORAGE_DIR`: Storage for model outputs
- `PDR_EXEC_PATH`: Location of executable binaries

### 3. Temporary Working Directory
For each model run:
1. A temporary directory is created
2. Required files are copied from package data
3. Configuration files are generated from templates
4. The model is executed
5. Results are stored in the database and storage directory
6. Temporary files are cleaned up (unless `--keep-tmp` is specified)

## Comparing with Example Script

The `example.py` script offers a simplified approach:
- Sets explicit paths to executables
- Creates temporary directories
- Runs the PDR model directly
- Copies input/output files manually

The `pdr_run` framework provides these advantages:
- Standardized command-line interface
- Parameter management through database
- Automatic file handling and cleanup
- Support for parameter grids
- Parallel execution capabilities
- Consistent logging and error handling

## Advanced Usage

### Parameter Grid Studies
```bash
# Run a grid of models with different density and radiation field values
pdr_run --model-name grid_test --dens 1.0 2.0 3.0 --chi 1.0 10.0 100.0
```

### Parallel Execution
```bash
# Run models in parallel
pdr_run --model-name parallel_test --parallel --workers 4 --dens 1.0 2.0 --chi 1.0 2.0
```

### Job order of a grid (`pdr.queue_order`)

The workers pull jobs from one list (joblib pool). `pdr.queue_order` (YAML, or `--queue-order` on the command
line, which wins) sets its order:

| value | order |
|---|---|
| `natural` (default) | nested parameter loops (metal, dens, mass, chi; density ascending as the outer loop), exactly as before |
| `cost_desc` | most expensive predicted job first (LPT) |
| `cost_asc` | cheapest first |

Sort key: density layer first (log n_s), then the predicted cost within the layer; ties keep the natural order. The
cost of a node is the median `execution_time` of the finished jobs of the same model name at the same (log n_s,
log chi), else at the same log n_s; without history the heuristic is (log chi descending, log M descending)
(`pdr_run/core/queue_order.py`). A fresh grid therefore starts with the highest density layer, highest chi, highest
mass; a grid restarted over a database with finished nodes uses their measured times. The first 10 jobs and their
predicted cost are logged at INFO at the start. Database job ids stay in natural order (only the hand-out order
changes), so `--rerun`, resume and `status` are unaffected. A grid that is already running is not changed; the
key applies at the next start. Effect (grid-1 tier-0 performance assessment): LPT -1.8 % wall at 6 workers vs
the tier-0 order; the main effect is the shorter tail (the long n_s = 1e7 jobs no longer run alone at the end).

### Configuration File Override Examples
```bash
# Environment overrides config file
export PDR_DB_PASSWORD=production_password
pdr_run --config config_with_different_password.yaml  # Uses production_password

# Mix of config file and command line
pdr_run --config base_config.yaml --model-name override_name --dens 5.0
```

### Switching Between Environments
```bash
# Development (SQLite + Local)
export PDR_DB_TYPE=sqlite PDR_STORAGE_TYPE=local
pdr_run --model-name dev_test

# Production (MySQL + SFTP)
export PDR_DB_TYPE=mysql PDR_STORAGE_TYPE=sftp
pdr_run --config production.yaml --model-name prod_run
```

## Run Status and Exit Handling

`pdrexe` exits with status 0 both when the global iteration converged and when it did not (many aborts
are a plain Fortran `STOP`). pdr_run therefore does not trust the exit code alone.
`pdr_run.models.job_status.determine_job_status()` classifies every run, in this order:

1. The first-iteration stall watchdog fired (see [First-iteration stall watchdog](#first-iteration-stall-watchdog))
   -> `stalled`; the wall-time cap fired (see [Wall-Time Cap](#wall-time-cap)) -> `timeout`.
2. `pdrexe` returned a non-zero exit code (any value, also a negative one from a signal) -> `aborted`.
3. `pdroutput/pdrstruct_s.hdf5` does not exist -> `missing_output`.
4. Otherwise the convergence outcome is read from **`pdroutput/run_status.json`** if that file exists.
   If it is absent or unparsable (a warning is logged, the run is not aborted), the fallback is one of
   three fixed lines in `pdroutput/TEXTOUT` (the captured screen output):
   `Model CONVERGED in N iterations (eps=...)`,
   `Model CONVERGED in N iterations (relaxed eps=..., strict eps=...)`,
   `Model NOT CONVERGED after N iterations (eps=...)`.
   If neither source gives a result (e.g. an older `pdrexe`), the job is `finished`, i.e. the legacy
   exit-code-only behaviour, and a warning is logged.

pdr_run reads only `run_status.json` and `TEXTOUT`. It does not open the HDF5 group
`/Parameters/Run status` of the model file.

Fields understood in `run_status.json` (all optional): `converged`, `global_iterations`, `eps_final`,
`tsearch_flagged_shells`, `chem_relaxed_calls`, `deferred_iterations`, `code_version`, `git_hash`.
`converged` must be one of the strings `strict`, `relaxed` or `no` (the values the TEXTOUT fallback
produces); any other value is logged and treated as `finished`.

What KOSMA-tau writes (reference: wiki page
[Run status, exit codes and solver flags](https://github.com/markusroellig/kosma-tau/wiki/Run-status,-exit-codes-and-solver-flags)):

- **Exit codes of `pdrexe`**: 0 = the global iteration ended (converged or not); 2 = controlled abort
  (`abort_run`, `run_status.json` with `converged = "no"` and `exit_reason = <tag>`, e.g. `nan_ebal`,
  `config_tsearch_tol_res_invalid`, `cmread_missing_formation_reaction`); 3 = the code's own chemistry stall
  watchdog (`feature/chem-seeding`, `exit_reason = "stalled"`, `stall_*` fields, `pdroutput/pdrstall_s.hdf5`);
  1 = Fortran `error stop` / runtime error; 137/143 = killed by pdr_run (cap or first-iteration watchdog).
  pdr_run maps every non-zero code to `aborted`; the `exit_reason` of the stored `run_status.json` tells which.
- **`tsearch_flagged_shells`** counts the shells of the final global iteration whose temperature-search status
  bit mask (`/Local quantities/Auxiliary/Solver health`, column 0) is non-zero: 1 residual not met,
  2 bracket collapsed, 4 chemistry not converged at the accepted T, 8 several roots, 16 no root in range,
  32 thermal front, 64 evaluation budget, 128 coarse scan needed, 256 inner map not settled. In grid-1 tier 0,
  95 % of the flagged shells carry only bit 256 (a self-consistency warning in the deep interior of dense
  clumps; balance residual <= 0.05); bits 4 and 16 are errors.
- **Chemistry counters** (`feature/chem-seeding` binaries with `numerical_params.chem_health_dataset = 1`):
  `chem_calls`, `chem_failed_calls`, `chem_newton_iterations`, `chem_seeds_from_failed`, `chem_seed_restores`,
  `chem_ptc_used`, `chem_ptc_failed`, `chem_ptc_polish_relaxed`, `chem_unconverged_shells`, `risk_class`
  (`low` / `slow-global` / `medium` / `high`). pdr_run passes them through untouched (they are not DB columns
  yet); read them from the stored `run_status<model>.json` or the HDF5 attributes.

### Job states

| `status` | Meaning | Post-processing (UV continuum, ONION, SIMLINE) | Result files stored |
|---|---|---|---|
| `finished` | converged (`strict`), no flagged shells (or legacy fallback, see above) | yes | yes |
| `finished_relaxed` | converged only under the relaxed criterion (`converged = relaxed`) | yes | yes |
| `flagged` | converged (`strict` or `relaxed`), but `tsearch_flagged_shells > 0`: at least one shell of the final iteration has a non-zero temperature-search status (see the bit list above); usable model, inspect the bits before a fit | yes | yes |
| `not_converged` | `converged = no`; wins over `flagged` | no | yes |
| `aborted` | `pdrexe` exit code != 0 | no | logs only |
| `missing_output` | exit code 0, but no `pdrstruct_s.hdf5` | no | logs only |
| `timeout` | wall-time cap exceeded, process group killed | no | logs only |
| `stalled` | first global iteration not finished within `pdr.stall_first_iteration_s`, process group killed | no | logs only |

Other values written by the framework: `running` (`active` = true), `skipped` (model already exists in
storage, see below), `ERROR` (exception while running `pdrexe`), `exception` and `exception_runtime` /
`exception_setup_outer` (exception in the worker; `exception_runtime` only for a genuinely unexpected
exception, never for a failed post-processing step; never written over a terminal status of the table above
or `failed_storage`), `failed_storage` (results could not be stored after all
retries, see [Storage Retries](#storage-retries)),
`reset_stale` (see [Stale-Job Recovery](#stale-job-recovery)) and the legacy `problem`. Every one of these
except `running` clears the `active` and `pending` flags of the job row.

### Database columns

`pdr_model_jobs` carries 12 additive, nullable columns (list `_PDR_MODEL_JOB_ADDITIVE_COLUMNS` in
`pdr_run/database/db_manager.py`, same columns as `PDRModelJob` in `pdr_run/database/models.py`):

| Column | Type | Meaning |
|---|---|---|
| `run_status_converged` | VARCHAR(20) | `strict`, `relaxed` or `no` |
| `run_status_global_iterations` | INTEGER | number of global iterations |
| `run_status_eps_final` | FLOAT | final convergence measure (TEXTOUT fallback: the strict eps in the relaxed case) |
| `run_status_tsearch_flagged_shells` | INTEGER | shells flagged by the temperature search |
| `run_status_chem_relaxed_calls` | INTEGER | chemistry calls accepted under the relaxed criterion |
| `run_status_deferred_iterations` | INTEGER | deferred iterations (from `run_status.json` only) |
| `run_status_code_version` | VARCHAR(100) | `pdrexe` version string (JSON only) |
| `run_status_git_hash` | VARCHAR(64) | `pdrexe` git hash (JSON only) |
| `uvcont_applied` | BOOLEAN | UV continuum was written to the model file |
| `uvcont_closure_ok` | BOOLEAN | photon-closure gate of the UV continuum tool passed |
| `uvcont_error` | TEXT | error message of the UV continuum step, if it failed |
| `postproc_error` | TEXT | error messages of failed ONION / SIMLINE steps (`ONION <species>: ...; SIMLINE: ...`); the job status is not changed by them |

The TEXTOUT fallback fills only `converged`, `global_iterations` and `eps_final`; the other `run_status_*`
columns stay NULL. For `aborted`, `missing_output`, `timeout` and `stalled` all `run_status_*` columns are NULL.

`ensure_additive_columns(engine)` adds any of these columns that is missing to an existing table with
`ALTER TABLE ... ADD COLUMN` (portable across SQLite, MySQL and PostgreSQL). It is called at the end of
`create_tables()`, so both a normal run and `--reset-stale-jobs` migrate an old database automatically; a
fresh database gets the columns from `create_all()`. It never raises: on failure a warning is logged and
the columns stay unpopulated. `pdr_run --check` does not migrate; it reports the missing columns
(`db.additive_columns`).

### Post-processing and result storage per status

The model's own status is kept. `run_kosma_tau` runs UV continuum, ONION and SIMLINE only for a usable
model: `finished`, `finished_relaxed`, `flagged` (`job_status.POSTPROCESS_STATUSES`). For every other status
this is skipped and logged, and the classification stays (`aborted`, `timeout`, `stalled`, `missing_output`,
`not_converged`).

What is stored (`copy_pdroutput`):

- Always: the screen log `TEXTOUT<model>`, `pdr_config<model>.json` and `PDRNEW<model>.INP` if present.
- For `aborted`, `timeout`, `stalled` and `missing_output` additionally `run_status<model>.json` (if the model
  wrote one) and `pdrexe_error<model>.log` (from `pdrexe_error.log`; gzip-compressed when
  `storage.compress_files` matches it, e.g. `pdrexe_error*.log`, and cut to head+tail when
  `storage.error_log_head_tail_bytes` is set, see [Whole-file compression](#whole-file-compression-of-stored-results)),
  but **no result files**: the output of such a
  run is partial, and a stored `pdrstruct<model>.hdf5` would be taken for a finished node by the
  skip-existing logic.
- For `finished`, `finished_relaxed`, `flagged` and `not_converged` (complete output,
  `job_status.COMPLETE_OUTPUT_STATUSES`) all result files: HDF4/HDF5, `chemchk`, `MCDRT`, `CTRL_IND`. A
  `not_converged` model is stored for inspection but not post-processed.

A failing ONION or SIMLINE step is logged, written to `postproc_error` and does not change the status or
prevent the storage of the model output. A failing UV continuum step goes to `uvcont_error` as before.
A file that cannot be stored after all retries gives the job the terminal status `failed_storage`; the other
files are still tried and nothing overwrites that status later. (The earlier status is then only in the log.)

### Exit code and summary line

At the end of a `--single` or grid run pdr_run prints one line, e.g.

```
Job states: 12 finished, 1 not_converged, 1 aborted, 0 failed_storage
```

(actual `status` values, most frequent first; `N with post-processing errors` is appended if any job has a
`postproc_error` or `uvcont_error`) and exits with

| Exit code | Meaning |
|---|---|
| 0 | every job ended in `finished`, `finished_relaxed`, `flagged` or `skipped` and no job has a post-processing error |
| 1 | some jobs failed: any other status (`not_converged`, `aborted`, `timeout`, `missing_output`, `failed_storage`, `exception*`, ...) or a recorded post-processing error |
| 2 | run-level error: the run raised an exception, or no job was run (e.g. missing PDR directory) |

Other errors that end the program before any job runs (unknown config section, failed `--check`) exit 1
as before. The outcome per node is also in the database; the columns to look at are `status`, `active`,
`pending`, `execution_time` (wall time of `pdrexe` only: `time_of_finish - time_of_start`; NULL for skipped nodes), `run_status_*`, `uvcont_*`, `postproc_error`, `time_of_start`, `time_of_finish`:

```sql
-- overview of a grid
SELECT status, COUNT(*) FROM pdr_model_jobs GROUP BY status;

-- everything that needs attention
SELECT id, model_job_name, status, run_status_converged, run_status_global_iterations,
       run_status_eps_final, uvcont_error, postproc_error
FROM pdr_model_jobs
WHERE status NOT IN ('finished', 'finished_relaxed', 'flagged', 'skipped') OR postproc_error IS NOT NULL;
```

Nodes are never retried automatically; use `--rerun` ([step 8](#8-rerun-failed-nodes)).

## Reacting to KOSMA-tau code flags

The job status says *that* a node needs attention; the KOSMA-tau flags say *why*. The table maps the
flags to the pdr_run action. The JSON options named are KOSMA-tau configuration keys (template level), not
pdr_run keys; a changed template or binary needs a copy of the pdr_run config (new `pdr_file_name`,
`json_template_file`, `exe_revision`) and a new `pdr_run --check`.

| Flag / status | Where to see it | Cause | pdr_run action |
|---|---|---|---|
| `timeout` after `max_walltime_s`, `stalled` after `stall_first_iteration_s`, or `aborted` with `exit_reason = stalled` (code watchdog, exit 3) | job status, stored `pdrexe_error*.log`, `run_status<model>.json` (`stall_worst_species`, `stall_av`) | chemistry stall at the sulphur/carbon front of dense clumps (n_s >= 1e6, chi >= 1e4); Newton solves re-seeded from failed solves | rerun with a binary from `feature/chem-seeding` and a template with `numerical_params.chem_seed_mode = 2`, `chem_failure_fallback = 1` (plus `chem_log_compact`, `chem_health_dataset`, `chem_risk_class`, `stall_watchdog` = 1): see [Healing with a second binary](#healing-with-a-second-binary) |
| `flagged`, flagged shells with bit 256 only, residual <= 1e-3 | `status --json` (`tsearch_flagged`), `Solver health` dataset | slow inner fixed point (levels/chemistry/balance) in cold dense gas | keep; quote the caveat for the deep columns; optional test with `tsearch_inner_max = 16` |
| `flagged` with bit 4, or `chem_unconverged_shells > 0` | `Solver health` column 9 = 0 at the flagged shell; `run_status` counters | chemistry not converged at the accepted temperature (n_s = 1e7, chi <= 1; S/C front) | `--rerun flagged` restricted to the node with the seeding binary; a node that stays "strict with unconverged shells" is kept as `flagged` (decision 2026-10-18) |
| `flagged` with bit 8 or 32 at A_V 0.05-0.5 of chi >= 1e5 nodes | `Solver health`, T jumps by a factor e across one shell | unresolved thermal front at the surface | rerun with `stepper_accuracy_scale = 0.5` if the surface lines matter; otherwise keep with caveat |
| `finished_relaxed`, or `global_it` >= 40 | `status --json` (`converged`, `global_it`) | global non-contraction (trace-species columns moving 3-20 % per sweep at n_s = 1e7) | no cure in the code yet; keep with caveat or exclude from fits; the seeding binary converged the three tier-0 cases after 19-31 iterations |
| `aborted` with `exit_reason` `nan_ebal` / `nan_species_density` | `run_status<model>.json` | NaN in the balance or chemistry (rate fits outside their range) | template with `clamp_rate_temperatures = 1`; rerun |
| `aborted` with `config_*` / `cmread_*` tags | `run_status<model>.json`, `pdrexe_error*.log` | invalid key value / network-species mismatch | fix template or network, `--check`, rerun |
| `not_converged` | job status | `max_iterations` reached | `--rerun not_converged` with a larger `numerical_params.max_iterations` or the seeding binary |
| `failed_storage`, `exception*`, `reset_stale` | job status | framework, not the code | [Storage Retries](#storage-retries), [Stale-Job Recovery](#stale-job-recovery), then `--rerun failed` |

### Healing with a second binary

Worked example: the nine stalled/timed-out nodes of grid-1 tier 0 (2026-10-18, lab-notebook entry
`2026-10-17_grid1-tier0-healing`). The grid stays on its frozen binary; only the missing nodes are recomputed
with a fixed binary, and the provenance of the mix is explicit in the database (own executable row, own
template hash) and in the files (`/Parameters/Informations`, embedded config).

1. Prepare an own base directory with symlinks to the grid base plus the new binary and template, and a
   **copy** of the grid config that differs only in `pdr.base_dir`, `pdr_file_name`, `json_template_file`,
   `exe_revision`, `compilation_date` (`configs/grid1_tier0_heal.yaml`). `model_name` stays the same, so the
   results land in the same store and job table.
2. `pdr_run --check --config configs/grid1_tier0_heal.yaml` until nothing FAILs.
3. Move the partial files of the nodes to be healed into an attic (`pdrgrid/attic_v240/`, `mv -n`, and a
   server-side copy on the remote) so that the old logs stay available.
4. Run exactly the missing nodes, restricting the parameter lists on the command line and keeping the total
   number of `pdrexe` processes within the machine limit:
   ```bash
   C="--grid --parallel --config configs/grid1_tier0_heal.yaml --rerun stalled,timeout"
   pdr_run $C --workers 3 --dens 60 --mass -30 -20 -10 --chi 60
   pdr_run $C --workers 2 --dens 70 --mass -20 -10 --chi 60
   ```
   `--rerun stalled,timeout` recomputes only nodes whose latest job is in one of these states; nodes already
   healed are skipped.
5. Acceptance gate: the pdr_run rerun must reproduce the reference run of the same node with the same binary
   bit for bit (full comparison of `pdrstruct<model>.hdf5`; allowed differences: `/Parameters/Informations`,
   the embedded config text, and the datasets pdr_run's post-processing extends: `Line emission IR`,
   `Intensity array length`, `Metadata`). In the tier-0 healing, 70 of 74 datasets were identical for every
   node, the four others only extended.
6. Document the mix: the healed nodes carry the second executable row; quote both binaries in any product
   of the grid.

## Wall-Time Cap

`pdr.max_walltime_s` (seconds, default `None` = no cap) limits the run time of each `pdrexe`. The value is
read by `run_pdr()` from the `pdr` section of the config file (default from `PDR_CONFIG`). `pdrexe` is
started in its own session (`start_new_session=True`, no shell), and `Popen.wait(timeout=max_walltime_s)`
is used. On expiry the whole process group is sent `SIGTERM`; if it is still alive after 10 s it gets
`SIGKILL`, and pdr_run waits up to 30 s more to reap it. The run is then classified `timeout` (no other
source is consulted), the error `wall-time cap of ...s exceeded, killing process group` is logged, and the
worker moves on. A timed-out job keeps the status `timeout` (no post-processing runs for it, only its logs
are stored), and `execution_time` holds the wall time up to the kill. Recompute timed-out nodes later with a
larger cap, e.g. `pdr_run --rerun timeout --config grid_longcap.yaml`. The cap applies to `pdrexe` only, not to ONION, SIMLINE (own
`simline.timeout`, default 3600 s) or the UV continuum (own `uv_continuum.timeout`, default 300 s).

```yaml
pdr:
  max_walltime_s: 21600   # 6 h per model
```

Choose the cap from the slowest node of the grid, not the average. `pdr_run --check` warns if it is unset
(`run.walltime`). The cap also sets the default stale threshold (below).

The cap also sets the idle timeout of the MySQL sessions (`wait_timeout` and `interactive_timeout`, set on
every new connection): `max(86400, 2 * max_walltime_s)` seconds, at most MySQL's maximum 31536000 s; 86400 s
without a cap. No worker holds a database session or transaction open while `pdrexe`, ONION, SIMLINE, the UV
continuum or the result uploads run, and pooled connections are re-checked on checkout (`pool_pre_ping`).
If a session cannot be closed (server dropped the connection), a WARNING is logged and the connection is
discarded; an exception in the worker after the run never replaces a terminal job status (`finished`,
`timeout`, `failed_storage`, ... stay; only a job without one gets `exception*`).

### First-iteration stall watchdog

`pdr.stall_first_iteration_s` (seconds, default `None` = off, behaviour unchanged) kills a `pdrexe` whose first
global iteration does not finish in time. While `pdrexe` runs, `run_pdr()` reads the new part of
`pdroutput/TEXTOUT` (the captured screen output) once a minute and looks for a progress line with an iteration
step >= 2,

```
***** current shell #:    1 ***** current iteration step:   2 ***********
```

which `pdrexe` prints when the second global iteration starts. If no such line has appeared
`stall_first_iteration_s` seconds after the start, the process group is killed exactly as for the wall-time cap
(SIGTERM, SIGKILL after 10 s) and the job is classified **`stalled`** (terminal, logs only, class `bad` in
`status --json`, selected by `--rerun failed` or `--rerun stalled`). Once step 2 has been seen the watchdog is
off and only `max_walltime_s` applies. No `pdrexe` change is involved. `pdr_run --check` names the setting in
`run.walltime` (WARN if it is not smaller than `max_walltime_s`).

Calibration on grid 1, tier 0 (321 finished nodes, 6 concurrent jobs on halley, from the stored TEXTOUT): the
first global iteration took 1.11-2.03 h (median 1.38 h; per density layer the maximum grows from 1.31 h at
n_s = 1e1 to 2.03 h at 1e6). The three `timeout` nodes `100_60_{-30,-20,-10}_60_00` never reached step 2: they
were at shell 544/443/337 after 30 h, stuck for hours in single shells. With 28800 s (8 h, ~4x the slowest
legitimate first iteration) each would have been stopped after 8 h instead of 30 h.

```yaml
pdr:
  max_walltime_s: 108000
  stall_first_iteration_s: 28800   # 8 h
```

## Stale-Job Recovery

A job that is `running` but whose driver process died (crash, reboot or power loss of the host, `kill -9`)
never reaches a terminal status: `pdrexe` either finishes or hits the wall-time cap while the driver lives,
so a `running` row long after any possible run time is abandoned. Such rows keep `active = true`.

```bash
pdr_run --reset-stale-jobs --dry-run --config my_config.yaml   # only report
pdr_run --reset-stale-jobs --config my_config.yaml             # set status 'reset_stale', active/pending = false
pdr_run --reset-stale-jobs --stale-after-hours 12 --config my_config.yaml
```

- `find_stale_jobs()` (`pdr_run/database/queries.py`) selects rows with `status = 'running'` and
  `time_of_start` older than the threshold. Rows with `time_of_start` NULL are never selected.
- Threshold, in order: `--stale-after-hours` (hours); otherwise `1.5 * pdr.max_walltime_s`; otherwise
  `DEFAULT_STALE_AFTER_S` = 6 h. `--stale-after-hours` accepts fractions.
- `--reset-stale-jobs` also resets rows that never started (`status = 'pending'`, `pending = 1`, `time_created`
  older than the threshold): the queued rows a killed driver leaves behind. `--check` reports the pending and
  running rows of the configured `pdr.model_name` (`db.stale_jobs`).
- A driver killed a few minutes ago leaves rows younger than the default threshold (1.5 x `max_walltime_s`, else
  6 h), which the reset skips. Give a smaller `--stale-after-hours` for them (`0` = every running/pending row),
  only when no pdr_run of that database is running (`--dry-run` first).
- `reset_stale_jobs()` only marks the rows (`reset_stale`, `active = false`, `pending = false`). It creates
  no replacement job and leaves the old row in place. The action runs and exits; no model is started.
- Every grid run also calls `find_stale_jobs()` after creating its job entries and only logs a warning
  (never fails); resetting is always an explicit operator action, so a job of a concurrent, unrelated
  pdr_run invocation is not touched behind your back.
- Use it after a crash or reboot of the host, **when no pdr_run of that database is still running**. If
  another driver runs on the same database with long nodes, set the threshold above the longest node.
- To recompute the reset nodes, run the same grid command again (see
  [Rerun failed nodes](#8-rerun-failed-nodes)).

## Storage Retries

Remote storage backends retry transient errors with bounded exponential backoff
(`pdr_run/utils/retry.py`, `retry_with_backoff(max_retries=3, initial_delay=2.0, backoff=2.0)`): 1 initial
attempt plus 3 retries, waiting 2 s, 4 s and 8 s. Each failed attempt is logged as a WARNING, the final
failure as an ERROR.

| Backend | Retried operations | Retried errors |
|---|---|---|
| SFTP | `store_file`, `retrieve_file`, `list_files`, `file_exists` | `paramiko.SSHException`, socket errors and timeouts, `ConnectionError`, `OSError`, `EOFError`; **not** retried: `FileNotFoundError` and `paramiko.AuthenticationException` (permanent) |
| rclone | `store_file` (S3: stat, delete, copyto, verify; other remotes: mkdir, copyto), `retrieve_file`, `list_files`, `sync_directory`, `file_exists` | `SubprocessError`, `RuntimeError`, `OSError` |

For rclone `file_exists`, only a stderr that looks like a transport problem (`timeout`, `connection
refused`, `no such host`, ...) is retried; an ordinary "path not found" is the normal answer for a node not
yet computed and is not retried. The local backend does not retry.

When the retries are exhausted, `store_file` keeps its old contract and **returns `False`** instead of
raising (`file_exists` returns `False`, treating the file as absent). Callers in `pdr_run.models.kosma_tau` (`copy_pdroutput`, `copy_onionoutput`, `run_simline`) check this
(a local-backend exception counts as a failure as well): the remaining files are still tried, and the job
gets the status `failed_storage`, which is not overwritten later. The reason is in the log
(`Storing ... failed after retries`).

Files are replaced safely: SFTP uploads to `<name>.part` and renames it over the target (`posix_rename`), the
local backend copies to `<name>.part` and uses `os.replace`; an interrupted transfer leaves an existing file
intact. rclone does not use a `.part` name: see "RClone storage on S3" below for how it replaces objects.
A leftover `.part` file is removed on failure (SFTP, local).

### Whole-file compression of stored results

`storage.compress_files` is a list of `fnmatch` patterns (default `[]` = nothing is compressed, behaviour
unchanged). A result file whose **stored name** (e.g. `pdrchem<model>.hdf5`, `chemchk<model>.out`) or whose
**local name** (`pdrchem_c.hdf5`, `chemchk.out`) matches a pattern is stored as `<name>.gz`:

```yaml
storage:
  compress_files: ["TEXTOUT*", "pdrchem*.hdf5", "chemchk*.out", "pdrexe_error*.log"]   # recommended for grid 1
  error_log_head_tail_bytes: null   # optional size guard, e.g. 500000000
```

- **`pdrexe_error*.log`**: the pdrexe log of a `timeout`/`stalled`/`aborted`/`missing_output` job (stored only
  for those) is stored through the same path and follows `compress_files`. The grid-1 tier-0 timeouts wrote
  4.3-5.4 GB each (millions of `Tier1 MAXIT fail` warnings); gzip level 6 gives ~17x (500 MB -> 29-31 MB).
  `storage.error_log_head_tail_bytes` (default `None` = stored in full) stores a larger log as its first and
  last N/2 bytes with a marker line `[pdr_run: K bytes omitted, original size S bytes; ...]` in between; the
  cut is applied before compression, the local log is not changed.

- Measured on a (5,0,0) node: `pdrchem_c.hdf5` 65 MB -> 21 MB, `chemchk.out` 29 MB -> 3 MB. Per-dataset HDF5
  compression does not help (the size is per-object overhead of 1000 small datasets).
- The file is compressed by streaming (gzip level 6, no file name or time in the header, so the same input
  gives the same bytes) into a temporary `<local file>.gz` next to the local output, uploaded with the normal
  atomic `.part` logic and retries, and removed afterwards, also on failure. A compression error counts like
  a storage failure (`failed_storage`).
- The database refers to what is stored: `HDFFile.file_name_hdf5_c`, `full_path_hdf5_c` and
  `PDRModelJob.output_hdf5_chem_file` / `output_chemchk_file` carry the `.gz` name, `sha256_sum_hdf5_c` and
  `file_size_hdf5_c` are checksum and size of the `.gz`. The uncompressed size is only logged
  (`Compressed ...: N -> M bytes`), there is no column for it.
- `pdrstruct<model>.hdf5` is never compressed (compressed internally, read directly by downstream tools),
  even if a pattern matches it. TEXTOUT, CTRL_IND and the other files are compressed only if matched.
- **Use `TEXTOUT*` for remote storage.** The screen log is large ASCII (10-20x smaller as `.gz`, a 22 MB test log
  gave 1.0 MB) and can reach > 1 GB for long runs; a co-author who wrote grids to the same S3 server had
  reproducible trouble with exactly these large TEXTOUT objects. The pattern matches the PDR log
  (`TEXTOUT<model>`), the ONION logs (`TEXTOUT<model>_<species>`) and the SIMLINE log
  (`SIMLINE<model>.TEXTOUT_SIMLINE`). The default stays `[]` (explicit is better than a silent format change for
  local runs); `pdr_run --check` shows a WARN for sftp/rclone storage when TEXTOUT is not covered.
  `compress_files` applies to the ONION and SIMLINE outputs as well, not only to the PDR step.
  The database column `output_textout_file` carries the `.gz` name.

### RClone storage on S3

Setup: `storage.type: rclone`, `rclone_remote: <remote>:<bucket>/<prefix>`, e.g.
`kosmatau:noices/grid1-2026`. Everything below the remote's base path is the key prefix; the model's
`model_path` is appended (`<base>/<model_path>/pdrgrid/<file>`). `base_dir` and `use_mount` are not used by the
rclone backend (the files are not written through a mount). `remote_path_prefix` is removed from the start of
`model_path` when it matches whole path components (`/home/u/runs` strips `/home/u/runs/m/x`, not
`/home/u/runs2/m/x`); use it to avoid absolute local path components in the keys. Keys are normalised
(no `//`, no leading `/` on object stores, `..` is refused), maximum 1024 bytes.

- **No buckets are created.** pdr_run never runs `mkdir` on S3 remotes (directories are key prefixes), and
  passes `--s3-no-check-bucket` so that a missing bucket gives a clear `NoSuchBucket` error ("ask an admin")
  instead of an attempt to create it (on the Cologne server `rclone mkdir <bucket>` hangs until the client
  timeout). The remote type is read from `rclone listremotes --long` (or `rclone_remote_type`, or the
  `RCLONE_CONFIG_<NAME>_TYPE` variable). Non-S3 rclone remotes keep `mkdir` (bounded by a timeout).
- **Overwriting an object.** S3 has no rename; an emulated rename is a server-side copy plus delete, which is
  not atomic and fails for large objects or on servers with a fragile copy. Instead `store_file` looks at the
  key (`lsjson`), deletes an existing object explicitly (`deletefile`), then uploads straight to the final key.
  A (multipart) upload becomes visible only when it completes, so a half-finished upload never shows up as a
  result. The window between delete and the end of the upload is covered by the status model: a failed store
  gives `failed_storage` and the node is recomputed/re-stored by `--rerun`. Deleting first also avoids the
  rclone rule "same size/hash, only the time differs: update the modification time with a server-side copy"
  (seen on the server: 300 MB re-upload of identical content took 1.2 s as a metadata copy).
- **Verification after every upload.** Size always, MD5 whenever the server reports one. On the Cologne server
  `rclone lsjson --hash` returns the MD5 also for multipart objects (rclone stores it as object metadata), so
  a 1.5 GB object is verified by checksum. A mismatch deletes and re-uploads (retry). Switch off with
  `rclone_verify: false`.
- **Large files.** Objects > `rclone_upload_cutoff_mb` (256) are uploaded in `rclone_chunk_size_mb` (64) chunks
  with `rclone_upload_concurrency` (4) parallel parts, raised automatically so that a file never needs more than
  9000 of the 10 000 allowed parts (limit about 560 GB at 64 MB). Memory per running upload is about
  chunk x concurrency (measured: 322 MB peak RSS for a 1.5 GB file); with 6 workers uploading at the same time
  budget about 2 GB. The 5 GB single-PUT limit is never reached (multipart above the cutoff).
- **Timeouts.** Every rclone call has `--contimeout 30s --timeout 300s` (idle) and a hard subprocess timeout:
  120 s for metadata calls, `300 s + size / rclone_min_rate_mb_s` (1 MB/s) for transfers. A hung call is killed
  and retried (2 s, 4 s, 8 s backoff, `rclone_max_retries`); it can never block a worker forever. rclone's own
  retries are set to 1 so that the pdr_run retry (which restarts delete + upload + verify) is the only one.
  Permanent errors (`AccessDenied`, bad key, `NoSuchBucket`, exit status 7, key too long) are not retried.
- **rclone binary and single-object lookups.** `rclone_binary` (default `rclone`, i.e. the one on `PATH`; `~` is
  expanded) selects the executable for every call, so a newer rclone can be used without replacing the system
  one, e.g. `rclone_binary: ~/bin/rclone-v1.75.1`. Its version is read once (`rclone version`). With rclone
  >= 1.57 every single-object lookup (overwrite check before an upload, verification after it, download size,
  existence check) is one `lsjson --stat [--hash] <key>`, which is one HEAD request on S3. Older rclone has no
  `--stat`: `lsjson --hash <key>` and `lsf <key>` list the whole parent prefix and filter it, so their cost grows
  with the number of objects next to the key. Measured on halley (2026-10-07, prefix
  `grid1_tier0/simlinegrid/` with 64 724 objects): rclone 1.53.3 `lsjson --hash` 9.9-14.9 s, `lsf` 8.6-15.8 s;
  rclone 1.75.1 `lsjson --stat --hash` 0.14-0.19 s; in `pdrgrid/` (1 776 objects) 0.36-0.64 s vs 0.15-0.20 s.
  Size and MD5 agree between the two versions (rclone 1.75 names the hash `md5`, 1.53 `MD5`; both are read).
  On S3, `lsjson --stat` of a missing key exits 0 with a directory entry; this counts as absent. rclone >= 1.57
  prints `NOTICE: s3: s3 provider "" not known` when the remote has no `provider` (harmless). `pdr_run --check`
  shows the version and the lookup method in the storage line.
- **Existence checks** use one `lsjson --stat` (rclone >= 1.57) or one `lsf` per candidate name; `lsf` exits 0 with empty output for a missing file in an
  existing directory and 3 ("directory not found") for a missing directory/bucket: both mean "absent". Any other
  failure (timeout, credentials) is retried and then logged as an ERROR (treated as absent, as before).
  The skip-existing test therefore costs one `lsf` (about 0.3 s) for a plain name and a second for `<name>.gz`.
- **Measured on halley (rclone 1.53.3, 2026-10-01):** small object 0.8 s, overwrite 1.0 s, 1.5 GB random file
  155 s upload with MD5 verification (about 10 MB/s), 146 s overwrite (delete + upload), 139 s download.
  After an aborted multipart upload (killed process) the unfinished parts stay on the server until they are
  aborted; ask the admin or run `rclone cleanup <remote>:<bucket>` (removes multipart uploads older than 24 h).
- **Files per node:** about 8-12 objects per node (`pdrstruct`, `pdrchem`, `chemchk`, `TEXTOUT`,
  `pdr_config`, `PDRNEW`, `CTRL_IND`, optional `pdr<model>.hdf` and `MCDRT`); ONION adds 7 per species and
  SIMLINE one object per output file. Each is a separate `copyto` (about 1 s on this server); at about
  230 nodes this is a few minutes in total, no bundling is done.
- `pdr_run --check` probes the real code path (write, overwrite, read back, compare, delete) with a probe object
  in the configured prefix, and reports "bucket missing; ask an admin" if the bucket is not listed by
  `rclone lsd <remote>:`. For a remote without bucket in `rclone_remote` it only warns.
- Skip-existing and `--rerun` look for `pdrstruct<model>.hdf5` and accept `pdrstruct<model>.hdf5.gz`; the
  skip path registers `.gz` names if only those are stored. `kosma_tau.retrieve_decompressed()` fetches a
  stored file uncompressed whether it is stored plain or as `.gz` (used for the SIMLINE input).
- Switching the setting for an already stored node: a new store writes the other variant next to the old one
  (`pdr_run` has no delete operation); remove the old file by hand. Read the `.gz` with `gunzip -k`, `zcat` or
  `h5py.File(io.BytesIO(gzip.open(f).read()))`.
- `pdr_run --check` shows the setting in the line `run.compression`.

### Placeholder checksums

If a node's `pdrstruct<model>.hdf5` already exists in storage, the PDR step is skipped (status `skipped`)
and `update_db_pdr_output_entries()` registers the file in table `hdf_files` (new entries; an existing entry only gets its paths updated) without
downloading it. The
`sha256_sum`, `sha256_sum_hdf5_s` and `sha256_sum_hdf5_c` columns then contain the constant
`UNVERIFIED:no-local-hash-model-skipped-exists-remotely` (`UNVERIFIED_CHECKSUM_SENTINEL` in
`pdr_run/models/kosma_tau.py`), never a hash. A real digest is 64 lowercase hex characters, so the
placeholder cannot be mistaken for one and any comparison against it fails visibly. The `file_size*`
columns are the real size for local storage and `0` for SFTP/rclone (logged as "without a verified size or
checksum"). Find such rows with:

```sql
SELECT id, file_name FROM hdf_files WHERE sha256_sum LIKE 'UNVERIFIED:%';
```

## Post-Processing Steps

Order inside one job (`run_kosma_tau`): `pdrexe` -> UV continuum (optional) -> ONION per species ->
SIMLINE (optional) -> result storage (`copy_pdroutput`). UV continuum, ONION and SIMLINE run only for a
usable model (`finished`, `finished_relaxed`, `flagged`; see
[Post-processing and result storage per status](#post-processing-and-result-storage-per-status)); their
failures are recorded in `postproc_error` / `uvcont_error`. ONION and SIMLINE read the local
`pdroutput/pdrstruct_s.hdf5`, so the file stored under `pdrgrid/` already contains their additions. If the
model already exists in storage (`skipped`), nothing of this runs unless forced.

### UV continuum

Adds the H2 dissociation continuum to the model file in place (about 5 s and 0.7 GB RSS per model, see the
`kosma_h2` documentation in the KOSMA-tau repository). Opt-in:

```yaml
uv_continuum:
  enabled: true
  kosma_tau_dir: /path/to/kosma-tau      # checkout containing h2py/; required
  python_executable: /path/to/python     # default: the interpreter running pdr_run
  timeout: 300                           # seconds (default)
  force: false                           # pass --force to the tool
  extra_args: []                         # extra CLI arguments, appended
```

- The tool is `<kosma_tau_dir>/h2py/postprocess_uv_continuum.py <workdir>/pdroutput/pdrstruct_s.hdf5`. It runs
  with `PYTHONPATH=<kosma_tau_dir>/h2py:$PYTHONPATH`, so `kosma_h2` is importable without changing the driver
  environment. The chosen `python_executable` needs `numpy` and `h5py`.
- Only jobs in a usable state (`finished`, `finished_relaxed`, `flagged`) are processed.
- Output: the datasets `Integrated quantities/Spectrum/UV Continuum/...` and a provenance line under
  `Parameters/Postprocessing` inside `pdrstruct_s.hdf5` (stored as `pdrgrid/pdrstruct<model>.hdf5`); the
  tool log is `pdroutput/TEXTOUT_UVCONT` in the temporary job directory (removed with it unless `--keep-tmp`).
- Exit code 0 -> `uvcont_applied = uvcont_closure_ok = true`. Exit code 3 (photon-closure gate rejected the
  write; file unchanged) -> both `false`, the job is **not** failed, a warning is logged. Any other exit code,
  a missing tool or model file, a missing `kosma_tau_dir` or a timeout -> the exception text is written to
  `uvcont_error`, the model stays in its physics status and the grid continues. `uvcont_error` is reset to
  NULL on success.

### ONION

Runs for every species of the job (`--species`, `model_parameters.species`; default list in
`pdr_run/config/default_config.py`). For each species `set_oniondir()` copies
`<pdr.base_dir>/onioninpdata/ONION3.INP.<species>` to `ONION3.INP`; a missing file makes that species fail
(text in `postproc_error`, status unchanged, the other species and the model storage continue), after
`pdrexe` has already run. Check the species list against `onioninpdata/`
before starting (`pdr_run --check`, `post.onion`). Results go to
`<model_path>/oniongrid/ONION<model>.<file>` for `jerg_`, `jtemp_`, `linebt_` and `ONION3_<species>.OUT`
files plus `TEXTOUT<model>_<species>`. The ONION exit status is not evaluated.
`--force-onion` also runs ONION for a skipped node.

### SIMLINE

Radiative-transfer post-processing through `<simline_dir>/python/run_simline.py`. Opt-in per config or per
run:

```yaml
simline:
  enabled: true
  species: [CO, 13CO]        # optional; default: the pipeline's own simline_config.json
  simline_dir: /path/to/kosma-tau/simline   # default: <pdr.base_dir>/simline
  config_file: /path/to/simline_config.json # default: <simline_dir>/python/simline_config.json
  timeout: 3600              # seconds (default)
  bundle_outputs: false      # true: side files as ONE archive SIMLINE<model>.tar.gz (default false)
```

```bash
pdr_run --grid --force-simline --config my_config.yaml   # also for nodes whose PDR step is skipped
```

- `--force-simline` runs SIMLINE even if `simline.enabled` is false and even if the model already exists in
  storage; in that case the model file is fetched from storage (`pdrgrid/pdrstruct<model>.hdf5`), which makes
  RT-only re-runs of an existing grid possible. Without the flag, a skipped node is not processed.
- Input/output: the pipeline works IN PLACE on the grid file. In a grid run that is
  `pdroutput/pdrstruct_s.hdf5`, which `copy_pdroutput` stores afterwards as `pdrgrid/pdrstruct<model>.hdf5`; in an
  RT-only rerun (and `scripts/backfill_simline.py`) the stored `pdrgrid/pdrstruct<model>.hdf5` is fetched, augmented
  and stored back under the same name (overwritten). The SIMLINE rows replace the ONION rows of the same species
  (`By species/<sp>`, `Metadata/...`, `Line emission IR`) and add the `SIMLINE output/...` tree. The side files go
  to `simlineoutput/` (log: `TEXTOUT_SIMLINE`).
- Concurrency: the pipeline writes `simline.obs` (per-model beam) and `simlineinp_<model>_<species>.*` into its
  `simline_dir`. Each job therefore runs with its own `<job tmp dir>/simline_job/` that only symlinks `bin/`,
  `molecules/` and `obs.template` from the configured `simline_dir` (passed to the pipeline through the per-job
  config), so parallel workers never share a written file and the configured `simline_dir` (e.g. a frozen,
  read-only base) is never written. The directory is removed with the job directory.
- Output in storage: the grid file `pdrgrid/pdrstruct<model>.hdf5` (no separate SIMLINE HDF5 object since
  2026-10-19; grids stored earlier still have `simlinegrid/pdrstruct<model>_simline.hdf5` and are not migrated
  automatically) plus, under `simlinegrid/`, the ~420 side files of
  `simlineoutput/` (FITS cubes, ASCII spectra, `TEXTOUT_SIMLINE`) in one of two layouts:
  - `bundle_outputs: false` (default): one object per file, `simlinegrid/SIMLINE<model>.<file>`
    (`storage.compress_files` applies per file, e.g. `TEXTOUT_SIMLINE.gz`).
  - `bundle_outputs: true`: ONE gzipped tar `simlinegrid/SIMLINE<model>.tar.gz` whose flat members have exactly
    the names of the file-by-file layout (`SIMLINE<model>.<file>`, `TEXTOUT_SIMLINE` uncompressed inside), so
    `tar -xzf SIMLINE<model>.tar.gz` in `simlinegrid/` reproduces the old layout. One upload instead of ~420:
    with rclone/S3 (~2.5 s per object incl. delete-before-overwrite and MD5 verify) this cuts the SIMLINE
    storage time of a node from ~15-20 min to a few seconds. The local copy (`use_local_copy`) holds the
    archive as stored. `scripts/backfill_simline.py` calls `run_simline` with the grid config and therefore
    uses the same layout.
  - Migration of grids stored with the old two-file layout: `python scripts/consolidate_simline.py --config
    <cfg>.yaml [--job-ids ...] [--limit N] [--dry-run] [--nice N] [--purge]`. Per node it verifies with h5py that
    the `_simline.hdf5` file contains every dataset of the grid file and that all common datasets outside the
    SIMLINE-written paths are bit-identical (otherwise the node is refused), stores the old grid file as
    `pdrgrid/attic_pre_simline/pdrstruct<model>.hdf5`, overwrites `pdrgrid/pdrstruct<model>.hdf5` with the SIMLINE
    file, verifies the stored object and the local copy, and leaves the `_simline.hdf5` object in place unless
    `--purge` is given. Nodes without a SIMLINE object, or whose grid file already has SIMLINE `By species`
    datasets, are reported as already consolidated; exit status 1 if a node was refused or failed. No database
    changes.
  - Reading either layout: `pdr_run.models.kosma_tau.fetch_simline_outputs(storage, model_path, model, dest_dir)`
    retrieves the side files of one node into `dest_dir` as plain `SIMLINE<model>.<file>` (archive if present,
    else the single files, `.gz` decompressed) and returns their names, so a grid with old-layout and bundled
    nodes reads uniformly. If a node has both (re-run with a different setting), the archive wins.
- A non-zero exit of the pipeline is recorded as `SIMLINE: ...` in `postproc_error`; the status of the job
  stays and `copy_pdroutput` still stores the model files.
- Partial runs: `run_simline.py` exits 0 as soon as ONE species succeeded. After an exit 0, `run_simline`
  therefore checks (`kosma_tau.check_simline_outputs`) the driver's `SUMMARY` in `TEXTOUT_SIMLINE`
  (`Failed species: ...`; a missing SUMMARY counts as incomplete) and, for every species of the driver's
  `Species:` header (else `simline.species`) not listed as failed, the expected outputs: at least one FITS
  `pdrstruct<model>_simline_<species>.<transition>.fits` in `simlineoutput/` and, if `h5py` is importable and
  the working copy is readable, a dataset `Integrated quantities/Intensities/By species/<species>` with
  attribute `source = "SIMLINE"` (ONION's datasets of the same name carry `ONION`). An incomplete run is stored
  in full and then recorded as
  `SIMLINE: partial: failed species C+, 13C+; missing outputs O [fits], O [hdf5]` in `postproc_error`; the job
  status stays (the model is fine). `scripts/backfill_simline.py` selects these nodes like any other
  `SIMLINE:` segment. If storing also failed, `failed_storage` takes precedence and the partial result is only
  logged. In `pdr_run status --json` such a node is class `warn` with `simline_failed_species` (failed plus
  missing-output species) and `warn_reason` (`"SIMLINE partial: C+, 13C+, O missing"`). `pdr_run --check` verifies driver, binary,
  `obs.template`, `molecules/` and the config (`post.simline`), and names the side-file layout.

## UQ campaigns

Blocks of the KOSMA-tau uncertainty study (noise ensembles A1a/A1b, iteration test A3, knob pairs B, rate Monte
Carlo C1, off-node nodes E2) are not grid nodes: the **same node** is run many times with a different override of
the template. The grid machinery (one database row and one stored result per node) cannot express that, so a
campaign is a **database-free side path**: `pdr_run campaign` reuses only the job-environment set-up, the
process-group kill, the stall watchdog and the job-state classification of the grid runner. No database row is
read or written (`pdr_model_jobs` is untouched, no `PDR_DB_*` environment is needed), nothing is uploaded to the
storage backend, no ONION/SIMLINE/UV-continuum step runs. The IR and UV spectra in `pdrstruct_s.hdf5` come from
`pdrexe` itself (`h2.level_treatment 1` in the template), so the single result file is complete.

Three pieces:

| piece | what it does |
|---|---|
| `pdr_run campaign FILE` (`pdr_run/cli/campaign.py`) | runs the jobs of a campaign file with one pinned binary, stores the results, writes the manifest |
| `tools/uq_jobs.py BLOCK ...` | generates the campaign file of a block |
| `tools/uq_collect.py CAMPAIGN_DIR` | collects all results of a campaign into one HDF5 (`collected.h5`) |

### Campaign file

YAML (or JSON). `pdr` is the `pdr` section of a grid config (the pinned binary lives in `pdr.base_dir`, see
[Production Grid Run](#production-grid-run) step 3); `template` is the grid-1 tier-0 template with its four node
placeholders.

```yaml
campaign:
  name: uq_a1a_20261021        # results: <out_dir>/<name>/<label>/
  block: A1a
  out_dir: /data/uq
  template: /path/pdr_config.json.grid1.template
  metal: 100                   # percent, grid convention
  workers: 6                   # concurrent pdrexe (halley: 7 is the memory limit)
pdr:
  base_dir: /path/to/copy_base
  pdr_file_name: pdrexe_uq_<hash>
  onion_file_name: onionexe    # must exist in base_dir (the job environment links them), they are not run
  getctrlind_file_name: getctrlind
  mrt_file_name: mrt.exe
  chem_database: chem_rates_grid1_2026-10.dat
  max_walltime_s: 86400
jobs:
  - label: a1a_n5_M0_x2_k03    # unique; directory name
    node: [5.0, 0.0, 2.0]      # log10 n_s, log10 M [Msun], log10 chi
    overrides:                 # deep-merged into the rendered template
      physical_params: {cosmic_ray_rate: 7.000000000021e-17}
```

The node is converted as the grid does (tenth-dex values via `10**(0.1*t)`, radius from the mass with the
template's `density_law_exponent`/`core_radius`, four-digit scientific formatting), so a campaign job on a grid
node has the input of the grid job; a job without overrides gets the substituted template byte-for-byte. Override
keys must exist in the template (typo guard); the `uncertainty` section of the `feature/uq-knobs` code is checked
against its key list (`pe_heating_scale`, `h2_formation_scale`, `grain_recombination_scale`, `crphot_scale`,
`h2star_scale.<cplus|splus|o|n|s|shplus|hplus>`, `collision_scale.<o_h|cplus_h|cplus_h2|h2_h>`,
`extra_global_iterations`, `reaction_factor_file`), and a binary without the section is refused when a job
uses it (the code would ignore the key silently).

### Running

```bash
pdr_run campaign FILE --check       # validate everything, write nothing; exit 0 = PASS
pdr_run campaign FILE --dry-run     # write manifest.json and one merged pdr_config.json per job, run nothing
pdr_run campaign FILE               # run; jobs whose model ran to the end and has a stored result are skipped (resume)
pdr_run campaign FILE --status      # table of the job states on disk
pdr_run campaign FILE --rerun [--only LABEL ...]    # recompute
```

Per job `<out_dir>/<name>/<label>/` holds `pdr_config.json` (the merged config that was run), `job.json`
(status, node, overrides, convergence fields, wall time, sha256 of config and result), `pdrstruct_s.hdf5` (only
for models that ran to the end: finished, finished_relaxed, flagged, not_converged), `run_status.json`,
`run.log.gz` (screen output). `<out_dir>/<name>/manifest.json` records the binary, template and network sha256,
the pdr_run commit, the host and the full job list with overrides; a resumed campaign with a different
binary/template/network is refused (`--force` overrides). The campaign file is copied next to the manifest. The
exit code is 1 when a job ended in a state other than finished/finished_relaxed/flagged.

### Generating a block

```bash
python3 tools/uq_jobs.py a1a --name uq_a1a_20261021 --out a1a.yaml --out-dir /data/uq \
    --template /path/pdr_config.json.grid1.template --pdr-from grid1.yaml \
    --base-dir /path/to/copy_base --pdr-file-name pdrexe_uq_<hash> \
    --nodes "5,0,2;6,0,3" --k-max 9
```

`--pdr-from` copies the `pdr` section of a grid config; the explicit `--base-dir`, `--pdr-file-name`,
`--chem-database`, `--max-walltime-s` override it. Nodes: `--nodes "n,M,chi;..."`, `--nodes-file`, and/or
`--nodes-grid "logn=4,4.5;logM=0;logchi=1,2"` (cross product). Labels are `<block>_n<logn>_M<logM>_x<logchi>_<variant>`.

| block | jobs per node | overrides |
|---|---|---|
| `a1a` | k = 0..`--k-max` (10) | `physical_params.cosmic_ray_rate` = zeta * (1 + k * 1e-12), zeta from the template (`--zeta`) |
| `a1b` | `ref`, one per `--rows` line, `iter1` | one-line `reaction_factor_file` (factor `--factor`, default 1.1, on the network line; written to `--factor-dir`); `uncertainty.extra_global_iterations 1` (a scaling of the initial abundances does not exist in the code) |
| `a3` | `iter0`, `iter1`, `iter2`, `iter4` (`--extra`) | `uncertainty.extra_global_iterations` |
| `b` | `ref` + every `--knobs` entry (default all 15) x `--factors` (0.5, 2) | `uncertainty.<knob>` |
| `c1` | `ref` + one per `factors_NNNN.txt` of `--factor-dir` (first `--n`) | `uncertainty.reaction_factor_file`; the files come from `tools/uq/uq_rates.py sample` of the kosma-tau repository |
| `e2` | one per node | none (half-decade nodes etc.) |

The unperturbed `ref` job (same binary, same machine) is part of every block where k = 0 / +0 iterations is not
itself the reference: a perturbation is only meaningful against a reference of the same binary.

### Collecting

```bash
python3 tools/uq_collect.py /data/uq/uq_a1a_20261021 [--uv-lines]      # -> <campaign_dir>/collected.h5
python3 tools/uq_collect.py --single pdrstruct_s.hdf5                  # one file, JSON to the screen
```

`collected.h5` has one row per job in manifest order (a job without result is a NaN row with `has_output = 0`):
`/jobs/*` (label, status, convergence, iterations, node, overrides_json, hashes), `/ir/line_table` and
`/ir/intensity` (all 4779 lines of `Spectrum IR small`), `/uv/total`, `/columns/species` and `/columns/N`,
`/structure/{T_gas_surface,T_gas_deep,AV_total}`, `/structure/front_AV/{H_H2,Cp_C,C_CO,Sp_S}` (A_V where
n(H) = 2 n(H2), n(C+) = n(C), n(C) = n(CO), n(S+) = n(S), log-linear crossing from the surface), `/uq/uq_*` (the
root attributes of the files). Unfilled surface rows (n_H < 1 cm^-3) are ignored.

Tests: `pytest pdr_run/tests/unit/test_uq_campaign.py` (no `pdrexe`: a shell script stands in for it).

## Production Grid Run

Walkthrough for a tiered grid on one compute host with a MySQL database. Hostnames, paths and passwords
below are placeholders.

### 1. Deploy the branch

```bash
git clone <repository-url> pdr_run && cd pdr_run
git checkout <production-branch>
python3 -m venv venv && . venv/bin/activate
pip install -e .            # installs the pdr_run console script
pdr_run --help
```

### 2. Set the database password

```bash
export PDR_DB_PASSWORD='...'    # from a secrets store; never in the config file or the shell history
```

The variable overrides `database.password` of the config file (see [Database Configuration](#database-configuration)).

### 3. Freeze the template and the binary

Every job runs in a fresh temporary directory in which `pdrexe` is a **symlink** to
`<pdr.base_dir>/<pdr.pdr_file_name>`, and `pdrinpdata/`, `onioninpdata/`, `In/` and `templates/` are copied
from `pdr.base_dir` per job. A rebuild of `pdrexe` or an edit of the template during a multi-day grid would
therefore change the model halfway. Freeze both before the start:

```bash
cd /path/to/pdr.base_dir
cp /path/to/kosma-tau/pdrsrc/bin/main/pdrexe pdrexe_grid1_20260930   # pinned copy, unique name
sha256sum pdrexe_grid1_20260930
cp /path/to/frozen/pdr_config.json.template templates/pdr_config.json.template
sha256sum templates/pdr_config.json.template
chmod a-w pdrexe_grid1_20260930 templates/pdr_config.json.template
```

The `KOSMAtauExecutable` table records the file name, code revision, compilation date and SHA-256 of the
executable, so the run stays traceable. Use `pdr.pdr_file_name` for the pinned copy; per-grid templates can
also be passed with `--json-template`. Keep the hashes in your run notes.

#### Grid-1 template

`templates/pdr_config.json.grid1.template` is the frozen KOSMA-tau configuration of grid 1 (2026-10). It sets
every grid-1 decision explicitly and leaves exactly four node placeholders, filled per job from the
`model_params` axes: `KT_VARxnsur_` (surface density), `KT_VARrtot_` (cloud radius, from the clump mass),
`KT_VARsint_` (FUV field) and `KT_VARzmetal_` (metallicity). Copy it to `<pdr.base_dir>/templates/` (or pass it with
`--json-template`) and record its SHA-256; every job also stores the hash (`template_sha256`) and the resolved
configuration (`config_json`, see [Querying the configuration of a job](#querying-the-configuration-of-a-job)).
`pdr_run --check` renders it as a real job would and FAILs on any `KT_VAR` token left unfilled (`tpl.json`).

### 4. Run `pdr_run --check` until READY

```bash
pdr_run --check --config grid1.yaml --workers 8
```

Fix every FAIL and read every WARN (see [Preflight Check](#preflight-check)); repeat until the summary reads
READY. Exit code 0 = no FAIL. Repeat the check after every change of config, template or binary.

### 5. Start the grid

```yaml
# grid1.yaml
database:
  type: mysql
  host: localhost
  port: 3306
  database: pdr_production
  username: pdr_service
  password: null              # PDR_DB_PASSWORD
  pool_size: 5
  max_overflow: 5

storage:
  type: local
  base_dir: /data/pdr/models  # results: <base_dir>/<model_name>/pdrgrid, oniongrid, simlinegrid
  compress_files: ["TEXTOUT*", "pdrchem*.hdf5", "chemchk*.out"]   # stored as .gz, ~3x (HDF5) / ~10-20x (ASCII) smaller

pdr:
  model_name: grid1_tier0
  base_dir: /path/to/pdr.base_dir
  pdr_file_name: pdrexe_grid1_20260930
  onion_file_name: onionexe
  getctrlind_file_name: getctrlind
  mrt_file_name: mrt.exe
  json_template_file: pdr_config.json.template
  chem_database: chem_rates_grid.dat
  max_walltime_s: 21600

uv_continuum:
  enabled: true
  kosma_tau_dir: /path/to/kosma-tau

simline:
  enabled: false

model_params:          # canonical key; 'model_parameters' is accepted as an alias (not both)
  metal: ["100"]
  dens: ["30", "40", "50"]
  mass: ["5", "6"]
  chi: ["1", "10", "100"]
  species: [CO, C+, C]
```

```bash
nohup pdr_run --grid --parallel --workers 8 --config grid1.yaml > grid1_tier0.out 2>&1 &
```

`--workers` is the number of worker processes (one `pdrexe` each); it is only used with `--parallel`. Without
`--workers`, the count is the number of CPUs minus `reserved_cpus` (a `model_parameters` key, default 2);
`--cpus` is accepted but only used by `--check`, not by a run. Size the workers to CPUs and RAM
(`pdrexe` plus the ~0.7 GB of the UV continuum), and check that `workers * (pool_size + max_overflow)` does not
exceed the MySQL `max_connections` (`run.workers` reports it). `--model-name` on the command line overrides
`pdr.model_name`. `pdr_run` creates all job rows first (`pending = true`), then runs them.

### 6. Monitor

```bash
tail -f logs/pdr_run.log                                    # detailed log (PDR_LOG_DIR changes the directory)
mysql -u pdr_service -p pdr_production -e \
  "SELECT status, COUNT(*) FROM pdr_model_jobs GROUP BY status"
```

Watch `running` (should not exceed `--workers`), the `timeout`, `aborted`, `not_converged`, `failed_storage` and `exception*`
counts, and `time_of_start` of the oldest `running` job against `max_walltime_s`. The stored `TEXTOUT<model>`
of a node (`pdrgrid/`) is the screen output of that model.

### 7. Recover stale jobs after interruptions

After a crash or reboot of the host, first make sure no `pdr_run` and no `pdrexe` of the grid is left
(`pgrep -af pdr_run`, `pgrep -af pdrexe_grid1`), then:

```bash
pdr_run --reset-stale-jobs --dry-run --config grid1.yaml
pdr_run --reset-stale-jobs --config grid1.yaml
```

See [Stale-Job Recovery](#stale-job-recovery). Then restart the same grid command (step 8).

### 8. Rerun failed nodes

Rerunning the same command (same `model_name` and parameter lists) creates new job rows for every node whose
earlier row is no longer `pending` and, by default, skips (status `skipped`) every node whose
`<model_path>/pdrgrid/pdrstruct<model>.hdf5` already exists in storage. Consequently:

- Nodes that never produced a file (`aborted`, `timeout`, `missing_output`, `reset_stale`, exceptions before
  storage) are recomputed by simply restarting the command (their partial output is not stored, see above).
- Nodes whose result is stored but should be recomputed (`not_converged`, `flagged`, `failed_storage`, or
  after a change of binary/template) need **`--rerun STATE[,STATE...]`**:

  ```bash
  pdr_run --grid --config grid1.yaml --rerun not_converged        # only these
  pdr_run --grid --config grid1.yaml --rerun failed               # every non-success state
  pdr_run --grid --config grid1.yaml --rerun all                  # everything, also converged nodes
  ```

  `STATE` is `all`, `failed` (every state except `finished`, `finished_relaxed`, `flagged`, `skipped`) or a
  literal job status (`not_converged`, `aborted`, `timeout`, `failed_storage`, ...). The state of a node is
  the status of the most recent earlier job of that node in the database that computed it (`skipped` rows
  are ignored); a stored file without such a row is recomputed only by `all`. Without `--rerun` the behaviour
  is unchanged. The new job is recorded as a new row, and the stored files are replaced only when the
  new ones are written (write, then rename); if the recomputation fails, the old stored files stay and the
  new job row carries the failure. `--check` accepts `--rerun` and shows it in the report.
- Skipped nodes get `UNVERIFIED:` placeholder checksums in `hdf_files` (see above).
- To re-run a restricted set, restrict the parameter lists on the command line (`--dens`, `--chi`, ...
  override the config file); `--single` runs one node.
- A changed binary or template needs a new `pdr_file_name` / template and a new `pdr_run --check`.

### 9. Where results and logs are

| What | Where |
|---|---|
| Model files | `<storage.base_dir>/<model_name>/pdrgrid/`: `pdrstruct<model>.hdf5`, `pdrchem<model>.hdf5`, `pdr<model>.hdf` (if written), `TEXTOUT<model>`, `chemchk<model>.out`, `pdr_config<model>.json`, `CTRL_IND<model>`, `MCDRT<model>.tar.gz` |
| ONION | `<model_name>/oniongrid/ONION<model>.*`, `TEXTOUT<model>_<species>` |
| SIMLINE | `<model_name>/simlinegrid/SIMLINE<model>.*` (side files only; the HDF5 results are in `pdrgrid/pdrstruct<model>.hdf5`) |
| Job table | `pdr_model_jobs` (status, run-status and UV continuum columns, file paths); `hdf_files` (paths, checksums, sizes) |
| Driver log | `logs/pdr_run.log` (directory `PDR_LOG_DIR`, default `logs/`, relative to the working directory) and the redirected stdout |
| Temporary job directories | `pdr-job<ID>-*` in the system temp directory, removed after the job unless `--keep-tmp` |

`<model>` is `<metal>_<dens>_<mass>_<chi>_00`. With SFTP or rclone storage the same layout lives below the
remote `base_dir`.

## Troubleshooting

### Running Tests
Verify the framework is working correctly:

```bash
# Run the full test suite
cd /home/roellig/pdr/pdr/pdr_run/
python -m pytest pdr_run/tests/

# Run database-specific tests
make test-db

# Run MySQL integration tests
python pdr_run/tests/integration/run_mysql_tests.py
# The tests that DROP a database or user are skipped unless explicitly allowed, and they only
# touch test_-prefixed names (user test_pdr_user, database test_pdr_<id>) - never the production database
PDR_ALLOW_DESTRUCTIVE_DB_TESTS=1 python -m pytest pdr_run/tests/integration/test_mysql_integration.py

# Run storage tests
make test-storage
```

### Common Issues

#### Database Issues

1. **MySQL Connection Errors**:
   ```bash
   # Check if MySQL is running
   docker ps | grep mysql
   
   # Start MySQL service
   cd sandbox && docker compose up -d mysql
   
   # Test connection manually
   mysql -h localhost -u pdr_user -p pdr_test
   ```

2. **Password Authentication Failures**:
   - Always use `PDR_DB_PASSWORD` environment variable
   - Never hardcode passwords in config files
   - Check password contains no special characters that need escaping

3. **Missing MySQL Driver**:
   ```bash
   pip install mysql-connector-python
   ```

#### SFTP Storage Issues

1. **Authentication Failures**:
   ```bash
   # Test SFTP connection manually
   sftp your_username@your-server.com
   
   # Check environment variables
   echo $PDR_STORAGE_PASSWORD
   ```

2. **Permission Denied**:
   - Ensure remote directory exists and is writable
   - Check SSH key permissions (600 for private keys)
   - Verify user has access to the specified base directory

3. **Network/Firewall Issues**:
   ```bash
   # Test network connectivity
   ping your-server.com
   telnet your-server.com 22
   ```

#### Configuration Issues

1. **Environment Variable Not Recognized**:
   ```bash
   # Check current environment
   env | grep PDR_
   
   # Verify precedence
   python -c "
   from pdr_run.database.db_manager import DatabaseManager
   manager = DatabaseManager()
   print(f'Database type: {manager.config[\"type\"]}')
   print(f'Password source: {\"env\" if manager.config[\"password\"] else \"config\"}')"
   ```

2. **Config File Parsing Errors**:
   ```bash
   # Validate YAML syntax
   python -c "import yaml; yaml.safe_load(open('my_config.yaml'))"
   ```

### Database Password Issues

- If you see errors like `Access denied for user ... using password: YES` or the password in the connection string appears as `None`, it means the password was not set correctly.
- Always set your database password using the `PDR_DB_PASSWORD` environment variable:
  ```bash
  export PDR_DB_PASSWORD=your_db_password
  ```
- The framework will automatically use this value and override any value in the config file.
- For security, avoid hardcoding passwords in configuration files.

### SFTP Connection Issues

- For `[Errno 2] No such file or directory: ''` errors, check that local directory paths are properly specified
- Use `PDR_STORAGE_PASSWORD` environment variable for SFTP passwords
- Check SFTP server logs for authentication issues
- Verify network connectivity and firewall settings

### Known Limitations

Behaviour of the existing code that was found while writing the preflight check, and what could not be
verified without the production host, is listed in
[`docs/PREFLIGHT_FINDINGS_2026-09-30.md`](docs/PREFLIGHT_FINDINGS_2026-09-30.md). Also note:

- `--cpus` and `--random` are accepted by the argument parser but have no effect on a run (`--cpus` is used
  by `--check` only).
- A `--rerun` of a node that fails again keeps the old stored files (only the new job row shows the failure).

### Checking Logs
```bash
# View the last run log (if it exists)
ls -la logs/ && cat logs/pdr_run.log 2>/dev/null || echo "No log file found yet"

# View paramiko (SFTP) logs (if SFTP is used)
cat logs/paramiko.log 2>/dev/null || echo "No SFTP log file found"

# Check Docker service logs
cd sandbox && docker compose logs mysql

# List all available logs
find . -name "*.log" -type f 2>/dev/null || echo "No log files found"
```

### Configuration Debugging

```bash
# Print current configuration
pdr_run --model-name debug_config --dry-run

# Check storage backend
python -c "
from pdr_run.storage.base import get_storage_backend
storage = get_storage_backend()
print(f'Storage: {type(storage).__name__}')
if hasattr(storage, 'host'):
    print(f'Host: {storage.host}')
    print(f'User: {storage.user}')
    print(f'Base dir: {storage.base_dir}')
"

# Check database configuration
python -c "
from pdr_run.database.db_manager import get_db_manager
manager = get_db_manager()
print(f'DB Type: {manager.config.get(\"type\")}')
print(f'DB Host: {manager.config.get(\"host\", \"N/A\")}')
print(f'DB Name: {manager.config.get(\"database\", manager.config.get(\"path\", \"N/A\"))}')
"
```

## Development

### Setting Up the Development Environment

1. Clone the repository and navigate to the PDR framework:
   ```bash
   cd /home/roellig/pdr/pdr/pdr_run/
   ```

2. Install in development mode:
   ```bash
   make dev-install
   ```

3. Set up the sandbox environment:
   ```bash
   make setup-sandbox
   make start-services
   ```

4. Run tests:
   ```bash
   make test-all
   ```

### Sandbox Environment

The sandbox provides MySQL and SFTP services for development:

```bash
# Start all services
make start-services

# Reset and clean environment
make clean-sandbox

# Test services individually
make test-db
make test-storage
make test-integration

# View service logs
make logs

# Restart services
make restart

# Complete development setup
make full-dev-setup
```

See SANDBOX_README.md for detailed development instructions.

---

For more details, consult the full documentation or reach out to the development team.