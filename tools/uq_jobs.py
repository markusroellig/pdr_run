#!/usr/bin/env python3
"""Build a ``pdr_run campaign`` file for one block of the KOSMA-tau uncertainty study.

    python3 tools/uq_jobs.py BLOCK --name NAME --out FILE.yaml --out-dir DIR --template TEMPLATE \\
        --nodes "5,0,2;6.5,-1,1" --pdr-from CONFIG.yaml --pdr-file-name pdrexe_pinned [block options]

Blocks (every block lists the unperturbed reference job of each node first, label ...ref, except
A1a/A3 where k = 0 / +0 iterations is the reference):

  a1a  zeta noise ensemble: cosmic_ray_rate x (1 + k*1e-12), k = 0..K (--k-max, default 9).
  a1b  inert perturbations: one job per --rows LINE (physical line number of the row in --network):
       a one-line reaction_factor_file with the factor --factor (default 1.1) on that row; plus one job
       with uncertainty.extra_global_iterations 1 (initial-abundance scaling does not exist in the code).
  a3   iteration test: extra_global_iterations 1, 2, 4 (--extra) plus the +0 reference.
  b    knob pairs: every --knobs entry (default: all scalar knobs) x --factors (default 0.5,2).
  c1   rate Monte Carlo: one job per factor file of --factor-dir (written by tools/uq/uq_rates.py sample
       in the kosma-tau repository: factors_0000.txt ...), first --n of them (default all).
  e2   plain node list (off-grid, e.g. half-decade nodes): no overrides.

Nodes: --nodes "logn,logM,logchi;logn,logM,logchi" and/or --nodes-file FILE (one "logn logM logchi" per
line, # comments) and/or --nodes-grid "logn=4,4.5;logM=0;logchi=1,2" (cross product).

Labels are <block>_<node tag>_<variant>, e.g. a1a_n5_M0_x2_k03 (node tag n<logn>_M<logM>_x<logchi>).
Knob names: pe_heating_scale, h2_formation_scale, grain_recombination_scale, crphot_scale,
h2star_scale.<cplus|splus|o|n|s|shplus|hplus>, collision_scale.<o_h|cplus_h|cplus_h2|h2_h>.
"""
import argparse
import itertools
import json
import math
import os
import re
import sys

import yaml

SCALAR_KNOBS = ['pe_heating_scale', 'h2_formation_scale', 'grain_recombination_scale', 'crphot_scale']
H2STAR = ['cplus', 'splus', 'o', 'n', 's', 'shplus', 'hplus']
COLLISION = ['o_h', 'cplus_h', 'cplus_h2', 'h2_h']
ALL_KNOBS = (SCALAR_KNOBS + ['h2star_scale.' + c for c in H2STAR] + ['collision_scale.' + c for c in COLLISION])


def tag(node):
    return 'n%g_M%g_x%g' % tuple(node)


def parse_nodes(args):
    nodes = []
    if args.nodes:
        nodes += [tuple(float(x) for x in item.split(',')) for item in args.nodes.split(';') if item.strip()]
    if args.nodes_file:
        for line in open(args.nodes_file):
            line = line.split('#')[0].split()
            if line:
                nodes.append(tuple(float(x) for x in line))
    if args.nodes_grid:
        axes = dict(item.split('=') for item in args.nodes_grid.split(';') if item.strip())
        lists = [[float(x) for x in axes[k].split(',')] for k in ('logn', 'logM', 'logchi')]
        nodes += list(itertools.product(*lists))
    if not nodes or any(len(n) != 3 for n in nodes):
        sys.exit('uq_jobs: give nodes as logn,logM,logchi (--nodes / --nodes-file / --nodes-grid)')
    seen, out = set(), []
    for n in nodes:                                      # keep order, drop duplicates
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def nested(dotted, value):
    """'collision_scale.o_h', 2 -> {'collision_scale': {'o_h': 2}}"""
    out = value
    for key in reversed(dotted.split('.')):
        out = {key: out}
    return out


def template_zeta(path):
    text = re.sub(r'KT_VAR\w+?_(?![A-Za-z0-9])', '1.0', open(path).read())
    return json.loads(text)['physical_params']['cosmic_ray_rate']


def network_row(path, line_no):
    """(index, reactant1, reactant2) of the network row at physical line ``line_no`` (fixed columns of the
    KOSMA-tau network format, as tools/uq/uq_rates.py reads it)."""
    with open(path, encoding='latin-1') as fh:
        for i, raw in enumerate(fh, start=1):
            if i == line_no:
                line = raw.rstrip('\r\n')
                if line[:1] == '*' or not line.strip():
                    sys.exit(f'uq_jobs: line {line_no} of {path} is a comment/blank line')
                return int(line[1:7]), line[8:23].strip() or '-', line[24:39].strip() or '-'
    sys.exit(f'uq_jobs: {path} has no line {line_no}')


def write_factor_file(path, line_no, row, factor):
    idx, r1, r2 = row
    with open(path, 'w') as fh:
        fh.write(f'# one-row factor file: network line {line_no} x {factor}  (tools/uq_jobs.py, block A1b)\n')
        fh.write('# line index reactant1 reactant2 lnf0 z g s_out\n')
        fh.write('%d %d %s %s %.15e 0 0 0\n' % (line_no, idx, r1, r2, math.log(factor)))


def build_jobs(args, nodes):
    jobs = []

    def add(block, node, variant, overrides):
        jobs.append({'label': f'{block}_{tag(node)}_{variant}', 'node': list(node), 'overrides': overrides})

    block = args.block
    for node in nodes:
        if block == 'a1a':
            zeta = args.zeta if args.zeta else template_zeta(args.template)
            for k in range(args.k_max + 1):
                add(block, node, 'k%02d' % k, {'physical_params': {'cosmic_ray_rate': zeta * (1.0 + k * 1e-12)}})
        elif block == 'a1b':
            if not (args.network and args.rows):
                sys.exit('uq_jobs: a1b needs --network and --rows')
            add(block, node, 'ref', {})
            os.makedirs(args.factor_dir, exist_ok=True)
            for line_no in args.rows:
                fpath = os.path.abspath(os.path.join(args.factor_dir, f'a1b_line{line_no}_x{args.factor:g}.txt'))
                write_factor_file(fpath, line_no, network_row(args.network, line_no), args.factor)
                add(block, node, f'row{line_no}', {'uncertainty': {'reaction_factor_file': fpath}})
            add(block, node, 'iter1', {'uncertainty': {'extra_global_iterations': 1}})
        elif block == 'a3':
            add(block, node, 'iter0', {})
            for n in args.extra:
                add(block, node, f'iter{n}', {'uncertainty': {'extra_global_iterations': n}})
        elif block == 'b':
            add(block, node, 'ref', {})
            for knob in args.knobs:
                for f in args.factors:
                    add(block, node, f"{knob.replace('.', '-')}_x{f:g}", {'uncertainty': nested(knob, f)})
        elif block == 'c1':
            files = sorted(f for f in os.listdir(args.factor_dir) if re.fullmatch(r'factors_\d+\.txt', f))
            if args.n:
                files = files[:args.n]
            if not files:
                sys.exit(f'uq_jobs: no factors_NNNN.txt in {args.factor_dir}')
            add(block, node, 'ref', {})
            for f in files:
                number = re.fullmatch(r'factors_(\d+)\.txt', f).group(1)
                add(block, node, f'r{number}',
                    {'uncertainty': {'reaction_factor_file': os.path.abspath(os.path.join(args.factor_dir, f))}})
        elif block == 'e2':
            add(block, node, 'plain', {})
    return jobs


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('block', choices=['a1a', 'a1b', 'a3', 'b', 'c1', 'e2'])
    ap.add_argument('--name', required=True, help='campaign name (output goes to <out-dir>/<name>/<label>/)')
    ap.add_argument('--out', required=True, help='campaign file to write (.yaml or .json)')
    ap.add_argument('--out-dir', required=True, help='campaign base directory for the results')
    ap.add_argument('--template', required=True, help='grid-1 tier-0 template (pdr_config.json.grid1...)')
    ap.add_argument('--pdr-from', help="YAML whose 'pdr' section is copied (e.g. the grid-1 config on halley)")
    ap.add_argument('--base-dir', help='pdr.base_dir (holds the pinned binary and the data directories)')
    ap.add_argument('--pdr-file-name', help='pdr.pdr_file_name (the pinned binary inside base-dir)')
    ap.add_argument('--chem-database', help='pdr.chem_database (network file in base-dir/pdrinpdata)')
    ap.add_argument('--max-walltime-s', type=float)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--tmp-dir')
    ap.add_argument('--metal', type=float, default=100)
    ap.add_argument('--nodes')
    ap.add_argument('--nodes-file')
    ap.add_argument('--nodes-grid')
    ap.add_argument('--zeta', type=float, help='a1a: nominal zeta (default: the template value)')
    ap.add_argument('--k-max', type=int, default=9, help='a1a: k = 0..K')
    ap.add_argument('--network', help='a1b: network file (to read the named rows)')
    ap.add_argument('--rows', type=int, nargs='+', help='a1b: physical line numbers of the rows to perturb')
    ap.add_argument('--factor', type=float, default=1.1, help='a1b: factor on the row')
    ap.add_argument('--factor-dir', help='a1b: where the one-line files are written; c1: the sampler output')
    ap.add_argument('--extra', type=int, nargs='+', default=[1, 2, 4], help='a3: extra global iterations')
    ap.add_argument('--knobs', nargs='+', default=ALL_KNOBS, choices=ALL_KNOBS, help='b: knobs')
    ap.add_argument('--factors', type=float, nargs='+', default=[0.5, 2.0], help='b: factors per knob')
    ap.add_argument('--n', type=int, help='c1: number of factor files (default all)')
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.block in ('a1b', 'c1') and not args.factor_dir:
        sys.exit(f'uq_jobs: {args.block} needs --factor-dir')
    pdr = {}
    if args.pdr_from:
        pdr = dict((yaml.safe_load(open(args.pdr_from)) or {}).get('pdr') or {})
    for key, val in (('base_dir', args.base_dir), ('pdr_file_name', args.pdr_file_name),
                     ('chem_database', args.chem_database), ('max_walltime_s', args.max_walltime_s)):
        if val is not None:
            pdr[key] = val
    pdr = {k: pdr[k] for k in ('base_dir', 'pdr_file_name', 'chem_database', 'onion_file_name',
                               'getctrlind_file_name', 'mrt_file_name', 'max_walltime_s',
                               'stall_first_iteration_s') if k in pdr}
    jobs = build_jobs(args, parse_nodes(args))
    labels = [j['label'] for j in jobs]
    assert len(labels) == len(set(labels)), 'duplicate labels'
    camp = {'name': args.name, 'block': args.block.upper(), 'out_dir': os.path.abspath(args.out_dir),
            'template': os.path.abspath(args.template), 'metal': args.metal, 'workers': args.workers}
    if args.tmp_dir:
        camp['tmp_dir'] = args.tmp_dir
    doc = {'campaign': camp, 'pdr': pdr, 'jobs': jobs}
    with open(args.out, 'w') as fh:
        if args.out.endswith('.json'):
            json.dump(doc, fh, indent=1)
        else:
            yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=None, width=120)
    print(f'{args.out}: block {args.block.upper()}, {len(jobs)} jobs on {len(set(tuple(j["node"]) for j in jobs))} node(s)')


if __name__ == '__main__':
    main()
