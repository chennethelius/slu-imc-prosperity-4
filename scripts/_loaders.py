"""Shared loaders and field accessors for backtest result scripts."""
import csv
import io
import json
from pathlib import Path


def load_json(path: Path) -> dict | None:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def _read_activity_text(csv_text: str) -> list[dict]:
    """Parse activities CSV text, autodetecting delimiter (; or ,)."""
    if not csv_text:
        return []
    first_line = csv_text.split("\n", 1)[0]
    delim = ";" if ";" in first_line else ","
    return list(csv.DictReader(io.StringIO(csv_text), delimiter=delim))


def parse_activity_from_submission_log(path: Path) -> list[dict]:
    """Extract activitiesLog CSV embedded in a submission.log JSON."""
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    return _read_activity_text(data.get("activitiesLog", ""))


def load_activity_csv(run_dir: Path) -> list[dict]:
    """Load activity.csv from a run directory (snake_case delimited CSV)."""
    path = run_dir / "activity.csv"
    if not path.exists():
        return []
    with open(path) as f:
        return _read_activity_text(f.read())


def load_trades_csv(run_dir: Path) -> list[dict]:
    """Load trades.csv (comma-delimited)."""
    path = run_dir / "trades.csv"
    if not path.exists():
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def load_run(run_dir: Path) -> tuple[dict | None, list[dict]]:
    """Return (metrics.json dict, activity rows) — parses activity from submission.log."""
    metrics = load_json(run_dir / "metrics.json")
    activity = parse_activity_from_submission_log(run_dir / "submission.log")
    return metrics, activity


# Field accessors — handle both backtester CSV (snake_case) and Prosperity log (camelCase).
def _num(row: dict, *keys) -> float:
    for k in keys:
        v = row.get(k)
        if v not in (None, "", "0"):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    # Fall back to zero-returning parse of first key
    for k in keys:
        try:
            return float(row.get(k, 0) or 0)
        except (TypeError, ValueError):
            continue
    return 0.0


def get_pnl(row: dict) -> float:
    return _num(row, "profit_and_loss", "profitLoss")


def get_mid(row: dict) -> float:
    return _num(row, "mid_price", "midPrice")


def get_bid1(row: dict) -> float:
    return _num(row, "bid_price_1", "bidPrices[0]")


def get_ask1(row: dict) -> float:
    return _num(row, "ask_price_1", "askPrices[0]")
