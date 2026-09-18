#!/usr/bin/env python3
# -*- coding:utf-8 -*-

import argparse
import json
import os
import time

import cv2
import numpy as np
import torch
from loguru import logger

import pyrealsense2 as rs

from yolox.data.data_augment import ValTransform
from yolox.data.datasets import COCO_CLASSES
from yolox.exp import get_exp
from yolox.utils import fuse_model, postprocess, vis


def make_parser():
    parser = argparse.ArgumentParser("YOLOX RealSense Left-Half Demo")
    parser.add_argument("-expn", "--experiment-name", type=str, default=None)
    parser.add_argument("-n", "--name", type=str, default=None, help="model name")

    parser.add_argument(
        "-f",
        "--exp_file",
        default=None,
        type=str,
        help="experiment description file",
    )
    parser.add_argument("-c", "--ckpt", default=None, type=str, help="ckpt for eval")
    parser.add_argument(
        "--device",
        default="cpu",
        type=str,
        help="device to run our model, can be cpu or gpu",
    )
    parser.add_argument("--conf", default=0.3, type=float, help="test conf")
    parser.add_argument("--nms", default=0.3, type=float, help="test nms threshold")
    parser.add_argument("--tsize", default=None, type=int, help="test img size")
    parser.add_argument(
        "--fp16",
        dest="fp16",
        default=False,
        action="store_true",
        help="Adopting mix precision evaluating.",
    )
    parser.add_argument(
        "--legacy",
        dest="legacy",
        default=False,
        action="store_true",
        help="To be compatible with older versions",
    )
    parser.add_argument(
        "--fuse",
        dest="fuse",
        default=False,
        action="store_true",
        help="Fuse conv and bn for testing.",
    )

    parser.add_argument("--width", default=640, type=int, help="camera width")
    parser.add_argument("--height", default=480, type=int, help="camera height")
    parser.add_argument("--fps", default=30, type=int, help="camera fps")
    parser.add_argument(
        "--frame-timeout-ms",
        default=8000,
        type=int,
        help="milliseconds to wait for a RealSense frame before retrying",
    )
    parser.add_argument(
        "--max-frame-retries",
        default=3,
        type=int,
        help="consecutive frame timeouts before restarting the RealSense pipeline",
    )
    parser.add_argument(
        "--restart-wait-ms",
        default=2000,
        type=int,
        help="milliseconds to wait before retrying a failed RealSense restart",
    )
    parser.add_argument(
        "--json-out",
        default="/home/nextnet/AlbertoDir/go2_dt/camera_yolo/outputs/live_camera_state.json",
        type=str,
        help="path to write the live camera occupancy state JSON",
    )

    return parser


class Predictor(object):
    def __init__(
        self,
        model,
        exp,
        cls_names=COCO_CLASSES,
        device="cpu",
        fp16=False,
        legacy=False,
    ):
        self.model = model
        self.cls_names = cls_names
        self.num_classes = exp.num_classes
        self.confthre = exp.test_conf
        self.nmsthre = exp.nmsthre
        self.test_size = exp.test_size
        self.device = device
        self.fp16 = fp16
        self.preproc = ValTransform(legacy=legacy)

    def inference(self, img):
        img_info = {"id": 0}
        img_info["file_name"] = None

        height, width = img.shape[:2]
        img_info["height"] = height
        img_info["width"] = width
        img_info["raw_img"] = img

        ratio = min(self.test_size[0] / img.shape[0], self.test_size[1] / img.shape[1])
        img_info["ratio"] = ratio

        img, _ = self.preproc(img, None, self.test_size)
        img = torch.from_numpy(img).unsqueeze(0)
        img = img.float()
        if self.device == "gpu":
            img = img.cuda()
            if self.fp16:
                img = img.half()

        with torch.no_grad():
            t0 = time.time()
            outputs = self.model(img)
            outputs = postprocess(
                outputs,
                self.num_classes,
                self.confthre,
                self.nmsthre,
                class_agnostic=True,
            )
            logger.info("Infer time: {:.4f}s".format(time.time() - t0))
        return outputs, img_info


def filter_left_half_person(output, img_info, cls_conf):
    if output is None:
        return None, None, None

    output = output.cpu()
    ratio = img_info["ratio"]
    width = img_info["width"]

    bboxes = output[:, 0:4] / ratio
    cls = output[:, 6]
    scores = output[:, 4] * output[:, 5]

    centers_x = (bboxes[:, 0] + bboxes[:, 2]) / 2.0
    keep = scores >= cls_conf
    keep &= cls == 0
    keep &= centers_x < (width / 2.0)

    bboxes = bboxes[keep]
    scores = scores[keep]
    cls = cls[keep]

    if len(bboxes) == 0:
        return None, None, None

    # Keep only the largest visible person as a proxy for the closest one.
    areas = (bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1])
    best_idx = torch.argmax(areas)

    return (
        bboxes[best_idx : best_idx + 1],
        scores[best_idx : best_idx + 1],
        cls[best_idx : best_idx + 1],
    )


def get_connected_devices():
    ctx = rs.context()
    devices = []
    for device in ctx.query_devices():
        try:
            name = device.get_info(rs.camera_info.name)
        except RuntimeError:
            name = "Unknown RealSense"
        try:
            serial = device.get_info(rs.camera_info.serial_number)
        except RuntimeError:
            serial = "unknown-serial"
        devices.append(f"{name} ({serial})")
    return devices


def safe_stop_pipeline(pipeline, pipeline_started):
    if not pipeline_started:
        return False
    try:
        pipeline.stop()
        logger.info("RealSense stream stopped")
    except RuntimeError:
        logger.exception("RealSense pipeline stop failed")
        return False
    return False


def start_pipeline(pipeline, config, restart_wait_ms):
    while True:
        try:
            pipeline.start(config)
            logger.info("RealSense stream started")
            return True
        except RuntimeError as exc:
            devices = get_connected_devices()
            if devices:
                logger.warning(
                    "RealSense start failed: {}. Connected devices: {}. Retrying in {} ms",
                    exc,
                    ", ".join(devices),
                    restart_wait_ms,
                )
            else:
                logger.warning(
                    "RealSense start failed: {}. No camera detected. Retrying in {} ms",
                    exc,
                    restart_wait_ms,
                )
            time.sleep(restart_wait_ms / 1000.0)


def restart_pipeline(pipeline, config, pipeline_started, restart_wait_ms):
    logger.warning("Restarting RealSense pipeline after repeated frame timeouts")
    pipeline_started = safe_stop_pipeline(pipeline, pipeline_started)
    time.sleep(0.5)
    pipeline_started = start_pipeline(pipeline, config, restart_wait_ms)
    return pipeline_started


def write_state_json(output_path, payload):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    temp_path = f"{output_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=True, indent=2)
        fh.write("\n")
    os.replace(temp_path, output_path)


def build_state_payload(
    *,
    status,
    person_detected,
    person_count,
    frame_width,
    frame_height,
    detections,
    error=None,
):
    return {
        "status": status,
        "input_source": "realsense_color_yolox",
        "person_detected": bool(person_detected),
        "count": int(person_count),
        "detections": detections,
        "frame_width": int(frame_width),
        "frame_height": int(frame_height),
        "error": error,
        "timestamp": time.time(),
    }


def main():
    args = make_parser().parse_args()

    exp = get_exp(args.exp_file, args.name)
    if args.tsize is not None:
        exp.test_size = (args.tsize, args.tsize)
    exp.test_conf = args.conf
    exp.nmsthre = args.nms

    if not args.experiment_name:
        args.experiment_name = exp.exp_name

    model = exp.get_model()
    logger.info("Model Summary: {}".format(model))

    if args.device == "gpu":
        model.cuda()
        if args.fp16:
            model.half()

    model.eval()

    if args.ckpt is None:
        raise ValueError("--ckpt is required")

    ckpt = torch.load(args.ckpt, map_location="cpu")
    if "model" in ckpt:
        model.load_state_dict(ckpt["model"])
    else:
        model.load_state_dict(ckpt)

    if args.fuse:
        model = fuse_model(model)

    predictor = Predictor(
        model=model,
        exp=exp,
        cls_names=COCO_CLASSES,
        device=args.device,
        fp16=args.fp16,
        legacy=args.legacy,
    )

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)

    pipeline_started = start_pipeline(pipeline, config, args.restart_wait_ms)
    frame_retry_count = 0
    cv2.namedWindow("yolox-realsense", cv2.WINDOW_NORMAL)
    write_state_json(
        args.json_out,
        build_state_payload(
            status="starting",
            person_detected=False,
            person_count=0,
            frame_width=args.width,
            frame_height=args.height,
            detections=[],
        ),
    )

    try:
        while True:
            ok, frames = pipeline.try_wait_for_frames(args.frame_timeout_ms)
            if not ok:
                frame_retry_count += 1
                write_state_json(
                    args.json_out,
                    build_state_payload(
                        status="online",
                        person_detected=False,
                        person_count=0,
                        frame_width=args.width,
                        frame_height=args.height,
                        detections=[],
                        error=(
                            f"frame_timeout_{frame_retry_count}_of_{args.max_frame_retries}"
                        ),
                    ),
                )
                logger.warning(
                    "No frame arrived within {} ms ({}/{})",
                    args.frame_timeout_ms,
                    frame_retry_count,
                    args.max_frame_retries,
                )
                if frame_retry_count >= args.max_frame_retries:
                    pipeline_started = restart_pipeline(
                        pipeline,
                        config,
                        pipeline_started,
                        args.restart_wait_ms,
                    )
                    frame_retry_count = 0
                continue

            color_frame = frames.get_color_frame()
            if not color_frame:
                logger.warning("RealSense returned an empty color frame")
                write_state_json(
                    args.json_out,
                    build_state_payload(
                        status="online",
                        person_detected=False,
                        person_count=0,
                        frame_width=args.width,
                        frame_height=args.height,
                        detections=[],
                        error="empty_color_frame",
                    ),
                )
                continue
            frame_retry_count = 0
            frame = np.asanyarray(color_frame.get_data())

            outputs, img_info = predictor.inference(frame)
            output = outputs[0] if outputs is not None else None
            bboxes, scores, cls = filter_left_half_person(output, img_info, args.conf)

            detections = []
            person_detected = bboxes is not None
            if bboxes is not None:
                for bbox, score, cls_idx in zip(bboxes, scores, cls):
                    x1, y1, x2, y2 = [float(v) for v in bbox.tolist()]
                    detections.append(
                        {
                            "label": COCO_CLASSES[int(cls_idx.item())],
                            "score": float(score.item()),
                            "bbox_xyxy": [x1, y1, x2, y2],
                        }
                    )

            write_state_json(
                args.json_out,
                build_state_payload(
                    status="online",
                    person_detected=person_detected,
                    person_count=len(detections),
                    frame_width=frame.shape[1],
                    frame_height=frame.shape[0],
                    detections=detections,
                ),
            )

            if bboxes is None:
                result_frame = frame
            else:
                result_frame = vis(
                    frame,
                    bboxes,
                    scores,
                    cls,
                    args.conf,
                    COCO_CLASSES,
                )

            cv2.imshow("yolox-realsense", result_frame)
            ch = cv2.waitKey(1)
            if ch == 27 or ch == ord("q") or ch == ord("Q"):
                break
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        write_state_json(
            args.json_out,
            build_state_payload(
                status="offline",
                person_detected=False,
                person_count=0,
                frame_width=args.width,
                frame_height=args.height,
                detections=[],
            ),
        )
        safe_stop_pipeline(pipeline, pipeline_started)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
