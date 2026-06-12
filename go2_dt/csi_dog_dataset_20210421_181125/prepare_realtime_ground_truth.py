#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from analyze_csi_dataset import parse_measurement_line


def measurement_line(path: Path) -> str:
    for raw_line in path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if "Measurement:" in line:
            return line
    raise ValueError(f"{path} does not contain a Measurement line")


def collect_valid_files(dataset_root: Path) -> dict[str, list[Path]]:
    items: dict[str, list[Path]] = {}
    for class_dir in sorted(dataset_root.iterdir()):
        if not class_dir.is_dir() or not (class_dir / "txt").exists():
            continue
        label = class_dir.name
        items[label] = []
        for path in sorted((dataset_root / label / "txt").glob("*.txt")):
            try:
                line = measurement_line(path)
                parse_measurement_line(line, path, label)
                items[label].append(path)
            except Exception:
                continue
    return items


def build_sequence(
    primary_root: Path,
    secondary_root: Path,
    labels: list[str],
    block_size: int,
    total_samples: int,
) -> list[tuple[int, str, Path, str, str]]:
    primary = collect_valid_files(primary_root)
    secondary = collect_valid_files(secondary_root)
    merged = {
        label: primary.get(label, []) + [path for path in secondary.get(label, []) if path not in primary.get(label, [])]
        for label in labels
    }

    label_counts = {label: 0 for label in labels}
    for index in range(total_samples):
        label_counts[labels[(index // block_size) % len(labels)]] += 1

    for label in labels:
        needed_per_label = label_counts[label]
        if len(merged[label]) < needed_per_label:
            raise ValueError(f"Not enough samples for {label}: need {needed_per_label}, found {len(merged[label])}")

    label_offsets = {label: 0 for label in labels}
    sequence: list[tuple[int, str, Path, str, str]] = []

    for index in range(total_samples):
        current_label = labels[(index // block_size) % len(labels)]
        source_path = merged[current_label][label_offsets[current_label]]
        label_offsets[current_label] += 1
        source_name = "Pruebas" if str(primary_root) in str(source_path) else "dataset_original"
        sequence.append((index + 1, current_label, source_path, source_name, measurement_line(source_path)))

    return sequence


def write_ground_truth(sequence: list[tuple[int, str, Path, str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["sample_index", "expected_label", "source_dataset", "source_file", "measurement_line"],
        )
        writer.writeheader()
        for sample_index, expected_label, source_file, source_dataset, line in sequence:
            writer.writerow(
                {
                    "sample_index": sample_index,
                    "expected_label": expected_label,
                    "source_dataset": source_dataset,
                    "source_file": str(source_file),
                    "measurement_line": line,
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a deterministic ground-truth CSI stream for realtime tests.")
    parser.add_argument("--primary-root", type=Path, default=Path("Pruebas/csi_dog_dataset"))
    parser.add_argument("--secondary-root", type=Path, default=Path("csi_dog_dataset"))
    parser.add_argument("--output", type=Path, default=Path("realtime_inputs/ground_truth_stream.csv"))
    parser.add_argument("--labels", type=str, default="turtlebot,nothing")
    parser.add_argument("--block-size", type=int, default=40)
    parser.add_argument("--total-samples", type=int, default=400)
    args = parser.parse_args()

    if args.total_samples % args.block_size != 0:
        raise ValueError("total-samples must be a multiple of block-size")
    labels = [label.strip() for label in args.labels.split(",") if label.strip()]
    if not labels:
        raise ValueError("labels must contain at least one label")
    if (args.total_samples // args.block_size) % len(labels) != 0:
        raise ValueError("Use a number of blocks divisible by the number of labels")

    sequence = build_sequence(args.primary_root, args.secondary_root, labels, args.block_size, args.total_samples)
    write_ground_truth(sequence, args.output)
    print(f"Saved ground truth to {args.output}")
    print(f"Prepared {len(sequence)} samples in blocks of {args.block_size}")
    print(f"Labels used: {', '.join(labels)}")


if __name__ == "__main__":
    main()
