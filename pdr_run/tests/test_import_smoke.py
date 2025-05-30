"""Lightweight smoke tests for import functionality."""

def test_no_circular_imports():
    """Quick test that core imports work without circular dependencies."""
    try:
        from pdr_run.database import json_handlers
        from pdr_run.workflow.json_workflow import prepare_json_config
        # If we get here, imports worked
        assert True
    except ImportError as e:
        if "circular" in str(e).lower() or "import" in str(e).lower():
            print(f"Circular import detected: {e}")
            assert False, f"Circular import detected: {e}"
        raise

def test_basic_parameter_substitution():
    """Lightweight test of parameter substitution without database."""
    from pdr_run.database.json_handlers import apply_parameters_to_json
    
    template = {"value": "${test}", "other": "KT_VARother_"}
    params = {"test": "123", "other": "456"}
    result = apply_parameters_to_json(template, params)
    
    assert result["value"] == 123, f"Expected 123, got {result['value']}"
    assert result["other"] == 456, f"Expected 456, got {result['other']}"