"""Native routing selectivity and revisit parity; no host decision mirror."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BINARY = os.environ.get('MNCS_BIN') or os.environ.get('MNCS_BINARY')
pytestmark = pytest.mark.skipif(not BINARY, reason='explicit MNCS compiler required')


def call(function, rows):
    arguments = [{'sequence': {'values': [{'sequence': {'values': [
        {'integer': {'value': value}} for value in row]}} for row in rows]}}]
    result = subprocess.run([BINARY, 'call', str(ROOT / 'native/mncs/automation/coherence.mncs'),
        '--module', 'mncs.automation.coherence.v1', '--function', function,
        '--args-json', json.dumps(arguments), '--library', str(ROOT / 'native')],
        capture_output=True, text=True, timeout=30, check=True)
    document = json.loads(result.stdout)
    assert document['status'] == 'returned', document
    assert len(document['call']['artifact_sha256']) == 64
    return [item['integer']['value'] for item in document['call']['returned'][0]['sequence']['values']]


def test_changed_claim_wakes_family_but_not_verification():
    assert call('route_batch', [[1, 1, 32, 32, 1, 0, 0, 0],
                                [1, 1, 4, 32, 1, 0, 0, 0]]) == [1, 0]


def test_unknown_chain_and_cold_state_cannot_authorize_reuse():
    assert call('route_batch', [[1, 0, 2, 0, 1, 0, 0, 0],
                                [0, 1, 2, 0, 1, 0, 0, 0]]) == [2, 3]


def test_scope_and_current_receipt_preserve_quiet():
    assert call('route_batch', [[1, 1, 2, 2, 0, 0, 0, 0],
                                [1, 1, 2, 0, 1, 0, 0, 0]]) == [0, 0]


def test_revisit_and_boundary_deadline_use_existing_authority():
    assert call('route_batch', [[1, 1, 0, 0, 1, 1, 1, 0],
                                [1, 1, 0, 0, 1, 1, 3, 0],
                                [1, 1, 0, 0, 1, 0, 0, 1]]) == [1, 0, 1]


def test_file_classification_is_conservative_and_declaration_first():
    assert call('classify_files', [[1, 1], [0, 1], [0, 0]]) == [1, 13, 2]


def test_full_bounded_batch_returns_every_decision():
    assert call('route_batch', [[1, 1, 2, 0, 1, 0, 0, 0]] * 16) == [0] * 16
