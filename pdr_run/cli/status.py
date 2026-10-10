"""``pdr_run status`` - read-only snapshot of a grid run as JSON.

Data source of the live grid dashboard (design: GRID_DASHBOARD_BRAINSTORM,
Sect. 5).  Strictly read-only: the database is opened read-only (SQLite
``mode=ro`` + ``query_only``; MySQL ``SET SESSION TRANSACTION READ ONLY``; only
``SELECT`` is issued, tables are reflected, nothing is created or altered).
The only file ever written is ``--out`` (and, with ``--with-physics``, the
physics cache).  ``config_json``, passwords and user names never enter the
payload, and the final text is scrubbed of any configured secret.

Schema: ``pdr_run/schemas/status_v1.schema.json`` (``"schema": "pdr_run.status/1"``).

Node state = the latest non-``skipped`` job row of the node (same rule as
``--rerun``: skipped rows only record that a stored result existed).
"""

import argparse
import json
import math
import os
import re
import shutil
import socket
import sys
import time
from datetime import datetime, timedelta, timezone

import yaml

SCHEMA_ID = 'pdr_run.status/1'
DEFAULT_CACHE = os.path.join('~', '.cache', 'pdr_run', 'status_physics.json')
DEFAULT_EVENT_WINDOW_H = 24
MAX_EVENTS = 200
POSTPROC_TEXT_MAX = 200

# status -> UI class (registry key ``status_classes`` may override single entries)
STATUS_CLASS = {
    'finished': 'ok', 'skipped': 'ok', 'finished_relaxed': 'warn', 'flagged': 'warn',
    'running': 'run', 'pending': 'pending', 'created': 'pending',
}
# anything else (not_converged, aborted, missing_output, timeout, stalled, failed_storage,
# exception*, error*, reset_stale, ...) is 'bad'
PHYSICS_STATUSES = ('finished', 'finished_relaxed', 'flagged', 'not_converged')
OUTPUT_STATUSES = PHYSICS_STATUSES          # complete structure output stored

AXIS_DEFAULTS = (
    dict(key='n', param='xnsur', label='n_s', unit='cm^-3', log=True),
    dict(key='M', param='mass', label='M', unit='Msun', log=True),
    dict(key='chi', param='sint', label='chi', unit='Draine', log=True),
    dict(key='Z', param='zmetal', label='Z', unit='solar', log=False),
)

_JOB_COLUMNS = (
    'id', 'model_job_name', 'status', 'active', 'pending', 'time_of_start',
    'time_of_finish', 'time_updated', 'execution_time', 'kosmatau_parameters_id',
    'kosmatau_executable_id', 'run_status_converged', 'run_status_global_iterations',
    'run_status_eps_final', 'run_status_tsearch_flagged_shells',
    'run_status_chem_relaxed_calls', 'uvcont_applied', 'uvcont_closure_ok',
    'uvcont_error', 'postproc_error', 'template_sha256')
_PARAM_COLUMNS = ('id', 'xnsur', 'mass', 'sint', 'zmetal', 'rtot')

_RE_SHELL = re.compile(r'current shell #:\s*(\d+)\s*\*+\s*current iteration step:\s*(\d+)')
_RE_AV = re.compile(r'AV\s*=\s*([\d.eE+-]+)')
_RE_TEMP = re.compile(r'T\(gas\):\s*([\d.eE+-]+)')
_RE_BRENT = re.compile(r'brent', re.IGNORECASE)
_RE_MAXIT = re.compile(r'MAXIT', re.IGNORECASE)
# run_simline's partial-failure segment of postproc_error:
# "SIMLINE: partial: failed species C+, 13C; missing outputs O [fits], O [hdf5]"
_RE_SIMLINE_PARTIAL = re.compile(r'SIMLINE: partial: (.*?)(?:; (?:SIMLINE:|ONION )|$)')
_RE_SIMLINE_FAILED = re.compile(r'failed species ([^;]*)')
_RE_SIMLINE_MISSING = re.compile(r'missing outputs ([^;]*)')


class StatusError(Exception):
    """User-level error (bad config, unreachable database, unknown model name)."""


# ----------------------------------------------------------------- helpers

def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S') if isinstance(dt, datetime) else None


def _parse_ts(text):
    """--since: ISO date/time; a trailing Z or an offset means UTC-aware and is
    converted to the (naive) local time the database stores."""
    try:
        s = text.strip().replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise StatusError(f"--since: cannot parse {text!r} as an ISO timestamp") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _seconds(value):
    """Interval column as seconds (SQLite/MySQL store it as datetime since the epoch)."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, datetime):
        return (value - datetime(1970, 1, 1)).total_seconds()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(value):
    """run_status_converged is a short string; booleans where unambiguous."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ('true', '1', 't', 'yes'):
        return True
    if s in ('false', '0', 'f', 'no'):
        return False
    return str(value)


def _num(x, ndigits=None):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return round(x, ndigits) if ndigits is not None else x


def _quantile(sorted_vals, q):
    if not sorted_vals:
        return None
    pos = q * (len(sorted_vals) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def status_class(status, overrides=None):
    if overrides and status in overrides:
        return overrides[status]
    return STATUS_CLASS.get(status, 'bad')


# ------------------------------------------------------------ configuration

def load_settings(config_path, registry_path=None):
    """(file_config, registry) dictionaries.  The registry is a small YAML next
    to the pdr_run config (``<config stem>.grid.yaml`` if present) or --registry."""
    cfg = {}
    if config_path:
        try:
            with open(config_path) as fh:
                cfg = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise StatusError(f"cannot read config {config_path}: {exc}") from exc
    reg = {}
    if not registry_path and config_path:
        cand = os.path.splitext(config_path)[0] + '.grid.yaml'
        if os.path.isfile(cand):
            registry_path = cand
    if registry_path:
        try:
            with open(registry_path) as fh:
                reg = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise StatusError(f"cannot read registry {registry_path}: {exc}") from exc
    return cfg, reg


def _secrets(cfg):
    from pdr_run.utils.logging import is_sensitive_field
    found = []

    def walk(obj, key=''):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, str(k))
        elif isinstance(obj, str) and key and len(obj) >= 4 and is_sensitive_field(key):
            found.append(obj)
    walk(cfg)
    for var in ('PDR_DB_PASSWORD', 'PDR_DB_USERNAME', 'PDR_STORAGE_PASSWORD', 'PDR_STORAGE_USER'):
        val = os.environ.get(var)
        if val and len(val) >= 4:
            found.append(val)
    return sorted(set(found), key=len, reverse=True)


# ----------------------------------------------------------------- database

def open_readonly(db_cfg, timeout=10):
    """(connection, info) of a read-only connection; env PDR_DB_* overrides the
    file exactly as in the rest of pdr_run (DatabaseManager precedence)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from pdr_run.database.db_manager import DatabaseManager
    try:
        dm = DatabaseManager(db_cfg or {})
    except ValueError as exc:
        raise StatusError(f"database configuration incomplete: {str(exc).splitlines()[0]}") from exc
    c = dm.config
    dbtype = c.get('type', 'sqlite')
    if dbtype == 'sqlite':
        path = c.get('path')
        if not path or path == ':memory:' or not os.path.isfile(path):
            raise StatusError(f"SQLite database file not found: {path}")
        ap = os.path.abspath(path)
        url = f"sqlite:///file:{ap}?mode=ro&uri=true"
        engine = create_engine(url, poolclass=NullPool)
        name = os.path.basename(ap)
    else:
        key = {'mysql': 'connection_timeout', 'postgresql': 'connect_timeout'}.get(dbtype)
        args = dict(c.get('connect_args') or {})
        if key:
            args[key] = int(timeout)
        engine = create_engine(dm._build_connection_string(), poolclass=NullPool, connect_args=args)
        name = str(c.get('database'))
    try:
        conn = engine.connect()
        if dbtype == 'sqlite':
            conn.exec_driver_sql('PRAGMA query_only = ON')
        elif dbtype == 'mysql':
            conn.exec_driver_sql('SET SESSION TRANSACTION READ ONLY')
        elif dbtype == 'postgresql':
            conn.exec_driver_sql('SET default_transaction_read_only = on')
    except Exception as exc:  # noqa: BLE001
        raise StatusError(f"cannot connect to the {dbtype} database: {type(exc).__name__}") from exc
    return conn, dict(type=dbtype, database=name)


def _reflect(conn, names):
    from sqlalchemy import MetaData, Table, inspect
    have = set(inspect(conn).get_table_names())
    md = MetaData()
    out = {}
    for n in names:
        out[n] = Table(n, md, autoload_with=conn) if n in have else None
    return out


def _select_cols(table, wanted):
    return [table.c[c] for c in wanted if c in table.c]


def fetch_model(conn, model_name):
    """(model_name_id, model_path) for the newest registration of *model_name*."""
    from sqlalchemy import select
    t = _reflect(conn, ['model_names'])['model_names']
    if t is None:
        raise StatusError("database has no model_names table")
    rows = conn.execute(select(t.c.id, t.c.model_name, t.c.model_path)
                        .where(t.c.model_name == model_name).order_by(t.c.id.desc())).fetchall()
    if not rows:
        known = [r[0] for r in conn.execute(select(t.c.model_name).order_by(t.c.id.desc()).limit(8))]
        raise StatusError(f"model name {model_name!r} not in the database; recent: {known}")
    return rows[0][0], rows[0][2]


def fetch_job_rows(conn, model_name_id):
    """All job rows of the model with their parameter columns, plus executable
    sha256 per id.  One SELECT per table; ``config_json`` is never selected."""
    from sqlalchemy import select
    t = _reflect(conn, ['pdr_model_jobs', 'kosmatau_parameters', 'kosmatau_executables'])
    jobs, pars, exes = t['pdr_model_jobs'], t['kosmatau_parameters'], t['kosmatau_executables']
    if jobs is None or pars is None:
        raise StatusError("database lacks pdr_model_jobs / kosmatau_parameters")
    jcols = _select_cols(jobs, _JOB_COLUMNS)
    pcols = [pars.c[c].label('p_' + c) for c in _PARAM_COLUMNS if c in pars.c]
    q = (select(*jcols, *pcols)
         .select_from(jobs.outerjoin(pars, jobs.c.kosmatau_parameters_id == pars.c.id))
         .where(jobs.c.model_name_id == model_name_id).order_by(jobs.c.id))
    rows = [dict(r._mapping) for r in conn.execute(q)]
    exe = {}
    if exes is not None and 'sha256_sum' in exes.c:
        exe = {r[0]: r[1] for r in conn.execute(select(exes.c.id, exes.c.sha256_sum))}
    return rows, exe


# --------------------------------------------------------------- node model

def latest_rows(rows):
    """{node: (latest non-skipped row, number of non-skipped rows)}; a node that
    has only skipped rows keeps its latest skipped row."""
    out, only_skipped = {}, {}
    for r in rows:                              # ordered by id ascending
        node = r['model_job_name']
        if r.get('status') == 'skipped':
            only_skipped[node] = r
            continue
        prev = out.get(node)
        out[node] = (r, 1 if prev is None else prev[1] + 1)
    for node, r in only_skipped.items():        # result stored by an earlier database/run
        out.setdefault(node, (r, 1))
    return out


def _log_axis_value(spec, value):
    if value is None or (spec.get('log', True) and value <= 0):
        return None
    return round(math.log10(value), 4) if spec.get('log', True) else round(float(value), 6)


def build_axes(latest, registry):
    """Axes from the parameter columns (or the registry), values = sorted unique
    log10 (or linear) values.  Returns (axes list, per-node index lists)."""
    specs = [dict(a) for a in (registry.get('axes') or AXIS_DEFAULTS)]
    axes, node_idx = [], {n: [] for n in latest}
    for spec in specs:
        param = spec.get('param') or spec['key']
        vals = {}
        for node, (r, _) in latest.items():
            vals[node] = _log_axis_value(spec, r.get('p_' + param))
        uniq = sorted({v for v in vals.values() if v is not None})
        if not uniq and not spec.get('values'):
            continue
        if spec.get('values'):
            uniq = list(spec['values'])
        if spec['key'] == 'Z' and len(uniq) < 2 and 'axes' not in registry:
            continue                           # constant metallicity: not an axis
        axes.append(dict(key=spec['key'], param=param, label=spec.get('label', spec['key']),
                         unit=spec.get('unit', ''), log=bool(spec.get('log', True)), values=uniq))
        for node in latest:
            v = vals[node]
            node_idx[node].append(min(range(len(uniq)), key=lambda i: abs(uniq[i] - v))
                                  if v is not None and uniq else None)
    return axes, node_idx


def simline_partial(postproc_error):
    """Species of a partial SIMLINE run recorded in *postproc_error*
    (``run_simline``'s ``SimlinePartialError``): failed species plus species
    with missing outputs, in order, without duplicates; None if the text has
    no ``SIMLINE: partial:`` segment."""
    m = _RE_SIMLINE_PARTIAL.search(postproc_error or '')
    if not m:
        return None
    seg, species = m.group(1), []
    for rx in (_RE_SIMLINE_FAILED, _RE_SIMLINE_MISSING):
        mm = rx.search(seg)
        if mm:
            for item in mm.group(1).split(','):
                sp = item.split(' [')[0].strip()
                if sp and sp not in species:
                    species.append(sp)
    return species


def node_entry(node, row, nreruns, idx, now, classes):
    st = row.get('status') or 'pending'
    t0, t1 = row.get('time_of_start'), row.get('time_of_finish')
    exec_s = _seconds(row.get('execution_time'))
    if exec_s is None and isinstance(t0, datetime) and isinstance(t1, datetime):
        exec_s = (t1 - t0).total_seconds()
    cls = status_class(st, classes)
    pp = row.get('postproc_error')
    if pp and cls == 'ok':
        cls = 'warn'
    e = dict(id=node, idx=idx, job_id=row['id'], status=st, **{'class': cls}, reruns=nreruns - 1)
    opt = dict(
        t_start=_iso(t0), t_finish=_iso(t1), exec_s=_num(exec_s, 1),
        converged=_flag(row.get('run_status_converged')),
        global_it=row.get('run_status_global_iterations'),
        eps=_num(row.get('run_status_eps_final')),
        tsearch_flagged=row.get('run_status_tsearch_flagged_shells'),
        chem_relaxed=row.get('run_status_chem_relaxed_calls'))
    e.update({k: v for k, v in opt.items() if v is not None})
    uv = {k: row.get(c) for k, c in (('applied', 'uvcont_applied'), ('closure_ok', 'uvcont_closure_ok'),
                                     ('error', 'uvcont_error')) if row.get(c) is not None}
    if uv:
        if isinstance(uv.get('error'), str):
            uv['error'] = uv['error'][:POSTPROC_TEXT_MAX]
        for k in ('applied', 'closure_ok'):
            if k in uv:
                uv[k] = bool(uv[k])
        e['uvcont'] = uv
    if pp:
        e['postproc_error'] = str(pp)[:POSTPROC_TEXT_MAX]
        lost = simline_partial(str(pp))
        if lost is not None:
            e['simline_failed_species'] = lost
        if cls == 'warn':
            e['warn_reason'] = ('post-processing error' if lost is None else
                                f"SIMLINE partial: {', '.join(lost)} missing" if lost else
                                'SIMLINE partial: incomplete output')
    return e


def row_stamp(row):
    stamps = [row.get(k) for k in ('time_updated', 'time_of_finish', 'time_of_start')]
    stamps = [s for s in stamps if isinstance(s, datetime)]
    return max(stamps) if stamps else None


def build_events(latest, rows, since, now):
    """Events derived from the job rows (stateless).  Newest first, capped."""
    ev = []
    for r in rows:
        if r.get('status') == 'skipped':
            continue
        node, st = r['model_job_name'], r.get('status') or 'pending'
        t0, t1 = r.get('time_of_start'), r.get('time_of_finish')
        if isinstance(t0, datetime) and t0 >= since:
            ev.append((t0, dict(t=_iso(t0), kind='job_started', node=node, sev='info',
                                text=f"job {r['id']} started")))
        if isinstance(t1, datetime) and t1 >= since and st not in ('running', 'pending'):
            cls = status_class(st)
            sev = {'ok': 'info', 'warn': 'warn'}.get(cls, 'error')
            ex = _seconds(r.get('execution_time'))
            ex = ex if ex is not None else ((t1 - t0).total_seconds() if isinstance(t0, datetime) else None)
            text = f"job {r['id']} {st}" + (f", {ex / 3600:.2f} h" if ex else '')
            if r.get('run_status_global_iterations') is not None:
                text += f", {r['run_status_global_iterations']} it"
            ev.append((t1, dict(t=_iso(t1), kind='job_finished', node=node, sev=sev, text=text)))
            if r.get('postproc_error'):
                ev.append((t1, dict(t=_iso(t1), kind='postproc_error', node=node, sev='warn',
                                    text=str(r['postproc_error'])[:POSTPROC_TEXT_MAX])))
    ev.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in ev[:MAX_EVENTS]], len(ev) > MAX_EVENTS


# -------------------------------------------------------------- local probes

def _read_tail(path, nbytes=65536):
    try:
        with open(path, 'rb') as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - nbytes))
            return fh.read().decode(errors='ignore')
    except OSError:
        return None


def parse_textout_tail(text):
    """Progress from the tail of a running job's TEXTOUT (same patterns as
    tools/mcp/pdr_monitor.py; the parser is not imported because that tool lives
    in the KOSMA-tau repository)."""
    out = {}
    m = None
    for m in _RE_SHELL.finditer(text):
        pass
    if m:
        out['shell'], out['global_it'] = int(m.group(1)), int(m.group(2))
    for key, rx in (('av', _RE_AV), ('t_gas', _RE_TEMP)):
        last = None
        for last in rx.finditer(text):
            pass
        if last:
            try:
                out[key] = float(last.group(1))
            except ValueError:
                pass
    out['brent_fallbacks'] = len(_RE_BRENT.findall(text))
    out['maxit'] = len(_RE_MAXIT.findall(text))
    return out


def local_processes(proc_root='/proc'):
    """{job_id: info} for ``pdrexe*`` processes whose cwd is ``pdr-job<ID>-*``."""
    found = {}
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return found
    try:
        hz = os.sysconf('SC_CLK_TCK')
        with open(os.path.join(proc_root, 'uptime')) as fh:
            uptime = float(fh.read().split()[0])
    except (OSError, ValueError):
        return found
    for pid in pids:
        base = os.path.join(proc_root, pid)
        try:
            with open(os.path.join(base, 'cmdline'), 'rb') as fh:
                argv0 = fh.read().split(b'\0')[0].decode(errors='ignore')
            if not os.path.basename(argv0).startswith('pdrexe'):
                continue
            cwd = os.readlink(os.path.join(base, 'cwd'))
            m = re.search(r'pdr-job(\d+)-', cwd)
            if not m:
                continue
            with open(os.path.join(base, 'stat')) as fh:
                f = fh.read().rsplit(')', 1)[1].split()
            utime, stime, start = int(f[11]), int(f[12]), int(f[19])
            age = max(uptime - start / hz, 1e-3)
            rss_kb = 0
            with open(os.path.join(base, 'status')) as fh:
                for line in fh:
                    if line.startswith('VmRSS:'):
                        rss_kb = int(line.split()[1])
            info = dict(pid=int(pid), rss_gb=round(rss_kb / 1048576, 2),
                        cpu_pct=int(round(100.0 * (utime + stime) / hz / age)), cwd=cwd)
            tail = _read_tail(os.path.join(cwd, 'pdroutput', 'TEXTOUT'))
            if tail:
                info.update(parse_textout_tail(tail))
                try:
                    info['last_line_age_s'] = int(time.time() - os.path.getmtime(
                        os.path.join(cwd, 'pdroutput', 'TEXTOUT')))
                except OSError:
                    pass
            found[int(m.group(1))] = info
        except (OSError, ValueError, IndexError):
            continue
    return found


def local_resources(paths, proc_root='/proc'):
    res = {}
    try:
        res['load1'] = float(open(os.path.join(proc_root, 'loadavg')).read().split()[0])
    except (OSError, ValueError):
        pass
    res['cores'] = os.cpu_count()
    try:
        mem = {}
        for line in open(os.path.join(proc_root, 'meminfo')):
            k, v = line.split(':')
            mem[k] = int(v.split()[0])
        res['mem_total_gb'] = round(mem['MemTotal'] / 1048576, 1)
        res['mem_avail_gb'] = round(mem['MemAvailable'] / 1048576, 1)
    except (OSError, ValueError, KeyError):
        pass
    disks, seen = [], set()
    for label, p in paths:
        if p and os.path.isdir(p):
            try:
                du = shutil.disk_usage(p)
                key = os.stat(p).st_dev
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            disks.append(dict(mount=label, path=p, free_gb=round(du.free / 1e9, 1)))
    res['disk'] = disks
    return res


def count_stored(struct_dir):
    if not struct_dir or not os.path.isdir(struct_dir):
        return None
    n = 0
    with os.scandir(struct_dir) as it:
        for e in it:
            if e.name.startswith('pdrstruct') and (e.name.endswith('.hdf5') or e.name.endswith('.hdf5.gz')):
                n += 1
    return n


# ------------------------------------------------------------------ payload

def collector_version():
    from pdr_run import __version__
    v = f"pdr_run {__version__}"
    try:
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        r = subprocess.run(['git', '-C', here, 'describe', '--always', '--dirty'],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            v += '+g' + r.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return v


def build_status(conn, dbinfo, model_name, cfg, registry, since=None, with_physics=False,
                 with_local=False, struct_dir=None, cache_path=None, molfrac_source=None,
                 physics_budget_s=20.0, min_free_gb=20.0, started=None):
    started = started or time.monotonic()
    now = datetime.now()
    model_id, model_path = fetch_model(conn, model_name)
    rows, exe_sha = fetch_job_rows(conn, model_id)
    latest = latest_rows(rows)
    classes = registry.get('status_classes')
    pdr_cfg = (cfg.get('pdr') or {})
    cap = registry.get('max_walltime_s', pdr_cfg.get('max_walltime_s'))
    workers = registry.get('workers')

    axes, node_idx = build_axes(latest, registry)
    facet = registry.get('facet_axis') or ('M' if any(a['key'] == 'M' for a in axes) else None)

    entries = {n: node_entry(n, r, k, node_idx[n], now, classes) for n, (r, k) in latest.items()}
    order = sorted(latest, key=lambda n: latest[n][0]['id'])
    by_status, by_class = {}, {}
    for e in entries.values():
        by_status[e['status']] = by_status.get(e['status'], 0) + 1
        by_class[e['class']] = by_class.get(e['class'], 0) + 1
    for c in ('ok', 'warn', 'bad', 'run', 'pending'):
        by_class.setdefault(c, 0)

    def changed(n):
        if since is None:
            return True
        r, _ = latest[n]
        s = row_stamp(r)
        return r.get('status') == 'running' or (s is not None and s >= since)

    # run-time samples (finished-type nodes, latest row) and censored timeouts
    samples = []
    for n in order:
        r, _ = latest[n]
        e = entries[n]
        if since is not None and not changed(n):
            continue                            # delta: the dashboard keeps the earlier samples
        if e['status'] in OUTPUT_STATUSES and e.get('exec_s'):
            samples.append((e['idx'], e['exec_s'], False))
        elif e['status'] == 'timeout' and cap:
            samples.append((e['idx'], float(cap), True))
    all_exec = sorted(e['exec_s'] for e in entries.values() if e['status'] in OUTPUT_STATUSES and e.get('exec_s'))

    running = []
    procs = local_processes() if with_local else {}
    urgent = []
    stale_after = (cap * 1.5) if cap else 6 * 3600
    for n in order:
        r, _ = latest[n]
        if r.get('status') != 'running':
            continue
        t0 = r.get('time_of_start')
        el = (now - t0).total_seconds() if isinstance(t0, datetime) else None
        item = dict(job_id=r['id'], node=n, t_start=_iso(t0), elapsed_s=_num(el, 0))
        if cap:
            item['cap_s'] = cap
            if el is not None:
                item['frac_cap'] = round(el / cap, 3)
        pi = procs.get(r['id'])
        if pi:
            item.update({k: v for k, v in pi.items() if k != 'cwd'})
            item['warnings'] = dict(brent_fallbacks=item.pop('brent_fallbacks', 0),
                                    maxit=item.pop('maxit', 0))
        running.append(item)
        if el is not None and el > stale_after:
            urgent.append(dict(kind='stale_running',
                               text=f"job {r['id']} ({n}) running {el / 3600:.1f} h > {stale_after / 3600:.1f} h"))

    n_total = len(entries)
    pending_nodes = [n for n in order if entries[n]['status'] in ('pending', 'created')]
    eta = dict(p10=None, p50=None, p90=None, model='median-quantile-naive', n_fit=len(all_exec))
    if len(all_exec) >= 3 and workers:
        med, q10, q90 = (_quantile(all_exec, 0.5), _quantile(all_exec, 0.1), _quantile(all_exec, 0.9))
        busy = sum(max(med - (i.get('elapsed_s') or 0), 0.0) for i in running)
        for key, per in (('p10', q10), ('p50', med), ('p90', q90)):
            work = len(pending_nodes) * per + busy
            eta[key] = _iso(now + timedelta(seconds=work / workers))
    core_s = sum(_seconds(r.get('execution_time')) or 0.0 for r in rows if r.get('status') not in ('skipped', 'pending', 'running'))
    cores_per_job = registry.get('cores_per_job', 1)

    templ = sorted({r['template_sha256'] for r, _ in latest.values() if r.get('template_sha256')})
    if len(templ) > 1:
        urgent.append(dict(kind='template_mismatch', text=f"{len(templ)} different template_sha256 among the nodes"))
    bins = sorted({exe_sha.get(r.get('kosmatau_executable_id')) for r, _ in latest.values()
                   if exe_sha.get(r.get('kosmatau_executable_id'))})
    if len(bins) > 1:
        urgent.append(dict(kind='binary_mismatch', text=f"{len(bins)} different executables among the nodes"))

    if struct_dir is None:
        cand = os.path.join(model_path or '', 'pdrgrid')
        struct_dir = cand if os.path.isdir(cand) else None
    stored = count_stored(struct_dir)
    with_output = sum(1 for e in entries.values() if e['status'] in OUTPUT_STATUSES)
    storage = dict(struct_dir_local=struct_dir is not None, stored_nodes=stored,
                   backlog_nodes=None if stored is None else max(with_output - stored, 0),
                   failed_storage=by_status.get('failed_storage', 0))

    gen = datetime.now(timezone.utc)
    payload = {
        'schema': SCHEMA_ID,
        'generated_at': gen.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'collector': dict(version=collector_version(), host=socket.gethostname(), elapsed_s=None,
                          utc_offset_s=int(datetime.now().astimezone().utcoffset().total_seconds()),
                          times='local time of the collector host, naive ISO'),
        'grid': {
            'id': registry.get('id') or model_name, 'database': dbinfo['database'],
            'db_type': dbinfo['type'], 'model_name': model_name, 'tier': registry.get('tier'),
            'axes': axes, 'facet_axis': facet, 'max_walltime_s': cap, 'workers': workers,
            'cores_per_job': cores_per_job, 'binary_sha256': bins, 'template_sha256': templ,
            'notebook_entry': registry.get('notebook_entry'),
            'status_classes': {s: status_class(s, classes) for s in sorted(by_status)},
        },
        'summary': dict(total=n_total, by_status=dict(sorted(by_status.items())), by_class=by_class,
                        n_rows=len(rows), core_hours=round(core_s * cores_per_job / 3600, 2), eta=eta),
        'delta': since is not None,
        'since': _iso(since),
    }

    payload['nodes'] = [entries[n] for n in order if changed(n)]
    payload['running'] = running
    payload['eta_inputs'] = dict(
        axes=[a['key'] for a in axes],
        samples=[dict(idx=i, exec_s=_num(s, 1), censored=c) for i, s, c in samples],
        queue=[entries[n]['idx'] for n in pending_nodes], workers=workers, cap_s=cap)
    ev_since = since or (now - timedelta(hours=DEFAULT_EVENT_WINDOW_H))
    payload['events'], payload['events_truncated'] = build_events(latest, rows, ev_since, now)
    payload['storage'] = storage

    res_paths = [('tmp', os.environ.get('TMPDIR') or '/tmp'), ('storage', model_path), ('cwd', os.getcwd())]
    if with_local:
        res = local_resources(res_paths)
        payload['resources'] = res
        for d in res.get('disk', []):
            if d['free_gb'] < min_free_gb:
                urgent.append(dict(kind='disk_low', text=f"{d['mount']} {d['free_gb']} GB free"))
    payload['urgent'] = urgent

    if with_physics:
        from pdr_run.cli import status_physics as sp
        want = [(n, latest[n][0]['id']) for n in order
                if changed(n) and entries[n]['status'] in PHYSICS_STATUSES]
        cache = sp.PhysicsCache(os.path.expanduser(cache_path or DEFAULT_CACHE))
        recs, meta = sp.collect_physics(
            want, struct_dir, cache,
            molfrac_source or (registry.get('physics') or {}).get('molfrac_source', 'densities'),
            budget_s=physics_budget_s)
        payload['physics'] = recs
        payload['physics_meta'] = meta
    payload['collector']['elapsed_s'] = round(time.monotonic() - started, 2)
    return payload


def dumps(payload, secrets=(), indent=None):
    text = json.dumps(payload, indent=indent, separators=(',', ':') if indent is None else None,
                      default=str, allow_nan=False)
    for s in secrets:
        text = text.replace(s, '***')
    return text


def format_table(p):
    s, g = p['summary'], p['grid']
    lines = [f"grid {g['id']} (model {g['model_name']}, db {g['database']}): {s['total']} nodes",
             "  " + ', '.join(f"{k}={v}" for k, v in s['by_status'].items())]
    for r in p['running']:
        lines.append(f"  running job {r['job_id']} {r['node']}: {r.get('elapsed_s')} s"
                     + (f" ({100 * r['frac_cap']:.0f}% of cap)" if 'frac_cap' in r else ''))
    for u in p['urgent']:
        lines.append(f"  URGENT {u['kind']}: {u['text']}")
    return '\n'.join(lines)


# ---------------------------------------------------------------------- CLI

def build_parser():
    ap = argparse.ArgumentParser(
        prog='pdr_run status', description='Read-only grid status snapshot (see pdr_run/cli/status.py).')
    ap.add_argument('--json', action='store_true', help='emit the v1 JSON payload (default: short table)')
    ap.add_argument('--config', help='pdr_run YAML configuration (database section, pdr.model_name)')
    ap.add_argument('--model-name', help='model (grid tier) name; default pdr.model_name of the config')
    ap.add_argument('--registry', help="grid registry YAML (default: '<config stem>.grid.yaml' if present)")
    ap.add_argument('--with-physics', action='store_true',
                    help='add per-node physics summaries read from the stored pdrstruct HDF5 files (needs h5py)')
    ap.add_argument('--struct-dir', help='directory with pdrstruct<node>.hdf5[.gz] '
                                         '(default: <model_path>/pdrgrid of the database)')
    ap.add_argument('--physics-cache', metavar='FILE', default=None,
                    help=f'physics cache file (default {DEFAULT_CACHE})')
    ap.add_argument('--physics-budget', type=float, default=20.0, metavar='SECONDS',
                    help='stop reading new HDF5 files after this many seconds; the next call continues (default 20)')
    ap.add_argument('--molfrac-source', choices=('densities', 'dataset'), default=None,
                    help="molecular fraction for A_V(H/H2): from Densities (default) or the stored dataset")
    ap.add_argument('--with-local', action='store_true',
                    help='add local process/TEXTOUT progress and resources (load, memory, disk); Linux only')
    ap.add_argument('--min-free-gb', type=float, default=20.0, help='disk_low threshold (default 20)')
    ap.add_argument('--since', metavar='TIMESTAMP',
                    help='delta mode: nodes/events/physics changed since this ISO timestamp')
    ap.add_argument('--out', metavar='FILE', help='write the output here instead of stdout')
    ap.add_argument('--indent', type=int, default=None, help='pretty-print with this indent')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    started = time.monotonic()
    conn = None
    try:
        cfg, registry = load_settings(args.config, args.registry)
        model_name = args.model_name or (cfg.get('pdr') or {}).get('model_name')
        if not model_name:
            raise StatusError("no model name: use --model-name or pdr.model_name in the config")
        since = _parse_ts(args.since) if args.since else None
        conn, dbinfo = open_readonly(cfg.get('database'))
        payload = build_status(
            conn, dbinfo, model_name, cfg, registry, since=since, with_physics=args.with_physics,
            with_local=args.with_local, struct_dir=args.struct_dir or registry.get('struct_dir'),
            cache_path=args.physics_cache, molfrac_source=args.molfrac_source,
            physics_budget_s=args.physics_budget, min_free_gb=args.min_free_gb, started=started)
    except StatusError as exc:
        print(f"pdr_run status: {exc}", file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()
    secrets = _secrets(cfg)
    text = dumps(payload, secrets, args.indent) if args.json else format_table(payload)
    if args.out:
        tmp = args.out + '.tmp'
        with open(tmp, 'w') as fh:
            fh.write(text + '\n')
        os.replace(tmp, args.out)                  # readers never see a half-written file
    else:
        sys.stdout.write(text + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
