#!/usr/bin/env python3
"""
Demo launcher:
  1) RX viewer on PC: UDP/RTP/H264 port 5002 -> autovideosink + FPS overlay
  2) TX sender on PC: MP4 -> 720p15 H264 CBR 20 Mbps -> AP:5000
  3) Optional local USB-camera YOLO window at 120p
  4) Local interface rate monitor

The MP4 TX/RX path is kept equivalent to the manual pipeline that was verified to work.
The USB YOLO path is local-only: it does not consume the 60 GHz link.
"""

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple


def run_cmd(cmd: str, timeout: float = 5.0) -> Tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def read_interface_bytes_linux(iface: str) -> Tuple[int, int]:
    with open("/proc/net/dev", "r", encoding="utf-8") as f:
        for line in f:
            if ":" not in line:
                continue
            name, data = line.split(":", 1)
            if name.strip() == iface:
                fields = data.split()
                rx_bytes = int(fields[0])
                tx_bytes = int(fields[8])
                return tx_bytes, rx_bytes
    raise RuntimeError(f"No se encontró la interfaz {iface}")


def atomic_write_json(path: Path, payload: dict) -> None:
    """
    Thread/process-safe-ish JSON writer.

    The previous version always used:
        live_camera_state.json.tmp

    In this demo, the camera loop and the YOLO worker can both write the same
    JSON state file. If both writers used the same .tmp path, one writer could
    replace/remove it while the other was still trying to replace it, causing:

        FileNotFoundError: ... live_camera_state.json.tmp -> live_camera_state.json

    Use a unique temporary file per write and os.replace() for atomic publish.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_name(
        f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}"
    )

    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def build_camera_state(
    *,
    status: str,
    input_source: str,
    person_detected: bool,
    count: int,
    frame_width: int,
    frame_height: int,
    detections: list,
    error: Optional[str] = None,
) -> dict:
    return {
        "status": status,
        "input_source": input_source,
        "person_detected": bool(person_detected),
        "count": int(count),
        "detections": detections,
        "frame_width": int(frame_width),
        "frame_height": int(frame_height),
        "error": error,
        "timestamp": time.time(),
    }


def build_mp4_sender_pipeline(args: argparse.Namespace) -> str:
    """
    MP4 -> 720p15 -> H264 CBR -> RTP/UDP -> AP:5000.

    Equivalent to the manual TX pipeline that worked:
      - identity sync=true before the encoder
      - small leaky queue before x264enc
      - udpsink sync=false async=false
    """
    verbosity = "-v" if args.gst_verbose else "-q"

    return (
        f"gst-launch-1.0 {verbosity} "
        f"filesrc location={shlex.quote(str(args.video_file))} ! "
        f"qtdemux name=demux "
        f"demux.video_0 ! queue ! decodebin ! "
        f"videoconvert ! videoscale ! videorate ! "
        f"video/x-raw,width={args.tx_width},height={args.tx_height},framerate={args.tx_fps}/1 ! "
        f"identity sync=true ! "
        f"queue max-size-buffers=8 leaky=downstream ! "
        f"x264enc "
        f"tune=zerolatency "
        f"pass=cbr "
        f"bitrate={args.bitrate_kbps} "
        f"speed-preset=ultrafast "
        f"key-int-max={args.tx_fps} "
        f"bframes=0 "
        f"byte-stream=true "
        f"sliced-threads=true "
        f"vbv-buf-capacity={args.vbv_ms} "
        f"option-string=\"nal-hrd=cbr:force-cfr=1\" ! "
        f"h264parse config-interval=1 ! "
        f"rtph264pay config-interval=1 pt=96 mtu={args.rtp_mtu} ! "
        f"udpsink host={args.ap_eth_ip} port={args.ap_eth_port} sync=false async=false"
    )


def build_mp4_receiver_pipeline(args: argparse.Namespace) -> str:
    """RX viewer equivalent to the manual receiver that displayed frames reliably."""
    verbosity = "-v" if args.gst_verbose else "-q"

    return (
        f"gst-launch-1.0 {verbosity} "
        f"udpsrc port={args.pc_rx_port} "
        f'caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! '
        f"rtpjitterbuffer latency={args.buffer_ms} drop-on-latency=false ! "
        f"rtph264depay ! "
        f"h264parse ! "
        f"avdec_h264 ! "
        f"videoconvert ! "
        f"fpsdisplaysink video-sink=autovideosink sync=false text-overlay=true"
    )


def start_process(cmd: str, *, verbose: bool) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        shell=True,
        stdout=None if verbose else subprocess.DEVNULL,
        stderr=None if verbose else subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )


def terminate_processes(procs: List[subprocess.Popen]) -> None:
    for p in procs:
        try:
            if p.poll() is None:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            pass

    time.sleep(0.5)

    for p in procs:
        try:
            if p.poll() is None:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass


class LocalYoloEngine:
    """
    YOLOX person detector copied from the validated combined script logic:
    - YOLOX experiment + checkpoint
    - only COCO class person
    - only left half of the image
    - keep the largest person box
    """

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.input_source = "local_usb_camera_yolox_120p"
        self.error: Optional[str] = None

        import sys

        # Importar torch/torchvision ANTES de meter los paths de YOLOX.
        # Si no, puede coger una torchvision incompatible desde camera_yolo/yolox
        # y fallar con: operator torchvision::nms does not exist
        import torch
        try:
            import torchvision
            from torchvision.ops import nms as _torchvision_nms
        except Exception as exc:
            print(f"[YOLO][WARN] preload torchvision falló: {exc}", flush=True)

        project_root = Path(__file__).resolve().parent
        camera_yolo_dir = project_root / "camera_yolo"
        yolox_root = camera_yolo_dir / "YOLOX"
        yolox_site_packages = (
            camera_yolo_dir
            / "yolox"
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages"
        )

        for extra_path in (yolox_site_packages, yolox_root, project_root):
            if extra_path.exists():
                p = str(extra_path)
                if p not in sys.path:
                    sys.path.insert(0, p)

        from yolox.data.data_augment import ValTransform
        from yolox.data.datasets import COCO_CLASSES
        from yolox.exp import get_exp
        from yolox.utils import fuse_model, postprocess, vis

        self.torch = torch
        self.ValTransform = ValTransform
        self.COCO_CLASSES = COCO_CLASSES
        self.postprocess = postprocess
        self.vis = vis

        requested_device = args.yolo_device
        if requested_device == "gpu" and not torch.cuda.is_available():
            print("[YOLO][WARN] CUDA no disponible; YOLO usará CPU", flush=True)
            requested_device = "cpu"

        if requested_device == "gpu":
            try:
                torch.backends.cudnn.benchmark = True
            except Exception:
                pass

        exp = get_exp(str(args.yolo_exp_file), None)
        exp.test_conf = args.yolo_conf
        exp.nmsthre = args.yolo_nms
        exp.test_size = (args.yolo_tsize, args.yolo_tsize)

        model = exp.get_model()

        ckpt = torch.load(str(args.yolo_ckpt), map_location="cpu")
        if "model" in ckpt:
            model.load_state_dict(ckpt["model"])
        else:
            model.load_state_dict(ckpt)

        if requested_device == "gpu":
            model.cuda()
            if args.yolo_fp16:
                model.half()

        model.eval()

        if args.yolo_fuse:
            model = fuse_model(model)

        self.model = model
        self.exp = exp
        self.device = requested_device
        self.preproc = ValTransform(legacy=False)

        print(
            f"[YOLO] listo | device={self.device} | input_size={exp.test_size} | fp16={args.yolo_fp16}",
            flush=True,
        )

    def inference(self, frame):
        import torch

        img_info = {
            "height": frame.shape[0],
            "width": frame.shape[1],
            "raw_img": frame,
            "file_name": None,
        }

        ratio = min(self.exp.test_size[0] / frame.shape[0], self.exp.test_size[1] / frame.shape[1])
        img_info["ratio"] = ratio

        img, _ = self.preproc(frame, None, self.exp.test_size)
        img = torch.from_numpy(img).unsqueeze(0).float()

        if self.device == "gpu":
            img = img.cuda(non_blocking=True)
            if self.args.yolo_fp16:
                img = img.half()

        with torch.inference_mode():
            outputs = self.model(img)
            outputs = self.postprocess(
                outputs,
                self.exp.num_classes,
                self.exp.test_conf,
                self.exp.nmsthre,
                class_agnostic=True,
            )

        return outputs, img_info

    def filter_left_half_person(self, output, img_info):
        if output is None:
            return None, None, None

        torch = self.torch
        output = output.cpu()
        ratio = img_info["ratio"]
        width = img_info["width"]

        bboxes = output[:, 0:4] / ratio
        cls = output[:, 6]
        scores = output[:, 4] * output[:, 5]
        centers_x = (bboxes[:, 0] + bboxes[:, 2]) / 2.0

        bbox_w = bboxes[:, 2] - bboxes[:, 0]
        bbox_h = bboxes[:, 3] - bboxes[:, 1]
        area_ratio = (bbox_w * bbox_h) / max(1.0, float(img_info["width"] * img_info["height"]))
        height_ratio = bbox_h / max(1.0, float(img_info["height"]))
        aspect_ratio = bbox_h / torch.clamp(bbox_w, min=1.0)

        # Person filter for the demo:
        # - class must be COCO person
        # - detection must be in the right half
        # - score/shape must look like an upright human, not the Go2/dog robot
        keep = scores >= max(self.args.yolo_conf, self.args.yolo_person_min_score)
        keep &= cls == 0
        keep &= centers_x > (width / 2.0)
        keep &= height_ratio >= self.args.yolo_person_min_height_ratio
        keep &= area_ratio >= self.args.yolo_person_min_area_ratio
        keep &= aspect_ratio >= self.args.yolo_person_min_aspect_ratio

        bboxes = bboxes[keep]
        scores = scores[keep]
        cls = cls[keep]

        if len(bboxes) == 0:
            return None, None, None

        areas = (bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1])
        best_idx = torch.argmax(areas)

        return (
            bboxes[best_idx: best_idx + 1],
            scores[best_idx: best_idx + 1],
            cls[best_idx: best_idx + 1],
        )

    def process_frame(self, frame):
        outputs, img_info = self.inference(frame)
        output = outputs[0] if outputs is not None else None
        bboxes, scores, cls = self.filter_left_half_person(output, img_info)

        detections = []
        person_detected = bboxes is not None

        result_frame = frame.copy()

        if bboxes is not None:
            import cv2

            h, w = result_frame.shape[:2]

            for bbox, score, cls_idx in zip(bboxes, scores, cls):
                x1, y1, x2, y2 = [float(v) for v in bbox.tolist()]
                label = self.COCO_CLASSES[int(cls_idx.item())]
                score_f = float(score.item())

                detections.append(
                    {
                        "label": label,
                        "score": score_f,
                        "bbox_xyxy": [x1, y1, x2, y2],
                    }
                )

                # Clamp a imagen.
                x1i = max(0, min(w - 1, int(round(x1))))
                y1i = max(0, min(h - 1, int(round(y1))))
                x2i = max(0, min(w - 1, int(round(x2))))
                y2i = max(0, min(h - 1, int(round(y2))))

                # Estilo YOLO fino y visible.
                box_color = (0, 255, 0)
                text_color = (255, 255, 255)
                bg_color = (0, 140, 0)

                thickness = max(1, int(round(min(w, h) / 220)))
                font_scale = max(0.45, min(0.8, w / 900.0))
                font_thickness = max(1, thickness)

                cv2.rectangle(
                    result_frame,
                    (x1i, y1i),
                    (x2i, y2i),
                    box_color,
                    thickness,
                    lineType=cv2.LINE_AA,
                )

                text = f"{label} {score_f * 100:.0f}%"
                (tw, th), baseline = cv2.getTextSize(
                    text,
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    font_thickness,
                )

                label_y1 = max(0, y1i - th - baseline - 6)
                label_y2 = max(th + baseline + 6, y1i)
                label_x2 = min(w - 1, x1i + tw + 8)

                cv2.rectangle(
                    result_frame,
                    (x1i, label_y1),
                    (label_x2, label_y2),
                    bg_color,
                    -1,
                    lineType=cv2.LINE_AA,
                )

                cv2.putText(
                    result_frame,
                    text,
                    (x1i + 4, label_y2 - baseline - 3),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    text_color,
                    font_thickness,
                    cv2.LINE_AA,
                )

        atomic_write_json(
            self.args.camera_json_out,
            build_camera_state(
                status="online",
                input_source=self.input_source,
                person_detected=person_detected,
                count=len(detections),
                frame_width=frame.shape[1],
                frame_height=frame.shape[0],
                detections=detections,
            ),
        )

        return result_frame, person_detected, len(detections)



def run_local_yolo_camera_loop(args: argparse.Namespace, stop_flag: dict) -> None:
    """
    Local USB camera -> ventana OpenCV siempre visible.
    YOLO se carga/procesa en segundo plano para no bloquear la cámara.
    """
    import cv2
    import threading
    import numpy as np

    cap = None

    shared = {
        "latest_frame": None,
        "last_yolo_frame": None,
        "last_yolo_ts": 0.0,
        "detected": False,
        "count": 0,
        "status": "starting_camera",
        "error": None,
    }
    lock = threading.Lock()

    def set_status(status, error=None):
        with lock:
            shared["status"] = status
            shared["error"] = error

    def yolo_worker():
        try:
            set_status("loading_yolo")
            print("[YOLO] cargando modelo en background...", flush=True)
            engine = LocalYoloEngine(args)
            set_status("yolo_ready")
            print("[YOLO] modelo listo; empezando inferencia background", flush=True)

            frame_period = 1.0 / max(0.1, float(args.yolo_process_fps))
            last_process = 0.0
            last_log = 0.0

            while not stop_flag["stop"]:
                now = time.time()

                if now - last_process < frame_period:
                    time.sleep(0.02)
                    continue

                with lock:
                    frame = None if shared["latest_frame"] is None else shared["latest_frame"].copy()

                if frame is None:
                    time.sleep(0.02)
                    continue

                last_process = now

                try:
                    result_frame, detected, count = engine.process_frame(frame)

                    with lock:
                        shared["last_yolo_frame"] = result_frame
                        shared["last_yolo_ts"] = time.time()
                        shared["detected"] = bool(detected)
                        shared["count"] = int(count)
                        shared["status"] = "online"
                        shared["error"] = None

                    if now - last_log >= 1.0:
                        print(f"[YOLO] person_detected={detected} count={count}", flush=True)
                        last_log = now

                except Exception as exc:
                    msg = str(exc)
                    print(f"[YOLO][WARN] inferencia falló: {msg}", flush=True)
                    set_status("inference_error", msg)
                    time.sleep(0.2)

        except Exception as exc:
            msg = str(exc)
            print(f"[YOLO][ERROR] worker: {msg}", flush=True)
            set_status("fault", msg)

            try:
                atomic_write_json(
                    args.camera_json_out,
                    build_camera_state(
                        status="fault",
                        input_source="local_usb_camera_yolox_240p",
                        person_detected=False,
                        count=0,
                        frame_width=args.yolo_camera_width,
                        frame_height=args.yolo_camera_height,
                        detections=[],
                        error=msg,
                    ),
                )
            except Exception:
                pass

    try:
        atomic_write_json(
            args.camera_json_out,
            build_camera_state(
                status="starting",
                input_source="local_usb_camera_yolox_240p",
                person_detected=False,
                count=0,
                frame_width=args.yolo_camera_width,
                frame_height=args.yolo_camera_height,
                detections=[],
            ),
        )

        print(
            f"[CAMERA] abriendo {args.yolo_camera_device} "
            f"{args.yolo_camera_width}x{args.yolo_camera_height}@{args.yolo_camera_fps}",
            flush=True,
        )

        cap = cv2.VideoCapture(args.yolo_camera_device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError(f"No se pudo abrir cámara YOLO: {args.yolo_camera_device}")

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.yolo_camera_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.yolo_camera_height)
        cap.set(cv2.CAP_PROP_FPS, args.yolo_camera_fps)

        cv2.namedWindow(args.yolo_window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(args.yolo_window_name, args.yolo_window_width, args.yolo_window_height)

        print(
            f"[CAMERA] ventana activa: {args.yolo_window_name} | "
            f"target={args.yolo_camera_bitrate_kbps/1000:.1f} Mbps local demo",
            flush=True,
        )

        worker = threading.Thread(target=yolo_worker, daemon=True)
        worker.start()

        last_json = 0.0

        while not stop_flag["stop"]:
            ok, frame = cap.read()

            if not ok or frame is None:
                set_status("camera_read_error", "cap.read() returned false")
                time.sleep(0.03)
                continue

            with lock:
                shared["latest_frame"] = frame.copy()
                status = shared["status"]
                error = shared["error"]
                detected = shared["detected"]
                count = shared["count"]
                yolo_frame = shared["last_yolo_frame"]
                yolo_age = time.time() - shared["last_yolo_ts"] if shared["last_yolo_ts"] else 999.0

            # Cámara en directo con cajas YOLO cuando haya frame anotado reciente.
            # Si YOLO tarda, se mantiene el vídeo fluido mostrando frame crudo.
            max_yolo_age = max(0.75, 2.0 / max(0.1, float(args.yolo_process_fps)))

            if yolo_frame is not None and yolo_age <= max_yolo_age:
                shown = yolo_frame.copy()
            else:
                shown = frame.copy()

            label = f"CAMERA LIVE | YOLO={status} | person={detected} count={count}"
            if error:
                label += f" | err={error[:45]}"

            cv2.rectangle(shown, (0, 0), (shown.shape[1], 24), (0, 0, 0), -1)
            cv2.putText(
                shown,
                label,
                (6, 17),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

            cv2.imshow(args.yolo_window_name, shown)

            now = time.time()
            if now - last_json >= 1.0:
                atomic_write_json(
                    args.camera_json_out,
                    build_camera_state(
                        status=status,
                        input_source="local_usb_camera_yolox_240p",
                        person_detected=detected,
                        count=count,
                        frame_width=frame.shape[1],
                        frame_height=frame.shape[0],
                        detections=[],
                        error=error,
                    ),
                )
                last_json = now

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                stop_flag["stop"] = True
                break

        atomic_write_json(
            args.camera_json_out,
            build_camera_state(
                status="offline",
                input_source="local_usb_camera_yolox_240p",
                person_detected=False,
                count=0,
                frame_width=args.yolo_camera_width,
                frame_height=args.yolo_camera_height,
                detections=[],
            ),
        )

    except Exception as exc:
        print(f"[CAMERA][ERROR] {exc}", flush=True)
        try:
            atomic_write_json(
                args.camera_json_out,
                build_camera_state(
                    status="fault",
                    input_source="local_usb_camera_yolox_240p",
                    person_detected=False,
                    count=0,
                    frame_width=args.yolo_camera_width,
                    frame_height=args.yolo_camera_height,
                    detections=[],
                    error=str(exc),
                ),
            )
        except Exception:
            pass

    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass

        try:
            cv2.destroyWindow(args.yolo_window_name)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="MP4 TX/RX 60GHz + optional local USB YOLO demo")

    parser.add_argument("--video-file", type=Path, required=True)
    parser.add_argument("--local-iface", default="enP7s7")
    parser.add_argument("--local-video-ip", default="192.168.1.170")
    parser.add_argument("--ap-eth-ip", default="192.168.1.13")
    parser.add_argument("--ap-eth-port", type=int, default=5000)
    parser.add_argument("--pc-rx-port", type=int, default=5002)

    parser.add_argument("--tx-width", type=int, default=1280)
    parser.add_argument("--tx-height", type=int, default=720)
    parser.add_argument("--tx-fps", type=int, default=15)
    parser.add_argument("--bitrate-kbps", type=int, default=20000)
    parser.add_argument("--buffer-ms", type=int, default=500)
    parser.add_argument("--rtp-mtu", type=int, default=1200)
    parser.add_argument("--vbv-ms", type=int, default=1000)

    parser.add_argument("--enable-yolo-camera", action="store_true")
    parser.add_argument("--only-yolo-camera", action="store_true")
    parser.add_argument("--yolo-camera-device", default="/dev/video6")
    parser.add_argument("--yolo-camera-width", type=int, default=160)
    parser.add_argument("--yolo-camera-height", type=int, default=120)
    parser.add_argument("--yolo-camera-fps", type=int, default=15)
    parser.add_argument("--yolo-process-fps", type=float, default=5.0)
    parser.add_argument("--yolo-camera-bitrate-kbps", type=int, default=2000)
    parser.add_argument("--camera-json-out", type=Path, default=Path("camera_yolo/outputs/live_camera_state.json"))
    parser.add_argument("--yolo-exp-file", type=Path, default=Path("camera_yolo/YOLOX/exps/default/yolox_s.py"))
    parser.add_argument("--yolo-ckpt", type=Path, default=Path("camera_yolo/Weights/yolox_s.pth"))
    parser.add_argument("--yolo-device", default="gpu", choices=["cpu", "gpu"])
    parser.add_argument("--yolo-conf", type=float, default=0.25)
    parser.add_argument("--yolo-nms", type=float, default=0.45)
    parser.add_argument("--yolo-person-min-score", type=float, default=0.55)
    parser.add_argument("--yolo-person-min-height-ratio", type=float, default=0.35)
    parser.add_argument("--yolo-person-min-area-ratio", type=float, default=0.04)
    parser.add_argument("--yolo-person-min-aspect-ratio", type=float, default=1.15)
    parser.add_argument("--yolo-tsize", type=int, default=512)
    parser.add_argument("--yolo-fp16", action="store_true")
    parser.add_argument("--yolo-fuse", action="store_true")
    parser.add_argument("--yolo-window-name", default="usb-yolo-120p")
    parser.add_argument("--yolo-window-width", type=int, default=640)
    parser.add_argument("--yolo-window-height", type=int, default=480)

    parser.add_argument("--gst-verbose", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if not args.video_file.exists():
        print(f"[ERROR] No existe el vídeo: {args.video_file}", flush=True)
        return 1

    project_root = Path(__file__).resolve().parent
    for attr in ("camera_json_out", "yolo_exp_file", "yolo_ckpt"):
        value = getattr(args, attr)
        if not value.is_absolute():
            setattr(args, attr, project_root / value)

    if args.enable_yolo_camera:
        for required in (args.yolo_exp_file, args.yolo_ckpt):
            if not required.exists():
                print(f"[ERROR] No existe fichero YOLO requerido: {required}", flush=True)
                return 1

    stop_flag = {"stop": False}
    procs: List[subprocess.Popen] = []
    threads: List[threading.Thread] = []

    # Modo worker: ejecuta solo la cámara YOLO en el proceso principal.
    # Esto evita problemas de cv2.imshow() dentro de threads secundarios.
    if args.only_yolo_camera:
        print("[YOLO-WORKER] arrancando cámara YOLO en proceso independiente", flush=True)
        run_local_yolo_camera_loop(args, stop_flag)
        return 0

    def handle_signal(signum, frame):
        stop_flag["stop"] = True
        print(f"[VIDEO] Señal recibida: {signum}", flush=True)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        print("[VIDEO] Ruta hacia AP:", flush=True)
        rc, out, err = run_cmd(f"ip route get {shlex.quote(args.ap_eth_ip)}", timeout=3)
        print(out or err, flush=True)

        rx_cmd = build_mp4_receiver_pipeline(args)
        tx_cmd = build_mp4_sender_pipeline(args)

        print("[VIDEO] RX pipeline:", rx_cmd, flush=True)
        rx_proc = start_process(rx_cmd, verbose=args.verbose or args.gst_verbose)
        procs.append(rx_proc)

        time.sleep(1.0)

        print("[VIDEO] TX pipeline:", tx_cmd, flush=True)
        sender_proc = start_process(tx_cmd, verbose=args.verbose or args.gst_verbose)
        procs.append(sender_proc)

        if args.enable_yolo_camera:
            yolo_cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--only-yolo-camera",
                "--video-file", str(args.video_file),
                "--yolo-camera-device", str(args.yolo_camera_device),
                "--yolo-camera-width", str(args.yolo_camera_width),
                "--yolo-camera-height", str(args.yolo_camera_height),
                "--yolo-camera-fps", str(args.yolo_camera_fps),
                "--yolo-process-fps", str(args.yolo_process_fps),
                "--yolo-camera-bitrate-kbps", str(args.yolo_camera_bitrate_kbps),
                "--camera-json-out", str(args.camera_json_out),
                "--yolo-exp-file", str(args.yolo_exp_file),
                "--yolo-ckpt", str(args.yolo_ckpt),
                "--yolo-device", str(args.yolo_device),
                "--yolo-conf", str(args.yolo_conf),
                "--yolo-nms", str(args.yolo_nms),
                "--yolo-person-min-score", str(args.yolo_person_min_score),
                "--yolo-person-min-height-ratio", str(args.yolo_person_min_height_ratio),
                "--yolo-person-min-area-ratio", str(args.yolo_person_min_area_ratio),
                "--yolo-person-min-aspect-ratio", str(args.yolo_person_min_aspect_ratio),
                "--yolo-tsize", str(args.yolo_tsize),
                "--yolo-window-name", str(args.yolo_window_name),
                "--yolo-window-width", str(args.yolo_window_width),
                "--yolo-window-height", str(args.yolo_window_height),
            ]

            if args.yolo_fp16:
                yolo_cmd.append("--yolo-fp16")
            if args.yolo_fuse:
                yolo_cmd.append("--yolo-fuse")
            if args.verbose:
                yolo_cmd.append("--verbose")

            print("[YOLO] lanzando worker independiente:", " ".join(yolo_cmd), flush=True)

            yolo_proc = subprocess.Popen(
                yolo_cmd,
                stdout=None if args.verbose else subprocess.DEVNULL,
                stderr=None if args.verbose else subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            procs.append(yolo_proc)

        last_txrx = read_interface_bytes_linux(args.local_iface)
        last_t = time.time()

        print("", flush=True)
        print("============================================================", flush=True)
        print("[VIDEO] Demo vídeo MP4 60 GHz + YOLO USB local", flush=True)
        print(f"[VIDEO] Vídeo: {args.video_file}", flush=True)
        print(f"[VIDEO] TX MP4: {args.tx_width}x{args.tx_height}@{args.tx_fps}, {args.bitrate_kbps/1000:.1f} Mbps", flush=True)
        print(f"[VIDEO] UDP/RTP: PC -> {args.ap_eth_ip}:{args.ap_eth_port} -> PC:{args.pc_rx_port}", flush=True)
        if args.enable_yolo_camera:
            print(
                f"[YOLO] USB local: {args.yolo_camera_device} "
                f"{args.yolo_camera_width}x{args.yolo_camera_height}@{args.yolo_camera_fps}, "
                f"modelo={args.yolo_ckpt}",
                flush=True,
            )
        print("============================================================", flush=True)
        print("", flush=True)

        while not stop_flag["stop"]:
            time.sleep(1.0)

            if sender_proc.poll() is not None:
                print("[VIDEO][WARN] TX MP4 terminó. Relanzando para bucle.", flush=True)
                sender_proc = start_process(tx_cmd, verbose=args.verbose or args.gst_verbose)
                procs.append(sender_proc)

            if rx_proc.poll() is not None:
                print("[VIDEO][WARN] RX terminó. Relanzando visor.", flush=True)
                rx_proc = start_process(rx_cmd, verbose=args.verbose or args.gst_verbose)
                procs.append(rx_proc)

            now = time.time()
            txrx = read_interface_bytes_linux(args.local_iface)
            dt = now - last_t

            if dt > 0:
                tx_mbps = (txrx[0] - last_txrx[0]) * 8.0 / dt / 1_000_000.0
                rx_mbps = (txrx[1] - last_txrx[1]) * 8.0 / dt / 1_000_000.0
                print(f"[RATE] iface={args.local_iface} TX={tx_mbps:.1f} Mbps RX={rx_mbps:.1f} Mbps", flush=True)

            last_txrx = txrx
            last_t = now

        return 0

    finally:
        stop_flag["stop"] = True
        terminate_processes(procs)
        for t in threads:
            t.join(timeout=2.0)


if __name__ == "__main__":
    raise SystemExit(main())
