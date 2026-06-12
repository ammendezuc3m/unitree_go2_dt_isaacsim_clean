#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from analyze_csi_dataset import EXPECTED_TOKEN_COUNT, parse_measurement_line


DEFAULT_THRESHOLD = 48.90625
DEFAULT_BINARY_CLASSES = ["nothing", "turtlebot"]


def load_single_sample(path: Path):
    lines = [line.strip() for line in path.read_text(errors="ignore").splitlines() if "Measurement:" in line]
    if len(lines) != 1:
        raise ValueError(f"{path} should contain exactly one Measurement line and has {len(lines)}")
    sample = parse_measurement_line(lines[0], path, "unknown")
    if len(sample.phase) + len(sample.magnitude) != EXPECTED_TOKEN_COUNT - 8:
        raise ValueError(f"{path} has an unexpected CSI length")
    return sample


def classify_file(path: Path, threshold: float) -> tuple[str, float]:
    sample = load_single_sample(path)
    score = float(np.mean(sample.magnitude))
    label = "turtlebot" if score < threshold else "nothing"
    return label, score


def features_from_sample(sample, feature_mode: str) -> np.ndarray:
    if feature_mode == "magnitude":
        return sample.magnitude.astype(float)
    if feature_mode == "phase_magnitude":
        return np.hstack([sample.phase, sample.magnitude]).astype(float)
    raise ValueError(f"Unsupported feature mode: {feature_mode}")


def load_saved_model(model_path: Path):
    if model_path.suffix.lower() == ".json":
        return json.loads(model_path.read_text())
    try:
        import joblib
    except ImportError as exc:
        raise RuntimeError(
            f"Loading {model_path.name} requires joblib/scikit-learn. "
            "Use the .venv_svm environment or install those packages."
        ) from exc
    return joblib.load(model_path)


def model_class_names(model) -> list[str]:
    if "class_names" in model:
        return list(model["class_names"])
    return DEFAULT_BINARY_CLASSES


def predict_threshold_mean_array(model: dict, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    class_names = model_class_names(model)
    if len(class_names) != 2:
        raise ValueError("threshold_mean only supports binary models")
    scores = x.mean(axis=1)
    pred = (scores < model["threshold"]).astype(int)
    return pred, scores


def predict_nearest_centroid_array(model: dict, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if "centroids" in model:
        centroids = np.asarray(model["centroids"], dtype=float)
        distances = np.linalg.norm(x[:, None, :] - centroids[None, :, :], axis=2)
        pred = np.argmin(distances, axis=1)
        sorted_distances = np.sort(distances, axis=1)
        confidence = sorted_distances[:, 1] - sorted_distances[:, 0] if distances.shape[1] > 1 else -sorted_distances[:, 0]
        return pred, confidence

    centroid_nothing = np.asarray(model["centroid_nothing"], dtype=float)
    centroid_turtlebot = np.asarray(model["centroid_turtlebot"], dtype=float)
    dist_nothing = np.linalg.norm(x - centroid_nothing, axis=1)
    dist_turtlebot = np.linalg.norm(x - centroid_turtlebot, axis=1)
    pred = (dist_turtlebot < dist_nothing).astype(int)
    return pred, dist_nothing - dist_turtlebot


def gaussian_logpdf(x: np.ndarray, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
    return -0.5 * np.sum(np.log(2.0 * np.pi * var) + ((x - mean) ** 2) / var, axis=1)


def predict_diag_gaussian_array(model: dict, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if "means" in model:
        means = np.asarray(model["means"], dtype=float)
        variances = np.asarray(model["variances"], dtype=float)
        priors = np.asarray(model["priors"], dtype=float)
        scores = []
        for class_index in range(len(model_class_names(model))):
            scores.append(np.log(priors[class_index]) + gaussian_logpdf(x, means[class_index], variances[class_index]))
        score_matrix = np.vstack(scores).T
        pred = np.argmax(score_matrix, axis=1)
        sorted_scores = np.sort(score_matrix, axis=1)
        confidence = sorted_scores[:, -1] - sorted_scores[:, -2] if score_matrix.shape[1] > 1 else sorted_scores[:, -1]
        return pred, confidence

    mean_nothing = np.asarray(model["mean_nothing"], dtype=float)
    mean_turtlebot = np.asarray(model["mean_turtlebot"], dtype=float)
    var_nothing = np.asarray(model["var_nothing"], dtype=float)
    var_turtlebot = np.asarray(model["var_turtlebot"], dtype=float)
    logp_nothing = np.log(model["prior_nothing"]) + gaussian_logpdf(x, mean_nothing, var_nothing)
    logp_turtlebot = np.log(model["prior_turtlebot"]) + gaussian_logpdf(x, mean_turtlebot, var_turtlebot)
    pred = (logp_turtlebot > logp_nothing).astype(int)
    return pred, np.abs(logp_turtlebot - logp_nothing)


def predict_indices_with_model(model: dict, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if model.get("model_type") == "sklearn_pipeline":
        pred = model["pipeline"].predict(x)
        if hasattr(model["pipeline"], "decision_function"):
            raw_scores = model["pipeline"].decision_function(x)
            if raw_scores.ndim == 1:
                confidence = np.abs(raw_scores)
            else:
                sorted_scores = np.sort(raw_scores, axis=1)
                confidence = sorted_scores[:, -1] - sorted_scores[:, -2]
        elif hasattr(model["pipeline"], "predict_proba"):
            probs = model["pipeline"].predict_proba(x)
            sorted_probs = np.sort(probs, axis=1)
            confidence = sorted_probs[:, -1] - sorted_probs[:, -2]
        else:
            confidence = np.ones(len(pred), dtype=float)
        return np.asarray(pred, dtype=int), np.asarray(confidence, dtype=float)

    if model["model_type"] == "threshold_mean":
        return predict_threshold_mean_array(model, x)
    if model["model_type"] == "nearest_centroid":
        return predict_nearest_centroid_array(model, x)
    if model["model_type"] == "diag_gaussian":
        return predict_diag_gaussian_array(model, x)
    raise ValueError(f"Unsupported model type: {model['model_type']}")


def predict_with_saved_model(model, sample) -> tuple[str, float]:
    x = features_from_sample(sample, model["feature_mode"]).reshape(1, -1)
    pred_indices, scores = predict_indices_with_model(model, x)
    class_names = model_class_names(model)
    return class_names[int(pred_indices[0])], float(scores[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="CSI detector for one .txt file with exactly one Measurement line.")
    parser.add_argument("file", type=Path, help="Path to a .txt file with exactly one Measurement line")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Binary fallback threshold mode")
    parser.add_argument("--model", type=Path, help="Optional JSON model generated by train_csi_model.py")
    args = parser.parse_args()

    sample = load_single_sample(args.file)
    if args.model:
        model = load_saved_model(args.model)
        label, score = predict_with_saved_model(model, sample)
        print(f"model: {args.model}")
        print(f"model_type: {model['model_type']}")
        print(f"feature_mode: {model['feature_mode']}")
        print(f"class_names: {', '.join(model_class_names(model))}")
        print(f"decision_score: {score:.6f}")
    else:
        label, score = classify_file(args.file, args.threshold)
        print(f"mean_magnitude_score: {score:.6f}")
        print(f"threshold: {args.threshold:.6f}")

    print(f"file: {args.file}")
    print(f"prediction: {label}")


if __name__ == "__main__":
    main()
