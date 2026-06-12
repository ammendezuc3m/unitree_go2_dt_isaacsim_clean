#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path


def iter_ground_truth(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="") as fh:
        return list(csv.DictReader(fh))


def write_stream(input_path: Path, rows: list[dict[str, str]], interval: float, truncate: bool) -> None:
    input_path.parent.mkdir(parents=True, exist_ok=True)
    if truncate:
        input_path.write_text("")
    else:
        input_path.touch(exist_ok=True)

    total = len(rows)
    for index, row in enumerate(rows, start=1):
        with input_path.open("a") as fh:
            fh.write(row["measurement_line"] + "\n")
            fh.flush()
        print(
            f"Wrote sample {index:04d}/{total:04d} expected={row['expected_label']} source={row['source_dataset']}",
            flush=True,
        )
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a live CSI writer by appending prepared measurements to a stream file.")
    parser.add_argument("--ground-truth", type=Path, default=Path("realtime_inputs/ground_truth_stream.csv"))
    parser.add_argument("--input", type=Path, default=Path("realtime_inputs/live_csi_stream.txt"))
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--no-truncate", action="store_true")
    args = parser.parse_args()

    rows = iter_ground_truth(args.ground_truth)
    if args.max_samples is not None:
        rows = rows[: args.max_samples]

    write_stream(args.input, rows, args.interval, truncate=not args.no_truncate)


if __name__ == "__main__":
    main()
