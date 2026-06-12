#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold, cross_val_predict, cross_validate, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from analyze_csi_dataset import parse_measurement_line


DATASET_FOLDERS = {
    "new_scenario_empty": "empty",
    "new_scenario_person": "person",
}
CLASS_NAMES = ["empty", "person"]
LABEL_TO_INDEX = {label: idx for idx, label in enumerate(CLASS_NAMES)}
PATH_INDEX_RE = re.compile(r"_(\d+)$")


@dataclass
class InvalidSample:
    path: str
    reason: str


def matrix_to_text(matrix: np.ndarray) -> str:
    headers = ["true\\pred"] + CLASS_NAMES
    rows = [headers]
    for class_name, row in zip(CLASS_NAMES, matrix.tolist(), strict=True):
        rows.append([class_name] + [str(value) for value in row])
    widths = [max(len(row[col]) for row in rows) for col in range(len(headers))]
    return "\n".join("  ".join(value.ljust(widths[col]) for col, value in enumerate(row)) for row in rows)


def plot_confusion(matrix: np.ndarray, output_path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(5.2, 4.4))
    image = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(CLASS_NAMES)), CLASS_NAMES)
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


def sample_index_from_path(path: Path) -> int | None:
    match = PATH_INDEX_RE.search(path.stem)
    if match is None:
        return None
    return int(match.group(1))


def load_dataset(
    dataset_root: Path,
    min_index: int | None = None,
    max_index: int | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str], list[InvalidSample], dict[str, int]]:
    features = []
    labels = []
    paths: list[str] = []
    invalid_samples: list[InvalidSample] = []
    valid_counts = {label: 0 for label in CLASS_NAMES}

    for folder_name, label_name in DATASET_FOLDERS.items():
        class_dir = dataset_root / folder_name / "txt"
        if not class_dir.exists():
            raise ValueError(f"Missing dataset folder: {class_dir}")

        for path in sorted(class_dir.glob("*.txt")):
            sample_index = sample_index_from_path(path)
            if min_index is not None and (sample_index is None or sample_index < min_index):
                continue
            if max_index is not None and (sample_index is None or sample_index > max_index):
                continue

            lines = [line.strip() for line in path.read_text(errors="ignore").splitlines() if "Measurement:" in line]
            if len(lines) != 1:
                invalid_samples.append(InvalidSample(str(path), f"expected 1 Measurement line, found {len(lines)}"))
                continue
            try:
                sample = parse_measurement_line(lines[0], path, label_name)
            except Exception as exc:
                invalid_samples.append(InvalidSample(str(path), str(exc)))
                continue

            features.append(sample.magnitude.astype(float))
            labels.append(LABEL_TO_INDEX[label_name])
            paths.append(str(path))
            valid_counts[label_name] += 1

    if not features:
        raise ValueError(f"No valid CSI samples found under {dataset_root}")

    return np.vstack(features), np.asarray(labels, dtype=int), paths, invalid_samples, valid_counts


def build_candidates() -> list[tuple[str, object]]:
    candidates: list[tuple[str, object]] = []
    for c_value in (1.0, 3.0, 10.0, 30.0):
        for gamma in ("scale", 0.03, 0.1):
            name = f"svm_rbf_mag_C{c_value}_g{gamma}"
            candidates.append(
                (
                    name,
                    make_pipeline(
                        StandardScaler(),
                        SVC(
                            C=c_value,
                            gamma=gamma,
                            kernel="rbf",
                            class_weight="balanced",
                        ),
                    ),
                )
            )

    candidates.append(
        (
            "logreg_mag",
            make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=5000, class_weight="balanced"),
            ),
        )
    )
    return candidates


def evaluate_candidate(name: str, estimator, x: np.ndarray, y: np.ndarray, cv_splits: int, cv_repeats: int) -> dict[str, object]:
    cv = RepeatedStratifiedKFold(n_splits=cv_splits, n_repeats=cv_repeats, random_state=7)
    scores = cross_validate(
        estimator,
        x,
        y,
        cv=cv,
        scoring=["accuracy", "balanced_accuracy", "precision", "recall", "f1"],
        n_jobs=1,
    )
    return {
        "name": name,
        "accuracy_mean": float(scores["test_accuracy"].mean()),
        "accuracy_std": float(scores["test_accuracy"].std()),
        "balanced_accuracy_mean": float(scores["test_balanced_accuracy"].mean()),
        "balanced_accuracy_std": float(scores["test_balanced_accuracy"].std()),
        "precision_mean": float(scores["test_precision"].mean()),
        "recall_mean": float(scores["test_recall"].mean()),
        "f1_mean": float(scores["test_f1"].mean()),
    }


def choose_best_candidate(results: list[dict[str, object]]) -> dict[str, object]:
    return max(
        results,
        key=lambda item: (
            item["balanced_accuracy_mean"],
            -item["balanced_accuracy_std"],
            item["accuracy_mean"],
            item["f1_mean"],
        ),
    )


def holdout_metrics(estimator, x: np.ndarray, y: np.ndarray, test_ratio: float, seed: int) -> dict[str, object]:
    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=test_ratio,
        stratify=y,
        random_state=seed,
    )
    estimator.fit(x_train, y_train)
    y_pred = estimator.predict(x_test)
    matrix = confusion_matrix(y_test, y_pred, labels=[0, 1])
    return {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_test, y_pred)),
        "confusion_matrix": matrix.tolist(),
        "per_class_recall": per_class_recall(matrix),
    }


def out_of_fold_metrics(estimator, x: np.ndarray, y: np.ndarray, cv_splits: int) -> tuple[dict[str, object], np.ndarray]:
    cv = StratifiedKFold(n_splits=cv_splits, shuffle=True, random_state=13)
    y_pred = cross_val_predict(estimator, x, y, cv=cv, n_jobs=1)
    matrix = confusion_matrix(y, y_pred, labels=[0, 1])
    metrics = {
        "accuracy": float(accuracy_score(y, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y, y_pred)),
        "confusion_matrix": matrix.tolist(),
        "per_class_recall": per_class_recall(matrix),
    }
    return metrics, y_pred


def write_invalid_csv(rows: list[InvalidSample], output_path: Path) -> None:
    with output_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "reason"])
        for row in rows:
            writer.writerow([row.path, row.reason])


def write_predictions_csv(paths: list[str], y_true: np.ndarray, y_pred: np.ndarray, output_path: Path) -> None:
    with output_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "true_label", "predicted_label", "ok"])
        for path, true_value, pred_value in zip(paths, y_true.tolist(), y_pred.tolist(), strict=True):
            writer.writerow([path, CLASS_NAMES[true_value], CLASS_NAMES[pred_value], int(true_value == pred_value)])


def write_report(
    output_path: Path,
    dataset_root: Path,
    min_index: int | None,
    max_index: int | None,
    valid_counts: dict[str, int],
    invalid_samples: list[InvalidSample],
    search_results: list[dict[str, object]],
    best_result: dict[str, object],
    holdout: dict[str, object],
    oof: dict[str, object],
    model_path: Path,
) -> None:
    lines = [
        "CSI binary model: empty vs person",
        f"dataset_root: {dataset_root}",
        f"index_filter: min_index={min_index} max_index={max_index}",
        f"class_folders: {json.dumps(DATASET_FOLDERS, ensure_ascii=True)}",
        f"valid_counts: {valid_counts}",
        f"invalid_samples_skipped: {len(invalid_samples)}",
        "",
        "Candidate search ordered by consistency",
    ]
    ordered = sorted(
        search_results,
        key=lambda item: (
            item["balanced_accuracy_mean"],
            -item["balanced_accuracy_std"],
            item["accuracy_mean"],
            item["f1_mean"],
        ),
        reverse=True,
    )
    for item in ordered:
        lines.append(
            "- "
            + f"{item['name']}: "
            + f"bal_acc={item['balanced_accuracy_mean']:.6f} +/- {item['balanced_accuracy_std']:.6f}, "
            + f"acc={item['accuracy_mean']:.6f} +/- {item['accuracy_std']:.6f}, "
            + f"precision={item['precision_mean']:.6f}, recall={item['recall_mean']:.6f}, f1={item['f1_mean']:.6f}"
        )

    holdout_matrix = np.asarray(holdout["confusion_matrix"], dtype=int)
    oof_matrix = np.asarray(oof["confusion_matrix"], dtype=int)

    lines.extend(
        [
            "",
            f"Selected model: {best_result['name']}",
            f"Saved model: {model_path}",
            "",
            "5-fold repeated CV summary used for selection",
            f"balanced_accuracy_mean: {best_result['balanced_accuracy_mean']:.6f}",
            f"balanced_accuracy_std: {best_result['balanced_accuracy_std']:.6f}",
            f"accuracy_mean: {best_result['accuracy_mean']:.6f}",
            f"accuracy_std: {best_result['accuracy_std']:.6f}",
            "",
            "Single 5-fold out-of-fold confusion matrix",
            matrix_to_text(oof_matrix),
            f"oof_accuracy: {oof['accuracy']:.6f}",
            f"oof_balanced_accuracy: {oof['balanced_accuracy']:.6f}",
            "",
            "20% holdout confusion matrix",
            matrix_to_text(holdout_matrix),
            f"holdout_accuracy: {holdout['accuracy']:.6f}",
            f"holdout_balanced_accuracy: {holdout['balanced_accuracy']:.6f}",
            "",
            "Per-class recall on holdout",
        ]
    )
    for class_name in CLASS_NAMES:
        lines.append(f"- {class_name}: {holdout['per_class_recall'][class_name]:.6f}")

    lines.extend(["", "Skipped invalid files"])
    if invalid_samples:
        for row in invalid_samples[:20]:
            lines.append(f"- {row.path} | {row.reason}")
        if len(invalid_samples) > 20:
            lines.append(f"- ... {len(invalid_samples) - 20} more")
    else:
        lines.append("- none")

    output_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a robust CSI classifier for empty vs person.")
    parser.add_argument("--dataset-root", type=Path, default=Path("csi_dog_dataset"))
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cv-splits", type=int, default=5)
    parser.add_argument("--cv-repeats", type=int, default=5)
    parser.add_argument("--min-index", type=int)
    parser.add_argument("--max-index", type=int)
    parser.add_argument(
        "--output-model",
        type=Path,
        default=Path("analysis_outputs/csi_empty_person_model.joblib"),
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=Path("analysis_outputs/csi_empty_person_report.txt"),
    )
    parser.add_argument(
        "--output-search-json",
        type=Path,
        default=Path("analysis_outputs/csi_empty_person_search.json"),
    )
    parser.add_argument(
        "--output-confusion-csv",
        type=Path,
        default=Path("analysis_outputs/csi_empty_person_confusion_matrix.csv"),
    )
    parser.add_argument(
        "--output-confusion-png",
        type=Path,
        default=Path("analysis_outputs/csi_empty_person_confusion_matrix.png"),
    )
    parser.add_argument(
        "--output-predictions-csv",
        type=Path,
        default=Path("analysis_outputs/csi_empty_person_oof_predictions.csv"),
    )
    parser.add_argument(
        "--output-invalid-csv",
        type=Path,
        default=Path("analysis_outputs/csi_empty_person_invalid_samples.csv"),
    )
    args = parser.parse_args()

    args.output_model.parent.mkdir(parents=True, exist_ok=True)

    x, y, paths, invalid_samples, valid_counts = load_dataset(
        args.dataset_root,
        min_index=args.min_index,
        max_index=args.max_index,
    )
    candidates = build_candidates()
    search_results = [
        evaluate_candidate(name, estimator, x, y, args.cv_splits, args.cv_repeats)
        for name, estimator in candidates
    ]
    best_result = choose_best_candidate(search_results)
    estimator_lookup = {name: estimator for name, estimator in candidates}
    best_estimator = estimator_lookup[str(best_result["name"])]

    oof_metrics, oof_pred = out_of_fold_metrics(best_estimator, x, y, args.cv_splits)
    holdout = holdout_metrics(best_estimator, x, y, args.test_ratio, args.seed)

    final_estimator = estimator_lookup[str(best_result["name"])]
    final_estimator.fit(x, y)

    model_payload = {
        "model_type": "sklearn_pipeline",
        "model_name": str(best_result["name"]),
        "feature_mode": "magnitude",
        "class_names": CLASS_NAMES,
        "pipeline": final_estimator,
        "dataset_root": str(args.dataset_root),
        "index_filter": {
            "min_index": args.min_index,
            "max_index": args.max_index,
        },
        "dataset_folders": DATASET_FOLDERS,
        "valid_counts": valid_counts,
        "invalid_sample_count": len(invalid_samples),
        "selection": {
            "cv_splits": args.cv_splits,
            "cv_repeats": args.cv_repeats,
            "test_ratio": args.test_ratio,
            "seed": args.seed,
            "search_results": search_results,
            "best_result": best_result,
            "out_of_fold_metrics": oof_metrics,
            "holdout_metrics": holdout,
        },
    }

    oof_matrix = np.asarray(oof_metrics["confusion_matrix"], dtype=int)
    joblib.dump(model_payload, args.output_model)
    args.output_search_json.write_text(json.dumps(search_results, indent=2) + "\n")
    write_confusion_csv(oof_matrix, args.output_confusion_csv)
    plot_confusion(oof_matrix, args.output_confusion_png, "CSI empty vs person (5-fold OOF)")
    write_predictions_csv(paths, y, oof_pred, args.output_predictions_csv)
    write_invalid_csv(invalid_samples, args.output_invalid_csv)
    write_report(
        args.output_report,
        args.dataset_root,
        args.min_index,
        args.max_index,
        valid_counts,
        invalid_samples,
        search_results,
        best_result,
        holdout,
        oof_metrics,
        args.output_model,
    )

    print(f"Saved model to {args.output_model}")
    print(f"Saved report to {args.output_report}")
    print(f"Saved search results to {args.output_search_json}")
    print(f"Saved OOF confusion matrix to {args.output_confusion_csv}")
    print(f"Saved OOF confusion plot to {args.output_confusion_png}")
    print(f"Saved OOF predictions to {args.output_predictions_csv}")
    print(f"Saved invalid sample log to {args.output_invalid_csv}")
    print(matrix_to_text(oof_matrix))


if __name__ == "__main__":
    main()
