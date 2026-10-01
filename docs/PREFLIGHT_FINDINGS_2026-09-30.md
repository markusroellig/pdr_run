# Findings from the first preflight check (2026-09-30)

The preflight check (`pdr_run --check`, README section "Preflight Check") was written on branch
`feature/preflight-check` (10a5673..88fb842). Running it and reading the code for it turned up the following
behaviour of the existing code. Items 1-5 below are not changed on this branch and are open for a decision;
the four failure-handling problems in the next section were fixed on the same branch (2026-09-30).

## Existing behaviour worth fixing

1. **SFTP storage log path is hard-coded.** `SFTPStorage` writes the paramiko log to
   `/home/roellig/pdr/pdr/test_run/logs/paramiko.log` and ignores `storage.port`. The preflight check
   probes SFTP directly and does not use this class, so the check can pass where a real run would log to a
   non-existent path or connect on the wrong port.
2. **Storage environment variables are silently ignored** (`PDR_STORAGE_TYPE`, `PDR_STORAGE_DIR`, …) when the
   config file has a `storage:` section. The check reports this under `config.env`.
3. **A config file without a `pdr:` section is discarded as a whole** by the runner (the repo's own
   `test_config.yaml` is such a file). The check reports this under `config.file`/`config.sections`.
4. **Importing the runner creates `logs/pdr_run.log` in the current directory** (git-ignored).
5. **ONION inputs:** the default `species` list needs `ONION3.INP.<species>` files that the KOSMA-τ rundir
   used for the test did not have; the engine always runs ONION for the species list (`post.onion`).

## Failure-handling problems - FIXED 2026-09-30 (before the grid-1 tier-0 run)

Found by reading the code; all four confirmed and fixed (see README "Run Status and Exit Handling",
"Storage Retries", "Rerun failed nodes"):

- [x] **A. The real failure reason was overwritten.** ONION and SIMLINE ran regardless of the
  classification; for `aborted`/`timeout`/`missing_output` there is no `pdroutput/CTRL_IND`, ONION raised and
  `run_instance` overwrote the status with `exception_runtime` (same for `failed_storage`, which was
  re-raised); a SIMLINE failure also skipped `copy_pdroutput`. Now post-processing runs only for `finished`,
  `finished_relaxed`, `flagged`; failures go to the new column `postproc_error` (additive column, list of the
  preflight check updated); logs are stored for every status, result files for complete outputs.
- [x] **B. Storage failures did not reach the job status.** `store_file` returned `False` after the retries
  and the callers ignored it. Now the job gets `failed_storage`, which is not overwritten. `FileNotFoundError`
  (an `OSError`) was retried by SFTP although the comments said otherwise: now not retried, same for
  `paramiko.AuthenticationException`.
- [x] **C. The exit code ignored failed nodes.** Now 0 = all jobs in a success state, 1 = some failed,
  2 = run-level error, plus a one-line summary.
- [x] **D. No CLI way to recompute stored nodes.** New `--rerun STATE[,STATE...]`.
- [x] **E. Large chemistry outputs.** `storage.compress_files` (whole-file gzip, level 6, streamed) stores
  matching files as `<name>.gz`; recommendation for grid 1: `["pdrchem*.hdf5", "chemchk*.out"]`. The preflight
  check `run.compression` shows the setting. See README, "Whole-file compression of stored results".

## Not verifiable without the production host (halley)

- Real MySQL server: server version, `max_connections`, privileges needed by `db.write_rollback`, InnoDB
  rollback behaviour, connect latency. Only the failure path (closed port) was exercised.
- Real SFTP / rclone remote: only the failure path (connection refused) was exercised.
- The ifx build's `pdrexe --version` string and its `-dirty` marker (matched case-insensitively).
- A real `kosma_h2` checkout for the UV-continuum step (tested with a fake package).

## Note on trailing commas in KOSMA-τ templates

KOSMA-τ JSON templates may contain a trailing comma before a closing brace that strict JSON rejects. The
production binary reads such templates without error (the grid-1 candidate template `pdr_config.json.g1c_off`
has one and runs), so the check reports PASS with a note there.
