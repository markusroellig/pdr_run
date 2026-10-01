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
  matching files as `<name>.gz`; recommendation for grid 1: `["TEXTOUT*", "pdrchem*.hdf5", "chemchk*.out"]`. The preflight
  check `run.compression` shows the setting. See README, "Whole-file compression of stored results".

## rclone / S3 write path review (2026-10-01, run on halley against the real server)

Trigger: a co-author reported reproducible trouble overwriting objects and writing > 1 GB (mainly TEXTOUT) on the
target S3 server; maintainer asked for a review of the rclone path. Findings (file `pdr_run/storage/remote.py`
unless named otherwise; line numbers of the pre-review version 53abbc9):

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | high | `store_file` (l. 455-486) ran `rclone mkdir <remote>:<dir>` before every upload. On a bucket root this tries to create the bucket and hangs until the client timeout (reproduced); inside a bucket it is a no-op. | No mkdir on object stores; `--s3-no-check-bucket`; non-S3 remotes keep mkdir with a timeout. |
| 2 | high | No timeout anywhere (`subprocess.run` without `timeout`, no `--contimeout/--timeout`): one hung call blocks a worker forever. | Connect/idle timeouts + hard subprocess timeout (120 s metadata, 300 s + size/1 MB/s transfer); a timeout is retried. |
| 3 | high | Overwrite relied on `copyto` semantics. rclone skips or only touches the modification time (server-side copy) when size/hash match, and a failed overwrite leaves no defined state. README claimed "rclone copyto already writes to a temporary name" (not true for S3). | Delete the old object explicitly, upload to the final key, verify (see README "RClone storage on S3"). |
| 4 | high | No verification after upload; a truncated or wrong object was reported as stored. | `lsjson --hash`: size always, MD5 if reported (also reported for multipart objects here); mismatch is retried. |
| 5 | medium | Large files: default 5 MB chunks (10 000-part limit hit at 50 GB), cutoff/concurrency not controlled, rclone's own `--retries 3` re-uploads the whole file inside one call (and multiplies with ours). | Chunk 64 MB (auto-raised to stay below 9000 parts), cutoff 256 MB, concurrency 4, `--retries 1`. |
| 6 | high | `file_exists` (l. 587-628) treated every non-zero exit that was not a transport marker as "absent" (credentials, 403 and server errors included): a stored multi-hour node would be recomputed. `lsf` rc 3 was not recognised as the normal answer. | rc 3/4 or "not found" = absent; everything else retried, then ERROR + False. |
| 7 | medium | `_get_full_remote_path` (l. 423-444): prefix stripping by plain `startswith` (`/a/b` also stripped `/a/bc/x`); `os.path.join` could keep `//`; `remote:/key` leading slash on object stores. | Component-wise prefix removal, normalised keys, no leading `/` on object stores, `..` refused, 1024-byte key limit. |
| 8 | medium | Errors were not classified: permanent failures (`AccessDenied`, `NoSuchBucket`) were retried 4 times; `NoSuchBucket` had no useful message. | `_RClonePermanentError` (not retried), "bucket does not exist (ask an admin)". |
| 9 | medium | `retrieve_file` had no size check or timeout. | Timeout scaled with the remote size, size comparison. |
| 10 | medium | `copy_onionoutput` and `run_simline` (`kosma_tau.py`) used `_store`, so `compress_files` never applied to the ONION/SIMLINE TEXTOUT and ASCII outputs. | `_store_maybe_gz` in both. |
| 11 | low | `output_textout_file` in the database lost the `.gz` suffix (copy path l. ~1138 and skip path l. ~1985). | Names resolved like chem/chemchk. |
| 12 | medium | `--check` storage probe (`cli/preflight.py`) created a probe object next to a bucket-less remote path (bucket creation attempt) and never tested overwrite. | Probe runs the real `store_file/retrieve_file/delete_file` in the prefix, lists buckets with `lsd`, reports a missing bucket, does not probe a remote without bucket. |

Measured on halley (rclone 1.53.3, 2026-10-01, prefix `noices/_pdrrun_probe_20261001/`, removed afterwards): small
object 0.8 s, overwrite 1.0 s, gz TEXTOUT-like file (22 MB -> 1.0 MB) 0.8 s, 1.5 GB random file 155 s with MD5
verification, its overwrite 146 s, download 139 s (about 10 MB/s, peak RSS 322 MB), `file_exists` 0.2-0.4 s,
preflight probe 2.4 s. Compatibility note: rclone 1.50 (development machine) does not know `lsjson --no-mimetype`;
the code uses only `--hash`.

Open: no test against an ONION-sized (many small objects) run; throughput of 10 MB/s means a 1 GB uncompressed
TEXTOUT costs about 100 s per node, which is the reason for `TEXTOUT*` in `compress_files`; incomplete
multipart uploads after a killed worker are not cleaned by pdr_run (`rclone cleanup`).

## Not verifiable without the production host (halley)

- Real MySQL server: server version, `max_connections`, privileges needed by `db.write_rollback`, InnoDB
  rollback behaviour, connect latency. Only the failure path (closed port) was exercised.
- Real SFTP remote: only the failure path (connection refused) was exercised. rclone/S3: exercised on halley, see above.
- The ifx build's `pdrexe --version` string and its `-dirty` marker (matched case-insensitively).
- A real `kosma_h2` checkout for the UV-continuum step (tested with a fake package).

## Note on trailing commas in KOSMA-τ templates

KOSMA-τ JSON templates may contain a trailing comma before a closing brace that strict JSON rejects. The
production binary reads such templates without error (the grid-1 candidate template `pdr_config.json.g1c_off`
has one and runs), so the check reports PASS with a note there.
