#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from analyze_csi_dataset import build_matrix, load_class_samples


@dataclass
class Dataset:
    features: np.ndarray
    labels: np.ndarray
    feature_mode: str
    class_names: list[str]


def discover_class_names(dataset_root: Path) -> list[str]:
    class_names = []
    for child in sorted(dataset_root.iterdir()):
        if child.is_dir() and (child / "txt").exists():
            class_names.append(child.name)
    if not class_names:
        raise ValueError(f"No class folders with txt/ found under {dataset_root}")
    return class_names


def load_dataset(dataset_root: Path, feature_mode: str, class_names: list[str]) -> Dataset:
    features_by_class = []
    labels_by_class = []

    for label_index, class_name in enumerate(class_names):
        samples = load_class_samples(dataset_root / class_name / "txt", class_name)
        phase = build_matrix(samples, "phase")
        magnitude = build_matrix(samples, "magnitude")

        if feature_mode == "magnitude":
            class_features = magnitude
        elif feature_mode == "phase_magnitude":
            class_features = np.hstack([phase, magnitude])
        else:
            raise ValueError(f"Unsupported feature mode: {feature_mode}")

        features_by_class.append(class_features)
        labels_by_class.append(np.full(len(class_features), label_index, dtype=int))

    return Dataset(
        features=np.vstack(features_by_class).astype(float),
        labels=np.concatenate(labels_by_class),
        feature_mode=feature_mode,
        class_names=class_names,
    )


def stratified_split(labels: np.ndarray, test_ratio: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    train_indices = []
    test_indices = []
    for label in np.unique(labels):
        idx = np.where(labels == label)[0]
        idx = rng.permutation(idx)
        test_count = max(1, int(round(len(idx) * test_ratio)))
        test_indices.append(idx[:test_count])
        train_indices.append(idx[test_count:])
    return np.concatenate(train_indices), np.concatenate(test_indices)


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, class_count: int) -> np.ndarray:
    matrix = np.zeros((class_count, class_count), dtype=int)
    for true_label, pred_label in zip(y_true, y_pred, strict=True):
        matrix[int(true_label), int(pred_label)] += 1
    return matrix


def metrics_from_predictions(y_true: np.ndarray, y_pred: np.ndarray, class_names: list[str]) -> dict[str, object]:
    matrix = confusion_matrix(y_true, y_pred, len(class_names))
    per_class_recall = []
    for index in range(len(class_names)):
        total = int(matrix[index].sum())
        per_class_recall.append((matrix[index, index] / total) if total else 0.0)

    return {
        "accuracy": float(np.mean(y_true == y_pred)),
        "balanced_accuracy": float(np.mean(per_class_recall)),
        "confusion_matrix": matrix.tolist(),
        "class_names": class_names,
        "per_class_recall": {class_names[i]: float(per_class_recall[i]) for i in range(len(class_names))},
    }


def train_threshold_mean_magnitude(x_train: np.ndarray, y_train: np.ndarray, class_names: list[str]) -> dict:
    if len(class_names) != 2:
        raise ValueError("threshold_mean only supports binary datasets")

    scores = x_train.mean(axis=1)
    candidates = np.unique(scores)
    best = None
    for threshold in candidates:
        pred = (scores < threshold).astype(int)
        metrics = metrics_from_predictions(y_train, pred, class_names)
        item = (metrics["balanced_accuracy"], metrics["accuracy"], float(threshold), metrics)
        if best is None or item[:2] > best[:2]:
            best = item

    return {
        "model_type": "threshold_mean",
        "threshold": best[2],
        "feature_mode": "magnitude",
        "class_names": class_names,
        "train_metrics": best[3],
    }


def predict_threshold_mean(model: dict, x: np.ndarray) -> np.ndarray:
    return (x.mean(axis=1) < model["threshold"]).astype(int)


def train_nearest_centroid(x_train: np.ndarray, y_train: np.ndarray, feature_mode: str, class_names: list[str]) -> dict:
    centroids = []
    for class_index in range(len(class_names)):
        centroids.append(x_train[y_train == class_index].mean(axis=0))

    model = {
        "model_type": "nearest_centroid",
        "feature_mode": feature_mode,
        "class_names": class_names,
        "centroids": [centroid.tolist() for centroid in centroids],
    }
    pred = predict_nearest_centroid(model, x_train)
    model["train_metrics"] = metrics_from_predictions(y_train, pred, class_names)
    return model


def predict_nearest_centroid(model: dict, x: np.ndarray) -> np.ndarray:
    centroids = np.asarray(model["centroids"], dtype=float)
    distances = np.linalg.norm(x[:, None, :] - centroids[None, :, :], axis=2)
    return np.argmin(distances, axis=1)


def train_diag_gaussian(x_train: np.ndarray, y_train: np.ndarray, feature_mode: str, class_names: list[str]) -> dict:
    means = []
    variances = []
    priors = []
    for class_index in range(len(class_names)):
        class_samples = x_train[y_train == class_index]
        means.append(class_samples.mean(axis=0))
        variances.append(class_samples.var(axis=0) + 1e-6)
        priors.append(float(len(class_samples) / len(x_train)))

    model = {
        "model_type": "diag_gaussian",
        "feature_mode": feature_mode,
        "class_names": class_names,
        "means": [mean.tolist() for mean in means],
        "variances": [var.tolist() for var in variances],
        "priors": priors,
    }
    pred = predict_diag_gaussian(model, x_train)
    model["train_metrics"] = metrics_from_predictions(y_train, pred, class_names)
    return model


def gaussian_logpdf(x: np.ndarray, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
    return -0.5 * np.sum(np.log(2.0 * np.pi * var) + ((x - mean) ** 2) / var, axis=1)


def predict_diag_gaussian(model: dict, x: np.ndarray) -> np.ndarray:
    means = np.asarray(model["means"], dtype=float)
    variances = np.asarray(model["variances"], dtype=float)
    priors = np.asarray(model["priors"], dtype=float)
    scores = []
    for class_index in range(len(model["class_names"])):
        scores.append(np.log(priors[class_index]) + gaussian_logpdf(x, means[class_index], variances[class_index]))
    score_matrix = np.vstack(scores).T
    return np.argmax(score_matrix, axis=1)


def predict_with_model(model: dict, x: np.ndarray) -> np.ndarray:
    if model["model_type"] == "threshold_mean":
        return predict_threshold_mean(model, x)
    if model["model_type"] == "nearest_centroid":
        return predict_nearest_centroid(model, x)
    if model["model_type"] == "diag_gaussian":
        return predict_diag_gaussian(model, x)
    raise ValueError(f"Unsupported model type: {model['model_type']}")


def train_candidates(dataset_root: Path, test_ratio: float, seed: int) -> tuple[dict, list[dict]]:
    class_names = discover_class_names(dataset_root)
    datasets = {
        "magnitude": load_dataset(dataset_root, "magnitude", class_names),
        "phase_magnitude": load_dataset(dataset_root, "phase_magnitude", class_names),
    }
    train_idx, test_idx = stratified_split(datasets["magnitude"].labels, test_ratio, seed)
    labels = datasets["magnitude"].labels
    y_train = labels[train_idx]
    y_test = labels[test_idx]

    candidates = []

    if len(class_names) == 2:
        mag_train = datasets["magnitude"].features[train_idx]
        mag_test = datasets["magnitude"].features[test_idx]
        model = train_threshold_mean_magnitude(mag_train, y_train, class_names)
        model["test_metrics"] = metrics_from_predictions(y_test, predict_with_model(model, mag_test), class_names)
        candidates.append(model)

    for feature_mode in ("magnitude", "phase_magnitude"):
        dataset = datasets[feature_mode]
        x_train = dataset.features[train_idx]
        x_test = dataset.features[test_idx]
        for trainer in (train_nearest_centroid, train_diag_gaussian):
            model = trainer(x_train, y_train, feature_mode, class_names)
            model["test_metrics"] = metrics_from_predictions(y_test, predict_with_model(model, x_test), class_names)
            candidates.append(model)

    best = max(candidates, key=lambda item: (item["test_metrics"]["balanced_accuracy"], item["test_metrics"]["accuracy"]))
    return best, candidates


def format_confusion_matrix(matrix: list[list[int]], class_names: list[str]) -> str:
    headers = ["true\\pred"] + class_names
    rows = [headers]
    for class_name, row in zip(class_names, matrix, strict=True):
        rows.append([class_name] + [str(value) for value in row])

    widths = [max(len(row[col]) for row in rows) for col in range(len(headers))]
    return "\n".join(
        "  ".join(value.ljust(widths[col]) for col, value in enumerate(row))
        for row in rows
    )


def model_summary(model: dict) -> str:
    metrics = model["test_metrics"]
    lines = [
        f"model_type: {model['model_type']}",
        f"feature_mode: {model['feature_mode']}",
        f"class_names: {', '.join(model['class_names'])}",
        f"test_accuracy: {metrics['accuracy']:.6f}",
        f"test_balanced_accuracy: {metrics['balanced_accuracy']:.6f}",
        "test_confusion_matrix:",
        format_confusion_matrix(metrics["confusion_matrix"], model["class_names"]),
        "per_class_recall:",
    ]
    for class_name in model["class_names"]:
        lines.append(f"- {class_name}: {metrics['per_class_recall'][class_name]:.6f}")
    return "\n".join(lines)


def write_confusion_matrix_csv(matrix: list[list[int]], class_names: list[str], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["true\\pred", *class_names])
        for class_name, row in zip(class_names, matrix, strict=True):
            writer.writerow([class_name, *row])


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and compare simple CSI classifiers.")
    parser.add_argument("--dataset-root", type=Path, default=Path("csi_dog_dataset"))
    parser.add_argument("--output-model", type=Path, default=Path("analysis_outputs/csi_trained_model.json"))
    parser.add_argument("--output-report", type=Path, default=Path("analysis_outputs/csi_model_report.txt"))
    parser.add_argument(
        "--output-confusion-csv",
        type=Path,
        default=Path("analysis_outputs/csi_confusion_matrix.csv"),
    )
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    args.output_model.parent.mkdir(parents=True, exist_ok=True)

    best, candidates = train_candidates(args.dataset_root, args.test_ratio, args.seed)
    args.output_model.write_text(json.dumps(best, indent=2))
    write_confusion_matrix_csv(best["test_metrics"]["confusion_matrix"], best["class_names"], args.output_confusion_csv)

    report_lines = ["Best model", model_summary(best), "", "All candidates"]
    for candidate in candidates:
        report_lines.append("")
        report_lines.append(model_summary(candidate))
    args.output_report.write_text("\n".join(report_lines) + "\n")

    print(f"Saved best model to {args.output_model}")
    print(f"Saved comparison report to {args.output_report}")
    print(f"Saved confusion matrix CSV to {args.output_confusion_csv}")
    print(model_summary(best))


if __name__ == "__main__":
    main()
