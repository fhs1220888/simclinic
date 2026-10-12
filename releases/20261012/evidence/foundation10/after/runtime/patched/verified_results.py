"""Candidate postprocessing interface; install simclinic>=0.3 before use.

Callers must pass current context from the orchestrator, not from the result's
own manifest. This module is opt-in and does not wire existing tools by itself.
"""
from simclinic.core.record import load_record
from simclinic.result_guard import read_verified_json


def read_result_json(record_path, case_dir, artifact_id, *, expected_run_id, expected_input_version):
    record = load_record(record_path, case_dir)
    return read_verified_json(record, artifact_id, expected_run_id, expected_input_version)
