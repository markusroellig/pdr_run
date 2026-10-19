#!/usr/bin/env python3
"""Consolidate the SIMLINE HDF5 of an existing grid into the grid pdrstruct file.

Grids stored before 2026-10-19 hold two files per node: the ONION-only
``pdrgrid/pdrstruct<model>.hdf5`` and the SIMLINE-augmented
``simlinegrid/pdrstruct<model>_simline.hdf5``. Since the maintainer decision of
2026-10-19 everything model-related lives in the first one. For every finished
job of the model (``pdr.model_name`` of the config, or ``--model``) this script

1. fetches both files through the configured storage backend;
2. verifies with h5py that the SIMLINE file contains every dataset of the grid
   file and that every common dataset outside the SIMLINE-written paths
   (``By species/*``, ``Metadata/Metadata``, ``Metadata/Intensity array length``,
   ``Line emission IR``, ``SIMLINE output/*``) is bit-identical; a node that
   fails is refused and nothing is written for it;
3. stores the current grid file as ``pdrgrid/attic_pre_simline/<stored name>``,
   then the SIMLINE file as ``pdrgrid/pdrstruct<model>.hdf5`` (overwrite; a
   ``.gz`` grid object stays ``.gz``), and verifies the stored object (size and
   MD5 where the backend reports them, otherwise by download; the local copy
   must be bit-identical to the SIMLINE file);
4. keeps ``simlinegrid/pdrstruct<model>_simline.hdf5`` unless ``--purge`` is given. With ``--purge`` the
   SIMLINE object is deleted (remote and local copy) after a successful consolidation, and also for a node whose
   grid file already carries the SIMLINE results (result ``purged``), so a second ``--purge`` pass over a grid
   consolidated earlier removes the redundant objects without touching the grid files.

A node is "already consolidated" if its SIMLINE object is absent or its grid
file already carries SIMLINE ``By species`` datasets. The database is not
touched. Exit status 1 if any node was refused or failed.

    python scripts/consolidate_simline.py --config configs/grid1_tier0.yaml --dry-run
    python scripts/consolidate_simline.py --config configs/grid1_tier0.yaml --job-ids 403 --nice 19
    python scripts/consolidate_simline.py --config configs/grid1_tier0.yaml --purge
"""

import argparse
import filecmp
import logging
import os
import shutil
import sys
import tempfile

import yaml

logger = logging.getLogger('consolidate_simline')

# datasets written by the SIMLINE pipeline (replaced/added relative to ONION)
SIMLINE_PATHS = (
    'Integrated quantities/Intensities/By species',
    'Metadata/Metadata',
    'Metadata/Intensity array length',
    'Integrated quantities/Intensities/Line emission IR',
    'SIMLINE output',
)
ATTIC_DIR = 'attic_pre_simline'


def _is_simline_path(name):
    return any(name == p or name.startswith(p + '/') for p in SIMLINE_PATHS)


def _datasets(h5file):
    names = {}
    h5file.visititems(lambda n, o: names.__setitem__(n, o) if hasattr(o, 'shape') else None)
    return names


def verify_superset(main_path, sim_path):
    """Problems (list of text) of consolidating *sim_path* over *main_path*;
    empty list = every dataset of main is in sim and every common dataset
    outside SIMLINE_PATHS is bit-identical."""
    import h5py
    import numpy as np
    problems = []
    with h5py.File(main_path, 'r') as fm, h5py.File(sim_path, 'r') as fs:
        dm, ds = _datasets(fm), _datasets(fs)
        for name in sorted(dm):
            if name not in ds:
                problems.append(f'dataset missing in SIMLINE file: {name}')
            elif not _is_simline_path(name) and not np.array_equal(dm[name][()], ds[name][()]):
                problems.append(f'dataset differs: {name}')
    return problems


def has_simline_species(path):
    """True if the file has a ``By species`` dataset with source "SIMLINE"."""
    from pdr_run.models import kosma_tau
    return bool(kosma_tau._simline_hdf5_species(path))


def consolidate_node(storage, model_path, model, tmp_dir, purge=False, dry_run=False):
    """Consolidate one node; returns ``(result, detail)`` with result in
    'consolidated', 'already', 'refused', 'failed'."""
    from pdr_run.models import kosma_tau as kt

    main_name = os.path.join(model_path, 'pdrgrid', f'pdrstruct{model}.hdf5')
    sim_name = os.path.join(model_path, 'simlinegrid', f'pdrstruct{model}_simline.hdf5')
    main_stored = kt.resolve_stored_name(storage, main_name)
    sim_stored = kt.resolve_stored_name(storage, sim_name)
    if main_stored is None:
        return 'refused', 'no grid file in pdrgrid/'
    if sim_stored is None:
        return 'already', 'no SIMLINE object'
    if dry_run:
        return 'consolidated', 'dry run: would consolidate'

    main_raw = os.path.join(tmp_dir, 'main_raw')
    main_plain = os.path.join(tmp_dir, 'main.hdf5')
    sim_plain = os.path.join(tmp_dir, 'sim.hdf5')
    storage.retrieve_file(main_stored, main_raw)          # as stored (attic copy)
    if main_stored.endswith(kt.GZ_SUFFIX):
        kt.gunzip_file(main_raw, main_plain)
    else:
        main_plain = main_raw
    if has_simline_species(main_plain):
        if purge:
            # consolidated earlier (or by an in-place run): the SIMLINE object is redundant
            if not _purge(storage, sim_stored):
                return 'failed', f'already consolidated, but deleting {sim_stored} failed'
            return 'purged', 'grid file already carries SIMLINE By species; SIMLINE object deleted'
        return 'already', 'grid file already carries SIMLINE By species'
    kt.retrieve_decompressed(storage, sim_stored, sim_plain)
    problems = verify_superset(main_plain, sim_plain)
    if problems:
        return 'refused', '; '.join(problems[:5]) + (f' (+{len(problems) - 5} more)'
                                                    if len(problems) > 5 else '')

    # attic first, then overwrite
    attic = os.path.join(model_path, 'pdrgrid', ATTIC_DIR, os.path.basename(main_stored))
    if not kt._store(storage, main_raw, attic):
        return 'failed', f'storing {attic} failed'
    to_store = sim_plain
    if main_stored.endswith(kt.GZ_SUFFIX):
        to_store = sim_plain + kt.GZ_SUFFIX
        kt.gzip_file(sim_plain, to_store)
    if not kt._store(storage, to_store, main_stored):
        return 'failed', f'storing {main_stored} failed (attic copy exists)'
    err = _verify_stored(storage, main_stored, to_store, tmp_dir)
    if err:
        return 'failed', err
    if purge:
        if not _purge(storage, sim_stored):
            return 'failed', f'consolidated, but deleting {sim_stored} failed'
    return 'consolidated', 'attic written, grid file replaced' + (', SIMLINE object deleted' if purge else '')


def _verify_stored(storage, remote, local, tmp_dir):
    """None if the stored object equals *local* (size/MD5 via the backend's
    ``_remote_stat``, else by download) and its local copy, if any, too."""
    from pdr_run.storage.base import LocalCopy
    size = os.path.getsize(local)
    if hasattr(storage, '_remote_stat') and hasattr(storage, '_get_full_remote_path'):
        stat = storage._remote_stat(storage._get_full_remote_path(remote))
        if stat is None or stat['size'] != size:
            return f'verification: {remote} has size {stat and stat["size"]}, expected {size}'
        if stat['md5'] and stat['md5'].lower() != storage._local_md5(local):
            return f'verification: MD5 mismatch for {remote}'
    else:
        back = os.path.join(tmp_dir, 'verify_download')
        storage.retrieve_file(remote, back)
        if not filecmp.cmp(back, local, shallow=False):
            return f'verification: downloaded {remote} differs'
        os.remove(back)
    lc = getattr(storage, 'local_copy', None)
    if isinstance(lc, LocalCopy) and not filecmp.cmp(lc.local_path(remote), local, shallow=False):
        return f'verification: local copy of {remote} differs'
    return None


def _purge(storage, remote):
    if not hasattr(storage, 'delete_file'):
        logger.error("storage backend cannot delete files; --purge ignored")
        return False
    if storage.delete_file(remote) is False:
        return False
    from pdr_run.storage.base import LocalCopy
    lc = getattr(storage, 'local_copy', None)
    if isinstance(lc, LocalCopy) and os.path.exists(lc.local_path(remote)):
        os.remove(lc.local_path(remote))
    return True


# Same status set as scripts/backfill_simline.py: a flagged or relaxed node has a complete
# grid file too (the first production pass skipped them with status == 'finished').
NODE_STATUSES = ('finished', 'finished_relaxed', 'flagged')


def select_nodes(session, model_name, job_ids=None, limit=None):
    """[(job id, model_job_name, model_path)] of the stored nodes of a model name."""
    from pdr_run.database.models import ModelNames, PDRModelJob
    q = (session.query(PDRModelJob)
         .join(ModelNames, PDRModelJob.model_name_id == ModelNames.id)
         .filter(ModelNames.model_name == model_name, PDRModelJob.status.in_(NODE_STATUSES)))
    if job_ids:
        q = q.filter(PDRModelJob.id.in_(job_ids))
    q = q.order_by(PDRModelJob.id)
    if limit:
        q = q.limit(limit)
    return [(j.id, j.model_job_name, j.model_name.model_path) for j in q.all()]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--config', required=True, help='pdr_run YAML config of the grid')
    p.add_argument('--model', help='model name (default: pdr.model_name of the config)')
    p.add_argument('--job-ids', type=int, nargs='+', help='restrict to these job IDs')
    p.add_argument('--limit', type=int, help='process at most N jobs')
    p.add_argument('--dry-run', action='store_true', help='report what would be done only')
    p.add_argument('--purge', action='store_true',
                   help='after a verified consolidation delete simlinegrid/pdrstruct<model>_simline.hdf5')
    p.add_argument('--nice', type=int, default=0, help='niceness increment (e.g. 19)')
    p.add_argument('--tmp-root', help='parent directory of the per-job temp dirs')
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if args.nice:
        os.nice(args.nice)
    with open(args.config) as fh:
        config = yaml.safe_load(fh)
    model_name = args.model or config['pdr']['model_name']

    from pdr_run.database.db_manager import (
        close_session, database_config_for_run, get_db_manager)
    from pdr_run.database.models import ModelNames, PDRModelJob
    from pdr_run.storage.base import get_storage_backend
    storage = get_storage_backend(config)
    session = get_db_manager(database_config_for_run(config)).get_session()
    try:
        nodes = select_nodes(session, model_name, args.job_ids, args.limit)
    finally:
        close_session(session, "consolidate_simline")

    counts = {}
    for job_id, model, model_path in nodes:
        tmp_dir = tempfile.mkdtemp(prefix=f'simcons-job{job_id}-', dir=args.tmp_root)
        try:
            result, detail = consolidate_node(storage, model_path, model, tmp_dir,
                                              args.purge, args.dry_run)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"job {job_id}: {exc}", exc_info=True)
            result, detail = 'failed', str(exc)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        counts[result] = counts.get(result, 0) + 1
        print(f"job {job_id}\t{model}\t{result}\t{detail}")
    print(f"SUMMARY ({len(nodes)} nodes): " + ', '.join(f'{k} {v}' for k, v in sorted(counts.items())))
    return 1 if counts.get('refused') or counts.get('failed') else 0


if __name__ == '__main__':
    sys.exit(main())
