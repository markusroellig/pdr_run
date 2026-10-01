"""Physics summaries of finished nodes for ``pdr_run status --with-physics``.

Reads only the stored ``pdrstruct<node>.hdf5`` (local copies) with h5py, one
small set of slices per file, and caches the result per file (path, size,
mtime) so that each file is read once.  h5py is optional: without it the
extraction reports ``skipped`` and the rest of the status payload is
unaffected.  Nothing here writes anything except the cache file.

Quantities (dataset paths verified against KOSMA-tau rc2 output; the
(n_zones, n) layout is the one documented by ``h2py/kosma_h2/hdf5_reader.py``):

========== ==================================================================
log_Tsurf   log10 T_gas [K] of the first filled zone, column 2 of
            ``Local quantities/Gas state``
log_Tdeep   log10 T_gas [K] of the last (central) zone
log_H2IR_tot log10 of the sum of column 5 (I, erg cm-2 s-1 sr-1) of
            ``Integrated quantities/Spectrum/IR Lines/Spectrum IR small``
op_col      column o/p ratio of H2: ``Integrated quantities/Excitation/
            H2 ortho-para all levels``; fallback: odd-J over even-J sum of the
            last row of ``Local quantities/Auxiliary/Excitation/Level column
            densities`` (the H2 X block is the last n_x columns, J of
            ``Parameters/H2 energy levels X`` column 0)
AV_HH2      A_V (column 0 of ``Local quantities/Positions``) where the local
            molecular fraction first reaches 0.5 (linear interpolation
            between zones)
========== ==================================================================

"Filled" zones: n_H > 1 cm-3, A_V > 1e-10 and finite T (the same rule as
the paper figure scripts: the surface row of the output can be unfilled).
The molecular fraction is 2n(H2)/(n(H)+2n(H2)) from ``Local quantities/
Densities/Densities`` by default.  The stored dataset ``Local quantities/
Auxiliary/Molecular fraction`` changed its meaning between code versions and
the file does not say which one it holds, hence ``molfrac_source='dataset'``
is opt-in.  Species columns are located through the file's Metadata table
(rows named ``Densities``, label ``n(NAME)``).
"""

import gzip
import io
import json
import math
import os
import tempfile
import time

try:                                    # h5py is optional
    import h5py
    import numpy as np
except ImportError:                     # pragma: no cover - exercised via monkeypatch
    h5py = None
    np = None

ALGO_VERSION = 1                        # bump when a quantity's definition changes
QUANTITIES = ('log_Tsurf', 'log_Tdeep', 'log_H2IR_tot', 'op_col', 'AV_HH2')

P_GAS = 'Local quantities/Gas state'
P_POS = 'Local quantities/Positions'
P_DENS = 'Local quantities/Densities/Densities'
P_MOLFRAC = 'Local quantities/Auxiliary/Molecular fraction'
P_IR = 'Integrated quantities/Spectrum/IR Lines/Spectrum IR small'
P_OP = 'Integrated quantities/Excitation/H2 ortho-para all levels'
P_LEVCOL = 'Local quantities/Auxiliary/Excitation/Level column densities'
P_LEVX = 'Parameters/H2 energy levels X'
P_META = 'Metadata/Metadata'


def available():
    return h5py is not None


def _text(x):
    return x.decode(errors='ignore').strip('\x00 ') if isinstance(x, bytes) else str(x).strip()


def _species_columns(f, n_species):
    """{NAME: column} of the Densities dataset from the Metadata table."""
    cols = {}
    if P_META not in f:
        return cols
    for row in f[P_META][()]:
        r = [_text(x) for x in row]
        if len(r) > 4 and r[1] == 'Densities':
            try:
                idx = int(r[2])
            except ValueError:
                continue
            lab = r[4]
            if lab.startswith('n(') and lab.endswith(')'):
                lab = lab[2:-1]
            if 0 <= idx < n_species:
                cols[lab.upper()] = idx
    return cols


def _finite(x):
    return x is not None and math.isfinite(x)


def _log10(x):
    return round(math.log10(x), 6) if _finite(x) and x > 0 else None


def _molecular_fraction(f, molfrac_source):
    """(fraction array, source label) or (None, reason)."""
    if molfrac_source == 'dataset':
        if P_MOLFRAC in f:
            return np.asarray(f[P_MOLFRAC][()])[:, 0].astype(float), 'dataset'
        return None, 'no molecular-fraction dataset'
    if P_DENS not in f:
        return None, 'no Densities dataset'
    dens = f[P_DENS]
    cols = _species_columns(f, dens.shape[1])
    if 'H' not in cols or 'H2' not in cols:
        return None, 'H/H2 not found in the Metadata table'
    n_h = np.asarray(dens[:, cols['H']], dtype=float)
    n_h2 = np.asarray(dens[:, cols['H2']], dtype=float)
    with np.errstate(all='ignore'):
        return 2.0 * n_h2 / (n_h + 2.0 * n_h2), 'densities'


def _front_av(av, frac):
    """A_V where frac first reaches 0.5, linear between zones.
    Returns (value or None, flag) with flag in {'ok', 'surface', 'not_reached'}."""
    good = np.isfinite(frac)
    av, frac = av[good], frac[good]
    if frac.size == 0:
        return None, 'not_reached'
    hit = np.nonzero(frac >= 0.5)[0]
    if hit.size == 0:
        return None, 'not_reached'
    i = int(hit[0])
    if i == 0:
        return float(av[0]), 'surface'
    f0, f1 = frac[i - 1], frac[i]
    t = (0.5 - f0) / (f1 - f0) if f1 != f0 else 1.0
    return float(av[i - 1] + t * (av[i] - av[i - 1])), 'ok'


def _op_ratio(f):
    if P_OP in f:
        v = float(np.asarray(f[P_OP][()]).ravel()[0])
        if v > 0 and math.isfinite(v):
            return v
    if P_LEVCOL in f and P_LEVX in f:
        lev = np.asarray(f[P_LEVX][()])
        nx = lev.shape[0]
        row = np.asarray(f[P_LEVCOL][-1, -nx:], dtype=float)
        jq = lev[:, 0].astype(int)
        even, odd = row[jq % 2 == 0].sum(), row[jq % 2 == 1].sum()
        if even > 0:
            return float(odd / even)
    return None


def extract_from_file(fobj, molfrac_source='densities'):
    """Quantities from an open h5py file.  Returns (q dict, flags dict);
    a quantity that cannot be computed is None (never guessed)."""
    q = {k: None for k in QUANTITIES}
    flags = {}
    gas = np.asarray(fobj[P_GAS][()], dtype=float)
    pos = np.asarray(fobj[P_POS][()], dtype=float)
    av = pos[:, 0]
    with np.errstate(invalid='ignore'):
        ok = np.isfinite(gas[:, 0]) & (gas[:, 0] > 1.0) & (av > 1e-10) & np.isfinite(gas[:, 2])
    if ok.any():
        tgas = gas[ok, 2]
        q['log_Tsurf'] = _log10(float(tgas[0]))
        q['log_Tdeep'] = _log10(float(tgas[-1]))
        flags['zones'] = int(ok.sum())
        frac, src = _molecular_fraction(fobj, molfrac_source)
        if frac is None:
            flags['AV_HH2'] = src
        else:
            q['AV_HH2'], flags['AV_HH2'] = _front_av(av[ok], frac[ok])
            flags['molfrac_source'] = src
    else:
        flags['zones'] = 0
    if P_IR in fobj:
        ir = fobj[P_IR]
        if ir.ndim == 2 and ir.shape[1] >= 6:
            tot = float(np.nansum(ir[:, 5]))
            q['log_H2IR_tot'] = _log10(tot)
    op = _op_ratio(fobj)
    q['op_col'] = None if op is None else round(op, 6)
    if q['AV_HH2'] is not None:
        q['AV_HH2'] = round(q['AV_HH2'], 6)
    return q, flags


def extract_quantities(path, molfrac_source='densities'):
    """Open *path* (plain or ``.gz``) and extract; raises on unreadable files."""
    if path.endswith('.gz'):
        with gzip.open(path, 'rb') as g:
            buf = io.BytesIO(g.read())
        with h5py.File(buf, 'r') as f:
            return extract_from_file(f, molfrac_source)
    with h5py.File(path, 'r') as f:
        return extract_from_file(f, molfrac_source)


class PhysicsCache:
    """JSON cache {path: {size, mtime_ns, algo, molfrac_source, q, flags}}.
    Written atomically; a corrupt or foreign file is ignored (treated as empty)."""

    def __init__(self, path):
        self.path = path
        self.entries = {}
        self.dirty = False
        if path and os.path.isfile(path):
            try:
                with open(path) as fh:
                    data = json.load(fh)
                if data.get('algo') == ALGO_VERSION:
                    self.entries = data.get('entries', {})
            except (OSError, ValueError, AttributeError):
                self.entries = {}

    def get(self, file_path, st, molfrac_source):
        e = self.entries.get(file_path)
        if (e and e.get('size') == st.st_size and e.get('mtime_ns') == st.st_mtime_ns
                and e.get('molfrac_source') == molfrac_source):
            return e
        return None

    def put(self, file_path, st, molfrac_source, q, flags):
        self.entries[file_path] = dict(size=st.st_size, mtime_ns=st.st_mtime_ns,
                                       molfrac_source=molfrac_source, q=q, flags=flags)
        self.dirty = True

    def save(self):
        if not self.path or not self.dirty:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.path)),
                                   prefix='.status_cache_')
        try:
            with os.fdopen(fd, 'w') as fh:
                json.dump({'algo': ALGO_VERSION, 'entries': self.entries}, fh, separators=(',', ':'))
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def find_struct_file(struct_dir, node):
    """Stored result file of *node* under *struct_dir*: plain or .gz, else None."""
    if not struct_dir:
        return None
    base = os.path.join(struct_dir, f'pdrstruct{node}.hdf5')
    for cand in (base, base + '.gz'):
        if os.path.isfile(cand):
            return cand
    return None


def collect_physics(nodes, struct_dir, cache, molfrac_source='densities', budget_s=20.0):
    """Physics of *nodes* (list of (node_id, job_id)).

    Returns (records, meta).  records: [{node, job_id, q, flags}].  meta counts
    computed / cached / no_file / failed / deferred (budget exhausted: the next
    call continues, the cache keeps what is done) or ``skipped`` (no h5py)."""
    meta = dict(h5py=available(), computed=0, cached=0, no_file=0, failed=0, deferred=0,
                skipped=None, molfrac_source=molfrac_source)
    if not available():
        meta['skipped'] = 'h5py not importable'
        return [], meta
    if not struct_dir or not os.path.isdir(struct_dir):
        meta['skipped'] = 'no local structure-file directory (use --struct-dir)'
        return [], meta
    t0 = time.monotonic()
    records = []
    for node, job_id in nodes:
        fp = find_struct_file(struct_dir, node)
        if fp is None:
            meta['no_file'] += 1
            continue
        st = os.stat(fp)
        hit = cache.get(fp, st, molfrac_source)
        if hit is not None:
            meta['cached'] += 1
            records.append(dict(node=node, job_id=job_id, q=hit['q'], flags=hit['flags']))
            continue
        if time.monotonic() - t0 > budget_s:
            meta['deferred'] += 1
            continue
        try:
            q, flags = extract_quantities(fp, molfrac_source)
        except Exception as exc:  # noqa: BLE001 - a bad file must not break the status
            meta['failed'] += 1
            records.append(dict(node=node, job_id=job_id, q={k: None for k in QUANTITIES},
                                flags={'error': f'{type(exc).__name__}: {exc}'[:160]}))
            continue
        cache.put(fp, st, molfrac_source, q, flags)
        meta['computed'] += 1
        records.append(dict(node=node, job_id=job_id, q=q, flags=flags))
    cache.save()
    return records, meta
