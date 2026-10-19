"""Order in which the jobs of a parameter grid are handed to the workers.

``pdr.queue_order`` selects the order of the job list that ``run_parameter_grid``
passes to the worker pool (a pull queue: a free worker takes the next job):

``natural``    the order of ``generate_parameter_combinations`` (metal, dens, mass, chi
               nested loops, density ascending as the outer loop). Default, unchanged.
``cost_desc``  most expensive predicted job first (longest-processing-time-first, LPT);
               shortens the tail in which only a few long jobs are left.
``cost_asc``   the reverse (cheapest first).

Sort key (see :func:`order_jobs`): the density layer is the dominant key (grid-1 tier 0:
the n_s = 1e7 layer is 37 % of the core-hours), then the predicted cost within the layer.
Ties keep the natural order (stable sort).

Predicted cost of a node, in this order of preference:

1. median ``execution_time`` of finished jobs of the same model name at the same
   (log n_s, log chi);
2. median at the same log n_s;
3. heuristic (:func:`heuristic_cost_key`): (log chi, log M), larger = more expensive.

Within one density layer either all nodes have a history prediction (2 covers the layer)
or none has, so seconds and heuristic ranks are never compared with each other.
"""

import logging
from statistics import median

logger = logging.getLogger(__name__)

QUEUE_ORDERS = ('natural', 'cost_desc', 'cost_asc')


def validate_queue_order(value):
    """Return ``value`` if it is a valid ``pdr.queue_order``, else raise ValueError."""
    if value not in QUEUE_ORDERS:
        raise ValueError(f"pdr.queue_order must be one of {QUEUE_ORDERS}, got {value!r}")
    return value


def _log_value(text):
    """Grid parameter string (tenths of a dex, e.g. '70') -> log10 value (7.0)."""
    return 0.1 * float(text)


def parse_job_name(job_name):
    """'<metal>_<dens>_<mass>_<chi>_00' -> (log n_s, log M, log chi) or None."""
    parts = str(job_name).split('_')
    if len(parts) < 4:
        return None
    try:
        return _log_value(parts[1]), _log_value(parts[2]), _log_value(parts[3])
    except ValueError:
        return None


def heuristic_cost_key(log_chi, log_mass):
    """Cost rank of a node within a density layer when there is no history.

    Larger = more expensive. Chemistry stalls and long convergence tails grow with
    the FUV field (grid-1 tier 0: stalls at n_s >= 1e6, chi >= 1e4) and the clump
    mass sets the column depth, so: (log chi descending, log M descending).
    """
    return (log_chi, log_mass)


def history_medians(history):
    """history: iterable of (job_name, exec_s) -> ({(n, chi): median}, {n: median})."""
    by_pair, by_dens = {}, {}
    for name, exec_s in history:
        parsed = parse_job_name(name)
        if parsed is None or exec_s is None:
            continue
        log_n, _, log_chi = parsed
        by_pair.setdefault((log_n, log_chi), []).append(float(exec_s))
        by_dens.setdefault(log_n, []).append(float(exec_s))
    return ({k: median(v) for k, v in by_pair.items()},
            {k: median(v) for k, v in by_dens.items()})


def predict_cost(combination, medians):
    """Predicted seconds of one (metal, dens, mass, chi) node, or None (no history)."""
    by_pair, by_dens = medians
    log_n, log_chi = _log_value(combination[1]), _log_value(combination[3])
    if (log_n, log_chi) in by_pair:
        return by_pair[(log_n, log_chi)]
    return by_dens.get(log_n)


def order_jobs(param_combinations, job_ids, mode='natural', history=()):
    """Return (job_ids, costs) in the requested order.

    ``param_combinations[i]`` belongs to ``job_ids[i]``. ``costs[i]`` is the predicted
    seconds of the i-th returned job (None where only the heuristic applies).
    ``natural`` returns the input list unchanged.
    """
    validate_queue_order(mode)
    if mode == 'natural':
        return list(job_ids), [None] * len(job_ids)
    medians = history_medians(history)
    keys, costs = [], []
    for comb in param_combinations:
        log_n, log_mass, log_chi = _log_value(comb[1]), _log_value(comb[2]), _log_value(comb[3])
        pred = predict_cost(comb, medians)
        costs.append(pred)
        within = (pred,) if pred is not None else heuristic_cost_key(log_chi, log_mass)
        keys.append((log_n,) + within)
    sign = -1 if mode == 'cost_desc' else 1       # negated keys: stable ascending sort keeps ties natural
    order = sorted(range(len(job_ids)), key=lambda i: tuple(sign * x for x in keys[i]))
    return [job_ids[i] for i in order], [costs[i] for i in order]


def fetch_history(model_name_id):
    """(job_name, exec_s) of the finished jobs of one model name; [] if unavailable."""
    from pdr_run.database import get_db_manager
    from pdr_run.database.models import PDRModelJob
    from pdr_run.models.job_status import COMPLETE_OUTPUT_STATUSES
    session = get_db_manager().get_session()
    try:
        rows = (session.query(PDRModelJob.model_job_name, PDRModelJob.execution_time)
                .filter(PDRModelJob.model_name_id == model_name_id,
                        PDRModelJob.status.in_(COMPLETE_OUTPUT_STATUSES),
                        PDRModelJob.execution_time.isnot(None)).all())
    finally:
        session.close()
    return [(n, t.total_seconds() if hasattr(t, 'total_seconds') else float(t)) for n, t in rows]


def log_order(mode, job_ids, costs, count=10):
    """INFO line with the first ``count`` jobs and their predicted cost."""
    parts = []
    for jid, cost in list(zip(job_ids, costs))[:count]:
        parts.append(f"{jid}({'%.0f s' % cost if cost is not None else 'heuristic'})")
    logger.info("Queue order '%s', first %d of %d jobs: %s", mode, min(count, len(job_ids)),
                len(job_ids), ", ".join(parts))
