import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import stream_trades  # noqa: E402


@pytest.fixture(autouse=True)
def _old_entry_unless_a_test_asks(monkeypatch):
    """The older tests describe the trade logic with the entry at the setup candle's close; the entry trigger (a trade only when the option trades above the
    setup candle's high) is switched off for them and switched on explicitly in the tests that are about it."""
    monkeypatch.setattr(stream_trades, "ENTRY_TRIGGER", False)
