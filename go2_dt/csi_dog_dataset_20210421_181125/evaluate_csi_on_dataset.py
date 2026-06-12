#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from csi_binary_detector import (
    DEFAULT_THRESHOLD,
    classify_file,
    load_saved_model,
    load_single_sample,
    model_class_names,
    predict_with_saved_model,
)


def discover_labels(dataset_root: Path) -> list[str]:
    labels = []
    for child in sorted(dataset_root.iterdir()):
        if child.is_dir() and (child / "txt").exists():
            labels.append(child.name)
    if not labels:
        raise ValueError(f"No class folders with txt/ found under {dataset_root}")
    return labels


def confusion_matrix(rows: list[dict], prediction_key: str, class_names: list[str]) -> list[list[int]]:
    index_of = {name: idx for idx, name in enumerate(class_names)}
    matrix = [[0 for _ in class_names] for _ in class_names]
    for row in rows:
        true_label = row["true_label"]
        pred_label = row[prediction_key]
        if true_label in index_of and pred_label in index_of:
            matrix[index_of[true_label]][index_of[pred_label]] += 1
    return matrix


def accuracy_metrics(rows: list[dict], prediction_key: str, class_names: list[str]) -> dict[str, object]:
    matrix = confusion_matrix(rows, prediction_key, class_names)
    correct = sum(matrix[i][i] for i in range(len(class_names)))
    total = sum(sum(row) for row in matrix)
    per_class_recall = {}
    for index, class_name in enumerate(class_names):
        row_total = sum(matrix[index])
        per_class_recall[class_name] = (matrix[index][index] / row_total) if row_total else 0.0

    return {
        "accuracy": (correct / total) if total else 0.0,
        "balanced_accuracy": sum(per_class_recall.values()) / len(class_names),
        "confusion_matrix": matrix,
        "per_class_recall": per_class_recall,
    }


def format_confusion_matrix(matrix: list[list[int]], class_names: list[str]) -> list[str]:
    headers = ["true\\pred"] + class_names
    rows = [headers]
    for class_name, row in zip(class_names, matrix, strict=True):
        rows.append([class_name] + [str(value) for value in row])
    widths = [max(len(row[col]) for row in rows) for col in range(len(headers))]
    return ["  ".join(value.ljust(widths[col]) for col, value in enumerate(row)) for row in rows]


def dataset_files(dataset_root: Path) -> list[tuple[str, Path]]:
    items = []
    for label in discover_labels(dataset_root):
        for path in sorted((dataset_root / label / "txt").glob("*.txt")):
            items.append((label, path))
    return items


def evaluate(dataset_root: Path, model_path: Path, threshold: float) -> tuple[list[dict], list[str]]:
    model = load_saved_model(model_path)
    class_names = sorted(set(discover_labels(dataset_root)) | set(model_class_names(model)))
    threshold_supported = set(class_names) == {"nothing", "turtlebot"}
    rows = []

    for true_label, path in dataset_files(dataset_root):
        try:
            sample = load_single_sample(path)
            model_prediction, model_score = predict_with_saved_model(model, sample)
            if threshold_supported:
                threshold_prediction, threshold_score = classify_file(path, threshold)
                threshold_ok = threshold_prediction == true_label
            else:
                threshold_prediction = "unsupported"
                threshold_score = ""
                threshold_ok = ""

            row = {
                "file": str(path),
                "true_label": true_label,
                "threshold_prediction": threshold_prediction,
                "threshold_score": threshold_score,
                "threshold_ok": threshold_ok,
                "model_prediction": model_prediction,
                "model_score": model_score,
                "model_ok": model_prediction == true_label,
                "status": "ok",
                "error": "",
            }
        except Exception as exc:
            row = {
                "file": str(path),
                "true_label": true_label,
                "threshold_prediction": "skipped",
                "threshold_score": "",
                "threshold_ok": "",
                "model_prediction": "skipped",
                "model_score": "",
                "model_ok": "",
                "status": "skipped",
                "error": str(exc),
            }
        rows.append(row)

    return rows, class_names


def write_csv(rows: list[dict], csv_path: Path) -> None:
    fieldnames = [
        "file",
        "true_label",
        "status",
        "error",
        "threshold_prediction",
        "threshold_score",
        "threshold_ok",
        "model_prediction",
        "model_score",
        "model_ok",
    ]
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    rows: list[dict],
    class_names: list[str],
    report_path: Path,
    dataset_root: Path,
    model_path: Path,
    threshold: float,
) -> None:
    valid_rows = [row for row in rows if row["status"] == "ok"]
    skipped_rows = [row for row in rows if row["status"] != "ok"]
    threshold_rows = [row for row in valid_rows if row["threshold_prediction"] not in ("unsupported", "skipped")]
    model_metrics = accuracy_metrics(valid_rows, "model_prediction", class_names)
    threshold_metrics = accuracy_metrics(threshold_rows, "threshold_prediction", ["nothing", "turtlebot"]) if threshold_rows else None

    model_errors = [row for row in valid_rows if not row["model_ok"]]
    threshold_errors = [row for row in threshold_rows if not row["threshold_ok"]]

    lines = [
        f"Dataset evaluated: {dataset_root}",
        f"Model used: {model_path}",
        f"Threshold used: {threshold:.6f}",
        f"Classes: {', '.join(class_names)}",
        f"Total samples: {len(rows)}",
        f"Valid samples: {len(valid_rows)}",
        f"Skipped samples: {len(skipped_rows)}",
        "",
        "Model detector",
        f"accuracy: {model_metrics['accuracy']:.6f}",
        f"balanced_accuracy: {model_metrics['balanced_accuracy']:.6f}",
        "confusion_matrix:",
        *format_confusion_matrix(model_metrics["confusion_matrix"], class_names),
        "per_class_recall:",
    ]

    for class_name in class_names:
        lines.append(f"- {class_name}: {model_metrics['per_class_recall'][class_name]:.6f}")

    lines.append("")
    lines.append("Threshold detector")
    if threshold_metrics is None:
        lines.append("- unsupported for multiclass datasets")
    else:
        lines.extend(
            [
                f"accuracy: {threshold_metrics['accuracy']:.6f}",
                f"balanced_accuracy: {threshold_metrics['balanced_accuracy']:.6f}",
                "confusion_matrix:",
                *format_confusion_matrix(threshold_metrics["confusion_matrix"], ["nothing", "turtlebot"]),
            ]
        )

    lines.extend(["", f"Skipped files ({len(skipped_rows)}):"])
    if skipped_rows:
        lines.extend(f"- {row['file']} | {row['error']}" for row in skipped_rows)
    else:
        lines.append("- none")

    lines.extend(["", f"Model errors ({len(model_errors)}):"])
    if model_errors:
        lines.extend(
            f"- {row['file']} | true={row['true_label']} | pred={row['model_prediction']} | score={row['model_score']:.6f}"
            for row in model_errors
        )
    else:
        lines.append("- none")

    lines.extend(["", f"Threshold errors ({len(threshold_errors)}):"])
    if threshold_errors:
        lines.extend(
            f"- {row['file']} | true={row['true_label']} | pred={row['threshold_prediction']} | score={row['threshold_score']:.6f}"
            for row in threshold_errors
        )
    else:
        lines.append("- none")

    report_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a trained CSI model on a labeled dataset.")
    parser.add_argument("--dataset-root", type=Path, default=Path("Pruebas/csi_dog_dataset"))
    parser.add_argument("--model", type=Path, default=Path("analysis_outputs/csi_trained_model.json"))
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--csv-out", type=Path, default=Path("analysis_outputs/pruebas_predictions.csv"))
    parser.add_argument("--report-out", type=Path, default=Path("analysis_outputs/pruebas_evaluation_report.txt"))
    args = parser.parse_args()

    args.csv_out.parent.mkdir(parents=True, exist_ok=True)
    rows, class_names = evaluate(args.dataset_root, args.model, args.threshold)
    write_csv(rows, args.csv_out)
    write_report(rows, class_names, args.report_out, args.dataset_root, args.model, args.threshold)
    print(f"Saved predictions to {args.csv_out}")
    print(f"Saved report to {args.report_out}")


if __name__ == "__main__":
    main()
