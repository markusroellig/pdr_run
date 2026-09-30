"""Classify a finished (or killed) KOSMA-tau ``pdrexe`` run.

``pdrexe`` exits 0 both when the global iteration converged AND when it did
not (and many aborts use a plain Fortran ``STOP``, which is also exit 0).
The real outcome lives in ``pdroutput/run_status.json`` (when the model
writes it - a KOSMA-tau code change tracked separately) or, as a fallback,
in one of three fixed lines printed to stdout (captured in
``pdroutput/TEXTOUT``):

    Model CONVERGED in N iterations (eps=...)
    Model CONVERGED in N iterations (relaxed eps=..., strict eps=...)
    Model NOT CONVERGED after N iterations (eps=...)

This module is pure (no I/O side effects beyond reading the two files) and
is deliberately independent of the database/session machinery in
``pdr_run.models.kosma_tau`` so it can be unit tested without a DB.
"""

import json
import logging
import os
import re

logger = logging.getLogger('dev')

# Status values returned by determine_job_status().
STATUS_FINISHED = 'finished'
STATUS_FINISHED_RELAXED = 'finished_relaxed'
STATUS_NOT_CONVERGED = 'not_converged'
STATUS_FLAGGED = 'flagged'
STATUS_ABORTED = 'aborted'
STATUS_MISSING_OUTPUT = 'missing_output'
STATUS_TIMEOUT = 'timeout'

# Statuses for which downstream post-processing (onion/simline/uv continuum)
# is considered to be operating on a usable model.
SUCCESS_STATUSES = (STATUS_FINISHED, STATUS_FINISHED_RELAXED, STATUS_FLAGGED)

# All statuses this module can produce - used by callers that need to treat
# a job as terminal (see database.queries._update_job_status).
ALL_STATUSES = SUCCESS_STATUSES + (
    STATUS_NOT_CONVERGED, STATUS_ABORTED, STATUS_MISSING_OUTPUT, STATUS_TIMEOUT,
)

# Statuses whose pdroutput/ is a complete, valid structure output: safe to
# store as the node's result (a not_converged model still ran to the end and
# wrote every output file). For timeout/aborted/missing_output the files, if
# any, are partial and are NOT stored as results - a partial
# pdrstruct<model>.hdf5 would be mistaken for a finished node by the
# skip-existing logic; only the logs are stored for diagnosis.
COMPLETE_OUTPUT_STATUSES = SUCCESS_STATUSES + (STATUS_NOT_CONVERGED,)

# Statuses for which ONION/SIMLINE/UV-continuum post-processing runs: the
# converged (strict/relaxed) and flagged models. A not_converged structure is
# stored but not post-processed (post-processing a model that has to be
# recomputed anyway wastes hours).
POSTPROCESS_STATUSES = SUCCESS_STATUSES

# Job states that count as success for the pdr_run exit code. 'skipped'
# (result already stored) counts as success; 'flagged' is a usable model.
STATUS_SKIPPED = 'skipped'
JOB_SUCCESS_STATES = SUCCESS_STATUSES + (STATUS_SKIPPED,)

RUN_STATUS_FILE = 'run_status.json'
STRUCT_FILE = 'pdrstruct_s.hdf5'
TEXTOUT_FILE = 'TEXTOUT'

# The run_status.json fields this module understands and forwards; see the
# KOSMA-tau side contract in kosma-tau/CLAUDE.md ("H2 Emission Spectra" /
# run lifecycle notes) - kept in sync manually, there is no shared schema.
_JSON_FIELDS = (
    'converged', 'global_iterations', 'eps_final', 'tsearch_flagged_shells',
    'chem_relaxed_calls', 'deferred_iterations', 'code_version', 'git_hash',
)

_RE_RELAXED = re.compile(
    r'Model CONVERGED in (\d+) iterations '
    r'\(relaxed eps=([0-9.eE+\-]+), strict eps=([0-9.eE+\-]+)\)')
_RE_STRICT = re.compile(
    r'Model CONVERGED in (\d+) iterations \(eps=([0-9.eE+\-]+)\)')
_RE_NOT_CONVERGED = re.compile(
    r'Model NOT CONVERGED after (\d+) iterations \(eps=([0-9.eE+\-]+)\)')


def _empty_fields():
    return {k: None for k in _JSON_FIELDS}


def read_run_status_json(pdroutput_dir):
    """Read ``run_status.json`` from *pdroutput_dir* if present.

    Returns the parsed dict, or ``None`` if the file is absent or cannot be
    parsed. A malformed file is logged and treated as absent (falls back to
    TEXTOUT parsing) - a bad status file must never crash the driver
    mid-grid.
    """
    path = os.path.join(pdroutput_dir, RUN_STATUS_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning(f"Could not parse {path}: {exc}")
        return None


def parse_textout_convergence(textout_path):
    """Fallback: scan TEXTOUT for one of the three known convergence lines.

    Returns a partial fields dict (``converged``/``global_iterations``/
    ``eps_final``) or ``None`` if none of the three patterns is found (e.g.
    the run aborted before reaching the convergence check, or predates the
    convergence-message format).
    """
    if not os.path.isfile(textout_path):
        return None
    try:
        with open(textout_path, 'r', errors='replace') as f:
            text = f.read()
    except OSError as exc:
        logger.warning(f"Could not read {textout_path}: {exc}")
        return None

    m = _RE_RELAXED.search(text)
    if m:
        return {
            'converged': 'relaxed',
            'global_iterations': int(m.group(1)),
            'eps_final': float(m.group(3)),
        }
    m = _RE_STRICT.search(text)
    if m:
        return {
            'converged': 'strict',
            'global_iterations': int(m.group(1)),
            'eps_final': float(m.group(2)),
        }
    m = _RE_NOT_CONVERGED.search(text)
    if m:
        return {
            'converged': 'no',
            'global_iterations': int(m.group(1)),
            'eps_final': float(m.group(2)),
        }
    return None


def determine_job_status(returncode, timed_out, workdir,
                          pdroutput_subdir='pdroutput'):
    """Classify a completed (or killed) ``pdrexe`` run.

    Args:
        returncode: subprocess exit code (``int``), or ``None`` if the
            process never produced one.
        timed_out (bool): ``True`` if the wall-time cap fired and the
            process group was killed.
        workdir (str): directory the model ran in (contains
            ``pdroutput/``).
        pdroutput_subdir (str): name of the output subdirectory.

    Returns:
        ``(status, fields)``: *status* is one of the ``STATUS_*``
        constants; *fields* is a dict with the ``run_status.json`` fields
        (``None`` where unavailable - the TEXTOUT fallback only ever fills
        ``converged``/``global_iterations``/``eps_final``).
    """
    fields = _empty_fields()
    pdroutput_dir = os.path.join(workdir, pdroutput_subdir)

    if timed_out:
        return STATUS_TIMEOUT, fields

    if returncode != 0:
        return STATUS_ABORTED, fields

    struct_path = os.path.join(pdroutput_dir, STRUCT_FILE)
    if not os.path.isfile(struct_path):
        return STATUS_MISSING_OUTPUT, fields

    run_status = read_run_status_json(pdroutput_dir)
    if run_status is not None:
        fields.update({k: run_status.get(k) for k in _JSON_FIELDS if k in run_status})
        converged = fields.get('converged')
    else:
        textout_path = os.path.join(pdroutput_dir, TEXTOUT_FILE)
        parsed = parse_textout_convergence(textout_path)
        if parsed is None:
            # Exit 0, output present, but no recognizable convergence line
            # (e.g. a build that predates this message). Preserve the
            # pre-existing exit-code-only behaviour rather than inventing a
            # new failure mode from an unparsed log.
            logger.warning(
                f"{textout_path}: exit 0 but no convergence line found; "
                "defaulting to 'finished' (legacy exit-code behaviour)")
            return STATUS_FINISHED, fields
        fields.update(parsed)
        converged = parsed.get('converged')

    flagged_shells = fields.get('tsearch_flagged_shells') or 0
    if converged == 'no':
        return STATUS_NOT_CONVERGED, fields
    if flagged_shells > 0:
        return STATUS_FLAGGED, fields
    if converged == 'relaxed':
        return STATUS_FINISHED_RELAXED, fields
    if converged == 'strict':
        return STATUS_FINISHED, fields

    logger.warning(
        f"Unrecognized convergence value {converged!r} in run status for "
        f"{workdir}; defaulting to 'finished'")
    return STATUS_FINISHED, fields
