from opentelemetry.sdk.trace import TracerProvider

from wellapi.applications import WellApi
from wellapi.telemetry.config import TelemetryHandle


def test_use_telemetry_returns_handle_and_enables(monkeypatch):
    import wellapi.telemetry.config as config_mod

    # use_telemetry does `from wellapi.telemetry.config import configure_telemetry`
    # at call time, so patching it on the config module is what takes effect.
    fake = TelemetryHandle(TracerProvider(), object(), object())
    monkeypatch.setattr(config_mod, "configure_telemetry", lambda: fake)

    app = WellApi()
    handle = app.use_telemetry()
    assert handle is fake
    assert app.telemetry is fake


def test_init_exports_public_symbols():
    from wellapi.telemetry import TelemetryHandle as ExportedHandle
    from wellapi.telemetry import configure_telemetry

    assert ExportedHandle is TelemetryHandle
    assert callable(configure_telemetry)
