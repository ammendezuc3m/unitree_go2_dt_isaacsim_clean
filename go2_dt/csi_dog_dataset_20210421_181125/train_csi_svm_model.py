#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from analyze_csi_dataset import parse_measurement_line


CLASS_NAMES = ["nothing", "person_1", "turtlebot"]
LABEL_TO_INDEX = {label: idx for idx, label in enumerate(CLASS_NAMES)}


def sample_weight_for_path(
    path: Path,
    root: Path,
    class_name: str,
    recent_min_index: dict[str, int],
    recent_weight: float,
) -> float:
    weight = 1.0
    try:
        sample_index = int(path.stem.split("_")[-1])
    except Exception:
        return weight

    if path.is_relative_to(root) and sample_index >= recent_min_index[class_name]:
        weight *= recent_weight
    return weight


def load_dataset(
    roots: list[Path],
    feature_mode: str,
    recent_min_index: dict[str, int],
    recent_weight: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    features = []
    labels = []
    weights = []
    for root in roots:
        for class_name in CLASS_NAMES:
            for path in sorted((root / class_name / "txt").glob("*.txt")):
                lines = [line.strip() for line in path.read_text(errors="ignore").splitlines() if "Measurement:" in line]
                if len(lines) != 1:
                    continue
                try:
                    sample = parse_measurement_line(lines[0], path, class_name)
                except Exception:
                    continue
                if feature_mode == "magnitude":
                    feat = sample.magnitude.astype(float)
                elif feature_mode == "phase_magnitude":
                    feat = np.hstack([sample.phase, sample.magnitude]).astype(float)
                else:
                    raise ValueError(f"Unsupported feature mode: {feature_mode}")
                features.append(feat)
                labels.append(LABEL_TO_INDEX[class_name])
                weights.append(sample_weight_for_path(path, root, class_name, recent_min_index, recent_weight))
    return np.vstack(features), np.asarray(labels, dtype=int), np.asarray(weights, dtype=float)


def matrix_to_text(matrix: np.ndarray) -> str:
    headers = ["true\\pred"] + CLASS_NAMES
    rows = [headers]
    for class_name, row in zip(CLASS_NAMES, matrix.tolist(), strict=True):
        rows.append([class_name] + [str(value) for value in row])
    widths = [max(len(row[col]) for row in rows) for col in range(len(headers))]
    return "\n".join("  ".join(value.ljust(widths[col]) for col, value in enumerate(row)) for row in rows)


def plot_confusion(matrix: np.ndarray, output_path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    image = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(CLASS_NAMES)), CLASS_NAMES, rotation=20, ha="right")
    ax.set_yticks(range(len(CLASS_NAMES)), CLASS_NAMES)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title(title)
    for row in range(matrix.shape[0]):
        for col in range(matrix.shape[1]):
            ax.text(col, row, str(matrix[row, col]), ha="center", va="center", color="black")
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def write_confusion_csv(matrix: np.ndarray, output_path: Path) -> None:
    with output_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["true\\pred", *CLASS_NAMES])
        for class_name, row in zip(CLASS_NAMES, matrix.tolist(), strict=True):
            writer.writerow([class_name, *row])


def per_class_recall(matrix: np.ndarray) -> dict[str, float]:
    return {
        class_name: float(matrix[idx, idx] / max(1, matrix[idx].sum()))
        for idx, class_name in enumerate(CLASS_NAMES)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train an SVM CSI model on one or more datasets.")
    parser.add_argument(
        "--train-roots",
        type=Path,
        nargs="+",
        default=[Path("csi_dog_dataset")],
    )
    parser.add_argument("--feature-mode", choices=["magnitude", "phase_magnitude"], default="magnitude")
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--recent-weight", type=float, default=1.25)
    parser.add_argument("--nothing-recent-min", type=int, default=1162)
    parser.add_argument("--turtlebot-recent-min", type=int, default=1162)
    parser.add_argument("--person-recent-min", type=int, default=1100)
    parser.add_argument("--output-model", type=Path, default=Path("analysis_outputs/csi_trained_model_svm_combined.joblib"))
    parser.add_argument("--output-report", type=Path, default=Path("analysis_outputs/csi_svm_combined_report.txt"))
    parser.add_argument(
        "--output-confusion-csv",
        type=Path,
        default=Path("analysis_outputs/csi_svm_combined_confusion_matrix.csv"),
    )
    parser.add_argument(
        "--output-confusion-png",
        type=Path,
        default=Path("analysis_outputs/csi_svm_combined_confusion_matrix.png"),
    )
    args = parser.parse_args()

    recent_min_index = {
        "nothing": args.nothing_recent_min,
        "person_1": args.person_recent_min,
        "turtlebot": args.turtlebot_recent_min,
    }

    features, labels, weights = load_dataset(args.train_roots, args.feature_mode, recent_min_index, args.recent_weight)
    x_train, x_test, y_train, y_test, w_train, w_test = train_test_split(
        features,
        labels,
        weights,
        test_size=args.test_ratio,
        random_state=args.seed,
        stratify=labels,
    )

    candidates = [
        {"C": 1.0, "gamma": "scale"},
        {"C": 3.0, "gamma": 0.1},
        {"C": 10.0, "gamma": 0.1},
        {"C": 30.0, "gamma": 0.1},
        {"C": 30.0, "gamma": 0.03},
        {"C": 100.0, "gamma": 0.1},
    ]

    best = None
    best_metrics = None
    best_params = None

    for params in candidates:
        pipeline = make_pipeline(
            StandardScaler(),
            SVC(
                C=params["C"],
                gamma=params["gamma"],
                kernel="rbf",
                class_weight="balanced",
            ),
        )
        pipeline.fit(x_train, y_train, svc__sample_weight=w_train)
        pred = pipeline.predict(x_test)
        matrix = confusion_matrix(y_test, pred, labels=[0, 1, 2])
        metrics = {
            "accuracy": float(accuracy_score(y_test, pred)),
            "balanced_accuracy": float(balanced_accuracy_score(y_test, pred)),
            "confusion_matrix": matrix.tolist(),
            "per_class_recall": per_class_recall(matrix),
        }
        key = (
            metrics["balanced_accuracy"],
            metrics["per_class_recall"]["turtlebot"],
            metrics["accuracy"],
        )
        if best is None or key > best:
            best = key
            best_metrics = metrics
            best_params = params

    final_pipeline = make_pipeline(
        StandardScaler(),
        SVC(
            C=best_params["C"],
            gamma=best_params["gamma"],
            kernel="rbf",
            class_weight="balanced",
        ),
    )
    final_pipeline.fit(features, labels, svc__sample_weight=weights)

    payload = {
        "model_type": "sklearn_pipeline",
        "model_name": "svm_rbf_combined",
        "feature_mode": args.feature_mode,
        "class_names": CLASS_NAMES,
        "pipeline": final_pipeline,
        "train_roots": [str(root) for root in args.train_roots],
        "recent_weighting": {
            "recent_weight": args.recent_weight,
            "recent_min_index": recent_min_index,
        },
        "validation": {
            "test_ratio": args.test_ratio,
            "seed": args.seed,
            "selected_hyperparameters": best_params,
            "metrics": best_metrics,
        },
    }

    matrix = np.asarray(best_metrics["confusion_matrix"], dtype=int)
    args.output_model.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(payload, args.output_model)
    write_confusion_csv(matrix, args.output_confusion_csv)
    plot_confusion(matrix, args.output_confusion_png, "Combined SVM holdout")

    report_lines = [
        "Combined SVM CSI model",
        "train_roots:",
        *[f"- {root}" for root in args.train_roots],
        f"feature_mode: {args.feature_mode}",
        "model: RBF SVM with StandardScaler",
        f"recent_weight: {args.recent_weight}",
        f"recent_min_index: {recent_min_index}",
        f"selected_hyperparameters: C={best_params['C']} gamma={best_params['gamma']} class_weight=balanced",
        f"holdout_accuracy: {best_metrics['accuracy']:.6f}",
        f"holdout_balanced_accuracy: {best_metrics['balanced_accuracy']:.6f}",
        "holdout_confusion_matrix:",
        matrix_to_text(matrix),
        "holdout_per_class_recall:",
    ]
    for class_name in CLASS_NAMES:
        report_lines.append(f"- {class_name}: {best_metrics['per_class_recall'][class_name]:.6f}")
    args.output_report.write_text("\n".join(report_lines) + "\n")

    print(f"Saved combined SVM model to {args.output_model}")
    print(f"Saved report to {args.output_report}")
    print(f"Saved confusion matrix CSV to {args.output_confusion_csv}")
    print(f"Saved confusion matrix PNG to {args.output_confusion_png}")
    print(matrix_to_text(matrix))


if __name__ == "__main__":
    main()
