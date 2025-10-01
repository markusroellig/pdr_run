import json
import multiprocessing
import os
from pathlib import Path

import pytest

from pdr_run.core.engine import run_parameter_grid, _build_default_config
from pdr_run.database.db_manager import get_db_manager, reset_db_manager


@pytest.mark.integration
@pytest.mark.mysql
def test_mysql_connection_pressure(tmp_path):
    if os.environ.get("PDR_DB_TYPE") != "mysql":
        pytest.skip("MySQL backend not configured")

    params = {
        "metal": ["0.5"],
        "dens": ["3.0", "4.0"],
        "mass": ["1.0"],
        "chi": ["10.0"],
        "reserved_cpus": 0,
    }

    common_db = {
        "type": "mysql",
        "host": os.environ["PDR_DB_HOST"],
        "port": int(os.environ.get("PDR_DB_PORT", "3306")),
        "database": os.environ["PDR_DB_DATABASE"],
        "username": os.environ["PDR_DB_USERNAME"],
        "password": os.environ["PDR_DB_PASSWORD"],
        "diagnostics_enabled": True,
    }

    variants = [
        ("default_pool", {"pool_size": 20, "max_overflow": 30, "n_workers": min(4, multiprocessing.cpu_count())}),
        ("constrained_pool", {"pool_size": 4, "max_overflow": 0, "n_workers": 2}),
    ]

    artifacts = {}

    for label, pool_overrides in variants:
        reset_db_manager()
        config = _build_default_config(params=params)
        config["database"].update(common_db)
        config["database"].update(pool_overrides)
        config["pdr"]["base_dir"] = config["pdr"].get("base_dir", ".")
        diagnostics_path = tmp_path / f"{label}_snapshot.json"

        run_parameter_grid(
            params=params,
            model_name=f"diagnostics_{label}",
            config=config,
            parallel=True,
            n_workers=min(4, multiprocessing.cpu_count()),
            diagnostics_output_path=str(diagnostics_path),
            keep_tmp=True,
        )

        db_manager = get_db_manager()
        snapshot = db_manager.get_diagnostics_snapshot(include_events=False)
        snapshot["label"] = label
        artifacts[label] = snapshot
        (tmp_path / f"{label}_snapshot_inline.json").write_text(json.dumps(snapshot, indent=2))

    assert "default_pool" in artifacts and "constrained_pool" in artifacts
    assert artifacts["default_pool"]["pool_capacity"] >= artifacts["constrained_pool"]["pool_capacity"]
    assert artifacts["default_pool"]["checkouts"] >= artifacts["constrained_pool"]["checkouts"]