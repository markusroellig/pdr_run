#!/usr/bin/env python3
"""Backfill the SIMLINE post-processing for finished grid nodes whose SIMLINE
step failed.

Selects the jobs of one model (``pdr.model_name`` of the config, or
``--model``) with status ``finished`` whose ``postproc_error`` contains a
``SIMLINE: ...`` segment (including ``SIMLINE: partial: ...`` of a pipeline
run that lost species) and re-runs pdr_run's own
``pdr_run.models.kosma_tau.run_simline`` for each of them, one at a time, in a
fresh temporary directory. ``run_simline`` fetches the stored
``pdrgrid/pdrstruct<model>.hdf5`` from the configured storage, runs the
pipeline and stores ``simlinegrid/...`` exactly as during a grid run.
(With ``simline.bundle_outputs: true`` in the config the side files go into one
archive ``simlinegrid/SIMLINE<model>.tar.gz``, as in the grid run.)

Database bookkeeping mirrors ``run_kosma_tau``:

* success: the ``SIMLINE: ...`` segment is removed from ``postproc_error``
  (other segments, e.g. ``ONION ...``, are kept); an empty remainder becomes
  NULL, which is what a node whose post-processing succeeded carries;
* the pipeline raises: the segment is replaced by ``SIMLINE: <new error>``
  (a run that is still incomplete gives ``SIMLINE: partial: ...`` again);
* storing a result file failed (``run_simline`` returns False): status
  ``failed_storage`` via ``_mark_failed_storage``, as in a grid run.

Usage (on the grid host, credentials from the environment / config):

    python scripts/backfill_simline.py --config configs/grid1_tier0.yaml --dry-run
    python scripts/backfill_simline.py --config configs/grid1_tier0.yaml --job-ids 403
    python scripts/backfill_simline.py --config configs/grid1_tier0.yaml --job-ids 570 628 --rerun-ok
    python scripts/backfill_simline.py --config configs/grid1_tier0.yaml --nice 19
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile
import time

import yaml

SEGMENT_SEP = '; '
SIMLINE_PREFIX = 'SIMLINE:'

logger = logging.getLogger('backfill_simline')


def split_segments(postproc_error):
    """Split a ``postproc_error`` text into its ``'; '``-joined step segments.

    ``run_kosma_tau`` joins one ``"<STEP>: <error>"`` text per failed step with
    ``'; '``. An error message itself may contain ``'; '``, so a piece that does
    not start with a known step name is glued back to the previous segment.
    """
    if not postproc_error:
        return []
    pieces = postproc_error.split(SEGMENT_SEP)
    steps = ('SIMLINE:', 'ONION ')
    segments = []
    for piece in pieces:
        if segments and not piece.startswith(steps):
            segments[-1] += SEGMENT_SEP + piece
        else:
            segments.append(piece)
    return segments


def replace_simline_segment(postproc_error, new_error=None):
    """Return *postproc_error* with its SIMLINE segment removed (``new_error``
    None) or replaced by ``SIMLINE: <new_error>``; None if nothing remains."""
    kept = [s for s in split_segments(postproc_error) if not s.startswith(SIMLINE_PREFIX)]
    if new_error is not None:
        kept.append(f"{SIMLINE_PREFIX} {new_error}")
    return SEGMENT_SEP.join(kept) if kept else None


def select_jobs(session, model_name, job_ids=None, limit=None, rerun_ok=False):
    """Finished jobs of *model_name* with a SIMLINE post-processing error.

    With *rerun_ok* (only together with *job_ids*) the listed finished jobs
    are selected whatever their ``postproc_error``: nodes whose SIMLINE run
    lost species before partial failures were recorded, or whose SIMLINE
    step was interrupted by a driver stop."""
    from pdr_run.database.models import ModelNames, PDRModelJob

    if rerun_ok and not job_ids:
        raise ValueError('rerun_ok requires explicit job_ids')
    q = (session.query(PDRModelJob)
         .join(ModelNames, PDRModelJob.model_name_id == ModelNames.id)
         .filter(ModelNames.model_name == model_name,
                 PDRModelJob.status == 'finished'))
    if not rerun_ok:
        q = q.filter(PDRModelJob.postproc_error.like('%SIMLINE:%'))
    if job_ids:
        q = q.filter(PDRModelJob.id.in_(job_ids))
    q = q.order_by(PDRModelJob.id)
    if limit:
        q = q.limit(limit)
    return q.all()


def backfill_job(job_id, config, session, fail_dir=None, tmp_root=None):
    """Re-run SIMLINE for one job; returns 'ok', 'failed' or 'failed_storage'."""
    from pdr_run.database.models import PDRModelJob
    from pdr_run.models import kosma_tau

    tmp_dir = tempfile.mkdtemp(prefix=f'simbackfill-job{job_id}-', dir=tmp_root)
    try:
        try:
            ok = kosma_tau.run_simline(job_id, tmp_dir, config=config, session=session)
            error = None
        except Exception as exc:  # noqa: BLE001 - recorded like run_kosma_tau's _postproc
            logger.error(f"SIMLINE failed for job {job_id}: {exc}", exc_info=True)
            ok, error = None, str(exc)
            if fail_dir:
                textout = os.path.join(tmp_dir, 'simlineoutput', 'TEXTOUT_SIMLINE')
                if os.path.isfile(textout):
                    os.makedirs(fail_dir, exist_ok=True)
                    shutil.copyfile(textout, os.path.join(
                        fail_dir, f'TEXTOUT_SIMLINE.job{job_id}'))

        job = session.get(PDRModelJob, job_id)
        if error is not None:
            job.postproc_error = replace_simline_segment(job.postproc_error, error)
            session.commit()
            return 'failed'
        if ok is False:
            kosma_tau._mark_failed_storage(job_id, session, "SIMLINE output")
            return 'failed_storage'
        job.postproc_error = replace_simline_segment(job.postproc_error)
        session.commit()
        return 'ok'
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--config', required=True, help='pdr_run YAML config of the grid')
    p.add_argument('--model', help='model name (default: pdr.model_name of the config)')
    p.add_argument('--job-ids', type=int, nargs='+', help='restrict to these job IDs')
    p.add_argument('--rerun-ok', action='store_true',
                   help='with --job-ids: also re-run listed finished jobs without a SIMLINE error '
                        '(partial runs recorded as ok, or SIMLINE interrupted by a driver stop)')
    p.add_argument('--limit', type=int, help='process at most N jobs')
    p.add_argument('--dry-run', action='store_true', help='list the selected jobs only')
    p.add_argument('--nice', type=int, default=0, help='niceness increment (e.g. 19)')
    p.add_argument('--fail-dir', help='keep TEXTOUT_SIMLINE of failed jobs here')
    p.add_argument('--tmp-root', help='parent directory of the per-job temp dirs')
    args = p.parse_args(argv)
    if args.rerun_ok and not args.job_ids:
        p.error('--rerun-ok requires --job-ids')

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if args.nice:
        os.nice(args.nice)

    with open(args.config) as fh:
        config = yaml.safe_load(fh)
    model_name = args.model or config['pdr']['model_name']

    from pdr_run.database.db_manager import (
        close_session, database_config_for_run, get_db_manager)
    session = get_db_manager(database_config_for_run(config)).get_session()
    try:
        jobs = select_jobs(session, model_name, args.job_ids, args.limit, args.rerun_ok)
        logger.info(f"{len(jobs)} job(s) of model {model_name} selected")
        if args.dry_run:
            for job in jobs:
                print(f"{job.id}\t{job.model_job_name}\t{job.postproc_error}")
            return 0
        job_ids = [j.id for j in jobs]
        counts = {}
        t_start = time.time()
        for i, job_id in enumerate(job_ids, 1):
            t0 = time.time()
            result = backfill_job(job_id, config, session, args.fail_dir, args.tmp_root)
            counts[result] = counts.get(result, 0) + 1
            logger.info(f"[{i}/{len(job_ids)}] job {job_id}: {result} "
                        f"({time.time() - t0:.0f} s)")
        logger.info(f"done in {time.time() - t_start:.0f} s: {counts}")
        return 0 if set(counts) <= {'ok'} else 1
    finally:
        close_session(session, "backfill_simline")


if __name__ == '__main__':
    sys.exit(main())
