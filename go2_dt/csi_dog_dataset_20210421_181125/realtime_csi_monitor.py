#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter, deque
from pathlib import Path

from analyze_csi_dataset import parse_measurement_line
from csi_binary_detector import DEFAULT_THRESHOLD, load_saved_model, predict_with_saved_model


def classify_measurement_line(model: dict, line: str, source_path: Path) -> tuple[str, float]:
    sample = parse_measurement_line(line.strip(), source_path, "unknown")
    return predict_with_saved_model(model, sample)


def print_prediction(index: int, label: str, score: float, line: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = line.split("Measurement:", 1)[1].strip()
    parts = [part.strip() for part in payload.split(",")]
    csi_timestamp = parts[1] if len(parts) > 1 else "unknown"
    print(
        f"[{timestamp}] sample={index:04d} csi_ts={csi_timestamp} "
        f"prediction={label} score={score:.6f}",
        flush=True,
    )


def print_smoothed_prediction(index: int, raw_label: str, stable_label: str, streak: int, score: float, line: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = line.split("Measurement:", 1)[1].strip()
    parts = [part.strip() for part in payload.split(",")]
    csi_timestamp = parts[1] if len(parts) > 1 else "unknown"
    print(
        f"[{timestamp}] sample={index:04d} csi_ts={csi_timestamp} "
        f"prediction={stable_label} raw={raw_label} streak={streak} score={score:.6f}",
        flush=True,
    )


def choose_stable_label(
    stable_label: str | None,
    raw_label: str,
    candidate_label: str | None,
    candidate_streak: int,
    recent_raw_labels: deque[str],
    confirm_count: int,
    vote_threshold: int,
) -> tuple[str, int]:
    counts = Counter(recent_raw_labels)
    best_label, best_count = counts.most_common(1)[0]

    if stable_label is None:
        return raw_label, best_count

    if candidate_label != stable_label and candidate_streak >= confirm_count:
        stable_label = candidate_label
        counts = Counter(recent_raw_labels)
        return stable_label, counts.get(stable_label, 0)

    current_count = counts.get(stable_label, 0)
    if best_label != stable_label and best_count >= vote_threshold and best_count > current_count:
        return best_label, best_count

    return stable_label, current_count


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def monitor_stream(
    input_path: Path,
    model_path: Path,
    poll_interval: float,
    confirm_count: int,
    vote_window: int,
    vote_threshold: int,
    output_csv: Path | None,
    output_state_json: Path | None,
    max_samples: int | None,
) -> None:
    model = load_saved_model(model_path)

    output_handle = None
    writer = None
    if output_csv is not None:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        output_handle = output_csv.open("w", newline="")
        writer = csv.DictWriter(
            output_handle,
            fieldnames=[
                "sample_index",
                "wall_time",
                "prediction",
                "raw_prediction",
                "streak",
                "stable_votes",
                "score",
                "csi_timestamp",
                "raw_line",
            ],
        )
        writer.writeheader()

    sample_index = 0
    read_offset = 0
    stable_label = None
    candidate_label = None
    candidate_streak = 0
    recent_raw_labels: deque[str] = deque(maxlen=vote_window)

    try:
        print(f"Monitoring {input_path}", flush=True)
        print(f"Using model {model_path}", flush=True)
        print(f"Label change confirmation count: {confirm_count}", flush=True)
        print(f"Vote window: {vote_window} | vote threshold: {vote_threshold}", flush=True)
        if output_state_json is not None:
            print(f"Writing live state to {output_state_json}", flush=True)

        while True:
            if input_path.exists():
                size = input_path.stat().st_size
                if size < read_offset:
                    read_offset = 0

                with input_path.open("r", errors="ignore") as fh:
                    fh.seek(read_offset)
                    lines = fh.readlines()
                    read_offset = fh.tell()

                for raw_line in lines:
                    line = raw_line.strip()
                    if not line or "Measurement:" not in line:
                        continue

                    sample_index += 1
                    wall_time = time.strftime("%Y-%m-%d %H:%M:%S")

                    try:
                        raw_label, score = classify_measurement_line(model, line, input_path)
                        payload = line.split("Measurement:", 1)[1].strip()
                        parts = [part.strip() for part in payload.split(",")]
                        csi_timestamp = parts[1] if len(parts) > 1 else ""

                        if raw_label == candidate_label:
                            candidate_streak += 1
                        else:
                            candidate_label = raw_label
                            candidate_streak = 1

                        recent_raw_labels.append(raw_label)
                        stable_label, stable_votes = choose_stable_label(
                            stable_label=stable_label,
                            raw_label=raw_label,
                            candidate_label=candidate_label,
                            candidate_streak=candidate_streak,
                            recent_raw_labels=recent_raw_labels,
                            confirm_count=confirm_count,
                            vote_threshold=vote_threshold,
                        )

                        print_smoothed_prediction(
                            sample_index,
                            raw_label,
                            stable_label,
                            candidate_streak,
                            score,
                            line,
                        )

                        if writer is not None:
                            writer.writerow(
                                {
                                    "sample_index": sample_index,
                                    "wall_time": wall_time,
                                    "prediction": stable_label,
                                    "raw_prediction": raw_label,
                                    "streak": candidate_streak,
                                    "stable_votes": stable_votes,
                                    "score": f"{score:.6f}",
                                    "csi_timestamp": csi_timestamp,
                                    "raw_line": line,
                                }
                            )
                            output_handle.flush()

                        if output_state_json is not None:
                            atomic_write_json(
                                output_state_json,
                                {
                                    "sample_index": sample_index,
                                    "wall_time": wall_time,
                                    "prediction": stable_label,
                                    "raw_prediction": raw_label,
                                    "streak": candidate_streak,
                                    "stable_votes": stable_votes,
                                    "score": float(score),
                                    "csi_timestamp": csi_timestamp,
                                },
                            )

                    except Exception as exc:
                        print(
                            f"[{wall_time}] sample={sample_index:04d} prediction=error error={exc}",
                            flush=True,
                        )

                        if writer is not None:
                            writer.writerow(
                                {
                                    "sample_index": sample_index,
                                    "wall_time": wall_time,
                                    "prediction": "error",
                                    "score": "",
                                    "csi_timestamp": "",
                                    "raw_line": line,
                                }
                            )
                            output_handle.flush()

                        if output_state_json is not None:
                            atomic_write_json(
                                output_state_json,
                                {
                                    "sample_index": sample_index,
                                    "wall_time": wall_time,
                                    "prediction": "error",
                                    "score": None,
                                    "csi_timestamp": "",
                                    "error": str(exc),
                                },
                            )

                    if max_samples is not None and sample_index >= max_samples:
                        return

            time.sleep(poll_interval)

    finally:
        if output_handle is not None:
            output_handle.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read CSI measurements from a live file and predict labels in real time."
    )
    parser.add_argument("--input", type=Path, default=Path("realtime_inputs/live_csi_stream.txt"))
    parser.add_argument("--model", type=Path, default=Path("analysis_outputs/csi_trained_model.json"))
    parser.add_argument("--poll-interval", type=float, default=0.25)
    parser.add_argument(
        "--confirm-count",
        type=int,
        default=3,
        help="Require this many consecutive raw predictions before switching the published label.",
    )
    parser.add_argument(
        "--vote-window",
        type=int,
        default=9,
        help="Window size for majority voting over recent raw predictions.",
    )
    parser.add_argument(
        "--vote-threshold",
        type=int,
        default=6,
        help="Minimum votes inside the window to switch the published label.",
    )
    parser.add_argument("--output-csv", type=Path, default=Path("analysis_outputs/live_predictions.csv"))
    parser.add_argument(
        "--output-state-json",
        type=Path,
        default=Path("analysis_outputs/live_prediction_state.json"),
        help="JSON file with the latest live prediction for external consumers like Isaac Sim.",
    )
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="Reserved for compatibility.")
    args = parser.parse_args()

    args.input.parent.mkdir(parents=True, exist_ok=True)
    args.input.touch(exist_ok=True)

    monitor_stream(
        input_path=args.input,
        model_path=args.model,
        poll_interval=args.poll_interval,
        confirm_count=args.confirm_count,
        vote_window=args.vote_window,
        vote_threshold=args.vote_threshold,
        output_csv=args.output_csv,
        output_state_json=args.output_state_json,
        max_samples=args.max_samples,
    )


if __name__ == "__main__":
    main()
