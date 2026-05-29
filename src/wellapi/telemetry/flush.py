import time
import typing

if typing.TYPE_CHECKING:
    from wellapi.telemetry.config import TelemetryHandle


def force_flush(handle: "TelemetryHandle", timeout_millis: int = 3000) -> None:
    """Flush tracer, meter and logger providers before the Lambda freezes.

    The timeout is a budget shared across all three signals, so flushing cannot
    add up to 3x the per-provider timeout to the response. Flushing the handle's
    providers also drains spans from project instrumentors, because those share
    the same (global) providers. Never raises — a failed export must not fail the
    invocation.
    """
    deadline = time.perf_counter() + timeout_millis / 1000
    for provider in (
        handle.tracer_provider,
        handle.meter_provider,
        handle.logger_provider,
    ):
        if not hasattr(provider, "force_flush"):
            continue
        remaining_ms = max(int((deadline - time.perf_counter()) * 1000), 0)
        if remaining_ms <= 0:
            break
        try:
            provider.force_flush(remaining_ms)
        except Exception:
            pass
