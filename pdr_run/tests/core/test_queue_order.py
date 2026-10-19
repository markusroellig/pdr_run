"""pdr.queue_order: order of the grid jobs handed to the workers."""

import itertools

import pytest

from pdr_run.core import queue_order as qo

# (metal, dens, mass, chi) strings in tenths of a dex, nested loops as in
# generate_parameter_combinations (density outermost after metal).
COMBS = list(itertools.product(['100'], ['50', '60', '70'], ['10', '30'], ['0', '20']))
IDS = list(range(101, 101 + len(COMBS)))


def name(c):
    return '_'.join(c) + '_00'


def test_natural_is_input_order_and_ignores_history():
    out, costs = qo.order_jobs(COMBS, IDS, 'natural', history=[(name(COMBS[0]), 9.0)])
    assert out == IDS and costs == [None] * len(IDS)


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        qo.validate_queue_order('lpt')
    with pytest.raises(ValueError):
        qo.order_jobs(COMBS, IDS, 'bogus')


def test_heuristic_without_history_density_then_chi_then_mass_descending():
    out, costs = qo.order_jobs(COMBS, IDS, 'cost_desc')
    got = [COMBS[IDS.index(j)][1:] for j in out]
    assert got == sorted([c[1:] for c in COMBS],
                         key=lambda t: (-int(t[0]), -int(t[2]), -int(t[1])))
    assert got[0] == ('70', '30', '20') and got[-1] == ('50', '10', '0')
    assert all(c is None for c in costs)


def test_cost_asc_is_reverse_key_order():
    out, _ = qo.order_jobs(COMBS, IDS, 'cost_asc')
    got = [COMBS[IDS.index(j)][1:] for j in out]
    assert got[0] == ('50', '10', '0') and got[-1] == ('70', '30', '20')


def test_history_layer_first_and_sorted_by_predicted_cost():
    # 1e7 layer: chi 1 (log 0) cheap 3 h, chi 100 (log 2) 20 h; 1e6 layer: 30 h (must stay behind 1e7)
    hist = [(name(('100', '70', '10', '0')), 3 * 3600), (name(('100', '70', '30', '0')), 5 * 3600),
            (name(('100', '70', '10', '20')), 20 * 3600),
            (name(('100', '60', '10', '0')), 30 * 3600),
            (name(('100', '50', '10', '20')), 100.0)]
    out, costs = qo.order_jobs(COMBS, IDS, 'cost_desc', hist)
    got = [COMBS[IDS.index(j)] for j in out]
    assert [c[1] for c in got[:4]] == ['70'] * 4
    assert [c[3] for c in got[:4]] == ['20', '20', '0', '0']      # 20 h pair before 4 h pair
    assert costs[0] == 20 * 3600 and costs[2] == 4 * 3600         # median of 3 h and 5 h
    # layer 1e6 has history at chi=0 only; chi=20 falls back to the layer median
    layer6 = [c for c in got if c[1] == '60']
    assert len(layer6) == 4 and all(c is not None for c in costs[4:8])
    # layer 1e5 comes last
    assert [c[1] for c in got[8:]] == ['50'] * 4


def test_stable_tie_break_keeps_natural_order():
    hist = [(name(c), 1000.0) for c in COMBS]                     # every node costs the same
    combs = [('100', '70', m, '0') for m in ('30', '30', '30')]   # identical keys
    ids = [7, 3, 9]
    for mode in ('cost_desc', 'cost_asc'):
        assert qo.order_jobs(combs, ids, mode, hist)[0] == [7, 3, 9]


def test_history_ignores_unparseable_names_and_missing_times():
    med = qo.history_medians([('junk', 5.0), (name(COMBS[0]), None), (name(COMBS[0]), 7.0)])
    assert med[1] == {5.0: 7.0}


def test_heuristic_cost_key():
    assert qo.heuristic_cost_key(4.0, 1.0) > qo.heuristic_cost_key(3.0, 3.0)
    assert qo.heuristic_cost_key(4.0, 3.0) > qo.heuristic_cost_key(4.0, 1.0)
