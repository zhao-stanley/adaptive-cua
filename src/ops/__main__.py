"""Inspect local logs: python -m src.ops [--db runs/runs.sqlite3] [--run ID]."""

import argparse
import json
from pathlib import Path

from src.ops.database import RunDatabase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("runs/runs.sqlite3"))
    parser.add_argument("--run", help="Show events for this run instead of listing runs.")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error(f"No log database at {args.db}; run the agent first.")
    database = RunDatabase(args.db)
    print(json.dumps(database.events(args.run) if args.run else database.runs(), indent=2))


if __name__ == "__main__":
    main()
