from dataclasses import dataclass

import wellapi.telemetry.flush as flush_mod
from wellapi.telemetry.flush import force_flush


class FlushProvider:
    def __init__(self):
        self.calls = []

    def force_flush(self, timeout_millis):
        self.calls.append(timeout_millis)
        return True


class NoFlushProvider:
    pass


@dataclass
class Handle:
    tracer_provider: object
    meter_provider: object
    logger_provider: object


def test_flushes_all_three_providers():
    tp, mp, lp = FlushProvider(), FlushProvider(), FlushProvider()
    force_flush(Handle(tp, mp, lp), timeout_millis=3000)
    assert len(tp.calls) == len(mp.calls) == len(lp.calls) == 1


def test_skips_providers_without_force_flush():
    tp = FlushProvider()
    force_flush(Handle(tp, NoFlushProvider(), NoFlushProvider()), timeout_millis=3000)
    assert len(tp.calls) == 1  # no AttributeError from the no-flush providers


def test_shared_budget_decreases(monkeypatch):
    # perf_counter() is called once for the deadline, then once per provider.
    ticks = iter([0.0, 0.0, 1.0, 2.0])
    monkeypatch.setattr(flush_mod.time, "perf_counter", lambda: next(ticks))
    tp, mp, lp = FlushProvider(), FlushProvider(), FlushProvider()
    force_flush(Handle(tp, mp, lp), timeout_millis=3000)
    assert tp.calls[0] == 3000
    assert mp.calls[0] == 2000
    assert lp.calls[0] == 1000


def test_flush_never_raises():
    class Boom:
        def force_flush(self, timeout_millis):
            raise RuntimeError("export failed")

    force_flush(Handle(Boom(), NoFlushProvider(), NoFlushProvider()))  # must not raise
