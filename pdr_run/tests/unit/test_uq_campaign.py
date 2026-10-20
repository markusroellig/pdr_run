"""Tests of the UQ campaign tooling: ``pdr_run campaign``, tools/uq_jobs.py, tools/uq_collect.py.

No pdrexe is run: the end-to-end test uses a shell script that copies a small synthetic result file.
"""
import json
import math
import os
import stat
import sys

import h5py
import numpy as np
import pytest
import yaml

from pdr_run.cli import campaign
from pdr_run.models.parameters import compute_radius, from_string_to_par

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import uq_collect  # noqa: E402
import uq_jobs  # noqa: E402

TEMPLATE = os.path.join(REPO, 'templates', 'pdr_config.json.grid1.template')
SAMPLE = '/home/roellig/pdr/pdr/kosma-tau/rundir_test/pdroutput_gridnode_h2b_new/pdrstruct_s.hdf5'


def make_struct(path, uq_attr=0):
    """Small synthetic pdrstruct_s.hdf5 with the datasets the collector reads (5 species, 6 shells)."""
    names = ['ELECTR', 'H', 'H2', 'C+', 'C']
    av = np.array([0.0, 0.1, 0.2, 0.4, 0.8, 1.6])
    nh = np.full(6, 1e4)
    nh[0] = 0.0                                    # unfilled surface row
    h = np.array([0, 1e4, 5e3, 1e3, 1e1, 1e-1])
    h2 = (1e4 - h) / 2
    cp = np.array([0, 1e-4, 1e-4, 5e-5, 1e-5, 1e-6])
    c = np.array([0, 1e-7, 1e-6, 5e-5, 9e-5, 1e-4])
    dens = np.zeros((6, 5))
    dens[:, 1], dens[:, 2], dens[:, 3], dens[:, 4] = h, h2, cp, c
    gas = np.zeros((6, 6))
    gas[:, 0] = nh
    gas[:, 2] = [0, 300, 200, 100, 50, 20]
    with h5py.File(path, 'w') as f:
        f.create_dataset('Additional output/species involved', data=np.array([[n] for n in names], dtype='S'))
        f['Integrated quantities/Column densities'] = np.arange(1.0, 6.0)[None, :]
        ir = np.zeros((4, 6))
        ir[:, 5] = [1e-6, 2e-6, 0.0, 4e-6]
        f['Integrated quantities/Spectrum/IR Lines/Spectrum IR small'] = ir
        uv = np.zeros((3, 7))
        uv[:, 6] = [1e-3, 2e-3, 3e-3]
        f['Integrated quantities/Spectrum/UV Lines/Spectrum UV small'] = uv
        f['Local quantities/Positions'] = np.column_stack([av, av, av, av, av])
        f['Local quantities/Gas state'] = gas
        f['Local quantities/Densities/Densities'] = dens
        f.attrs['uq_pe_heating_scale'] = 2.0
        f.attrs['uq_reaction_factor_file'] = 'none'
        f.attrs['uq_extra_global_iterations'] = uq_attr


# ----------------------------------------------------------------------------------------- collector
def test_crossing_log_linear():
    av = np.array([0.0, 1.0, 2.0])
    a = np.array([100.0, 10.0, 1.0])
    b = np.array([1.0, 10.0, 100.0])      # ln(a/b) = ln 100, 0, -ln 100: sign change between points 1 and 2
    assert uq_collect.crossing(av, a, b) == pytest.approx(1.0)      # a/b = 1 exactly at the middle point
    assert math.isnan(uq_collect.crossing(av, b, a * 0 + 1000.0))      # never crosses
    assert math.isnan(uq_collect.crossing(np.array([0.0, 1.0]), np.array([1.0, 1.0]), np.array([1.0, 2.0])))


def test_collect_file_synthetic(tmp_path):
    p = tmp_path / 's.hdf5'
    make_struct(p)
    d = uq_collect.collect_file(str(p))
    assert d['n_shells'] == 5                                   # unfilled row dropped
    assert (d['T_surface'], d['T_deep']) == (300.0, 20.0)
    assert d['AV_total'] == pytest.approx(1.6)
    assert d['uv_total'] == pytest.approx(6e-3)
    # n(H) = 2 n(H2) crossing between AV 0.1 (H=1e4 > 0) and 0.2 (H=5e3 vs 2 H2 = 5e3: equal -> sign 0), found at 0.4
    assert 0.2 <= d['front_H_H2'] <= 0.4
    assert 0.2 <= d['front_Cp_C'] <= 0.4                        # C+ 1e-4 -> 5e-5 crosses C 1e-6 -> 5e-5 at AV ~0.4
    assert math.isnan(d['front_C_CO'])                          # no CO in the synthetic file
    assert d['uq']['uq_pe_heating_scale'] == 2.0


@pytest.mark.skipif(not os.path.isfile(SAMPLE), reason='sample model output not available')
def test_collect_file_real_output():
    d = uq_collect.collect_file(SAMPLE)
    assert d['N'].shape == (175,) and len(d['species']) == 175
    assert d['ir'].shape == (4779,) and d['ir_table'].shape == (4779, 5)
    assert d['T_surface'] > d['T_deep'] > 0
    assert 0 < d['front_H_H2'] < d['front_Cp_C'] < d['front_C_CO'] < d['AV_total']     # PDR stratification


def test_collect_campaign_marks_failed_jobs(tmp_path):
    cdir = tmp_path / 'camp'
    for label, with_output in (('a', True), ('b', False)):
        (cdir / label).mkdir(parents=True)
        if with_output:
            make_struct(cdir / label / 'pdrstruct_s.hdf5')
        (cdir / label / 'job.json').write_text(json.dumps(
            {'status': 'finished' if with_output else 'timeout', 'wall_s': 1.0,
             'fields': {'converged': 'strict', 'global_iterations': 4, 'eps_final': 0.01}}))
    (cdir / 'manifest.json').write_text(json.dumps({
        'name': 'c', 'block': 'T', 'template_sha256': 'x',
        'jobs': [{'label': l, 'node': [5, 0, 2], 'overrides': {'k': i}} for i, l in enumerate('ab')]}))
    uq_collect.collect_campaign(str(cdir), str(cdir / 'collected.h5'))
    with h5py.File(cdir / 'collected.h5') as h:
        assert list(h['jobs/has_output'][:]) == [1, 0]
        assert h['jobs/status'][1].decode() == 'timeout'
        assert h['ir/intensity'].shape == (2, 4) and np.isnan(h['ir/intensity'][1]).all()
        assert h['uq/uq_pe_heating_scale'][0] == 2.0 and np.isnan(h['uq/uq_pe_heating_scale'][1])
        assert h['columns/N'].shape == (2, 5)


# ------------------------------------------------------------------------------------------ rendering
def test_render_matches_grid_substitution():
    node = (7.0, -1.0, 2.0)
    text, cfg = campaign.render_config(open(TEMPLATE).read(), node, {})
    from pdr_run.models.kosma_tau import format_scientific
    xnsur, mass, sint = (from_string_to_par(s) for s in ('70', '-10', '20'))
    rtot = compute_radius(mass, xnsur, 1.5, 0.2)
    assert cfg['physical_params']['surface_density'] == float(format_scientific(xnsur))
    assert cfg['physical_params']['cloud_radius'] == float(format_scientific(rtot))
    assert cfg['physical_params']['radiation_field_strength'] == float(format_scientific(sint))
    assert cfg['physical_params']['metallicity'] == 1.0
    assert 'KT_VAR' not in text


def test_render_override_merge_keeps_other_keys():
    base_text, base = campaign.render_config(open(TEMPLATE).read(), (5, 0, 2), {})
    _, cfg = campaign.render_config(open(TEMPLATE).read(), (5, 0, 2),
                                    {'physical_params': {'cosmic_ray_rate': 7.00000000007e-17},
                                     'uncertainty': {'collision_scale': {'o_h': 2.0}}})
    assert cfg['physical_params']['cosmic_ray_rate'] == 7.00000000007e-17
    assert cfg['physical_params']['surface_density'] == base['physical_params']['surface_density']
    assert cfg['uncertainty'] == {'collision_scale': {'o_h': 2.0}}
    assert {k: v for k, v in cfg.items() if k not in ('uncertainty', 'physical_params')} == \
           {k: v for k, v in base.items() if k != 'physical_params'}


def test_check_overrides_catches_typos(tmp_path):
    tcfg = campaign._loads_json(campaign._PLACEHOLDER.sub('1.0', open(TEMPLATE).read()))
    assert campaign.check_overrides('j', {'physical_params': {'cosmic_ray_rate': 1.0}}, tcfg) == []
    assert campaign.check_overrides('j', {'physical_params': {'cosmic_ray_rat': 1.0}}, tcfg)
    assert campaign.check_overrides('j', {'uncertainty': {'pe_heating_scale': 2.0}}, tcfg) == []
    assert campaign.check_overrides('j', {'uncertainty': {'pe_heating': 2.0}}, tcfg)
    assert campaign.check_overrides('j', {'uncertainty': {'h2star_scale': {'cplus': 2.0}}}, tcfg) == []
    assert campaign.check_overrides('j', {'uncertainty': {'h2star_scale': {'cplus_': 2.0}}}, tcfg)
    assert campaign.check_overrides('j', {'uncertainty': {'reaction_factor_file': str(tmp_path / 'no')}}, tcfg)


# ------------------------------------------------------------------------------------------ generator
def run_gen(tmp_path, block, *extra):
    out = tmp_path / f'{block}.yaml'
    uq_jobs.main([block, '--name', 't', '--out', str(out), '--out-dir', str(tmp_path / 'o'), '--template', TEMPLATE,
                  '--base-dir', str(tmp_path), '--pdr-file-name', 'pdrexe', '--chem-database', 'n.dat',
                  '--nodes', '5,0,2;6.5,-1,1', *extra])
    return yaml.safe_load(open(out))


def test_generator_blocks(tmp_path):
    d = run_gen(tmp_path, 'a1a', '--k-max', '9')
    assert len(d['jobs']) == 20
    z = [j['overrides']['physical_params']['cosmic_ray_rate'] for j in d['jobs'][:10]]
    assert z[0] == 7e-17 and all(z[k] == 7e-17 * (1 + k * 1e-12) for k in range(10)) and len(set(z)) == 10

    d = run_gen(tmp_path, 'a3')
    assert [j['label'].split('_')[-1] for j in d['jobs'][:4]] == ['iter0', 'iter1', 'iter2', 'iter4']
    assert d['jobs'][2]['overrides'] == {'uncertainty': {'extra_global_iterations': 2}}

    d = run_gen(tmp_path, 'b', '--knobs', 'pe_heating_scale', 'collision_scale.o_h')
    assert len(d['jobs']) == 2 * (1 + 4)
    assert d['jobs'][4]['overrides'] == {'uncertainty': {'collision_scale': {'o_h': 2.0}}}

    d = run_gen(tmp_path, 'e2')
    assert [j['overrides'] for j in d['jobs']] == [{}, {}] and d['jobs'][1]['node'] == [6.5, -1.0, 1.0]

    d = run_gen(tmp_path, 'e2', '--nodes-grid', 'logn=4,4.5;logM=0;logchi=1,2')
    assert len(d['jobs']) == 2 + 4          # explicit --nodes plus the cross product


def test_generator_a1b_and_c1(tmp_path):
    net = tmp_path / 'net.dat'
    net.write_text('* comment\n' + '%7d %-15s %-15s %-15s %-15s\n' % (1234, 'H3+', 'CO', 'HCO+', 'H2'))
    fd = tmp_path / 'ff'
    d = run_gen(tmp_path, 'a1b', '--network', str(net), '--rows', '2', '--factor-dir', str(fd))
    labels = [j['label'] for j in d['jobs']]
    assert labels[:3] == ['a1b_n5_M0_x2_ref', 'a1b_n5_M0_x2_row2', 'a1b_n5_M0_x2_iter1']
    line = [l for l in open(d['jobs'][1]['overrides']['uncertainty']['reaction_factor_file']) if l[0] != '#'][0].split()
    assert line[:4] == ['2', '1234', 'H3+', 'CO'] and float(line[4]) == pytest.approx(math.log(1.1))

    sd = tmp_path / 'sampler'
    sd.mkdir()
    for i in range(3):
        (sd / f'factors_{i:04d}.txt').write_text('2 1234 H3+ CO 0.1\n')
    d = run_gen(tmp_path, 'c1', '--factor-dir', str(sd), '--n', '2')
    assert len(d['jobs']) == 2 * 3 and d['jobs'][2]['label'].endswith('_r0001')


# ------------------------------------------------------------------------------------ end-to-end run
def make_base(tmp_path):
    base = tmp_path / 'base'
    for d in ('pdrinpdata', 'onioninpdata', 'In', 'templates'):
        (base / d).mkdir(parents=True)
    (base / 'pdrinpdata' / 'net.dat').write_text('x')
    for name in ('onionexe', 'getctrlind', 'mrt.exe'):
        (base / name).write_text('#!/bin/sh\nexit 0\n')
    exe = base / 'pdrexe_fake'
    exe.write_text('#!/bin/bash\n# uncertainty.reaction_factor_file\n'
                   'cp "$FAKE_STRUCT" pdroutput/pdrstruct_s.hdf5\n'
                   'echo \'{"converged":"strict","global_iterations":3,"eps_final":0.01}\' > pdroutput/run_status.json\n'
                   'echo "Model CONVERGED in 3 iterations (eps=1.0E-02)"\n'
                   'if [ -n "$FAKE_FAIL" ]; then exit 3; fi\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return base


def test_campaign_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv('FAKE_STRUCT', str(tmp_path / 's.hdf5'))
    make_struct(tmp_path / 's.hdf5', uq_attr=1)
    base = make_base(tmp_path)
    cfile = tmp_path / 'c.yaml'
    uq_jobs.main(['a3', '--name', 'e2e', '--out', str(cfile), '--out-dir', str(tmp_path / 'out'),
                  '--template', TEMPLATE, '--base-dir', str(base), '--pdr-file-name', 'pdrexe_fake',
                  '--chem-database', 'net.dat', '--nodes', '5,0,2', '--extra', '1', '--workers', '2'])
    doc = yaml.safe_load(open(cfile))
    doc['pdr'].update(onion_file_name='onionexe', getctrlind_file_name='getctrlind', mrt_file_name='mrt.exe')
    cfile.write_text(yaml.safe_dump(doc))

    assert campaign.main([str(cfile), '--check']) == 0
    assert not (tmp_path / 'out').exists()                          # --check writes nothing
    assert campaign.main([str(cfile), '--dry-run']) == 0
    cdir = tmp_path / 'out' / 'e2e'
    assert (cdir / 'manifest.json').is_file() and (cdir / 'a3_n5_M0_x2_iter1' / 'pdr_config.json').is_file()
    assert not (cdir / 'a3_n5_M0_x2_iter1' / 'pdrstruct_s.hdf5').exists()

    assert campaign.main([str(cfile)]) == 0
    man = json.load(open(cdir / 'manifest.json'))
    assert man['binary_sha256'] and man['template_sha256'] and man['jobs'][1]['overrides']
    for label in ('a3_n5_M0_x2_iter0', 'a3_n5_M0_x2_iter1'):
        st = json.load(open(cdir / label / 'job.json'))
        assert st['status'] == 'finished' and st['fields']['global_iterations'] == 3
        assert (cdir / label / 'pdrstruct_s.hdf5').is_file() and (cdir / label / 'run.log.gz').is_file()
    cfg = json.load(open(cdir / 'a3_n5_M0_x2_iter1' / 'pdr_config.json'))
    assert cfg['uncertainty'] == {'extra_global_iterations': 1}

    # resume: nothing to do; rerun of one label recomputes only that one
    mtime = (cdir / 'a3_n5_M0_x2_iter0' / 'job.json').stat().st_mtime_ns
    assert campaign.main([str(cfile)]) == 0
    assert (cdir / 'a3_n5_M0_x2_iter0' / 'job.json').stat().st_mtime_ns == mtime

    uq_collect.collect_campaign(str(cdir), str(cdir / 'collected.h5'))
    with h5py.File(cdir / 'collected.h5') as h:
        assert list(h['jobs/has_output'][:]) == [1, 1]
        assert h['uq/uq_extra_global_iterations'][0] == 1

    # a changed binary is refused for the same campaign name
    (base / 'pdrexe_fake').write_text((base / 'pdrexe_fake').read_text() + '# changed\n')
    assert campaign.main([str(cfile), '--rerun']) == 2


def test_campaign_failed_job_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv('FAKE_STRUCT', str(tmp_path / 's.hdf5'))
    monkeypatch.setenv('FAKE_FAIL', '1')
    make_struct(tmp_path / 's.hdf5')
    base = make_base(tmp_path)
    spec = {'campaign': {'name': 'f', 'out_dir': str(tmp_path / 'out'), 'template': TEMPLATE, 'metal': 100,
                         'workers': 1},
            'pdr': {'base_dir': str(base), 'pdr_file_name': 'pdrexe_fake', 'chem_database': 'net.dat',
                    'onion_file_name': 'onionexe', 'getctrlind_file_name': 'getctrlind',
                    'mrt_file_name': 'mrt.exe'},
            'jobs': [{'label': 'j', 'node': [5, 0, 2], 'overrides': {}}]}
    cfile = tmp_path / 'f.yaml'
    cfile.write_text(yaml.safe_dump(spec))
    assert campaign.main([str(cfile)]) == 1                 # exit code 3 of the fake
    st = json.load(open(tmp_path / 'out' / 'f' / 'j' / 'job.json'))
    assert st['returncode'] == 3 and st['status'] == 'aborted' and not (tmp_path / 'out' / 'f' / 'j' / 'pdrstruct_s.hdf5').exists()
