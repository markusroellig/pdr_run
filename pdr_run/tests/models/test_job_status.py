"""Unit tests for pdr_run.models.job_status.determine_job_status().

Pure filesystem-based tests (no database, no subprocess) - each test
builds a workdir/pdroutput/ layout and checks the resulting status.
"""

import json

from pdr_run.models.job_status import (
    determine_job_status,
    STATUS_FINISHED, STATUS_FINISHED_RELAXED, STATUS_NOT_CONVERGED,
    STATUS_FLAGGED, STATUS_ABORTED, STATUS_MISSING_OUTPUT, STATUS_TIMEOUT,
)


def _mkoutput(tmp_path, struct_file=True):
    outdir = tmp_path / 'pdroutput'
    outdir.mkdir()
    if struct_file:
        (outdir / 'pdrstruct_s.hdf5').write_bytes(b'not a real hdf5 file')
    return outdir


def _write_run_status(outdir, **fields):
    with open(outdir / 'run_status.json', 'w') as f:
        json.dump(fields, f)


def _write_textout(outdir, text):
    with open(outdir / 'TEXTOUT', 'w') as f:
        f.write(text)


class TestTimeoutAndAbort:
    """timed_out and non-zero exit take priority over everything else."""

    def test_timeout_wins_over_everything(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_run_status(outdir, converged='strict', global_iterations=10,
                          eps_final=1e-6, tsearch_flagged_shells=0)
        status, fields = determine_job_status(0, True, str(tmp_path))
        assert status == STATUS_TIMEOUT

    def test_timeout_with_no_returncode(self, tmp_path):
        _mkoutput(tmp_path)
        status, fields = determine_job_status(None, True, str(tmp_path))
        assert status == STATUS_TIMEOUT

    def test_nonzero_exit_is_aborted(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_run_status(outdir, converged='strict')
        status, fields = determine_job_status(1, False, str(tmp_path))
        assert status == STATUS_ABORTED

    def test_nonzero_exit_without_any_output_is_aborted(self, tmp_path):
        # STOP-abort case: exit != 0, no pdroutput files at all yet.
        status, fields = determine_job_status(2, False, str(tmp_path))
        assert status == STATUS_ABORTED


class TestMissingOutput:
    def test_exit_zero_but_no_struct_file(self, tmp_path):
        _mkoutput(tmp_path, struct_file=False)
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_MISSING_OUTPUT

    def test_exit_zero_no_pdroutput_dir_at_all(self, tmp_path):
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_MISSING_OUTPUT


class TestRunStatusJson:
    def test_strict_converged(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_run_status(outdir, converged='strict', global_iterations=42,
                          eps_final=1e-6, tsearch_flagged_shells=0,
                          chem_relaxed_calls=0, deferred_iterations=0,
                          code_version='v2.3.0', git_hash='abc123')
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED
        assert fields['global_iterations'] == 42
        assert fields['eps_final'] == 1e-6
        assert fields['code_version'] == 'v2.3.0'
        assert fields['git_hash'] == 'abc123'

    def test_relaxed_converged(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_run_status(outdir, converged='relaxed', global_iterations=60,
                          eps_final=5e-2, tsearch_flagged_shells=0)
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED_RELAXED

    def test_not_converged(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_run_status(outdir, converged='no', global_iterations=100,
                          eps_final=0.3)
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_NOT_CONVERGED

    def test_flagged_takes_priority_over_strict_converged(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_run_status(outdir, converged='strict', global_iterations=42,
                          eps_final=1e-6, tsearch_flagged_shells=3)
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FLAGGED
        assert fields['tsearch_flagged_shells'] == 3

    def test_not_converged_takes_priority_over_flagged_shells(self, tmp_path):
        # converged == 'no' must win even if tsearch also flagged shells.
        outdir = _mkoutput(tmp_path)
        _write_run_status(outdir, converged='no', tsearch_flagged_shells=5)
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_NOT_CONVERGED

    def test_malformed_json_falls_back_to_textout(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        with open(outdir / 'run_status.json', 'w') as f:
            f.write('{not valid json')
        _write_textout(outdir, 'Model CONVERGED in 7 iterations (eps=1.0e-05)\n')
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED
        assert fields['global_iterations'] == 7

    def test_unrecognized_converged_value_defaults_to_finished(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_run_status(outdir, converged='maybe')
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED


class TestTextoutFallback:
    """No run_status.json (pre-migration KOSMA-tau build)."""

    def test_strict_converged_line(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_textout(outdir, 'some header\nModel CONVERGED in 123 iterations (eps=2.5e-07)\ntail\n')
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED
        assert fields['global_iterations'] == 123
        assert fields['eps_final'] == 2.5e-07
        assert fields['converged'] == 'strict'

    def test_relaxed_converged_line(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_textout(
            outdir,
            'Model CONVERGED in 88 iterations (relaxed eps=5.0e-02, strict eps=1.2e-01)\n')
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED_RELAXED
        assert fields['global_iterations'] == 88
        # eps_final captures the relaxed (achieved) tolerance, not the
        # stricter target that was not reached.
        assert fields['eps_final'] == 1.2e-01

    def test_not_converged_line(self, tmp_path):
        outdir = _mkoutput(tmp_path)
        _write_textout(outdir, 'Model NOT CONVERGED after 300 iterations (eps=4.4e-01)\n')
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_NOT_CONVERGED
        assert fields['global_iterations'] == 300

    def test_no_recognizable_line_defaults_to_finished_legacy(self, tmp_path, caplog):
        outdir = _mkoutput(tmp_path)
        _write_textout(outdir, 'pdrexe legacy build, no convergence banner\n')
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED
        assert fields['global_iterations'] is None

    def test_no_textout_file_defaults_to_finished_legacy(self, tmp_path):
        _mkoutput(tmp_path)  # struct file present, but no TEXTOUT at all
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED

    def test_relaxed_line_is_not_matched_by_strict_pattern(self, tmp_path):
        # regression guard: the strict regex must not greedily match a
        # relaxed line and report the wrong eps.
        outdir = _mkoutput(tmp_path)
        _write_textout(
            outdir,
            'Model CONVERGED in 5 iterations (relaxed eps=1.0e-01, strict eps=2.0e-01)\n')
        status, fields = determine_job_status(0, False, str(tmp_path))
        assert status == STATUS_FINISHED_RELAXED
        assert fields['converged'] == 'relaxed'
