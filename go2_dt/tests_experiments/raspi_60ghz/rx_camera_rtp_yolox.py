#!/usr/bin/env python3
# -*- coding: utf-8 -*-

GO2_ROOT = Path(__file__).resolve().parents[2]

import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def load_yolox(exp_file: str, ckpt: str, device: str, fp16: bool):
    yolox_root = Path(exp_file).resolve().parents[2]
    if str(yolox_root) not in sys.path:
        sys.path.insert(0, str(yolox_root))

    from yolox.exp import get_exp
    from yolox.utils import fuse_model

    exp = get_exp(exp_file, None)
    model = exp.get_model()
    model.eval()

    dev = torch.device("cuda" if device == "gpu" and torch.cuda.is_available() else "cpu")
    ckpt_data = torch.load(ckpt, map_location="cpu")

    state = ckpt_data["model"] if isinstance(ckpt_data, dict) and "model" in ckpt_data else ckpt_data
    model.load_state_dict(state, strict=False)
    model.to(dev)

    if fp16 and dev.type == "cuda":
        model.half()

    try:
        model = fuse_model(model)
    except Exception:
        pass

    return exp, model, dev


def preprocess_frame(frame, input_size):
    from yolox.data.data_augment import preproc
    img, ratio = preproc(frame, input_size)
    img = torch.from_numpy(img).unsqueeze(0).float()
    return img, ratio


def run_inference(exp, model, dev, frame, args):
    from yolox.utils import postprocess

    img, ratio = preprocess_frame(frame, (args.yolo_tsize, args.yolo_tsize))
    img = img.to(dev)

    if args.yolo_fp16 and dev.type == "cuda":
        img = img.half()

    with torch.no_grad():
        outputs = model(img)
        outputs = postprocess(
            outputs,
            num_classes=exp.num_classes,
            conf_thre=args.yolo_conf,
            nms_thre=args.yolo_nms,
            class_agnostic=True,
        )

    if outputs[0] is None:
        return []

    det = outputs[0].detach().float().cpu()
    bboxes = det[:, 0:4] / ratio
    obj_conf = det[:, 4]
    cls_conf = det[:, 5]
    cls = det[:, 6].int()
    scores = obj_conf * cls_conf

    h, w = frame.shape[:2]

    results = []
    for box, score, c in zip(bboxes, scores, cls):
        if int(c) != 0:
            continue

        x1, y1, x2, y2 = [float(v) for v in box.tolist()]
        bw = max(1.0, x2 - x1)
        bh = max(1.0, y2 - y1)

        center_x = (x1 + x2) / 2.0
        area_ratio = (bw * bh) / max(1.0, float(w * h))
        height_ratio = bh / max(1.0, float(h))
        aspect_ratio = bh / max(1.0, bw)

        if score < max(args.yolo_conf, args.person_min_score):
            continue
        if center_x >= w / 2.0:
            continue
        if height_ratio < args.person_min_height_ratio:
            continue
        if area_ratio < args.person_min_area_ratio:
            continue
        if aspect_ratio < args.person_min_aspect_ratio:
            continue

        results.append({
            "bbox": [x1, y1, x2, y2],
            "score": float(score),
            "area_ratio": float(area_ratio),
            "height_ratio": float(height_ratio),
            "aspect_ratio": float(aspect_ratio),
        })

    return results


def make_pipeline(port: int, latency_ms: int) -> str:
    return (
        f'udpsrc port={port} '
        'caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! '
        f'rtpjitterbuffer latency={latency_ms} drop-on-latency=false ! '
        'rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! '
        'video/x-raw,format=BGR ! appsink sync=false drop=true max-buffers=1'
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6000)
    ap.add_argument("--latency-ms", type=int, default=200)
    ap.add_argument("--json-out", default=str(GO2_ROOT / "camera_yolo" / "outputs" / "live_camera_state.json"))
    ap.add_argument("--window-name", default="Raspi camera RX + YOLO")
    ap.add_argument("--process-fps", type=float, default=5.0)

    ap.add_argument("--yolo-exp-file", default=str(GO2_ROOT / "camera_yolo" / "YOLOX" / "exps" / "default" / "yolox_s.py"))
    ap.add_argument("--yolo-ckpt", default=str(GO2_ROOT / "camera_yolo" / "Weights" / "yolox_s.pth"))
    ap.add_argument("--yolo-device", default="gpu", choices=["gpu", "cpu"])
    ap.add_argument("--yolo-conf", type=float, default=0.25)
    ap.add_argument("--yolo-nms", type=float, default=0.45)
    ap.add_argument("--yolo-tsize", type=int, default=256)
    ap.add_argument("--yolo-fp16", action="store_true")

    ap.add_argument("--person-min-score", type=float, default=0.55)
    ap.add_argument("--person-min-height-ratio", type=float, default=0.35)
    ap.add_argument("--person-min-area-ratio", type=float, default=0.04)
    ap.add_argument("--person-min-aspect-ratio", type=float, default=1.15)

    args = ap.parse_args()

    json_path = Path(args.json_out)

    print(f"[rx-yolo] Opening RTP/H264 port={args.port}")
    print(f"[rx-yolo] JSON out: {json_path}")
    print(f"[rx-yolo] Loading YOLOX: {args.yolo_ckpt}")

    exp, model, dev = load_yolox(args.yolo_exp_file, args.yolo_ckpt, args.yolo_device, args.yolo_fp16)
    print(f"[rx-yolo] YOLO ready | device={dev} | tsize={args.yolo_tsize}")

    pipeline = make_pipeline(args.port, args.latency_ms)
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

    if not cap.isOpened():
        raise RuntimeError(f"No pude abrir pipeline GStreamer: {pipeline}")

    last_infer = 0.0
    last_dets = []
    frame_count = 0

    atomic_write_json(json_path, {
        "status": "online",
        "source": "raspi_rtp",
        "person_detected": False,
        "count": 0,
        "timestamp": time.time(),
    })

    while True:
        ok, frame = cap.read()
        now = time.time()

        if not ok or frame is None:
            atomic_write_json(json_path, {
                "status": "stale",
                "source": "raspi_rtp",
                "person_detected": False,
                "count": 0,
                "timestamp": now,
                "error": "no_frame",
            })
            time.sleep(0.05)
            continue

        frame_count += 1

        if now - last_infer >= 1.0 / max(0.1, args.process_fps):
            try:
                last_dets = run_inference(exp, model, dev, frame, args)
                person = len(last_dets) > 0

                atomic_write_json(json_path, {
                    "status": "online",
                    "source": "raspi_rtp",
                    "person_detected": bool(person),
                    "count": int(len(last_dets)),
                    "timestamp": now,
                    "frame_count": int(frame_count),
                    "detections": last_dets,
                })

                print(f"[rx-yolo] person_detected={person} count={len(last_dets)}", flush=True)
            except Exception as exc:
                atomic_write_json(json_path, {
                    "status": "inference_error",
                    "source": "raspi_rtp",
                    "person_detected": False,
                    "count": 0,
                    "timestamp": now,
                    "error": str(exc),
                })
                print(f"[rx-yolo][ERROR] {exc}", flush=True)

            last_infer = now

        vis = frame.copy()
        for det in last_dets:
            x1, y1, x2, y2 = [int(round(v)) for v in det["bbox"]]
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(vis, f"person {det['score']:.2f}", (x1, max(20, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        cv2.putText(vis, f"Raspi RTP YOLO | person={len(last_dets)>0} count={len(last_dets)}",
                    (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        cv2.imshow(args.window_name, vis)
        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q"), ord("Q")):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
