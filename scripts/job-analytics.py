#!/usr/bin/env python3
import argparse
from pathlib import Path
from history_store import HistoryStore

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Show job market analytics from SQLite history.")
parser.add_argument("--db", type=Path, default=ROOT / "data/job-search.db")
parser.add_argument("--days", type=int, default=30)
args = parser.parse_args()
if args.days < 1:
    parser.error("--days must be positive")
with HistoryStore(args.db) as store:
    print(store.analytics_text(args.days), end="")
