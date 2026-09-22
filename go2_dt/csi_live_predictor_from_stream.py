#!/usr/bin/env python3
import argparse
import csv
import json
import sys
import time
from collections import Counter, deque
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent
CSI_PROJECT_DIR = PROJECT_ROOT / "csi_dog_dataset_20210421_181125"
VENV_SVM_SITE_PACKAGES = (
    PROJECT_ROOT
    / ".venv_svm"
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)

for extra_path in (VENV_SVM_SITE_PACKAGES, PROJECT_ROOT, CSI_PROJECT_DIR):
    if extra_path.exists():
        p = str(extra_path)
        if p not in sys.path:
            sys.path.insert(0, p)

from analyze_csi_dataset import parse_measurement_line
from csi_binary_detector import load_saved_model


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def extract_payload(line: str) -> Optional[str]:
    if "Measurement:" not in line:
        return None
    return line.split("Measurement:", 1)[1].strip()


def measurement_parts(line: str) -> list[str]:
    payload = extract_payload(line)
    if not payload:
        return []
    return [x.strip() for x in payload.split(",") if x.strip()]


def valid_csi_line(line: str, expected_tokens: int = 72) -> bool:
    parts = measurement_parts(line)
    if len(parts) != expected_tokens:
        return False
    if len(parts) >= 3 and parts[2] == "00:00:00:00:00:00":
        return False
    return True


def csi_timestamp(line: str) -> str:
    parts = measurement_parts(line)
    return parts[1] if len(parts) > 1 else "unknown"


def choose_stable_label(
    stable_label: Optional[str],
    raw_label: str,
    candidate_label: Optional[str],
    candidate_streak: int,
    recent_raw_labels: deque,
    confirm_count: int,
    vote_threshold: int,
) -> tuple[str, int]:
    counts = Counter(recent_raw_labels)
    best_label, best_count = counts.most_common(1)[0]

    if stable_label is None:
        return raw_label, best_count

    if candidate_label != stable_label and candidate_streak >= confirm_count:
        return candidate_label, counts.get(candidate_label, 0)

    current_count = counts.get(stable_label, 0)
    if best_label != stable_label and best_count >= vote_threshold and best_count > current_count:
        return best_label, best_count

    return stable_label, current_count


def follow_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(0, 2)

        while True:
            line = f.readline()
            if not line:
                time.sleep(0.05)
                continue
            yield line.rstrip("\n")


def main():
    parser = argparse.ArgumentParser(description="Live CSI predictor from externally streamed CSI file")
    parser.add_argument(
        "--input-file",
        type=Path,
        default=CSI_PROJECT_DIR / "realtime_inputs/live_csi_stream.txt",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=CSI_PROJECT_DIR / "analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib",
    )
    parser.add_argument(
        "--state-json",
        type=Path,
        default=CSI_PROJECT_DIR / "analysis_outputs/live_prediction_state.json",
    )
    parser.add_argument(
        "--predictions-csv",
        type=Path,
        default=CSI_PROJECT_DIR / "analysis_outputs/live_predictions_external_stream.csv",
    )
    parser.add_argument("--confirm-count", type=int, default=2)
    parser.add_argument("--vote-window", type=int, default=5)
    parser.add_argument("--vote-threshold", type=int, default=3)
    parser.add_argument("--expected-tokens", type=int, default=72)
    parser.add_argument("--max-invalid-log-every", type=float, default=5.0)
    args = parser.parse_args()

    if not args.model.exists():
        raise FileNotFoundError(f"No existe el modelo: {args.model}")

    model = load_saved_model(args.model)

    args.predictions_csv.parent.mkdir(parents=True, exist_ok=True)
    csv_fh = args.predictions_csv.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(
        csv_fh,
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
    csv_fh.flush()

    print(f"[CSI-LIVE] input={args.input_file}", flush=True)
    print(f"[CSI-LIVE] model={args.model}", flush=True)
    print(f"[CSI-LIVE] state_json={args.state_json}", flush=True)
    print(f"[CSI-LIVE] predictions_csv={args.predictions_csv}", flush=True)

    sample_index = 0
    invalid_count = 0
    last_invalid_log = 0.0

    stable_label: Optional[str] = None
    candidate_label: Optional[str] = None
    candidate_streak = 0
    recent_raw_labels = deque(maxlen=args.vote_window)

    atomic_write_json(
        args.state_json,
        {
            "status": "starting",
            "prediction": None,
            "raw_prediction": None,
            "timestamp": time.time(),
            "source": "external_csi_stream",
        },
    )

    try:
        for line in follow_file(args.input_file):
            now = time.time()
            wall_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

            if "Measurement:" not in line:
                continue

            if not valid_csi_line(line, args.expected_tokens):
                invalid_count += 1
                if now - last_invalid_log >= args.max_invalid_log_every:
                    parts = measurement_parts(line)
                    mac = parts[2] if len(parts) >= 3 else ""
                    print(
                        f"[{wall_time}] invalid_csi count={invalid_count} "
                        f"tokens={len(parts)} mac={mac} line={line[:140]}",
                        flush=True,
                    )
                    last_invalid_log = now
                continue

            sample_index += 1

            try:
                sample = parse_measurement_line(line.strip(), args.input_file, "live")
                raw_label, score = model.predict_sample(sample)

            except AttributeError:
                from csi_binary_detector import predict_with_saved_model
                sample = parse_measurement_line(line.strip(), args.input_file, "live")
                raw_label, score = predict_with_saved_model(model, sample)

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
                confirm_count=args.confirm_count,
                vote_threshold=args.vote_threshold,
            )

            ts = csi_timestamp(line)

            payload = {
                "status": "online",
                "sample_index": sample_index,
                "wall_time": wall_time,
                "prediction": stable_label,
                "raw_prediction": raw_label,
                "streak": candidate_streak,
                "stable_votes": stable_votes,
                "score": float(score),
                "csi_timestamp": ts,
                "timestamp": now,
                "source": "external_csi_stream",
                "invalid_count": invalid_count,
            }

            atomic_write_json(args.state_json, payload)

            writer.writerow(
                {
                    "sample_index": sample_index,
                    "wall_time": wall_time,
                    "prediction": stable_label,
                    "raw_prediction": raw_label,
                    "streak": candidate_streak,
                    "stable_votes": stable_votes,
                    "score": f"{float(score):.6f}",
                    "csi_timestamp": ts,
                    "raw_line": line,
                }
            )
            csv_fh.flush()

            print(
                f"[{wall_time}] sample={sample_index:04d} "
                f"prediction={stable_label} raw={raw_label} "
                f"streak={candidate_streak} votes={stable_votes} "
                f"score={float(score):.6f}",
                flush=True,
            )

    except KeyboardInterrupt:
        print("\n[CSI-LIVE] detenido por teclado", flush=True)
    finally:
        atomic_write_json(
            args.state_json,
            {
                "status": "offline",
                "prediction": stable_label,
                "timestamp": time.time(),
                "source": "external_csi_stream",
                "sample_index": sample_index,
                "invalid_count": invalid_count,
            },
        )
        csv_fh.close()


if __name__ == "__main__":
    main()
