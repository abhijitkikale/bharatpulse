"""BharatPulse FYERS execution engine: a sub-project under the dashboard.

The dashboard is the brain (what, when, how much, where the stop and target are); this package only carries those instructions out at FYERS and reports what the
broker actually did. It reads the dashboard's trade objects (data/stream_state.json) and never changes a dashboard file. PAPER is the default mode; LIVE needs an explicit,
typed activation in the running process and is never restored after a restart. See execution/README.md."""
__version__ = "0.1.0"
