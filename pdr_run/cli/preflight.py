"""Preflight check for production grid runs (``pdr_run --check``).

Performs every non-destructive check needed to trust a production grid
run - configuration, KOSMA-tau installation, template and input files,
scratch/disk space, storage backend, database, post-processing steps,
wall-time cap and worker counts - and prints a compact report: one line
per check (PASS / WARN / FAIL / SKIP) and a one-line summary.

Usage::

    python -m pdr_run.cli.runner --check [--config FILE] [--check-json]
                                 [--min-free-gb 20] [--check-timeout 5]

Exit code 0 if nothing FAILs (WARN and SKIP are fine), 1 otherwise.

Design rules
------------
* No side effects except probe files/rows that are always cleaned up:
  a probe file in the KOSMA-tau run directory, a probe file in the temp
  dir, a probe file (or remote object) in the storage backend, and one
  database INSERT that is rolled back.
* The check never calls ``create_tables()`` / ``ensure_additive_columns()``
  and never creates missing database files or storage directories. A
  missing column is reported as FAIL with the hint that a normal run
  would add it.
* Every check is isolated: an exception becomes a FAIL line for that
  check, the others still run. Network checks have a hard timeout.
* Secrets (passwords, tokens, user names) are never printed; every
  detail string is scrubbed of any secret value known to the check.
* While the check runs, console log handlers are detached and WARNING+
  records are collected instead of printed; DEBUG detail still goes to
  the normal log file (its path is printed in the footer).
"""

import copy
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

PASS, WARN, FAIL, SKIP = 'PASS', 'WARN', 'FAIL', 'SKIP'

DEFAULT_MIN_FREE_GB = 20.0
DEFAULT_TIMEOUT_S = 5.0
_DETAIL_WIDTH = 118

# Environment variables read anywhere in pdr_run (config/, core/, storage/,
# database/). Value: how the variable is used.
_ENV_DB = ['PDR_DB_TYPE', 'PDR_DB_HOST', 'PDR_DB_PORT', 'PDR_DB_DATABASE',
           'PDR_DB_USERNAME', 'PDR_DB_PASSWORD', 'PDR_DB_FILE']
_ENV_STORAGE_DEFAULTBUILD = ['PDR_STORAGE_TYPE', 'PDR_STORAGE_DIR', 'PDR_STORAGE_HOST',
                             'PDR_STORAGE_USER', 'PDR_STORAGE_PASSWORD']
_ENV_STORAGE_ONLY_NOFILE = ['PDR_STORAGE_RCLONE_REMOTE', 'PDR_STORAGE_USE_MOUNT',
                            'PDR_STORAGE_REMOTE_PATH_PREFIX']
_ENV_PDR = ['PDR_BASE_DIR', 'PDR_EXEC_PATH']
_ENV_OTHER = ['PDR_LOG_DIR']
_ENV_HIDDEN = {'PDR_DB_PASSWORD', 'PDR_DB_USERNAME', 'PDR_STORAGE_PASSWORD',
               'PDR_STORAGE_USER'}


# ---------------------------------------------------------------- plumbing

@dataclass
class Result:
    name: str
    status: str
    detail: str = ''
    elapsed_s: float = 0.0


@dataclass
class Ctx:
    """State shared between checks."""
    config_path: Optional[str] = None
    json_template: Optional[str] = None
    min_free_gb: float = DEFAULT_MIN_FREE_GB
    timeout: float = DEFAULT_TIMEOUT_S
    workers: Optional[int] = None
    cpus: Optional[int] = None
    force_simline: bool = False
    cli_species: Optional[List[str]] = None
    rerun: Optional[tuple] = None          # --rerun selection (informational)

    file_config: Optional[dict] = None     # YAML as loaded (None: no file)
    config_ok: bool = False
    eff: Dict[str, Any] = field(default_factory=dict)  # config the run would use
    from_file: bool = False                # True: eff is the file, False: built-in defaults + env
    params: Dict[str, Any] = field(default_factory=dict)
    secrets: List[str] = field(default_factory=list)
    results: List[Result] = field(default_factory=list)

    # filled by checks for dependent checks
    base_dir: Optional[str] = None
    db_manager: Any = None
    db_conn: Any = None
    db_ok: bool = False
    db_tables: Optional[set] = None
    db_type: Optional[str] = None
    db_max_connections: Optional[int] = None
    disk_paths: Dict[str, str] = field(default_factory=dict)  # label -> path
    template_path: Optional[str] = None    # JSON template found by tpl.json

    def scrub(self, text: str) -> str:
        for s in sorted(set(self.secrets), key=len, reverse=True):
            text = text.replace(s, '***')
        return text


class CheckOutcome(Exception):
    """Raised by a check to return early with a given status."""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _skip(detail):
    raise CheckOutcome(SKIP, detail)


def _fail(detail):
    raise CheckOutcome(FAIL, detail)


def _run_check(ctx: Ctx, name: str, fn: Callable[[Ctx], Tuple[str, str]]) -> None:
    t0 = time.monotonic()
    try:
        status, detail = fn(ctx)
    except CheckOutcome as out:
        status, detail = out.status, out.detail
    except Exception as exc:  # isolation: one broken check never stops the others
        status = FAIL
        detail = f"check crashed: {type(exc).__name__}: {exc}"
    detail = ' '.join(ctx.scrub(str(detail)).split())
    ctx.results.append(Result(name, status, detail, time.monotonic() - t0))


def _with_timeout(fn: Callable[[], Any], seconds: float, what: str) -> Any:
    """Run *fn* in a daemon thread; raise TimeoutError after *seconds*."""
    box: Dict[str, Any] = {}

    def target():
        try:
            box['value'] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller
            box['exc'] = exc

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(seconds)
    if th.is_alive():
        raise TimeoutError(f"{what} timed out after {seconds:g} s")
    if 'exc' in box:
        raise box['exc']
    return box.get('value')


def _short(text: str, n: int = 90) -> str:
    text = ' '.join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + '…'


def _writable_probe(directory: str) -> None:
    """Create and delete a probe file in *directory* (raises on failure)."""
    path = os.path.join(directory, f".pdr_run_preflight_{uuid.uuid4().hex[:8]}")
    try:
        with open(path, 'w') as fh:
            fh.write('probe')
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _nearest_existing(path: str) -> str:
    path = os.path.abspath(path)
    while not os.path.exists(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path


def strip_json_comments(text: str, bang: bool = False) -> str:
    """Remove comments outside strings, as the json-fortran reader does.

    KOSMA-tau's templates use ``//`` and even a single ``/`` as comment
    start (json-fortran's default comment character is ``/``; the rest of
    the line is skipped). Strict ``json`` rejects them. With *bang* a ``!``
    also starts a comment (simline pipeline config). Newlines are kept so
    error line numbers stay correct.
    """
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == '/' or (bang and c == '!'):
            while i < n and text[i] != '\n':
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return ''.join(out)


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


# ---------------------------------------------------------- effective config

def _collect_secrets(ctx: Ctx) -> None:
    from pdr_run.utils.logging import is_sensitive_field

    def walk(obj, key=''):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, str(k))
        elif isinstance(obj, str) and key and len(obj) >= 4 and is_sensitive_field(key):
            ctx.secrets.append(obj)

    walk(ctx.file_config or {})
    walk(ctx.eff or {})
    for var in ('PDR_DB_PASSWORD', 'PDR_STORAGE_PASSWORD', 'PDR_DB_USERNAME',
                'PDR_STORAGE_USER'):
        val = os.environ.get(var)
        if val and len(val) >= 4:
            ctx.secrets.append(val)


def _compute_params(ctx: Ctx) -> None:
    """Model parameters the way ``runner.main`` assembles them."""
    from pdr_run.config.default_config import DEFAULT_PARAMETERS, non_default_parameters
    params = DEFAULT_PARAMETERS.copy()
    params.update(non_default_parameters)
    cfg = ctx.file_config or {}
    for section in ('model_params', 'model_parameters', 'non_default_params',
                    'non_default_parameters'):
        if isinstance(cfg.get(section), dict):
            params.update(cfg[section])
    if ctx.cli_species:
        params['species'] = ctx.cli_species
    ctx.params = params


def _build_effective_config(ctx: Ctx) -> None:
    """The configuration a real run would use (see ``runner.main``)."""
    cfg = ctx.file_config
    if cfg and 'pdr' in cfg:
        ctx.eff = copy.deepcopy(cfg)
        ctx.from_file = True
    else:
        from pdr_run.core.engine import _build_default_config
        ctx.eff = _build_default_config(ctx.params)
        ctx.from_file = False
    ctx.eff.setdefault('database', {})
    ctx.eff.setdefault('pdr', {})
    for k, v in ctx.eff.items():
        if v is None and k in ('database', 'pdr'):
            ctx.eff[k] = {}


def _pdr_cfg(ctx: Ctx, key: str):
    from pdr_run.config.default_config import PDR_CONFIG
    val = (ctx.eff.get('pdr') or {}).get(key)
    return PDR_CONFIG.get(key) if val is None else val


def _resolve_storage(ctx: Ctx) -> Dict[str, Any]:
    """Mirror ``storage.base.get_storage_backend`` option resolution."""
    eff = ctx.eff
    if eff and 'storage' in eff and eff['storage'] is not None:
        sc = eff['storage']
        stype = sc.get('type', 'local')
        return dict(
            type=stype, source='config',
            base_dir=sc.get('base_dir', '/tmp/pdr_storage' if stype == 'local' else '/tmp'),
            host=sc.get('host', 'localhost'),
            user=sc.get('username', ''),
            password=sc.get('password') or os.environ.get('PDR_STORAGE_PASSWORD', ''),
            rclone_remote=sc.get('rclone_remote', 'default'),
            use_mount=sc.get('use_mount', False),
            remote_path_prefix=sc.get('remote_path_prefix', None),
            mount_point=sc.get('mount_point'),
        )
    stype = os.environ.get('PDR_STORAGE_TYPE', 'local')
    return dict(
        type=stype, source='environment',
        base_dir=os.environ.get('PDR_STORAGE_DIR', '/tmp/pdr_storage' if stype == 'local' else '/tmp'),
        host=os.environ.get('PDR_STORAGE_HOST', 'localhost'),
        user=os.environ.get('PDR_STORAGE_USER', ''),
        password=os.environ.get('PDR_STORAGE_PASSWORD', ''),
        rclone_remote=os.environ.get('PDR_STORAGE_RCLONE_REMOTE', 'default'),
        use_mount=os.environ.get('PDR_STORAGE_USE_MOUNT', 'false').lower() == 'true',
        remote_path_prefix=os.environ.get('PDR_STORAGE_REMOTE_PATH_PREFIX', None),
        mount_point=None,
    )


# ------------------------------------------------------------ 1. configuration

def check_config_file(ctx: Ctx):
    if not ctx.config_path:
        return WARN, ("no --config given: built-in defaults + environment "
                      "(defaults contain developer paths)")
    path = os.path.abspath(ctx.config_path)
    if not os.path.isfile(path):
        _fail(f"{path}: no such file")
    import yaml
    try:
        with open(path) as fh:
            cfg = yaml.safe_load(fh.read())
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ''
        _fail(f"{path}: YAML error{where}: {getattr(exc, 'problem', exc)}")
    if not isinstance(cfg, dict):
        _fail(f"{path}: top level is not a mapping")
    ctx.file_config = cfg
    ctx.config_ok = True
    if 'pdr' not in cfg:
        return WARN, (f"{path}: no 'pdr:' section - the runner then DISCARDS the file "
                      "and uses built-in defaults + env")
    return PASS, f"{path} ({len(cfg)} sections)"


def check_config_env(ctx: Ctx):
    cfg = ctx.file_config
    has_pdr = bool(cfg and 'pdr' in cfg)
    has_storage = bool(cfg and cfg.get('storage') is not None)
    notes, ignored = [], []
    honored = []
    for var in _ENV_DB:
        if var in os.environ:
            honored.append(var)   # DatabaseManager: env always wins
    for var in _ENV_STORAGE_DEFAULTBUILD:
        if var in os.environ:
            if not has_pdr or not has_storage:
                honored.append(var)
            else:
                ignored.append(var)
    for var in _ENV_STORAGE_ONLY_NOFILE:
        if var in os.environ:
            (honored if not has_storage else ignored).append(var)
    for var in _ENV_PDR:
        if var in os.environ:
            (honored if not has_pdr else ignored).append(var)
    for var in _ENV_OTHER:
        if var in os.environ:
            honored.append(var)
    if 'PDR_DB_PASSWORD' in honored:
        honored[honored.index('PDR_DB_PASSWORD')] = 'PDR_DB_PASSWORD(hidden)'
    if 'PDR_STORAGE_PASSWORD' in honored:
        honored[honored.index('PDR_STORAGE_PASSWORD')] = 'PDR_STORAGE_PASSWORD(hidden)'
    if honored:
        notes.append('override: ' + ', '.join(honored))
    if ignored:
        notes.append('IGNORED by the code because the config file defines the '
                     'section: ' + ', '.join(ignored))
    if not notes:
        return PASS, 'no PDR_* environment overrides set'
    return (WARN if ignored else PASS), '; '.join(notes)


def check_config_secrets(ctx: Ctx):
    dbcfg = ctx.eff.get('database') or {}
    if os.environ.get('PDR_DB_PASSWORD'):
        db = 'set (env)'
    elif dbcfg.get('password'):
        db = 'set (config file)'
    else:
        db = 'NOT set'
    st = _resolve_storage(ctx)
    stor = 'set' if st['password'] else 'not set'
    dbtype = (os.environ.get('PDR_DB_TYPE') or dbcfg.get('type') or 'sqlite')
    need_db = dbtype in ('mysql', 'postgresql')
    detail = f"DB password {db} (values never printed); storage password {stor}"
    if need_db and db == 'NOT set':
        _fail(detail + f" - required for {dbtype}")
    if dbcfg.get('password') and not os.environ.get('PDR_DB_PASSWORD'):
        return WARN, detail + " - password stored in the config file, prefer PDR_DB_PASSWORD"
    return PASS, detail


def check_config_sections(ctx: Ctx):
    cfg = ctx.file_config
    if cfg is None:
        _skip("no config file")
    from pdr_run.cli.runner import VALID_CONFIG_STRUCTURE, SECTION_NAME_ALIASES
    unknown_sections, unknown_keys = [], []
    for name, body in cfg.items():
        canon = SECTION_NAME_ALIASES.get(name, name)
        if canon not in VALID_CONFIG_STRUCTURE:
            unknown_sections.append(name)
        elif isinstance(body, dict):
            unknown_keys += [f"{name}.{k}" for k in body if k not in VALID_CONFIG_STRUCTURE[canon]]
    if unknown_sections:
        _fail("unknown top-level section(s) - the run aborts: " + ', '.join(unknown_sections))
    if unknown_keys:
        return WARN, "unlisted key(s) (typo? passed through): " + ', '.join(unknown_keys[:6])
    return PASS, "all sections and keys known"


# -------------------------------------------------- 2. KOSMA-tau installation

def check_base_dir(ctx: Ctx):
    base = _pdr_cfg(ctx, 'base_dir')
    ctx.base_dir = base
    if not base:
        _fail("pdr.base_dir not set")
    if not os.path.isdir(base):
        _fail(f"{base}: not a directory (runs abort: 'PDR directory does not exist')")
    n_inp = [d for d in ('pdrinpdata', 'onioninpdata', 'In') if os.path.exists(os.path.join(base, d))]
    return PASS, f"{os.path.realpath(base)} (has {', '.join(n_inp) or 'no input dirs'})"


def check_rundir_writable(ctx: Ctx):
    base = ctx.base_dir
    if not base or not os.path.isdir(base):
        _skip("no base_dir")
    try:
        _writable_probe(base)
    except OSError as exc:
        return WARN, (f"{base} not writable ({exc.strerror}); jobs run in temp dirs, "
                      "so this only matters for manual runs")
    ctx.disk_paths['run dir'] = base
    return PASS, "probe file created and removed"


def _exe_check(ctx: Ctx, key: str):
    name = _pdr_cfg(ctx, key)
    if not name:
        _fail(f"pdr.{key} not set")
    base = ctx.base_dir
    if not base:
        _skip("no base_dir")
    path = os.path.join(base, name)
    if os.path.islink(path) and not os.path.exists(path):
        _fail(f"{path}: broken symlink -> {os.readlink(path)}")
    if not os.path.exists(path):
        _fail(f"{path}: not found (a run raises FileNotFoundError)")
    if not os.path.isfile(path):
        _fail(f"{path}: not a regular file")
    if not os.access(path, os.X_OK):
        _fail(f"{path}: not executable")
    real = os.path.realpath(path)
    extra = f" -> {real}" if real != os.path.abspath(path) else ''
    return PASS, f"{name}{extra}"


def check_exe_pdr(ctx):
    return _exe_check(ctx, 'pdr_file_name')


def check_exe_onion(ctx):
    return _exe_check(ctx, 'onion_file_name')


def check_exe_getctrlind(ctx):
    return _exe_check(ctx, 'getctrlind_file_name')


def check_exe_mrt(ctx):
    return _exe_check(ctx, 'mrt_file_name')


def check_pdr_version(ctx: Ctx):
    name = _pdr_cfg(ctx, 'pdr_file_name')
    path = os.path.join(ctx.base_dir or '', name or '')
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        _skip("pdr executable not usable")
    with tempfile.TemporaryDirectory(prefix='pdr-preflight-') as cwd:
        try:
            proc = subprocess.Popen([path, '--version'], cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            _fail(f"cannot execute {path}: {exc}")
        try:
            stdout, stderr = proc.communicate(timeout=ctx.timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # also any children it started
            except OSError:
                pass
            proc.communicate()
            return WARN, (f"--version did not return within {ctx.timeout:g} s (killed); "
                          "executable may not support it")
    proc.stdout, proc.stderr = stdout, stderr
    text = (proc.stdout or '') + '\n' + (proc.stderr or '')
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not set(ln.strip()) <= set('-=*_ ')]
    keep = [ln for ln in lines if re.search(r'version|revision|git|hash|commit|compil|build|branch', ln, re.I)]
    summary = ' | '.join(keep or lines[:1]) or '(no output)'
    if proc.returncode != 0:
        return WARN, f"--version exited {proc.returncode}: {_short(summary)}"
    if re.search(r'dirty', text, re.I):
        return WARN, f"binary built from a DIRTY tree: {_short(summary)}"
    return PASS, _short(summary, 100)


def check_input_dirs(ctx: Ctx):
    from pdr_run.config.default_config import PDR_INP_DIRS
    base = ctx.base_dir
    if not base or not os.path.isdir(base):
        _skip("no base_dir")
    species = ctx.params.get('species')
    problems, warns, ok = [], [], []
    for d in PDR_INP_DIRS:
        p = os.path.join(base, d)
        if os.path.islink(p) and not os.path.exists(p):
            problems.append(f"{d}: broken symlink -> {os.readlink(p)}")
        elif not os.path.exists(p):
            if d == 'pdrinpdata':
                problems.append("pdrinpdata: missing")
            elif d == 'onioninpdata':
                (problems if species else warns).append("onioninpdata: missing")
            elif d == 'In':
                warns.append("In (dust tables): missing")
        elif not os.path.isdir(p):
            problems.append(f"{d}: not a directory")
        else:
            ok.append(d + ('->' + os.readlink(p) if os.path.islink(p) else ''))
    dangling = []
    pi = os.path.join(base, 'pdrinpdata')
    if os.path.isdir(pi):
        with os.scandir(pi) as it:
            dangling = [e.name for e in it if e.is_symlink() and not os.path.exists(e.path)]
        if dangling:
            warns.append(f"{len(dangling)} dangling symlink(s) in pdrinpdata: "
                         + ', '.join(sorted(dangling)[:3]))
    if problems:
        _fail('; '.join(problems + warns))
    if warns:
        return WARN, '; '.join(warns)
    return PASS, ', '.join(ok)


# ------------------------------------------------------- 3. template & inputs

def _find_template(ctx: Ctx, name: str) -> Optional[str]:
    """Search like ``engine._setup_template_files`` + ``open_template``."""
    base = ctx.base_dir or ''
    for sub in ('templates', 'pdrinpdata/templates', '.', 'pdrinpdata', 'onioninpdata', 'In'):
        p = os.path.normpath(os.path.join(base, sub, name))
        if os.path.isfile(p):
            return p
    return None


def _render_template(ctx: Ctx, text: str) -> str:
    """Substitute the KT_VAR placeholders exactly like ``create_json_from_job_id``."""
    from pdr_run.database.models import KOSMAtauParameters
    from pdr_run.models.kosma_tau import format_scientific, transform
    from pdr_run.models.kosma_tau import string_to_list
    subs = {}
    for col in KOSMAtauParameters.__table__.columns:
        default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
        if default is None:
            try:
                pytype = col.type.python_type
            except NotImplementedError:
                pytype = str
            default = {int: 1, float: 1.0, bool: False}.get(pytype, 'x')
        subs[col.name] = default
    for key in ('fuvstring', 'ifuvtype'):
        val = _first(ctx.params.get(key))
        if val is not None:
            subs[key] = int(float(val)) if key == 'ifuvtype' else val
    subs['species'] = 'CO'
    subs['CHEM_DATABASE_FILE'] = _pdr_cfg(ctx, 'chem_database')
    out = text
    for key, value in transform(subs).items():
        if key == 'KT_VARspecies_':
            out = out.replace(key, '["' + '", "'.join(string_to_list(str(value))) + '"]')
        elif key == 'KT_VARgrid_':
            out = out.replace(key, 'true' if value else 'false')
        else:
            out = out.replace(key, format_scientific(value))
    return out


def check_template_json(ctx: Ctx):
    name = _pdr_cfg(ctx, 'json_template_file')
    if ctx.json_template:
        path = ctx.json_template
        if not os.path.isfile(path):
            _fail(f"--json-template {path}: no such file")
        note = ''
        if name != 'pdr_config.json.template':
            note = (f"the engine copies --json-template to 'pdr_config.json.template' but "
                    f"pdr.json_template_file is '{name}': the run would read that one instead")
    else:
        path = _find_template(ctx, name)
        note = ''
        if not path:
            _fail(f"{name}: not found in base_dir/{{templates,pdrinpdata/templates,.,pdrinpdata,In}} "
                  "(run would skip JSON creation -> no pdrexe input)")
    with open(path) as fh:
        raw = fh.read()
    rendered = _render_template(ctx, raw)
    left = sorted(set(re.findall(r'KT_VAR\w*', rendered)))
    if left:
        _fail(f"{path}: placeholder(s) without a parameter column: {', '.join(left[:5])}")
    stripped = strip_json_comments(rendered)
    trailing = None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        # Trailing commas are accepted by json-fortran (the shipped template has one): note, not error.
        no_comma = re.sub(r',(\s*[}\]])', r'\1', stripped)
        try:
            parsed = json.loads(no_comma)
            trailing = stripped.count('\n', 0, exc.pos) + 1
        except json.JSONDecodeError:
            _fail(f"{path}: not parseable after substitution (comments allowed): "
                  f"line {exc.lineno} col {exc.colno}: {exc.msg}")
    ctx.template_path = path
    detail = f"{path} (placeholders substituted, {len(parsed)} sections, comments allowed)"
    if note:
        return WARN, detail + '; ' + note
    if trailing:
        # The shipped templates have one (near line 21); json-fortran skips stray commas.
        detail += f"; trailing comma near line {trailing} (json-fortran tolerates it)"
    return PASS, detail


def check_config_provenance(ctx: Ctx):
    import hashlib
    if not ctx.template_path:
        _skip("template check did not run or failed")
    with open(ctx.template_path, 'rb') as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return PASS, (f"each job stores its rendered pdr_config.json (pdr_model_jobs.config_json) "
                  f"and template sha256 {digest}")


def _data_file_check(ctx: Ctx, fname: str, required: bool = True):
    base = ctx.base_dir
    if not base:
        _skip("no base_dir")
    p = os.path.join(base, 'pdrinpdata', fname)
    if os.path.islink(p) and not os.path.exists(p):
        _fail(f"pdrinpdata/{fname}: broken symlink -> {os.readlink(p)}")
    if not os.path.isfile(p):
        if required:
            _fail(f"pdrinpdata/{fname}: not found")
        return WARN, f"pdrinpdata/{fname}: not found"
    size = os.path.getsize(p)
    if size == 0:
        _fail(f"pdrinpdata/{fname}: empty file")
    return PASS, f"pdrinpdata/{fname} ({size / 1e3:.1f} kB)"


def check_chem_db(ctx: Ctx):
    name = _pdr_cfg(ctx, 'chem_database')
    if not name:
        _fail("pdr.chem_database not set")
    return _data_file_check(ctx, name)


def check_binding_energies(ctx: Ctx):
    return _data_file_check(ctx, 'binding_energies.dist')


def check_fuv(ctx: Ctx):
    ifuvtype = _first(ctx.params.get('ifuvtype'))
    fuv = _first(ctx.params.get('fuvstring'))
    needed = str(ifuvtype) in ('5', '6', '5.0', '6.0')
    if not needed:
        _skip(f"ifuvtype={ifuvtype}: no FUV file read")
    if not fuv:
        _fail(f"ifuvtype={ifuvtype} needs a FUV file but fuvstring is empty")
    return _data_file_check(ctx, str(fuv))


# ------------------------------------------------------- 4. scratch and disk

def check_tmp_dir(ctx: Ctx):
    tmp = tempfile.gettempdir()
    _writable_probe(tmp)
    ctx.disk_paths['temp dir'] = tmp
    src = 'TMPDIR' if os.environ.get('TMPDIR') else 'system default'
    return PASS, f"{tmp} writable ({src}); jobs run in {tmp}/pdr-job*"


def check_disk_space(ctx: Ctx):
    st = _resolve_storage(ctx)
    if st['type'] == 'local':
        ctx.disk_paths['storage'] = _nearest_existing(st['base_dir'])
    dbcfg = ctx.eff.get('database') or {}
    dbpath = os.environ.get('PDR_DB_FILE') or dbcfg.get('path')
    dbtype = os.environ.get('PDR_DB_TYPE') or dbcfg.get('type') or 'sqlite'
    if dbtype == 'sqlite' and dbpath and dbpath != ':memory:':
        ctx.disk_paths['sqlite db'] = _nearest_existing(os.path.dirname(os.path.abspath(dbpath)))
    log_dir = os.environ.get('PDR_LOG_DIR', 'logs')
    ctx.disk_paths['log dir'] = _nearest_existing(log_dir)
    by_dev: Dict[int, List[Tuple[str, str]]] = {}
    for label, path in ctx.disk_paths.items():
        by_dev.setdefault(os.stat(path).st_dev, []).append((label, path))
    parts, low = [], []
    for entries in by_dev.values():
        free_gb = shutil.disk_usage(entries[0][1]).free / 1e9
        text = f"{'+'.join(l for l, _ in entries)} {free_gb:.0f} GB"
        parts.append(text)
        if free_gb < ctx.min_free_gb:
            low.append(text)
    detail = '; '.join(parts) + f" free (min {ctx.min_free_gb:g} GB)"
    if low:
        return WARN, "below threshold: " + '; '.join(low) + f" [limit {ctx.min_free_gb:g} GB]"
    return PASS, detail


# ----------------------------------------------------------- 5. storage

def _probe_payload() -> bytes:
    return f"pdr_run preflight {uuid.uuid4().hex}\n".encode() * 8


def _storage_local(ctx: Ctx, st):
    from pdr_run.storage.local import LocalStorage
    base = st['base_dir']
    if not os.path.isdir(base):
        anc = _nearest_existing(base)
        if not os.access(anc, os.W_OK):
            _fail(f"{base} does not exist and its parent {anc} is not writable")
        return WARN, f"{base} does not exist yet (a run creates it; {anc} is writable)"
    payload = _probe_payload()
    tag = f"preflight_probe_{uuid.uuid4().hex[:8]}"
    rel = os.path.join(tag, 'probe.bin')
    with tempfile.TemporaryDirectory(prefix='pdr-preflight-') as tmp:
        src, dst = os.path.join(tmp, 'src.bin'), os.path.join(tmp, 'dst.bin')
        with open(src, 'wb') as fh:
            fh.write(payload)
        t0 = time.monotonic()
        storage = LocalStorage(base)
        try:
            storage.store_file(src, rel)
            storage.retrieve_file(rel, dst)
            with open(dst, 'rb') as fh:
                same = fh.read() == payload
        finally:
            shutil.rmtree(os.path.join(base, tag), ignore_errors=True)
        dt = (time.monotonic() - t0) * 1e3
    if not same:
        _fail(f"{base}: read-back differs from what was written")
    return PASS, f"local {base}: write/read/compare/delete OK ({dt:.0f} ms)"


def _storage_sftp(ctx: Ctx, st):
    import paramiko
    host, base = st['host'], st['base_dir']
    payload = _probe_payload()
    name = f"{base.rstrip('/')}/preflight_probe_{uuid.uuid4().hex[:8]}"
    t = ctx.timeout
    t0 = time.monotonic()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kw = dict(username=st['user'], timeout=t, banner_timeout=t, auth_timeout=t)
        if st['password']:
            kw.update(password=st['password'], allow_agent=False, look_for_keys=False)
        client.connect(host, **kw)
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(t)
        try:
            sftp.stat(base)
        except IOError:
            _fail(f"sftp://{host}{base}: base directory not found")
        try:
            sftp.putfo(io.BytesIO(payload), name)
            buf = io.BytesIO()
            sftp.getfo(name, buf)
            same = buf.getvalue() == payload
        finally:
            try:
                sftp.remove(name)
            except Exception:  # noqa: BLE001
                pass
    finally:
        client.close()
    dt = (time.monotonic() - t0) * 1e3
    if not same:
        _fail(f"sftp {host}: read-back differs from what was written")
    return PASS, f"sftp {host}:{base}: connect/write/read/compare/delete OK ({dt:.0f} ms)"


def _storage_rclone(ctx: Ctx, st):
    t = ctx.timeout
    try:
        subprocess.run(['rclone', 'version'], check=True, capture_output=True, timeout=t)
    except FileNotFoundError:
        _fail("rclone is not installed or not in PATH")
    from pdr_run.storage.remote import RCloneStorage
    storage = RCloneStorage({'base_dir': st['base_dir'], 'rclone_remote': st['rclone_remote'],
                             'use_mount': st['use_mount'],
                             'remote_path_prefix': st['remote_path_prefix']})
    payload = _probe_payload()
    remote = storage._get_full_remote_path(f"preflight_probe_{uuid.uuid4().hex[:8]}")
    flags = ['--contimeout', f"{int(max(t, 1))}s", '--timeout', f"{int(max(t, 1))}s",
             '--retries', '1', '--low-level-retries', '1']
    hard = max(3 * t, 10)
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='pdr-preflight-') as tmp:
        src = os.path.join(tmp, 'src.bin')
        with open(src, 'wb') as fh:
            fh.write(payload)
        try:
            up = subprocess.run(['rclone', 'copyto', src, remote] + flags,
                                capture_output=True, timeout=hard)
            if up.returncode != 0:
                _fail(f"rclone copyto to {st['rclone_remote']} failed: "
                      f"{_short(up.stderr.decode(errors='replace'), 120)}")
            cat = subprocess.run(['rclone', 'cat', remote] + flags,
                                 capture_output=True, timeout=hard)
            same = cat.returncode == 0 and cat.stdout == payload
        finally:
            try:
                subprocess.run(['rclone', 'deletefile', remote] + flags,
                               capture_output=True, timeout=hard)
            except Exception:  # noqa: BLE001
                pass
    dt = (time.monotonic() - t0) * 1e3
    if not same:
        _fail(f"rclone {st['rclone_remote']}: read-back failed or differs")
    return PASS, f"rclone {st['rclone_remote']}: write/read/compare/delete OK ({dt:.0f} ms)"


def check_storage(ctx: Ctx):
    st = _resolve_storage(ctx)
    stype = st['type']
    if stype == 'local':
        return _storage_local(ctx, st)
    hard = max(4 * ctx.timeout, 15)
    probes = {'sftp': (_storage_sftp, hard), 'rclone': (_storage_rclone, hard + 15)}
    if stype in probes:
        fn, limit = probes[stype]
        try:
            return _with_timeout(lambda: fn(ctx, st), limit, f'{stype} probe')
        except CheckOutcome:
            raise
        except Exception as exc:  # noqa: BLE001 - connection/auth problems are FAILs, not crashes
            _fail(f"{stype} probe failed: {type(exc).__name__}: {_short(exc, 160)}")
    if stype == 'remote':
        _fail("storage type 'remote' is only an abstract base class (store_file raises "
              "NotImplementedError); use sftp or rclone")
    _fail(f"unsupported storage type '{stype}'")


# ----------------------------------------------------------- 6. database

def _db_engine(ctx: Ctx):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool
    from pdr_run.database.db_manager import DatabaseManager
    dm = DatabaseManager(ctx.eff.get('database') or {})   # env > file > defaults, validated
    ctx.db_manager = dm
    dbtype = dm.config.get('type', 'sqlite')
    ctx.db_type = dbtype
    url = dm._build_connection_string()
    connect_args = dict(dm.config.get('connect_args') or {})
    key = {'sqlite': 'timeout', 'mysql': 'connection_timeout',
           'postgresql': 'connect_timeout'}.get(dbtype)
    if key:
        connect_args[key] = int(max(ctx.timeout, 1))
    engine = create_engine(url, poolclass=NullPool, connect_args=connect_args)
    u = make_url(url)                     # credentials are deliberately left out of the display
    shown = f"{u.drivername}://{u.host or ''}{':' + str(u.port) if u.port else ''}/{u.database or ''}"
    return engine, dbtype, shown


def check_db_connect(ctx: Ctx):
    try:
        engine, dbtype, shown = _db_engine(ctx)
    except ValueError as exc:
        first = str(exc).strip().splitlines()[0]
        fields = re.findall(r'- (\w+): Set', str(exc))
        _fail(first + (f" (missing: {', '.join(fields)})" if fields else ''))
    dm = ctx.db_manager
    if dbtype == 'sqlite':
        path = dm.config.get('path')
        if path == ':memory:':
            return WARN, ("sqlite ':memory:' (default): every process gets an empty private DB; "
                          "set database.path or PDR_DB_FILE for a grid run")
        if not os.path.isfile(path):
            anc = _nearest_existing(os.path.dirname(os.path.abspath(path)))
            if not os.access(anc, os.W_OK):
                _fail(f"{path} does not exist and {anc} is not writable")
            return WARN, f"{path} does not exist yet (a run creates it); schema checks skipped"
        if not (os.access(path, os.R_OK) and os.access(path, os.W_OK)):
            _fail(f"{path}: not readable/writable by this user")
    from sqlalchemy import text
    where0 = shown if dbtype != 'sqlite' else dm.config.get('path')
    t0 = time.monotonic()

    def connect():
        conn = engine.connect()
        try:
            conn.execute(text('SELECT 1'))
            ver_sql = 'SELECT sqlite_version()' if dbtype == 'sqlite' else 'SELECT VERSION()'
            ver = conn.execute(text(ver_sql)).scalar()
        except Exception:
            conn.close()
            raise
        return conn, ver

    try:
        conn, ver = _with_timeout(connect, ctx.timeout + 3, f"{dbtype} connect")
    except ModuleNotFoundError as exc:
        _fail(f"database driver not installed: {exc}")
    except TimeoutError as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - report the driver's own message
        orig = getattr(exc, 'orig', exc)
        _fail(f"cannot connect to {where0}: {type(orig).__name__}: {orig}")
    dt = (time.monotonic() - t0) * 1e3
    ctx.db_conn, ctx.db_ok = conn, True
    where = shown if dbtype != 'sqlite' else dm.config.get('path')
    if dbtype == 'mysql':
        try:
            row = conn.execute(text("SHOW VARIABLES LIKE 'max_connections'")).fetchone()
            ctx.db_max_connections = int(row[1]) if row else None
        except Exception:  # noqa: BLE001
            pass
    return PASS, f"{dbtype} {str(ver)[:30]} at {where} ({dt:.0f} ms)"


def _need_db(ctx: Ctx):
    if not ctx.db_ok:
        _skip("no database connection")


def check_db_tables(ctx: Ctx):
    _need_db(ctx)
    from sqlalchemy import inspect
    from pdr_run.database.base import Base
    import pdr_run.database.models  # noqa: F401  (registers the models)
    existing = set(inspect(ctx.db_conn).get_table_names())
    ctx.db_tables = existing
    expected = set(Base.metadata.tables)
    missing = sorted(expected - existing)
    if missing:
        _fail(f"missing table(s): {', '.join(missing)} (a normal run creates them; "
              "--check does not)")
    return PASS, f"all {len(expected)} expected tables present"


def _columns_missing(ctx: Ctx):
    from sqlalchemy import inspect
    from pdr_run.database.base import Base
    from pdr_run.database.db_manager import _PDR_MODEL_JOB_ADDITIVE_COLUMNS
    insp = inspect(ctx.db_conn)
    additive = {n for n, _ in _PDR_MODEL_JOB_ADDITIVE_COLUMNS}
    generic, add_missing = {}, []
    for tname, table in Base.metadata.tables.items():
        if tname not in (ctx.db_tables or set()):
            continue
        have = {c['name'] for c in insp.get_columns(tname)}
        for col in table.columns.keys():
            if col not in have:
                if tname == 'pdr_model_jobs' and col in additive:
                    add_missing.append(col)
                else:
                    generic.setdefault(tname, []).append(col)
    # additive columns that ORM does not know: treat list as authoritative
    if 'pdr_model_jobs' in (ctx.db_tables or set()):
        have = {c['name'] for c in insp.get_columns('pdr_model_jobs')}
        add_missing = sorted(additive - have)
    return generic, add_missing, len(additive)


def check_db_columns(ctx: Ctx):
    _need_db(ctx)
    if ctx.db_tables is None:
        _skip("table check did not run")
    generic, _, _ = _columns_missing(ctx)
    if generic:
        txt = '; '.join(f"{t}: {', '.join(c[:4])}{'...' if len(c) > 4 else ''}"
                        for t, c in generic.items())
        _fail(f"column(s) missing vs. current models - {txt} (create_all() never ALTERs; "
              "needs a manual migration)")
    return PASS, "all model columns present in all existing tables"


def check_db_additive(ctx: Ctx):
    _need_db(ctx)
    if 'pdr_model_jobs' not in (ctx.db_tables or set()):
        _skip("pdr_model_jobs missing")
    _, missing, total = _columns_missing(ctx)
    if missing:
        _fail(f"{len(missing)}/{total} additive column(s) missing on pdr_model_jobs: "
              f"{', '.join(missing[:4])}{'...' if len(missing) > 4 else ''} - a normal run "
              "adds them (ensure_additive_columns); --check does not")
    return PASS, f"{total}/{total} additive run-status/uvcont/config-provenance columns present"


def check_db_rows(ctx: Ctx):
    _need_db(ctx)
    from sqlalchemy import func, select
    from pdr_run.database.base import Base
    parts = []
    for tname in ('pdr_model_jobs', 'model_names', 'kosmatau_parameters', 'chemical_databases'):
        if tname in (ctx.db_tables or set()):
            n = ctx.db_conn.execute(select(func.count()).select_from(Base.metadata.tables[tname])).scalar()
            parts.append(f"{tname.replace('pdr_model_', '').replace('kosmatau_', '')}={n}")
    jobs = Base.metadata.tables['pdr_model_jobs']
    if 'pdr_model_jobs' in (ctx.db_tables or set()):
        rows = ctx.db_conn.execute(
            select(jobs.c.status, func.count()).group_by(jobs.c.status)).fetchall()
        rows = sorted(rows, key=lambda r: -r[1])[:5]
        if rows:
            parts.append('status: ' + ', '.join(f"{s or 'NULL'}={n}" for s, n in rows))
    return PASS, '; '.join(parts) or 'no tables'


def check_db_stale(ctx: Ctx):
    _need_db(ctx)
    if 'pdr_model_jobs' not in (ctx.db_tables or set()):
        _skip("pdr_model_jobs missing")
    from sqlalchemy import func, select
    from pdr_run.database.base import Base
    from pdr_run.database.queries import DEFAULT_STALE_AFTER_S
    jobs = Base.metadata.tables['pdr_model_jobs']
    walltime = _pdr_cfg(ctx, 'max_walltime_s')
    stale_s = walltime * 1.5 if walltime else DEFAULT_STALE_AFTER_S
    running = ctx.db_conn.execute(
        select(func.count()).select_from(jobs).where(jobs.c.status == 'running')).scalar()
    cutoff = datetime.now() - timedelta(seconds=stale_s)
    stale = ctx.db_conn.execute(
        select(func.count()).select_from(jobs).where(jobs.c.status == 'running')
        .where(jobs.c.time_of_start.isnot(None)).where(jobs.c.time_of_start < cutoff)).scalar()
    detail = f"running={running}, stale(>{stale_s / 3600:.1f} h)={stale}"
    if stale:
        return WARN, detail + " - reset with: pdr_run --reset-stale-jobs"
    return PASS, detail


def check_db_write(ctx: Ctx):
    _need_db(ctx)
    if 'users' not in (ctx.db_tables or set()):
        _skip("users table missing")
    from sqlalchemy import func, select
    from pdr_run.database.base import Base
    users = Base.metadata.tables['users']
    probe = f"preflight_probe_{uuid.uuid4().hex[:8]}"
    conn = ctx.db_conn
    conn.rollback()                      # close the read transaction
    t0 = time.monotonic()
    trans = conn.begin()
    try:
        conn.execute(users.insert().values(username=probe, email=probe + '@invalid'))
        seen = conn.execute(select(func.count()).select_from(users)
                            .where(users.c.username == probe)).scalar()
    finally:
        trans.rollback()
    left = conn.execute(select(func.count()).select_from(users)
                        .where(users.c.username == probe)).scalar()
    conn.rollback()
    dt = (time.monotonic() - t0) * 1e3
    if seen != 1:
        _fail("INSERT inside the transaction was not visible")
    if left:
        _fail("probe row survived the rollback (non-transactional storage engine?)")
    return PASS, f"INSERT + rollback OK, no residue ({dt:.0f} ms)"


# --------------------------------------------------------- 7. post-processing

def check_onion_inputs(ctx: Ctx):
    base = ctx.base_dir
    species = ctx.params.get('species')
    if isinstance(species, str):
        from pdr_run.models.kosma_tau import string_to_list
        species = string_to_list(species)
    if not species:
        _skip("no species configured: onion does not run")
    if not base:
        _skip("no base_dir")
    d = os.path.join(base, 'onioninpdata')
    missing = [s for s in species if not os.path.isfile(os.path.join(d, f"ONION3.INP.{s}"))]
    if missing:
        _fail(f"ONION3.INP.<species> missing in onioninpdata for: {', '.join(missing[:8])}"
              " (a job fails at set_oniondir)")
    return PASS, f"onion inputs for {len(species)} species found; executable checked above"


_UVCONT_PROBE = (
    "import json, sys\n"
    "import h5py, numpy\n"
    "import kosma_h2\n"
    "from kosma_h2 import spectra\n"
    "from kosma_h2.atomic_data import get_data_dir\n"
    "print(json.dumps({'file': kosma_h2.__file__, 'data': str(get_data_dir())}))\n"
)


def check_uvcont(ctx: Ctx):
    cfg = ctx.eff.get('uv_continuum') or {}
    if not cfg.get('enabled', False):
        _skip("uv_continuum.enabled is false")
    from pdr_run.models.kosma_tau import UV_CONTINUUM_TOOL_RELPATH
    ktdir = cfg.get('kosma_tau_dir')
    if not ktdir:
        _fail("uv_continuum.enabled but kosma_tau_dir not set")
    tool = os.path.join(ktdir, UV_CONTINUUM_TOOL_RELPATH)
    if not os.path.isfile(tool):
        _fail(f"tool not found: {tool}")
    py = cfg.get('python_executable') or sys.executable
    if not (os.path.isfile(py) and os.access(py, os.X_OK)) and not shutil.which(py):
        _fail(f"python_executable not found: {py}")
    h2py = os.path.join(ktdir, 'h2py')
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join([h2py] + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))
    try:
        proc = subprocess.run([py, '-c', _UVCONT_PROBE], capture_output=True, text=True,
                              env=env, timeout=max(ctx.timeout * 6, 30), cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        _fail("importing kosma_h2 (h5py/numpy) timed out")
    if proc.returncode != 0:
        last = (proc.stderr.strip().splitlines() or ['?'])[-1]
        _fail(f"kosma_h2 not importable with {py} and PYTHONPATH={h2py}: {_short(last, 100)}")
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    missing = [f for f in ('uvh2b29.dat', 'uvh2c29.dat')
               if not os.path.isfile(os.path.join(info['data'], f))]
    if missing:
        _fail(f"kosma_h2 data dir {info['data']}: missing {', '.join(missing)}")
    notes = []
    if ctx.base_dir and not os.path.isdir(os.path.join(ctx.base_dir, 'In')):
        notes.append("dust inputs 'In/' absent, so the per-run extinction table AlAV.dat "
                     "cannot be produced")
    detail = (f"kosma_h2 importable ({py}), data files found; extinction table is embedded "
              "in each model file")
    return (WARN, detail + '; ' + notes[0]) if notes else (PASS, detail)


def check_simline(ctx: Ctx):
    cfg = ctx.eff.get('simline') or {}
    if not (cfg.get('enabled', False) or ctx.force_simline):
        _skip("simline.enabled is false (and no --force-simline)")
    simdir = cfg.get('simline_dir') or os.path.join(ctx.base_dir or '', 'simline')
    missing = []
    for rel, kind in (('python/run_simline.py', 'f'), ('bin/simline', 'x'),
                      ('obs.template', 'f'), ('molecules', 'd')):
        p = os.path.join(simdir, rel)
        ok = (os.path.isdir(p) if kind == 'd' else os.path.isfile(p)
              and (kind != 'x' or os.access(p, os.X_OK)))
        if not ok:
            missing.append(rel)
    if missing:
        _fail(f"{simdir}: missing/unusable {', '.join(missing)}")
    cfgfile = cfg.get('config_file') or os.path.join(simdir, 'python', 'simline_config.json')
    if not os.path.isfile(cfgfile):
        _fail(f"pipeline config not found: {cfgfile}")
    with open(cfgfile) as fh:
        try:
            json.loads(strip_json_comments(fh.read(), bang=True))
        except json.JSONDecodeError as exc:
            _fail(f"{cfgfile}: line {exc.lineno}: {exc.msg}")
    return PASS, f"{simdir}: driver, binary, obs.template, molecules/, config OK"


# --------------------------------------------------- 8. wall time & resources

def check_walltime(ctx: Ctx):
    wt = _pdr_cfg(ctx, 'max_walltime_s')
    if not wt:
        return WARN, ("pdr.max_walltime_s is unset: a hung pdrexe blocks its worker "
                      "forever (stale detection falls back to 6 h)")
    return PASS, f"max_walltime_s={wt:g} s ({wt / 3600:.1f} h); stale threshold {1.5 * wt / 3600:.1f} h"


def check_compression(ctx: Ctx):
    """One line: which stored files are gzip-compressed (storage.compress_files)."""
    pats = (ctx.eff.get('storage') or {}).get('compress_files') or []
    if isinstance(pats, str):
        pats = [pats]
    if not pats:
        return PASS, "storage.compress_files: none (results stored uncompressed)"
    return PASS, ("storage.compress_files: " + ", ".join(pats)
                  + " -> stored as <name>.gz (gzip level 6); pdrstruct is never compressed")


def check_workers(ctx: Ctx):
    import multiprocessing
    from pdr_run.core.engine import _calculate_cpu_count
    reserved = ctx.params.get('reserved_cpus', 2)
    total = multiprocessing.cpu_count()
    workers = ctx.workers if ctx.workers else _calculate_cpu_count(
        requested_cpus=ctx.cpus or 0, reserved_cpus=reserved)
    src = '--workers' if ctx.workers else ('--cpus' if ctx.cpus else 'auto')
    detail = f"{workers} workers ({src}) on {total} CPUs, {reserved} reserved"
    warn = []
    try:
        import psutil
        avail = psutil.virtual_memory().available / 1e9
        detail += f", {avail:.0f} GB RAM free"
        uvcont_on = bool((ctx.eff.get('uv_continuum') or {}).get('enabled'))
        if uvcont_on and avail / workers < 1.0:
            warn.append(f"only {avail / workers:.1f} GB RAM per worker (UV continuum needs ~0.7 GB each)")
    except ImportError:
        pass
    if workers > total:
        warn.append("more workers than CPUs")
    if ctx.db_max_connections and ctx.db_type == 'mysql':
        pool = (ctx.eff.get('database') or {})
        need = workers * (int(pool.get('pool_size', 5)) + int(pool.get('max_overflow', 5)))
        detail += f"; MySQL max_connections={ctx.db_max_connections}"
        if need > ctx.db_max_connections:
            warn.append(f"worst-case pool {need} connections exceeds server max_connections")
    if warn:
        return WARN, detail + ' - ' + '; '.join(warn)
    return PASS, detail


def check_imports(ctx: Ctx):
    import importlib
    mods = ['pdr_run.core.engine', 'pdr_run.models.kosma_tau', 'pdr_run.models.job_status',
            'pdr_run.database.queries', 'pdr_run.utils.retry', 'pdr_run.storage.local']
    st = _resolve_storage(ctx)['type']
    if st == 'sftp':
        mods.append('paramiko')
    dbtype = os.environ.get('PDR_DB_TYPE') or (ctx.eff.get('database') or {}).get('type') or 'sqlite'
    if dbtype == 'mysql':
        mods.append('mysql.connector')
    elif dbtype == 'postgresql':
        mods.append('psycopg2')
    bad = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{m} ({type(exc).__name__}: {_short(exc, 60)})")
    if bad:
        _fail('import failed: ' + '; '.join(bad))
    if st == 'rclone' and not shutil.which('rclone'):
        _fail("storage type is rclone but 'rclone' is not in PATH")
    return PASS, f"{len(mods)} modules/drivers importable (storage={st}, db={dbtype})"


# ------------------------------------------------------------------- driver

CHECKS: List[Tuple[str, Callable[[Ctx], Tuple[str, str]]]] = [
    ('config.file', check_config_file),
    ('config.sections', check_config_sections),
    ('config.env', check_config_env),
    ('config.secrets', check_config_secrets),
    ('python.imports', check_imports),
    ('kt.base_dir', check_base_dir),
    ('kt.rundir_write', check_rundir_writable),
    ('kt.exe.pdr', check_exe_pdr),
    ('kt.exe.onion', check_exe_onion),
    ('kt.exe.getctrlind', check_exe_getctrlind),
    ('kt.exe.mrt', check_exe_mrt),
    ('kt.pdr_version', check_pdr_version),
    ('kt.input_dirs', check_input_dirs),
    ('tpl.json', check_template_json),
    ('tpl.provenance', check_config_provenance),
    ('tpl.chem_network', check_chem_db),
    ('tpl.binding_energies', check_binding_energies),
    ('tpl.fuv_file', check_fuv),
    ('tmp.dir', check_tmp_dir),
    ('storage', check_storage),
    ('disk.free', check_disk_space),
    ('db.connect', check_db_connect),
    ('db.tables', check_db_tables),
    ('db.columns', check_db_columns),
    ('db.additive_columns', check_db_additive),
    ('db.rows', check_db_rows),
    ('db.stale_jobs', check_db_stale),
    ('db.write_rollback', check_db_write),
    ('post.onion', check_onion_inputs),
    ('post.uv_continuum', check_uvcont),
    ('post.simline', check_simline),
    ('run.walltime', check_walltime),
    ('run.compression', check_compression),
    ('run.workers', check_workers),
]


class _LogQuieter:
    """Detach console log handlers and collect WARNING+ records while checking.

    File handlers keep receiving everything, so the normal log file still
    holds the detail; the screen only shows the report.
    """

    class _Collector(logging.Handler):
        def __init__(self):
            super().__init__(level=logging.WARNING)
            self.count = 0
            self._seen = set()

        def emit(self, record):
            if id(record) not in self._seen:
                self._seen.add(id(record))
                self.count += 1

    def __enter__(self):
        self.collector = self._Collector()
        self.saved = []
        self.log_files = []
        names = ['', 'dev', 'production', 'pdr_run']
        for name in names:
            lg = logging.getLogger(name)
            removed = [h for h in lg.handlers
                       if isinstance(h, logging.StreamHandler)
                       and not isinstance(h, logging.FileHandler)]
            for h in removed:
                lg.removeHandler(h)
            for h in lg.handlers:
                if isinstance(h, logging.FileHandler) and h.baseFilename not in self.log_files:
                    self.log_files.append(h.baseFilename)
            lg.addHandler(self.collector)
            self.saved.append((lg, removed))
        self._warn = warnings.catch_warnings()
        self._warn.__enter__()
        warnings.simplefilter('ignore')
        return self

    def __exit__(self, *exc):
        self._warn.__exit__(*exc)
        for lg, removed in self.saved:
            lg.removeHandler(self.collector)
            for h in removed:
                lg.addHandler(h)
        return False


def _format_report(ctx: Ctx, elapsed: float, logfiles: List[str], n_logged: int) -> str:
    lines = []
    width = max(len(r.name) for r in ctx.results)
    for r in ctx.results:
        # PASS lines are cut to one screen line; WARN/FAIL keep their reason.
        detail = _short(r.detail, _DETAIL_WIDTH - width - 10 if r.status in (PASS, SKIP) else 400)
        lines.append(f"[{r.status}] {r.name:<{width}}  {detail}".rstrip())
    counts = {s: sum(1 for r in ctx.results if r.status == s) for s in (PASS, WARN, FAIL, SKIP)}
    verdict = 'NOT READY' if counts[FAIL] else ('READY (with warnings)' if counts[WARN] else 'READY')
    lines.append('')
    lines.append(f"Summary: {counts[PASS]} PASS, {counts[WARN]} WARN, {counts[FAIL]} FAIL, "
                 f"{counts[SKIP]} SKIP in {elapsed:.1f} s -> {verdict}")
    cfg = os.path.abspath(ctx.config_path) if ctx.config_path else 'none (built-in defaults)'
    log = ', '.join(logfiles) if logfiles else 'no log file configured'
    lines.append(f"Config: {cfg} | detailed log: {log} | "
                 f"{n_logged} WARNING+ log message(s) suppressed on screen"
                 + (f" | --rerun {','.join(ctx.rerun)}: matching stored nodes are recomputed"
                    if ctx.rerun else ""))
    return '\n'.join(lines)


def run_preflight(config_path: Optional[str] = None, json_output: bool = False,
                  min_free_gb: float = DEFAULT_MIN_FREE_GB,
                  timeout: float = DEFAULT_TIMEOUT_S, json_template: Optional[str] = None,
                  workers: Optional[int] = None, cpus: Optional[int] = None,
                  force_simline: bool = False, species: Optional[List[str]] = None,
                  rerun: Optional[tuple] = None, out=None) -> int:
    """Run all checks, print the report to *out* (default stdout), return the exit code."""
    out = out or sys.stdout
    ctx = Ctx(config_path=config_path, json_template=json_template,
              min_free_gb=min_free_gb, timeout=timeout, workers=workers, cpus=cpus,
              force_simline=force_simline, cli_species=species, rerun=rerun)
    t0 = time.monotonic()
    with _LogQuieter() as quiet:
        try:
            for name, fn in CHECKS:
                if name == 'config.file':
                    _run_check(ctx, name, fn)
                    if ctx.config_path and not ctx.config_ok:
                        break
                    # effective config is needed by everything else
                    try:
                        _compute_params(ctx)
                        _build_effective_config(ctx)
                        _collect_secrets(ctx)
                    except Exception as exc:  # noqa: BLE001
                        ctx.results.append(Result(
                            'config.effective', FAIL,
                            ctx.scrub(f"cannot build the effective configuration: "
                                      f"{type(exc).__name__}: {exc}")))
                        break
                    continue
                _run_check(ctx, name, fn)
        finally:
            if ctx.db_conn is not None:
                try:
                    ctx.db_conn.close()
                except Exception:  # noqa: BLE001
                    pass
        elapsed = time.monotonic() - t0
        logfiles, n_logged = quiet.log_files, quiet.collector.count
    if not ctx.results:
        ctx.results.append(Result('preflight', FAIL, 'no checks ran'))
    failed = any(r.status == FAIL for r in ctx.results)
    if json_output:
        counts = {s: sum(1 for r in ctx.results if r.status == s) for s in (PASS, WARN, FAIL, SKIP)}
        payload = {
            'ok': not failed,
            'summary': counts,
            'elapsed_s': round(elapsed, 3),
            'config_file': os.path.abspath(config_path) if config_path else None,
            'rerun': list(rerun) if rerun else None,
            'log_files': logfiles,
            'suppressed_warning_log_messages': n_logged,
            'checks': [dict(name=r.name, status=r.status, detail=r.detail,
                            elapsed_s=round(r.elapsed_s, 3)) for r in ctx.results],
        }
        out.write(ctx.scrub(json.dumps(payload, indent=2)) + '\n')
    else:
        out.write(ctx.scrub(_format_report(ctx, elapsed, logfiles, n_logged)) + '\n')
    return 1 if failed else 0
