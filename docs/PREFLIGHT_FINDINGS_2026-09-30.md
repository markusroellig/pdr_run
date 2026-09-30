# Findings from the first preflight check (2026-09-30)

The preflight check (`pdr_run --check`, README section "Preflight Check") was written on branch
`feature/preflight-check` (10a5673..88fb842). Running it and reading the code for it turned up the following
behaviour of the existing code. None of it is changed on this branch; each item is open for a decision.

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
