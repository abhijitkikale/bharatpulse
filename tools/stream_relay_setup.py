"""One-time setup for the TradingView -> stream relay (opening candles).

  python tools/stream_relay_setup.py

Makes a long random topic name for the free ntfy.sh relay, saves it in data/stream_relay.json (this PC only, git-ignored) and prints the webhook
URL to paste into the TradingView alert. Anyone who knows the topic can read or post messages on it, so keep the URL private. The messages are
only the 1-minute opening candles of Nifty (open, high, low, close, volume), nothing else."""
import json
import pathlib
import secrets

FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "stream_relay.json"


def main():
    if FILE.exists():
        topic = json.loads(FILE.read_text(encoding="utf-8"))["topic"]
        print("A topic already exists (kept).")
    else:
        topic = "bp-" + secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps({"topic": topic}), encoding="utf-8")
        print("New topic saved.")
    print()
    print("Webhook URL for the TradingView alert (keep it private):")
    print("  https://ntfy.sh/" + topic)
    print()
    print("See tradingview/LIVE-SETUP.md, section 'Opening candles from TradingView', for the alert steps.")


if __name__ == "__main__":
    main()
