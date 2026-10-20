"""``pdr_run campaign`` - run a list of (node, JSON-override) jobs with one pinned binary.

Purpose: the uncertainty-study blocks (noise ensembles, iteration tests, knob pairs, rate Monte
Carlo, off-node nodes) are not grid nodes: the same node is run many times with a different
override of the template.  The grid machinery (database rows keyed by node, one stored result per
node) cannot express that, so a campaign is a database-free side path that reuses only the
low-level pieces of the grid runner (execution environment of a job, process-group kill, stall
watchdog, job-state classification).  No database row is read or written; ``pdr_model_jobs`` is
untouched and no database credentials are needed.

Campaign file (YAML, or JSON)::

    campaign:
      name: uq_a1a_20261021          # output goes to <out_dir>/<name>/<label>/
      block: A1a                     # free text, copied to the manifest
      out_dir: /data/uq              # campaign base directory
      template: /path/pdr_config.json.grid1.template   # the grid-1 tier-0 template (4 node placeholders)
      metal: 100                     # metallicity in percent (grid convention, default 100)
      workers: 6                     # concurrent pdrexe (halley: 7 is the memory limit)
      tmp_dir: null                  # scratch for the per-job work directories (default: system tmp)
    pdr:                             # as in the grid config; base_dir holds the PINNED binary and data
      base_dir: /home/roellig/pdr/grid1_base
      pdr_file_name: pdrexe_uq_<hash>
      chem_database: chem_rates_grid1_2026-10.dat
      max_walltime_s: 86400
      stall_first_iteration_s: null
    jobs:
      - label: a1a_n5_M0_x2_k00      # unique; becomes the directory name
        node: [5.0, 0.0, 2.0]        # log10 n_s [cm^-3], log10 M [Msun], log10 chi [Draine]
        overrides: {physical_params: {cosmic_ray_rate: 7.0e-17}}   # deep-merged into the template

The node is converted exactly as the grid does (tenth-dex strings -> 10**(0.1*t), radius from the
mass with the template's density_law_exponent and core_radius, four-digit scientific formatting of
the placeholders), so a campaign job on a grid node has the same input as the grid job.  A node
that is not a multiple of 0.1 dex uses 10**x.

Per job the directory ``<out_dir>/<name>/<label>/`` receives ``pdr_config.json`` (the merged
config that was run), ``job.json`` (state, node, overrides, status fields, wall time, sha256 of the
config and of the result), ``pdrstruct_s.hdf5`` (only for jobs that ran to the end: finished,
finished_relaxed, flagged, not_converged), ``run_status.json`` and ``run.log.gz`` (screen output).
Everything model-related stays in the one HDF5 file.  ``<out_dir>/<name>/manifest.json`` records
the binary and template sha256, the job list and the overrides.

Usage::

    pdr_run campaign FILE --check       # validate (files, overrides, binary), nothing is written
    pdr_run campaign FILE --dry-run     # write manifest + one merged config per job, run nothing
    pdr_run campaign FILE               # run; finished jobs are skipped (resume)
    pdr_run campaign FILE --rerun       # also recompute jobs with a stored result
    pdr_run campaign FILE --status      # table of the job states on disk
    options: --only LABEL [LABEL ...], --workers N, --keep-tmp, --force (accept a changed binary/template)
"""

import argparse
import concurrent.futures
import datetime
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile

import yaml

logger = logging.getLogger('dev')

# keys of the optional "uncertainty" JSON section (kosma-tau branch feature/uq-knobs, json_reader.f90)
UQ_SCALARS = ('pe_heating_scale', 'h2_formation_scale', 'grain_recombination_scale', 'crphot_scale',
              'extra_global_iterations', 'reaction_factor_file')
UQ_H2STAR = ('cplus', 'splus', 'o', 'n', 's', 'shplus', 'hplus')
UQ_COLLISION = ('o_h', 'cplus_h', 'cplus_h2', 'h2_h')
UQ_MARKER = b'uncertainty.reaction_factor_file'      # present in a binary that has the section
STRUCT_FILE = 'pdrstruct_s.hdf5'


class CampaignError(Exception):
    """Invalid campaign file or environment."""


# ------------------------------------------------------------------------------------------ helpers
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _loads_json(text):
    """JSON with the comment/trailing-comma tolerance of the grid code path (preflight)."""
    from pdr_run.cli.preflight import strip_json_comments
    stripped = strip_json_comments(text)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return json.loads(re.sub(r',(\s*[}\]])', r'\1', stripped))


def deep_merge(base, over):
    """Return ``base`` with the nested dict ``over`` merged in (dicts recursively, other values replaced)."""
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def leaf_paths(tree, prefix=()):
    """All key paths of the nested dict ``tree`` down to its non-dict values."""
    for key, val in tree.items():
        if isinstance(val, dict):
            yield from leaf_paths(val, prefix + (key,))
        else:
            yield prefix + (key,)


def _has_path(tree, path):
    for key in path:
        if not isinstance(tree, dict) or key not in tree:
            return False
        tree = tree[key]
    return True


def node_tag(node):
    """Short label fragment of a node: n5.5_M-1_x2."""
    return 'n%g_M%g_x%g' % tuple(node)


# --------------------------------------------------------------------------------- config rendering
_PLACEHOLDER = re.compile(r'KT_VAR\w+?_(?![A-Za-z0-9])')


def node_values(node, alpha, rcore, metal):
    """Node (log n, log M, log chi) -> {placeholder: formatted text}, converted as the grid does."""
    from pdr_run.models.kosma_tau import format_scientific
    from pdr_run.models.parameters import compute_radius

    def lin(x):
        tenths = round(10.0 * x)
        return 10 ** (0.1 * tenths) if abs(10.0 * x - tenths) < 1e-9 else 10 ** x

    xnsur, mass, sint = (lin(float(x)) for x in node)
    rtot = compute_radius(mass, xnsur, alpha, rcore)
    return {'KT_VARxnsur_': format_scientific(xnsur), 'KT_VARrtot_': format_scientific(rtot),
            'KT_VARsint_': format_scientific(sint), 'KT_VARzmetal_': format_scientific(float(metal) * 0.01)}


def render_config(template_text, node, overrides, metal=100):
    """Merged config of one job: (text to write, parsed dict).

    The four node placeholders are substituted as the grid does; without overrides the text is the
    substituted template unchanged (byte-identical to the grid's config), with overrides the parsed
    JSON is deep-merged and re-dumped.
    """
    probe = _PLACEHOLDER.sub('1.0', template_text)
    phys = _loads_json(probe).get('physical_params', {})
    alpha = phys.get('density_law_exponent')
    rcore = phys.get('core_radius')
    if alpha is None or rcore is None:
        raise CampaignError("template lacks physical_params.density_law_exponent / core_radius")
    text = template_text
    for key, val in node_values(node, alpha, rcore, metal).items():
        text = text.replace(key, val)
    left = sorted(set(_PLACEHOLDER.findall(text)))
    if left:
        raise CampaignError(f"template has placeholders other than the four node values: {left} "
                            "(campaigns need a grid-1 style template)")
    cfg = _loads_json(text)
    if not overrides:
        return text, cfg
    cfg = deep_merge(cfg, overrides)
    return json.dumps(cfg, indent=2, ensure_ascii=False) + '\n', cfg


# ------------------------------------------------------------------------------------ campaign file
def load_campaign(path):
    """Read and normalise a campaign file (relative paths are resolved against the file's directory)."""
    path = os.path.abspath(path)
    with open(path) as fh:
        raw = yaml.safe_load(fh)          # JSON is YAML
    if not isinstance(raw, dict):
        raise CampaignError(f"{path}: not a mapping")
    camp = dict(raw.get('campaign') or {})
    pdr = dict(raw.get('pdr') or {})
    jobs = raw.get('jobs') or []
    here = os.path.dirname(path)

    def absp(p):
        return p if p is None or os.path.isabs(p) else os.path.normpath(os.path.join(here, p))

    for key in ('name', 'out_dir', 'template'):
        if not camp.get(key):
            raise CampaignError(f"campaign.{key} is required")
    camp['out_dir'], camp['template'] = absp(camp['out_dir']), absp(camp['template'])
    camp['tmp_dir'] = absp(camp.get('tmp_dir'))
    camp.setdefault('metal', 100)
    camp.setdefault('workers', 1)
    for key in ('base_dir', 'pdr_file_name', 'chem_database'):
        if not pdr.get(key):
            raise CampaignError(f"pdr.{key} is required")
    pdr['base_dir'] = absp(pdr['base_dir'])
    seen = set()
    norm = []
    for i, job in enumerate(jobs):
        label = str(job.get('label', ''))
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]*', label):
            raise CampaignError(f"job {i}: label {label!r} must match [A-Za-z0-9][A-Za-z0-9._+-]*")
        if label in seen:
            raise CampaignError(f"duplicate job label {label!r}")
        seen.add(label)
        node = job.get('node')
        if not (isinstance(node, (list, tuple)) and len(node) == 3):
            raise CampaignError(f"job {label}: node must be [log n, log M, log chi]")
        over = json.loads(json.dumps(job.get('overrides') or {}))
        uq = over.get('uncertainty')
        if isinstance(uq, dict) and uq.get('reaction_factor_file'):
            uq['reaction_factor_file'] = absp(uq['reaction_factor_file'])
        norm.append({'label': label, 'node': [float(x) for x in node], 'overrides': over})
    if not norm:
        raise CampaignError("no jobs")
    return {'path': path, 'campaign': camp, 'pdr': pdr, 'jobs': norm}


def campaign_dir(spec):
    return os.path.join(spec['campaign']['out_dir'], spec['campaign']['name'])


def check_overrides(label, overrides, template_cfg):
    """Problems of one job's overrides: unknown keys, wrong uncertainty keys."""
    problems = []
    for path in leaf_paths(overrides):
        if path[0] == 'uncertainty':
            sub = path[1:]
            ok = ((len(sub) == 1 and sub[0] in UQ_SCALARS)
                  or (len(sub) == 2 and sub[0] == 'h2star_scale' and sub[1] in UQ_H2STAR)
                  or (len(sub) == 2 and sub[0] == 'collision_scale' and sub[1] in UQ_COLLISION))
            if not ok:
                problems.append(f"{label}: unknown uncertainty key {'.'.join(path)}")
        elif not _has_path(template_cfg, path):
            problems.append(f"{label}: override key {'.'.join(path)} is not in the template (typo?)")
    uq = overrides.get('uncertainty') or {}
    rff = uq.get('reaction_factor_file')
    if rff and not os.path.isfile(rff):
        problems.append(f"{label}: reaction_factor_file {rff} does not exist")
    return problems


def validate(spec):
    """All problems of a campaign (list of strings; empty = ready to run). Writes nothing."""
    camp, pdr = spec['campaign'], spec['pdr']
    problems = []
    if not os.path.isfile(camp['template']):
        return [f"template {camp['template']} does not exist"]
    text = open(camp['template']).read()
    base = pdr['base_dir']
    exe = os.path.join(base, pdr['pdr_file_name'])
    if not os.path.isdir(base):
        problems.append(f"pdr.base_dir {base} does not exist")
    else:
        from pdr_run.config.default_config import PDR_CONFIG, PDR_INP_DIRS
        names = [pdr['pdr_file_name']] + [pdr.get(k, PDR_CONFIG[k]) for k in
                                          ('onion_file_name', 'getctrlind_file_name', 'mrt_file_name')]
        problems += [f"{os.path.join(base, n)} is missing" for n in names
                     if not os.path.exists(os.path.join(base, n))]
        problems += [f"{os.path.join(base, d)} is missing" for d in PDR_INP_DIRS[:2]
                     if not os.path.isdir(os.path.join(base, d))]
        if not os.path.isfile(os.path.join(base, 'pdrinpdata', pdr['chem_database'])):
            problems.append(f"network pdrinpdata/{pdr['chem_database']} is missing in {base}")
    try:
        render_config(text, spec['jobs'][0]['node'], {}, camp['metal'])
        template_cfg = _loads_json(_PLACEHOLDER.sub('1.0', text))
    except Exception as exc:                                   # noqa: BLE001 - report, never crash a check
        return problems + [f"template does not render: {exc}"]
    needs_uq = False
    for job in spec['jobs']:
        problems += check_overrides(job['label'], job['overrides'], template_cfg)
        needs_uq = needs_uq or 'uncertainty' in job['overrides']
        try:
            render_config(text, job['node'], job['overrides'], camp['metal'])
        except Exception as exc:                               # noqa: BLE001
            problems.append(f"{job['label']}: does not render: {exc}")
    if needs_uq and os.path.isfile(exe):
        with open(exe, 'rb') as fh:
            if UQ_MARKER not in fh.read():
                problems.append(f"{exe} has no 'uncertainty' section (build without feature/uq-knobs): "
                                "the overrides would be ignored silently")
    return problems


# ----------------------------------------------------------------------------------------- manifest
def _git_head(path):
    try:
        return subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def build_manifest(spec):
    camp, pdr = spec['campaign'], spec['pdr']
    exe = os.path.join(pdr['base_dir'], pdr['pdr_file_name'])
    chem = os.path.join(pdr['base_dir'], 'pdrinpdata', pdr['chem_database'])
    jobs = []
    for job in spec['jobs']:
        entry = dict(job)
        rff = (job['overrides'].get('uncertainty') or {}).get('reaction_factor_file')
        if rff and os.path.isfile(rff):
            entry['reaction_factor_file_sha256'] = sha256_file(rff)
        jobs.append(entry)
    return {
        'schema': 'pdr_run.campaign/1', 'name': camp['name'], 'block': camp.get('block', ''),
        'created': datetime.datetime.now().isoformat(timespec='seconds'), 'host': socket.gethostname(),
        'pdr_run_commit': _git_head(os.path.dirname(os.path.abspath(__file__))),
        'binary': exe, 'binary_sha256': sha256_file(exe) if os.path.isfile(exe) else None,
        'template': camp['template'], 'template_sha256': sha256_file(camp['template']),
        'network': chem, 'network_sha256': sha256_file(chem) if os.path.isfile(chem) else None,
        'metal': camp['metal'], 'campaign_file': spec['path'], 'campaign_file_sha256': sha256_file(spec['path']),
        'n_jobs': len(jobs), 'jobs': jobs,
    }


def write_manifest(spec, force=False):
    """Write <campaign>/manifest.json; refuse to continue a campaign with another binary/template/network."""
    cdir = campaign_dir(spec)
    os.makedirs(cdir, exist_ok=True)
    path = os.path.join(cdir, 'manifest.json')
    new = build_manifest(spec)
    if os.path.isfile(path):
        old = json.load(open(path))
        changed = [k for k in ('binary_sha256', 'template_sha256', 'network_sha256', 'metal')
                   if old.get(k) != new[k]]
        if changed and not force:
            raise CampaignError(f"existing manifest differs in {changed}: a campaign uses one pinned binary, "
                                "template and network (new campaign name, or --force)")
        new['created'] = old.get('created', new['created'])
    shutil.copy(spec['path'], os.path.join(cdir, 'campaign' + os.path.splitext(spec['path'])[1]))
    with open(path, 'w') as fh:
        json.dump(new, fh, indent=1)
    return new


# ------------------------------------------------------------------------------------------- running
def read_job_state(jdir):
    try:
        return json.load(open(os.path.join(jdir, 'job.json')))
    except (OSError, ValueError):
        return None


def job_done(jdir):
    """A job counts as done when its job.json says the model ran to the end (finished, finished_relaxed, flagged,
    not_converged - a deterministic rerun of a not_converged model would not help) and its result file is there."""
    from pdr_run.models.job_status import COMPLETE_OUTPUT_STATUSES
    state = read_job_state(jdir)
    if not state or state.get('status') not in COMPLETE_OUTPUT_STATUSES:
        return False
    return os.path.isfile(os.path.join(jdir, STRUCT_FILE))


def run_job(spec, job, template_text, keep_tmp=False):
    """Run one job in a private work directory and store its result under <campaign>/<label>/."""
    from pdr_run.core.engine import _setup_execution_environment
    from pdr_run.models.job_status import COMPLETE_OUTPUT_STATUSES, determine_job_status
    from pdr_run.models.kosma_tau import _kill_process_group, _wait_with_stall_watchdog

    camp, pdr = spec['campaign'], spec['pdr']
    jdir = os.path.join(campaign_dir(spec), job['label'])
    os.makedirs(jdir, exist_ok=True)
    text, _ = render_config(template_text, job['node'], job['overrides'], camp['metal'])
    with open(os.path.join(jdir, 'pdr_config.json'), 'w') as fh:
        fh.write(text)
    state = {'label': job['label'], 'node': job['node'], 'overrides': job['overrides'],
             'config_sha256': hashlib.sha256(text.encode()).hexdigest(), 'status': 'running',
             'start': datetime.datetime.now().isoformat(timespec='seconds'), 'host': socket.gethostname()}
    _write_state(jdir, state)
    for stale in (STRUCT_FILE, 'run_status.json', 'run.log.gz'):       # a rerun must not leave old results
        if os.path.exists(os.path.join(jdir, stale)):
            os.remove(os.path.join(jdir, stale))
    tmp = tempfile.mkdtemp(prefix=f"uq-{job['label']}-", dir=camp.get('tmp_dir'))
    t0 = datetime.datetime.now()
    try:
        _setup_execution_environment(tmp, pdr['base_dir'], {'pdr': pdr})
        shutil.copy(os.path.join(jdir, 'pdr_config.json'), os.path.join(tmp, 'pdr_config.json'))
        textout_path = os.path.join(tmp, 'pdroutput', 'TEXTOUT')
        max_wall, stall = pdr.get('max_walltime_s'), pdr.get('stall_first_iteration_s')
        with open(textout_path, 'w') as textout:
            proc = subprocess.Popen(['./' + pdr['pdr_file_name']], cwd=tmp, stdout=textout, stderr=textout,
                                    start_new_session=True)
            stalled = False
            if stall:
                rc, timed_out, stalled = _wait_with_stall_watchdog(proc, textout_path, max_wall, stall)
            else:
                try:
                    rc, timed_out = proc.wait(timeout=max_wall), False
                except subprocess.TimeoutExpired:
                    rc, timed_out = _kill_process_group(proc), True
        status, fields = determine_job_status(rc, timed_out, tmp, stalled=stalled)
        state.update(status=status, returncode=rc, fields=fields)
        pdro = os.path.join(tmp, 'pdroutput')
        if status in COMPLETE_OUTPUT_STATUSES and os.path.isfile(os.path.join(pdro, STRUCT_FILE)):
            shutil.copy(os.path.join(pdro, STRUCT_FILE), os.path.join(jdir, STRUCT_FILE))
            state['result_sha256'] = sha256_file(os.path.join(jdir, STRUCT_FILE))
        elif status in COMPLETE_OUTPUT_STATUSES:
            state['status'] = 'missing_output'
        if os.path.isfile(os.path.join(pdro, 'run_status.json')):
            shutil.copy(os.path.join(pdro, 'run_status.json'), os.path.join(jdir, 'run_status.json'))
        with open(textout_path, 'rb') as src, gzip.open(os.path.join(jdir, 'run.log.gz'), 'wb', 6) as dst:
            shutil.copyfileobj(src, dst)
    except Exception as exc:                                    # noqa: BLE001 - record, never kill the pool
        logger.error(f"job {job['label']}: {exc}", exc_info=True)
        state.update(status='exception', error=f"{type(exc).__name__}: {exc}")
    finally:
        state['end'] = datetime.datetime.now().isoformat(timespec='seconds')
        state['wall_s'] = round((datetime.datetime.now() - t0).total_seconds(), 1)
        _write_state(jdir, state)
        if keep_tmp:
            print(f"kept work directory {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return state


def _write_state(jdir, state):
    tmp = os.path.join(jdir, 'job.json.tmp')
    with open(tmp, 'w') as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, os.path.join(jdir, 'job.json'))


def run_campaign(spec, rerun=False, only=None, workers=None, keep_tmp=False):
    """Run every job that is not done (or all with ``rerun``); returns the list of job states."""
    template_text = open(spec['campaign']['template']).read()
    jobs = [j for j in spec['jobs'] if not only or j['label'] in only]
    todo = [j for j in jobs if rerun or not job_done(os.path.join(campaign_dir(spec), j['label']))]
    n_workers = max(1, int(workers or spec['campaign'].get('workers') or 1))
    print(f"campaign {spec['campaign']['name']}: {len(jobs)} jobs selected, {len(jobs) - len(todo)} already done, "
          f"{len(todo)} to run with {n_workers} worker(s)")
    states = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(run_job, spec, j, template_text, keep_tmp): j for j in todo}
        for fut in concurrent.futures.as_completed(futures):
            st = fut.result()
            states.append(st)
            print(f"{st['label']}: {st['status']} ({st.get('wall_s', 0):.0f} s)", flush=True)
    return states


def status_table(spec):
    rows = []
    for job in spec['jobs']:
        st = read_job_state(os.path.join(campaign_dir(spec), job['label'])) or {}
        f = st.get('fields') or {}
        rows.append((job['label'], st.get('status', 'not started'), f.get('global_iterations'),
                     st.get('wall_s'), 'yes' if job_done(os.path.join(campaign_dir(spec), job['label'])) else 'no'))
    width = max(len(r[0]) for r in rows)
    lines = [f"{'label':<{width}}  {'status':<16} {'iters':>5} {'wall_s':>8}  result"]
    lines += [f"{r[0]:<{width}}  {r[1]:<16} {str(r[2] if r[2] is not None else '-'):>5} "
              f"{str(r[3] if r[3] is not None else '-'):>8}  {r[4]}" for r in rows]
    counts = {}
    for r in rows:
        counts[r[1]] = counts.get(r[1], 0) + 1
    lines.append('states: ' + ', '.join(f'{n} {s}' for s, n in sorted(counts.items())))
    return '\n'.join(lines)


# --------------------------------------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(prog='pdr_run campaign', description=__doc__.split('\n')[0])
    ap.add_argument('file', help='campaign file (YAML/JSON)')
    ap.add_argument('--check', action='store_true', help='validate only; nothing is written')
    ap.add_argument('--dry-run', action='store_true',
                    help='write manifest and the merged pdr_config.json of every job, run nothing')
    ap.add_argument('--status', action='store_true', help='print the job states found on disk')
    ap.add_argument('--rerun', action='store_true', help='recompute jobs that already have a stored result')
    ap.add_argument('--only', nargs='+', metavar='LABEL', help='run only these jobs')
    ap.add_argument('--workers', type=int, help='override campaign.workers')
    ap.add_argument('--keep-tmp', action='store_true', help='keep the per-job work directories')
    ap.add_argument('--force', action='store_true', help='continue although binary/template/network changed')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        spec = load_campaign(args.file)
        if args.only:
            unknown = sorted(set(args.only) - {j['label'] for j in spec['jobs']})
            if unknown:
                raise CampaignError(f"--only: unknown labels {unknown}")
        if args.status:
            print(status_table(spec))
            return 0
        problems = validate(spec)
        for p in problems:
            print(f"FAIL  {p}")
        if problems:
            print(f"campaign {spec['campaign']['name']}: {len(problems)} problem(s), nothing run")
            return 1
        print(f"PASS  campaign {spec['campaign']['name']}: {len(spec['jobs'])} jobs, template, binary, network, "
              "overrides and factor files check out")
        if args.check:
            return 0
        write_manifest(spec, force=args.force)
        if args.dry_run:
            text = open(spec['campaign']['template']).read()
            for job in spec['jobs']:
                jdir = os.path.join(campaign_dir(spec), job['label'])
                os.makedirs(jdir, exist_ok=True)
                with open(os.path.join(jdir, 'pdr_config.json'), 'w') as fh:
                    fh.write(render_config(text, job['node'], job['overrides'], spec['campaign']['metal'])[0])
            print(f"dry run: manifest and {len(spec['jobs'])} configs written under {campaign_dir(spec)}")
            return 0
        states = run_campaign(spec, args.rerun, args.only, args.workers, args.keep_tmp)
        from pdr_run.models.job_status import JOB_SUCCESS_STATES
        bad = [s for s in states if s['status'] not in JOB_SUCCESS_STATES]
        print(f"done: {len(states) - len(bad)} ok, {len(bad)} not ok")
        return 1 if bad else 0
    except CampaignError as exc:
        print(f"pdr_run campaign: {exc}", file=sys.stderr)
        return 2
