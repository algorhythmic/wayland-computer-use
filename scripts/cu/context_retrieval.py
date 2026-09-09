"""WCU context contract: eligibility, exact lookup, complete bundles, byte budgets."""
from dataclasses import dataclass, field
import datetime as dt
import json
import re
import time

from .context_records import SCHEMA_VERSION, encode, validate_snapshot
from .recipes import batch_policy

CONTRACT_VERSION = 'wcu-context-1'
RENDERER = 'canonical-json-utf8-v1'
MAX_BYTES = 65536


@dataclass(frozen=True)
class SessionEvidence:
    """Trusted integration input, never constructed from caller-supplied fact claims."""
    revision: str
    facts: dict = field(default_factory=dict)
    collected_ns: int = field(default_factory=time.monotonic_ns)


def validate_request(request):
    allowed = {'intent', 'observation_ref', 'facts', 'exact', 'max_bytes', 'timeout_ms', 'backend'}
    if not isinstance(request, dict) or set(request)-allowed:
        raise ValueError('invalid_context_request')
    if not isinstance(request.get('intent'), str) or not 1 <= len(request['intent']) <= 1000:
        raise ValueError('intent must be 1 to 1000 characters')
    for key, default, maximum in (('max_bytes', 8192, MAX_BYTES), ('timeout_ms', 1000, 10000)):
        value = request.get(key, default)
        minimum = 256 if key == 'max_bytes' else 1
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError('invalid_'+key)
    if 'facts' in request and (not isinstance(request['facts'], dict) or len(encode(request['facts'])) > 8192):
        raise ValueError('invalid_fact_claims')
    exact = request.get('exact')
    if exact is not None and (not isinstance(exact, dict) or not exact or
            set(exact)-{'id', 'app', 'scope', 'command_id', 'shortcut', 'mode'} or
            any(not isinstance(v, str) for v in exact.values())):
        raise ValueError('invalid_exact_lookup')
    if request.get('backend', 'local') not in ('local', 'substring', 'braid'):
        raise ValueError('invalid_context_backend')
    if 'observation_ref' in request and not isinstance(request['observation_ref'], str):
        raise ValueError('invalid_observation_ref')


def tokens(text):
    return set(re.findall(r'[^\W_]+', text.casefold()))


def eligibility(action, facts, now=None):
    a = action['applicability']
    reasons = []
    if a['assignment'] != 'assigned':
        return 'excluded', [a['assignment']]
    if a.get('version_matches') is False or a.get('extra_installed') is False:
        return 'excluded', ['version_mismatch' if a.get('version_matches') is False else 'missing_optional_plugin']
    if a.get('configuration_error'):
        return 'exploration', ['configuration_unavailable']
    for requirement in a['requirements']:
        key = requirement['fact']
        if key == 'input_path':
            path = facts.get('input_path') or (['omarchy', facts['focused_app']] if facts.get('focused_app') else None)
            if path is None:
                reasons.append('observe:input_path')
            elif requirement['contains'] not in path:
                if path[-1] in ('ghostty', 'herdr') and facts.get('input_path_complete') is not True and requirement['contains'] in ('herdr','neovim'):
                    reasons.append('observe:terminal_input_path')
                else:
                    return 'excluded', ['input_path_mismatch']
            continue
        if key not in facts:
            reasons.append('observe:'+key)
        elif ('equals' in requirement and facts[key] != requirement['equals']) or ('one_of' in requirement and facts[key] not in requirement['one_of']):
            return 'excluded', ['mismatch:'+key]
    if a.get('version_matches') is None and facts.get('version_compatible:'+action['id']) is not True:
        reasons.append('verify_source_version')
    if a.get('collisions') or a.get('interception'):
        if facts.get('delivery_verified:'+action['id']) is not True:
            reasons.append('resolve_collision_or_interception')
    collected = action['evidence'].get('collected_at')
    try:
        age = ((now or dt.datetime.now(dt.timezone.utc))-dt.datetime.fromisoformat(collected)).total_seconds()
        if age < 0 or age > 86400:
            reasons.append('refresh_stale_catalog')
    except (TypeError, ValueError):
        reasons.append('collection_time_unknown')
    reasons += a.get('unresolved', [])
    if action['input'].get('placeholders') and facts.get('placeholders_resolved:'+action['id']) is not True:
        reasons.append('resolve_input_placeholders')
    return ('exploration' if reasons else 'eligible'), reasons


def exact_match(record, exact):
    for key, value in (exact or {}).items():
        actual = record['input']['shortcut'] if key == 'shortcut' else record.get(key)
        if key == 'mode':
            if value not in (actual or []):
                return False
        elif value != actual:
            return False
    return True


def closure(root, records):
    result, pending = {}, [root]
    while pending:
        identifier = pending.pop()
        if identifier in result:
            continue
        record = records.get(identifier)
        if record is None or (identifier != root and record['record_type'] != 'context'):
            raise ValueError('required_context_unavailable')
        result[identifier] = record
        pending += record.get('requires', [])
    return result


def action_view(record):
    return {key: record[key] for key in ('id', 'app', 'scope', 'mode', 'command_id', 'intent', 'input',
        'requires', 'outcome_check', 'requires_inspection', 'applicability', 'evidence')}


def render(payload):
    # Accounting includes itself; converge at a decimal-width boundary.
    for _ in range(8):
        size = len(encode(payload))
        if payload['output']['bytes'] == size:
            return encode(payload)
        payload['output']['bytes'] = size
    raise ValueError('accounting_did_not_converge')


def context_for_task(snapshot, request, evidence=None, backend=None):
    validate_request(request)
    end = time.monotonic()+request.get('timeout_ms', 1000)/1000
    validate_snapshot(snapshot)
    facts = {}
    if evidence and request.get('observation_ref') == evidence.revision and 0 <= time.monotonic_ns()-evidence.collected_ns <= 120_000_000_000:
        facts = evidence.facts
    limit = request.get('max_bytes', 8192)
    payload = {'contract_version': CONTRACT_VERSION, 'catalog': {'revision': snapshot['revision'], 'schema_version': SCHEMA_VERSION},
        'environment': {'observation_ref': evidence.revision if facts else None, 'facts': {k: v for k, v in facts.items() if ':' not in k}},
        'actions': [], 'exploration': [], 'context': {}, 'required_checks': [],
        'unresolved': sorted(set(request.get('facts', {}))-set(facts)), 'status': 'no_result',
        'output': {'unit': 'utf8_bytes', 'renderer': RENDERER, 'limit': limit, 'bytes': 0}}
    diagnostics = {'backend': 'local', 'excluded': {}, 'failures': [], 'coverage_errors': snapshot['coverage_errors']}
    records, candidates = snapshot['records'], []
    wanted = tokens(request['intent'])
    deadline_expired = False
    for r in records.values():
        if time.monotonic() >= end:
            deadline_expired = True
            break
        if r['record_type'] != 'action' or not exact_match(r, request.get('exact')):
            continue
        state, reasons = eligibility(r, facts)
        if state == 'excluded':
            diagnostics['excluded'][r['id']] = reasons
            continue
        text = ' '.join([r['intent'], r['source_description'], *r['aliases']])
        score = len(wanted & tokens(text))
        if request.get('backend') == 'substring':
            # Preserve keyboard_context.py's original full-binding substring
            # search as the comparison baseline, without curated aliases.
            source_text = json.dumps(r.get('source_record', {})).lower()
            score = int(all(t in source_text for t in request['intent'].lower().split()))
        if request.get('exact'):
            score = 1
        if score:
            candidates.append((r, state, reasons, score))
    if not deadline_expired and backend and request.get('backend') == 'braid' and not request.get('exact'):
        try:
            # Both classes are ranked independently, so unknown prerequisites cannot
            # crowd executable actions out of a backend candidate limit.
            ranked = []
            for state in ('eligible', 'exploration'):
                allowed = [r['id'] for r in records.values() if r['record_type'] == 'action' and eligibility(r, facts)[0] == state]
                order, info = backend.rank(snapshot, request['intent'], allowed, end)
                diagnostics.update(info)
                for rank, identifier in enumerate(order):
                    r = records[identifier]
                    st, reasons = eligibility(r, facts)
                    if identifier not in allowed or st != state:
                        raise ValueError('backend_eligibility_violation')
                    ranked.append((r, st, reasons, len(order)-rank))
            candidates = ranked
        except Exception as exc:
            diagnostics.update(backend='local', failures=[getattr(exc, 'code', type(exc).__name__)])
    elif request.get('backend') == 'braid' and backend is None:
        diagnostics['failures'].append('braid_unavailable')
    if len(render(payload)) > limit:
        payload = {'contract_version': CONTRACT_VERSION, 'status': 'infeasible_budget',
                   'output': {'unit': 'utf8_bytes', 'renderer': RENDERER, 'limit': limit, 'bytes': 0}}
        render(payload)
        return payload, diagnostics
    admitted, rejected_budget = set(), False
    for record, state, reasons, score in sorted(candidates, key=lambda x: (x[1] != 'eligible', -x[3], x[0]['id'])):
        if time.monotonic() >= end:
            deadline_expired = True
            break
        if record['alternative_group'] in admitted:
            continue
        try:
            bundle = closure(record['id'], records)
        except ValueError:
            diagnostics['failures'].append('required_context_unavailable')
            continue
        old_context = dict(payload['context'])
        key = 'actions' if state == 'eligible' else 'exploration'
        view = action_view(record)
        view['planning'] = batch_policy(record, state, reasons)
        if state != 'eligible':
            view.update(executable=False, missing_evidence=reasons)
        payload[key].append(view)
        payload['context'].update({i: r for i, r in bundle.items() if i != record['id']})
        payload['status'] = 'ok'
        if len(render(payload)) > limit:
            payload[key].pop()
            payload['context'] = old_context
            rejected_budget = True
        else:
            admitted.add(record['alternative_group'])
    if not payload['actions'] and not payload['exploration']:
        payload['status'] = 'budget_exhausted' if rejected_budget else 'no_result'
        payload['required_checks'] = ['No eligible assigned shortcut; inspect applicability or configure a binding.']
        if len(render(payload)) > limit:
            payload['required_checks'] = []
    elif rejected_budget:
        payload['status'] = 'budget_limited'
    if deadline_expired:
        payload['status'] = 'deadline'
    # Status can change length after packing: drop complete optional bundles only.
    while len(render(payload)) > limit and (payload['actions'] or payload['exploration']):
        target = payload['exploration'] or payload['actions']
        target.pop()
        needed = {i for r in payload['actions']+payload['exploration'] for i in closure(r['id'], records) if i != r['id']}
        payload['context'] = {i: r for i, r in payload['context'].items() if i in needed}
    render(payload)
    diagnostics['elapsed_ms'] = (time.monotonic()-(end-request.get('timeout_ms', 1000)/1000))*1000
    return payload, diagnostics
