#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


PHASE_COUNT = 32
MAGNITUDE_COUNT = 32
EXPECTED_TOKEN_COUNT = 72
CLASS_NAMES = ("nothing", "turtlebot")


@dataclass
class Sample:
    label: str
    path: Path
    timestamp: float
    phase: np.ndarray
    magnitude: np.ndarray


def parse_measurement_line(line: str, path: Path, label: str) -> Sample:
    if "Measurement:" not in line:
        raise ValueError(f"{path} does not contain a Measurement line")

    payload = line.split("Measurement:", 1)[1].strip()
    tokens = [token.strip() for token in payload.split(",")]
    if len(tokens) != EXPECTED_TOKEN_COUNT:
        raise ValueError(f"{path} has {len(tokens)} tokens, expected {EXPECTED_TOKEN_COUNT}")

    phase = np.array([float(value) for value in tokens[8:40]], dtype=float)
    magnitude = np.array([float(value) for value in tokens[40:72]], dtype=float)

    return Sample(
        label=label,
        path=path,
        timestamp=float(tokens[1]),
        phase=phase,
        magnitude=magnitude,
    )


def load_class_samples(class_dir: Path, label: str) -> list[Sample]:
    samples: list[Sample] = []
    for path in sorted(class_dir.glob("*.txt")):
        lines = [line.strip() for line in path.read_text(errors="ignore").splitlines() if "Measurement:" in line]
        if len(lines) != 1:
            raise ValueError(f"{path} should contain exactly one Measurement line and has {len(lines)}")
        samples.append(parse_measurement_line(lines[0], path, label))
    return samples


def build_matrix(samples: list[Sample], attr: str) -> np.ndarray:
    return np.vstack([getattr(sample, attr) for sample in samples])


def choose_samples(samples: list[Sample], count: int, rng: np.random.Generator) -> list[Sample]:
    count = min(count, len(samples))
    indices = rng.choice(len(samples), size=count, replace=False)
    return [samples[index] for index in sorted(indices)]


def plot_random_samples(ax: plt.Axes, samples: list[Sample], attr: str, title: str) -> None:
    x = np.arange(1, PHASE_COUNT + 1)
    for sample in samples:
        ax.plot(x, getattr(sample, attr), alpha=0.55, linewidth=1.2)
    ax.set_title(title)
    ax.set_xlabel("Subcarrier")
    ax.set_ylabel(attr.capitalize())
    ax.grid(alpha=0.2)


def plot_mean_std(ax: plt.Axes, data_a: np.ndarray, data_b: np.ndarray, title: str, ylabel: str) -> None:
    x = np.arange(1, data_a.shape[1] + 1)
    mean_a = data_a.mean(axis=0)
    mean_b = data_b.mean(axis=0)
    std_a = data_a.std(axis=0)
    std_b = data_b.std(axis=0)

    ax.plot(x, mean_a, label="nothing", linewidth=2.0)
    ax.fill_between(x, mean_a - std_a, mean_a + std_a, alpha=0.2)
    ax.plot(x, mean_b, label="turtlebot", linewidth=2.0)
    ax.fill_between(x, mean_b - std_b, mean_b + std_b, alpha=0.2)
    ax.set_title(title)
    ax.set_xlabel("Subcarrier")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.2)
    ax.legend()


def plot_distribution_summary(ax: plt.Axes, data_a: np.ndarray, data_b: np.ndarray, title: str) -> None:
    means_a = data_a.mean(axis=1)
    means_b = data_b.mean(axis=1)
    ax.hist(means_a, bins=30, alpha=0.6, label="nothing")
    ax.hist(means_b, bins=30, alpha=0.6, label="turtlebot")
    ax.set_title(title)
    ax.set_xlabel("Mean magnitude per sample")
    ax.set_ylabel("Count")
    ax.grid(alpha=0.2)
    ax.legend()


def pca_2d(features: np.ndarray) -> np.ndarray:
    centered = features - features.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    return centered @ vt[:2].T


def plot_projection(ax: plt.Axes, proj_a: np.ndarray, proj_b: np.ndarray) -> None:
    ax.scatter(proj_a[:, 0], proj_a[:, 1], s=16, alpha=0.5, label="nothing")
    ax.scatter(proj_b[:, 0], proj_b[:, 1], s=16, alpha=0.5, label="turtlebot")
    ax.set_title("2D projection of phase + magnitude")
    ax.set_xlabel("PC1")
    ax.set_ylabel("PC2")
    ax.grid(alpha=0.2)
    ax.legend()


def cohens_d(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    mean_diff = a.mean(axis=0) - b.mean(axis=0)
    pooled = np.sqrt((a.var(axis=0, ddof=1) + b.var(axis=0, ddof=1)) / 2.0)
    pooled[pooled == 0] = 1.0
    return mean_diff / pooled


def save_report(output_dir: Path, nothing: list[Sample], turtlebot: list[Sample], sample_count: int, seed: int) -> Path:
    rng = np.random.default_rng(seed)

    phase_nothing = build_matrix(nothing, "phase")
    phase_turtlebot = build_matrix(turtlebot, "phase")
    mag_nothing = build_matrix(nothing, "magnitude")
    mag_turtlebot = build_matrix(turtlebot, "magnitude")

    chosen_nothing = choose_samples(nothing, sample_count, rng)
    chosen_turtlebot = choose_samples(turtlebot, sample_count, rng)

    fig, axes = plt.subplots(3, 2, figsize=(15, 14))
    fig.suptitle("CSI dataset analysis: nothing vs turtlebot", fontsize=16)

    plot_random_samples(axes[0, 0], chosen_nothing, "magnitude", "Random magnitude samples: nothing")
    plot_random_samples(axes[0, 1], chosen_turtlebot, "magnitude", "Random magnitude samples: turtlebot")
    plot_mean_std(axes[1, 0], mag_nothing, mag_turtlebot, "Mean +/- std magnitude", "Magnitude")
    plot_mean_std(axes[1, 1], phase_nothing, phase_turtlebot, "Mean +/- std phase", "Phase")
    plot_distribution_summary(axes[2, 0], mag_nothing, mag_turtlebot, "Distribution of sample mean magnitudes")

    features_nothing = np.hstack([phase_nothing, mag_nothing])
    features_turtlebot = np.hstack([phase_turtlebot, mag_turtlebot])
    projection = pca_2d(np.vstack([features_nothing, features_turtlebot]))
    split = len(features_nothing)
    plot_projection(axes[2, 1], projection[:split], projection[split:])

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    figure_path = output_dir / "csi_analysis_overview.png"
    fig.savefig(figure_path, dpi=180)
    plt.close(fig)

    d_mag = cohens_d(mag_nothing, mag_turtlebot)
    d_phase = cohens_d(phase_nothing, phase_turtlebot)
    mag_sample_mean_gap = mag_nothing.mean(axis=1).mean() - mag_turtlebot.mean(axis=1).mean()
    phase_sample_mean_gap = phase_nothing.mean(axis=1).mean() - phase_turtlebot.mean(axis=1).mean()

    report_path = output_dir / "csi_analysis_summary.txt"
    report_path.write_text(
        "\n".join(
            [
                "CSI analysis summary",
                f"nothing samples: {len(nothing)}",
                f"turtlebot samples: {len(turtlebot)}",
                f"random samples plotted per class: {sample_count}",
                "",
                f"Mean of sample mean magnitudes, nothing: {mag_nothing.mean(axis=1).mean():.4f}",
                f"Mean of sample mean magnitudes, turtlebot: {mag_turtlebot.mean(axis=1).mean():.4f}",
                f"Gap in sample mean magnitudes (nothing - turtlebot): {mag_sample_mean_gap:.4f}",
                "",
                f"Mean of sample mean phases, nothing: {phase_nothing.mean(axis=1).mean():.4f}",
                f"Mean of sample mean phases, turtlebot: {phase_turtlebot.mean(axis=1).mean():.4f}",
                f"Gap in sample mean phases (nothing - turtlebot): {phase_sample_mean_gap:.4f}",
                "",
                f"Average absolute Cohen's d on magnitude subcarriers: {np.mean(np.abs(d_mag)):.4f}",
                f"Maximum absolute Cohen's d on magnitude subcarriers: {np.max(np.abs(d_mag)):.4f}",
                f"Average absolute Cohen's d on phase subcarriers: {np.mean(np.abs(d_phase)):.4f}",
                f"Maximum absolute Cohen's d on phase subcarriers: {np.max(np.abs(d_phase)):.4f}",
                "",
                f"Strongest magnitude subcarrier index (1-based): {int(np.argmax(np.abs(d_mag)) + 1)}",
                f"Strongest phase subcarrier index (1-based): {int(np.argmax(np.abs(d_phase)) + 1)}",
            ]
        )
        + "\n"
    )
    return figure_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze CSI samples for nothing vs turtlebot.")
    parser.add_argument("--dataset-root", type=Path, default=Path("csi_dog_dataset"))
    parser.add_argument("--output-dir", type=Path, default=Path("analysis_outputs"))
    parser.add_argument("--sample-count", type=int, default=8)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = {
        label: load_class_samples(args.dataset_root / label / "txt", label)
        for label in CLASS_NAMES
    }
    figure_path = save_report(output_dir, samples["nothing"], samples["turtlebot"], args.sample_count, args.seed)
    print(f"Saved figure to {figure_path}")
    print(f"Saved summary to {output_dir / 'csi_analysis_summary.txt'}")


if __name__ == "__main__":
    main()
