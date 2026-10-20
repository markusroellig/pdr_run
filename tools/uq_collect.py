#!/usr/bin/env python3
"""Collect the results of a ``pdr_run campaign`` into one HDF5 file (or summarise a single model file).

    python3 tools/uq_collect.py CAMPAIGN_DIR [--out FILE.h5] [--uv-lines]
    python3 tools/uq_collect.py --single pdrstruct_s.hdf5 [--out FILE.h5]

CAMPAIGN_DIR is <out_dir>/<name> (contains manifest.json and <label>/job.json). Output layout (every
dataset has one row per job in the order of the manifest; jobs without a result file are NaN rows with
has_output = 0, so a failed job never shifts the others):

  /jobs/{label,status,converged,overrides_json,config_sha256,result_sha256}   job table (strings)
  /jobs/{logn,logM,logchi,global_iterations,eps_final,wall_s,has_output}      numbers
  /ir/line_table        (n_lines, 5)  v', J', v'', J'', lambda [um]  (from the first job with output)
  /ir/intensity         (n_jobs, n_lines)  'Spectrum IR small' column 6 [erg cm-2 s-1 sr-1]
  /ir/line_table_identical   1 if every job has the same line table
  /uv/total             (n_jobs)  sum of 'Spectrum UV small' column 7 over all lines
  /uv/intensity         (n_jobs, n_uv)  only with --uv-lines
  /columns/species      names ('Additional output/species involved'); /columns/N (n_jobs, n_species) [cm-2]
  /structure/{T_gas_surface,T_gas_deep,AV_total,n_shells}
  /structure/front_AV/{H_H2,Cp_C,C_CO,Sp_S}   first sign change of w_a n_a - w_b n_b from the surface, A_V of
                         the log-linear interpolated crossing (NaN: no crossing). Pairs: n(H) = 2 n(H2);
                         n(C+) = n(C); n(C) = n(CO); n(S+) = n(S). Rows with n_H < 1 cm-3 (unfilled surface
                         rows) are ignored.
  /uq/<name>            the root attributes uq_* of every file (NaN / '' where a file lacks one)
  attrs of the root     campaign name, binary_sha256, template_sha256, collector version, source paths
"""
import argparse
import json
import math
import os
import sys

import h5py
import numpy as np

COLLECTOR_VERSION = 1
FRONTS = {  # name: (species a, weight a, species b, weight b); crossing of w_a n_a - w_b n_b
    'H_H2': ('H', 1.0, 'H2', 2.0),
    'Cp_C': ('C+', 1.0, 'C', 1.0),
    'C_CO': ('C', 1.0, 'CO', 1.0),
    'Sp_S': ('S+', 1.0, 'S', 1.0),
}
IR = 'Integrated quantities/Spectrum/IR Lines/Spectrum IR small'
UV = 'Integrated quantities/Spectrum/UV Lines/Spectrum UV small'
NAN = float('nan')


def crossing(av, a, b):
    """A_V of the first sign change of (a - b) from the first point; log-linear in the ratio a/b when both
    neighbours are positive, else linear in a - b. NaN when there is none or the first point has a == b."""
    d = a - b
    sign0 = np.sign(d[0]) if len(d) else 0
    if sign0 == 0:
        return NAN
    flip = np.nonzero(np.sign(d) == -sign0)[0]
    if flip.size == 0:
        return NAN
    j = int(flip[0])
    i = j - 1
    if min(a[i], b[i], a[j], b[j]) > 0:
        r0, r1 = math.log(a[i] / b[i]), math.log(a[j] / b[j])
    else:
        r0, r1 = d[i], d[j]
    return float(av[i] + (av[j] - av[i]) * r0 / (r0 - r1))


def species_names(f):
    raw = f['Additional output/species involved'][:]
    return [(x[0].decode() if isinstance(x[0], bytes) else str(x[0])).strip() for x in raw]


def collect_file(path, uv_lines=False):
    """Summary of one pdrstruct_s.hdf5 as a dict of numpy arrays / scalars."""
    out = {}
    with h5py.File(path, 'r') as f:
        names = species_names(f)
        out['species'] = names
        out['N'] = f['Integrated quantities/Column densities'][0, :].astype(float)
        ir = f[IR][:]
        out['ir_table'] = ir[:, :5]
        out['ir'] = ir[:, 5]
        uv = f[UV][:]
        out['uv_total'] = float(uv[:, 6].sum())
        if uv_lines:
            out['uv'] = uv[:, 6]
        av = f['Local quantities/Positions'][:, 0]
        gas = f['Local quantities/Gas state'][:]
        dens = f['Local quantities/Densities/Densities'][:]
        ok = gas[:, 0] >= 1.0                       # unfilled surface rows have n_H = 0
        out['n_shells'] = int(ok.sum())
        av, gas, dens = av[ok], gas[ok], dens[ok]
        out['T_surface'], out['T_deep'] = float(gas[0, 2]), float(gas[-1, 2])
        out['AV_total'] = float(av[-1])
        for name, (sa, wa, sb, wb) in FRONTS.items():
            if sa in names and sb in names:
                out['front_' + name] = crossing(av, wa * dens[:, names.index(sa)], wb * dens[:, names.index(sb)])
            else:
                out['front_' + name] = NAN
        out['uq'] = {k: (v.decode() if isinstance(v, bytes) else (v.item() if hasattr(v, 'item') else v))
                     for k, v in f.attrs.items() if k.startswith('uq_')}
    return out


def _strings(values):
    return np.array([str(v) for v in values], dtype=h5py.string_dtype())


def write_collection(out_path, rows, meta, uv_lines=False):
    """rows: list of dicts {label, state(dict|None), node, overrides, data (collect_file result or None)}."""
    first = next((r['data'] for r in rows if r['data']), None)
    with h5py.File(out_path, 'w') as h:
        for k, v in meta.items():
            h.attrs[k] = v
        h.attrs['collector_version'] = COLLECTOR_VERSION
        g = h.create_group('jobs')
        st = [r['state'] or {} for r in rows]
        fields = [s.get('fields') or {} for s in st]
        g['label'] = _strings(r['label'] for r in rows)
        g['status'] = _strings(s.get('status', 'not started') for s in st)
        g['converged'] = _strings(f.get('converged') or '' for f in fields)
        g['overrides_json'] = _strings(json.dumps(r['overrides'], sort_keys=True) for r in rows)
        g['config_sha256'] = _strings(s.get('config_sha256', '') for s in st)
        g['result_sha256'] = _strings(s.get('result_sha256', '') for s in st)
        for i, key in enumerate(('logn', 'logM', 'logchi')):
            g[key] = np.array([r['node'][i] for r in rows], dtype=float)
        g['global_iterations'] = np.array([NAN if f.get('global_iterations') is None else f['global_iterations']
                                           for f in fields], dtype=float)
        g['eps_final'] = np.array([NAN if f.get('eps_final') is None else f['eps_final'] for f in fields],
                                  dtype=float)
        g['wall_s'] = np.array([s.get('wall_s', NAN) for s in st], dtype=float)
        g['has_output'] = np.array([1 if r['data'] else 0 for r in rows], dtype=np.int8)
        if first is None:
            return
        n_lines, n_spec = len(first['ir']), len(first['species'])
        ir = h.create_group('ir')
        ir['line_table'] = first['ir_table']
        ir['intensity'] = np.array([r['data']['ir'] if r['data'] else np.full(n_lines, NAN) for r in rows])
        ir['line_table_identical'] = int(all(np.array_equal(r['data']['ir_table'], first['ir_table'])
                                             for r in rows if r['data']))
        uv = h.create_group('uv')
        uv['total'] = np.array([r['data']['uv_total'] if r['data'] else NAN for r in rows])
        if uv_lines:
            n_uv = len(first['uv'])
            uv['intensity'] = np.array([r['data']['uv'] if r['data'] else np.full(n_uv, NAN) for r in rows])
        c = h.create_group('columns')
        c['species'] = _strings(first['species'])
        c['N'] = np.array([r['data']['N'] if r['data'] else np.full(n_spec, NAN) for r in rows])
        c['species_identical'] = int(all(r['data']['species'] == first['species'] for r in rows if r['data']))
        s = h.create_group('structure')
        for key, name in (('T_surface', 'T_gas_surface'), ('T_deep', 'T_gas_deep'), ('AV_total', 'AV_total'),
                          ('n_shells', 'n_shells')):
            s[name] = np.array([r['data'][key] if r['data'] else NAN for r in rows], dtype=float)
        fr = s.create_group('front_AV')
        for name in FRONTS:
            fr[name] = np.array([r['data']['front_' + name] if r['data'] else NAN for r in rows])
        uq = h.create_group('uq')
        keys = sorted({k for r in rows if r['data'] for k in r['data']['uq']})
        for k in keys:
            vals = [r['data']['uq'].get(k) if r['data'] else None for r in rows]
            if all(v is None or isinstance(v, (int, float, np.integer, np.floating, bool)) for v in vals):
                uq[k] = np.array([NAN if v is None else float(v) for v in vals])
            else:
                uq[k] = _strings('' if v is None else v for v in vals)


def collect_campaign(cdir, out_path, uv_lines=False):
    manifest = json.load(open(os.path.join(cdir, 'manifest.json')))
    rows = []
    for job in manifest['jobs']:
        jdir = os.path.join(cdir, job['label'])
        try:
            state = json.load(open(os.path.join(jdir, 'job.json')))
        except (OSError, ValueError):
            state = None
        hdf = os.path.join(jdir, 'pdrstruct_s.hdf5')
        data = None
        if os.path.isfile(hdf):
            try:
                data = collect_file(hdf, uv_lines)
            except (OSError, KeyError, ValueError, IndexError) as exc:
                print(f"uq_collect: {job['label']}: unreadable result ({type(exc).__name__}: {exc})", file=sys.stderr)
        rows.append({'label': job['label'], 'state': state, 'node': job['node'],
                     'overrides': job['overrides'], 'data': data})
    meta = {'campaign': manifest['name'], 'block': manifest.get('block', ''), 'campaign_dir': os.path.abspath(cdir),
            'binary_sha256': manifest.get('binary_sha256') or '', 'template_sha256': manifest['template_sha256'],
            'network_sha256': manifest.get('network_sha256') or ''}
    write_collection(out_path, rows, meta, uv_lines)
    n_ok = sum(1 for r in rows if r['data'])
    print(f"{out_path}: {len(rows)} jobs, {n_ok} with output")
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('campaign_dir', nargs='?', help='<out_dir>/<name> of a campaign')
    ap.add_argument('--single', help='summarise one pdrstruct_s.hdf5 instead of a campaign')
    ap.add_argument('--out', help='output file (default <campaign_dir>/collected.h5; --single: print JSON)')
    ap.add_argument('--uv-lines', action='store_true', help='also store the per-line UV intensities')
    args = ap.parse_args(argv)
    if args.single:
        d = collect_file(args.single, args.uv_lines)
        if args.out:
            write_collection(args.out, [{'label': os.path.basename(args.single), 'state': None, 'node': [NAN] * 3,
                                         'overrides': {}, 'data': d}], {'source': os.path.abspath(args.single)},
                             args.uv_lines)
        else:
            scal = {k: v for k, v in d.items() if isinstance(v, (int, float))}
            scal.update(d['uq'])
            print(json.dumps(scal, indent=1))
        return 0
    if not args.campaign_dir:
        ap.error('campaign_dir or --single is required')
    collect_campaign(args.campaign_dir, args.out or os.path.join(args.campaign_dir, 'collected.h5'), args.uv_lines)
    return 0


if __name__ == '__main__':
    sys.exit(main())
