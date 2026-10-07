# Changelog

All notable changes to pdr_run. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Details and configuration of every item are in the [README](README.md); the commit hash is given per item.

## [Unreleased] - branch `feature/preflight-check` (grid-1 production readiness, 2026-09-29 .. 2026-10-02)

Prepared for the KOSMA-tau grid 1, tier 0 run on halley (MySQL database, rclone/S3 storage, 6 workers).
Deployed on halley at `d4fd3ec`; rollback target `master` (`dc215a9`).

### Added

- **SIMLINE output bundling** `simline.bundle_outputs` (default `false`): the ~420 side files of a node's
  `simlineoutput/` are stored as ONE archive `simlinegrid/SIMLINE<model>.tar.gz` (flat members named as in the
  file-by-file layout) instead of one object each; `pdrstruct<model>_simline.hdf5` stays its own object. With
  rclone/S3 (~2.5 s per object) the SIMLINE storage of a node drops from ~15-20 min to seconds (one real tier-0
  node: 419 files, 26.4 MB -> 11.0 MB archive, tar 0.54 s). New reader `kosma_tau.fetch_simline_outputs` returns
  the same plain files from either layout (mixed grids); `backfill_simline.py` follows the config; `--check`
  (`post.simline`) names the layout; key added to the config validation. Default false keeps the stored layout
  of existing configs unchanged; the grid config opts in. See [SIMLINE](README.md#simline).
- **SIMLINE backfill** `scripts/backfill_simline.py`: re-runs `run_simline` sequentially (optional `--nice`)
  for `finished` jobs of a model whose `postproc_error` has a `SIMLINE:` segment; on success the segment is
  removed (NULL if nothing remains), on a new failure it is replaced, a storage failure gives `failed_storage`
  as in a grid run. Options `--dry-run`, `--limit`, `--job-ids`, `--fail-dir`. Used for the grid-1 tier-0 nodes
  whose SIMLINE failed before the `tools/config_utils.py` fix of 2026-10-04.
- **Preflight check** `pdr_run --check` (`10a5673`): reports READY / NOT READY for a production run without
  starting a model. Checks the config file and sections, environment overrides, secrets (set/not set, never
  printed), the KOSMA-tau install (`pdrexe --version`, `-dirty` = WARN), the rendered template, chemical
  network, binding energies and FUV file, scratch and disk space, a storage write/read/delete probe, the
  database (tables, additive columns, stale rows, rolled-back INSERT), post-processing inputs and resources
  (wall-time cap, workers, CPUs, RAM, MySQL `max_connections`). See [Preflight Check](README.md#preflight-check).
- **Run-status classification** (`08f441d`): every job is classified `finished`, `finished_relaxed`,
  `flagged`, `not_converged`, `aborted`, `missing_output` or `timeout` from the wall-time cap, the exit code,
  the presence of `pdrstruct_s.hdf5`, `pdroutput/run_status.json` and the fixed convergence lines of
  `TEXTOUT`. New additive columns `run_status_*` hold the convergence details.
- **Wall-time cap** `pdr.max_walltime_s` (`08f441d`): `pdrexe` runs in its own process group; on expiry the
  group gets SIGTERM, then SIGKILL after 10 s, and the job is `timeout`. `--check` warns when the cap is unset.
- **UV continuum post-processing** (`08f441d`): H2 dissociation continuum via `kosma_h2` for finished jobs,
  results in the `uvcont_*` columns; own `uv_continuum.timeout`.
- **Stale-job recovery** `--reset-stale-jobs [--stale-after-hours H] [--dry-run]` (`e6d4161`, `461174a`):
  marks `running` rows of a dead driver and never-started `pending` rows older than the threshold
  (`1.5 x max_walltime_s`, else 6 h) as `reset_stale`. Every grid run warns about stale rows.
- **`--rerun STATE[,STATE...]`** (`96ac9f1`): recomputes stored nodes in the given states (`all`, `failed`
  or literal job statuses, e.g. `timeout`); the default still skips existing nodes.
- **Exit codes** (`96ac9f1`): 0 = all jobs succeeded or were skipped, 1 = some jobs failed or a
  post-processing step failed, 2 = run-level error; a job-state summary line is printed at the end.
- **Column `postproc_error`** (`96ac9f1`): failures of ONION, SIMLINE or the UV continuum are recorded here
  and no longer overwrite the model status.
- **Whole-file gzip** `storage.compress_files` (`eeb2df6`): glob list of result files stored as `.gz`
  (recommended: `TEXTOUT*`, `pdrchem*.hdf5`, `chemchk*.out`).
- **Config provenance** (`53abbc9`): columns `pdr_model_jobs.config_json` (the resolved `pdr_config.json`
  as a JSON object, queryable with `JSON_EXTRACT`) and `template_sha256`. See
  [Querying the configuration of a job](README.md#querying-the-configuration-of-a-job).
- **Grid-1 template** `templates/pdr_config.json.grid1.template` (`cf5cf26`, `0b3ce15`, `e0ca7a8`): the
  frozen KOSMA-tau grid-1 configuration with four node placeholders (density, radius, FUV, metallicity),
  U-1 on (`heating.cr_heating_per_ionisation 1`).
- **Grid-1 templates with decision C4-(b)** `templates/pdr_config.json.grid1_cs0.template` and
  `pdr_config.json.grid1_crA_cs0.template` (2026-10-02): the grid-1 template (and its CR-attenuation variant A)
  plus `radiative_transfer.carbon_shielding_convergence 0` and `carbon_shielding_relaxation 1.0` (variant A also
  `cosmic_ray_rate 1.0e-16`, co-author decision 2026-10-02) and `h2.grain_op_tau_c 1000` (decision 2026-10-02 after
  the gate 2026-10-02_grid1-tauc1000-gd); generated by
  `make_grid1_cs0_templates.py` in the KOSMA-tau repository. The final tier-0 template is frozen from one of them
  after the CR-attenuation gate; `pdr_config.json.grid1.template` stays as the record of 2026-10-01.
- **Local copy of remote results** `storage.use_local_copy` / `storage.local_copy_dir` (`461174a`): after
  a verified rclone or SFTP upload the stored file is also written atomically under a local root with the
  same relative key. A failed copy is a WARNING, never a failed job.
- **Column `execution_time`** (`461174a`): wall time of `pdrexe` per job (NULL for skipped nodes).
- **`model_params` alias** (`461174a`): the grid axes are read from `model_params` (canonical) or
  `model_parameters` (alias); giving both is an error, and `--check` names the key in use.
- **Grid status snapshot** `pdr_run status [--json]` (`6e5a5c5`, from `feature/status-json` `78caec4`): strictly
  read-only view of one grid (SQLite `mode=ro`, MySQL `READ ONLY` session, SELECT only) for the Grid Run Monitor
  dashboard; schema `pdr_run.status/1` (`pdr_run/schemas/status_v1.schema.json`). Per-node state (latest
  non-skipped row), running jobs with elapsed time vs the wall-time cap, run-time samples and ETA inputs,
  `--since` events, optional local probes (`--with-local`) and MVP physics (`--with-physics`, h5py: surface/deep
  gas T, H2 IR line total, column o/p, A_V of the H/H2 front; cached per file). `config_json`, user names and
  passwords never enter the payload; configured secrets are scrubbed from the text. Intended for a read-only
  database user. See [Grid Status Snapshot](README.md#grid-status-snapshot-pdr_run-status).

### Changed

- Post-processing (UV continuum, ONION, SIMLINE) runs only for `finished`, `finished_relaxed` and `flagged`
  jobs (`96ac9f1`). Logs (`TEXTOUT`, `run_status.json`, `pdrexe_error.log`) are stored for every status,
  result files only for complete outputs (including `not_converged`).
- Uploads are written to `<name>.part` and renamed (SFTP, local) (`96ac9f1`).
- Storage retries are bounded and a storage failure after the retries is the job status `failed_storage`
  (`e6d4161`, `96ac9f1`). SFTP no longer retries `FileNotFoundError` or authentication errors.
- Placeholder checksums are unmistakable strings instead of plausible-looking values (`e6d4161`).
- The MySQL integration tests that drop a database or user run only with `PDR_ALLOW_DESTRUCTIVE_DB_TESTS=1`
  and only on `test_`-prefixed names (`test_pdr_user`, `test_pdr_<id>`) (`461174a`).

### Fixed

- **rclone/S3 write path** (`fa215e1`), following the operator notes for the Cologne S3 server: no
  `rclone mkdir` (buckets are never created; the bucket must exist), delete before overwrite, size and MD5
  verification after upload (MD5 also for multipart objects), timeouts on every rclone call, multipart
  upload in 64 MB chunks above 256 MB (`rclone_upload_cutoff_mb`, `rclone_chunk_size_mb`).
- The model status is no longer overwritten by `exception_runtime` when ONION fails on an aborted or
  timed-out job, and a SIMLINE failure no longer skips copying `pdroutput` (`96ac9f1`).
- `KeyError: 'parameters'` when the config used the README key `model_parameters` (`461174a`).
- `pdr_run --check` no longer reports the word `KT_VAR` in a template comment as an unfilled placeholder
  (`e0ca7a8`).
- SIMLINE race between parallel workers (`d4fd3ec`): every job now runs SIMLINE in its own
  `<tmp>/simline_job/` (binary, molecules and `obs.template` symlinked from `simline.simline_dir`), so one
  worker can no longer overwrite another's `simline.obs`; the shared, frozen `simline_dir` is never written.
- Missing `PDR_CONFIG` import in the preflight code (`88fb842`).
- `test_github_issue_7_example` no longer depends on the host's rclone configuration (`461174a`).

## 2026-08-31 and earlier (`master`)

- `4246aca` SIMLINE radiative-transfer post-processing step.
- `dc215a9` Template key `chemical_network_file` (the key KOSMA-tau reads); before this fix grids silently
  used the `chem_rates.dat` symlink.
- Earlier history: see `git log master`.
