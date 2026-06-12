#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare realtime predictions against prepared ground truth.")
    parser.add_argument("--ground-truth", type=Path, default=Path("realtime_inputs/ground_truth_stream.csv"))
    parser.add_argument("--predictions", type=Path, default=Path("analysis_outputs/live_predictions.csv"))
    parser.add_argument("--report", type=Path, default=Path("analysis_outputs/live_predictions_report.txt"))
    args = parser.parse_args()

    truth_rows = load_csv(args.ground_truth)
    pred_rows = load_csv(args.predictions)
    pred_by_index = {int(row["sample_index"]): row for row in pred_rows}

    compared = []
    missing = []
    for truth in truth_rows:
        sample_index = int(truth["sample_index"])
        pred = pred_by_index.get(sample_index)
        if pred is None:
            missing.append(truth)
            continue
        compared.append((truth, pred))

    correct = sum(1 for truth, pred in compared if truth["expected_label"] == pred["prediction"])
    errors = [(truth, pred) for truth, pred in compared if truth["expected_label"] != pred["prediction"]]
    accuracy = correct / len(compared) if compared else 0.0

    lines = [
        f"Ground truth samples: {len(truth_rows)}",
        f"Predictions received: {len(pred_rows)}",
        f"Compared samples: {len(compared)}",
        f"Missing predictions: {len(missing)}",
        f"Accuracy: {accuracy:.6f}",
        "",
        f"Mispredictions ({len(errors)}):",
    ]
    if errors:
        for truth, pred in errors:
            lines.append(
                f"- sample={truth['sample_index']} expected={truth['expected_label']} "
                f"predicted={pred['prediction']} source={truth['source_file']}"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append(f"Missing predictions detail ({len(missing)}):")
    if missing:
        for truth in missing:
            lines.append(f"- sample={truth['sample_index']} expected={truth['expected_label']} source={truth['source_file']}")
    else:
        lines.append("- none")

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n")
    print(f"Saved realtime evaluation report to {args.report}")
    print(f"Compared samples: {len(compared)}")
    print(f"Accuracy: {accuracy:.6f}")


if __name__ == "__main__":
    main()
