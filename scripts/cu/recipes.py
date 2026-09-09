"""Validated recipe records and evidence-based batch boundaries; never an executor."""
from .context_records import SCHEMA_VERSION, atomic_json, stable_id
from .observation import validate_condition


def batch_policy(action, state, missing):
    if state != 'eligible':
        return {'decision': 'inspect', 'required_evidence': missing}
    if not action.get('outcome_check'):
        return {'decision': 'inspect_outcome', 'batch_boundary': 'after_this_action'}
    return {'decision': 'guarded_sequence', 'batch_boundary': 'next_unresolved_decision'}


def recipe(intent, action_ids, entry, exit_check, fingerprints, steps):
    validate_condition(entry)
    validate_condition(exit_check)
    if not action_ids or len(action_ids) != len(steps) or len(steps) > 12:
        raise ValueError('Recipe must identify each action in a bounded sequence')
    return {'id': stable_id('recipe', intent, action_ids), 'record_type': 'recipe', 'schema_version': SCHEMA_VERSION,
        'intent': intent, 'action_ids': list(action_ids), 'entry': entry, 'exit_check': exit_check,
        'fingerprints': dict(fingerprints), 'steps': steps, 'validation_history': [], 'failure_cases': []}


def applicability(record, snapshot, facts):
    from .context_retrieval import eligibility
    reasons = []
    for app, fingerprint in record['fingerprints'].items():
        if snapshot['source_manifest'].get(app, {}).get('fingerprint') != fingerprint:
            reasons.append('configuration_changed:'+app)
    for identifier in record['action_ids']:
        action = snapshot['records'].get(identifier)
        if action is None or action.get('record_type') != 'action':
            reasons.append('action_removed:'+identifier)
        else:
            state, missing = eligibility(action, facts)
            if state != 'eligible':
                reasons += missing
    history = record['validation_history']
    if not history or history[-1]['outcome'] != 'verified' or history[-1]['fingerprints'] != record['fingerprints']:
        reasons.append('recipe_requires_revalidation')
    return {'eligible': not reasons, 'reasons': sorted(set(reasons))}


def record_validation(record, snapshot, observation_ref, outcome, final_artifact_verified, failure_code=None):
    if outcome not in ('verified', 'failed') or not observation_ref:
        raise ValueError('Validation requires observation identity and an explicit outcome')
    if outcome == 'verified' and final_artifact_verified is not True:
        raise ValueError('Successful input submission alone does not validate a recipe')
    entry = {'catalog_revision': snapshot['revision'], 'observation_ref': observation_ref,
             'outcome': outcome, 'fingerprints': dict(record['fingerprints']), 'artifact_verified': final_artifact_verified is True}
    record['validation_history'] = (record['validation_history']+[entry])[-32:]
    if failure_code:
        record['failure_cases'] = (record['failure_cases']+[{'code': failure_code, 'catalog_revision': snapshot['revision']}])[-32:]
    return record


def save(path, record):
    atomic_json(path, record)
