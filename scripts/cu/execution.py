"""Request-local recovery ledger and shared cooperative execution deadline."""
import time

DEFAULT_BUDGET_MS = 60000
MAX_BUDGET_MS = 120000
RECOVERY_BUDGET_MS = 5000


class Deadline:
    def __init__(self, milliseconds=DEFAULT_BUDGET_MS):
        if type(milliseconds) is not int or not 1 <= milliseconds <= MAX_BUDGET_MS:
            raise ValueError('duration_ms must be an integer from 1 to 120000')
        self.budget_ms = milliseconds
        self.end = time.monotonic() + milliseconds / 1000

    def remaining(self, maximum=None):
        value = self.end - time.monotonic()
        if value <= 0:
            raise TimeoutError('execution_deadline')
        return min(value, maximum) if maximum is not None else value

    def milliseconds(self, maximum=30000):
        return max(1, int(self.remaining(maximum / 1000) * 1000))


class Ledger:
    def __init__(self, steps):
        self.steps = [dict(index=i, action=s['action'], status='unattempted',
                           injection='not_started', verification='not_requested',
                           submitted_segments=0, in_flight_unknown=False,
                           application_accepted='unverified') for i, s in enumerate(steps)]
        self.current = None
        self.stop_reason = None

    def begin(self, index):
        self.current = self.steps[index]
        self.current['status'] = 'pending'
        return self.current

    def injecting(self):
        self.current.update(injection='in_flight', in_flight_unknown=True)

    def submitted(self):
        self.current['submitted_segments'] += 1
        self.current.update(injection='partial', in_flight_unknown=False)

    def completed(self, watermark):
        self.current.update(injection='submitted', in_flight_unknown=False,
                            action_completed_ns=watermark)

    def summary(self):
        completed = [s['index'] for s in self.steps if s['injection'] == 'submitted']
        return {'steps_total': len(self.steps),
                'steps_completed': sum(s['status'] == 'done' for s in self.steps),
                'stopped': self.stop_reason is not None, 'stop_reason': self.stop_reason,
                'last_completed_action': completed[-1] if completed else None,
                'interrupted_step': next((s['index'] for s in self.steps
                    if s['status'] not in ('done', 'unattempted')), None),
                'unattempted_steps': [s['index'] for s in self.steps if s['status'] == 'unattempted'],
                'steps': self.steps}

    def performed(self):
        if any(s['in_flight_unknown'] or s['injection'] == 'partial' for s in self.steps):
            return 'unknown'
        return any(s['injection'] == 'submitted' for s in self.steps)
