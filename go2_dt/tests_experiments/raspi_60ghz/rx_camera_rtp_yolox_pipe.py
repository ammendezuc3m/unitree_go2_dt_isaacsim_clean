#!/usr/bin/env python3
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np


COCO_PERSON_CLASS = 0


def atomic_write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def build_camera_state(status, person_detected, count, frame_width, frame_height, detections, error=None):
    return {
        "status": status,
        "input_source": "raspi_rtp_h264_pipe",
        "person_detected": bool(person_detected),
        "count": int(count),
        "detections": detections,
        "frame_width": int(frame_width),
        "frame_height": int(frame_height),
        "error": error,
        "timestamp": time.time(),
    }


class YoloXEngine:
    def __init__(self, args):
        # Importar torch/torchvision ANTES de meter paths de YOLOX.
        # Si no, Python puede coger camera_yolo/yolox/lib/.../torchvision,
        # que en esta Spark falla con: operator torchvision::nms does not exist.
        import torch
        try:
            import torchvision
            from torchvision.ops import nms as _torchvision_nms
            print(f"[rx-yolo] torchvision preload OK: {getattr(torchvision, '__version__', 'unknown')}", flush=True)
        except Exception as exc:
            print(f"[rx-yolo][WARN] torchvision preload falló: {exc}", flush=True)

        project_root = Path(__file__).resolve().parents[2]
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
            print("[rx-yolo][WARN] CUDA no disponible; usando CPU", flush=True)
            requested_device = "cpu"

        exp = get_exp(str(args.yolo_exp_file), None)
        exp.test_conf = args.yolo_conf
        exp.nmsthre = args.yolo_nms
        exp.test_size = (args.yolo_tsize, args.yolo_tsize)

        model = exp.get_model()
        ckpt = torch.load(str(args.yolo_ckpt), map_location="cpu")
        model.load_state_dict(ckpt["model"] if "model" in ckpt else ckpt)

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
        self.args = args

        print(f"[rx-yolo] YOLO ready | device={self.device} | tsize={args.yolo_tsize}", flush=True)

    def inference(self, frame):
        torch = self.torch

        img_info = {
            "height": frame.shape[0],
            "width": frame.shape[1],
            "raw_img": frame,
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

    def process_frame(self, frame):
        torch = self.torch

        outputs, img_info = self.inference(frame)
        output = outputs[0] if outputs is not None else None

        if output is None:
            return frame, False, 0, []

        output = output.cpu()
        ratio = img_info["ratio"]
        width = img_info["width"]

        bboxes = output[:, 0:4] / ratio
        cls = output[:, 6]
        scores = output[:, 4] * output[:, 5]

        centers_x = (bboxes[:, 0] + bboxes[:, 2]) / 2.0
        bbox_w = bboxes[:, 2] - bboxes[:, 0]
        bbox_h = bboxes[:, 3] - bboxes[:, 1]

        area_ratio = (bbox_w * bbox_h) / max(1.0, float(frame.shape[0] * frame.shape[1]))
        height_ratio = bbox_h / max(1.0, float(frame.shape[0]))
        aspect_ratio = bbox_h / torch.clamp(bbox_w, min=1.0)

        keep = scores >= max(self.args.yolo_conf, self.args.yolo_person_min_score)
        keep &= cls == COCO_PERSON_CLASS
        keep &= centers_x < (width / 2.0)
        keep &= height_ratio >= self.args.yolo_person_min_height_ratio
        keep &= area_ratio >= self.args.yolo_person_min_area_ratio
        keep &= aspect_ratio >= self.args.yolo_person_min_aspect_ratio

        bboxes = bboxes[keep]
        scores = scores[keep]
        cls = cls[keep]

        detections = []

        if len(bboxes) == 0:
            return frame, False, 0, detections

        areas = (bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1])
        best_idx = torch.argmax(areas)

        bboxes = bboxes[best_idx:best_idx + 1]
        scores = scores[best_idx:best_idx + 1]
        cls = cls[best_idx:best_idx + 1]

        for bbox, score, cls_idx in zip(bboxes, scores, cls):
            x1, y1, x2, y2 = [float(v) for v in bbox.tolist()]
            detections.append({
                "label": self.COCO_CLASSES[int(cls_idx.item())],
                "score": float(score.item()),
                "bbox_xyxy": [x1, y1, x2, y2],
            })

        result_frame = self.vis(frame, bboxes, scores, cls, self.args.yolo_conf, self.COCO_CLASSES)
        return result_frame, True, len(detections), detections


def start_gst_receiver(args):
    pipeline = [
        "gst-launch-1.0", "-q",
        "udpsrc", f"port={args.port}",
        "caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
        "!", "rtpjitterbuffer", f"latency={args.latency_ms}", "drop-on-latency=false",
        "!", "rtph264depay",
        "!", "h264parse",
        "!", "avdec_h264",
        "!", "videoconvert",
        "!", "videoscale",
        "!", "videorate",
        "!", f"video/x-raw,format=BGR,width={args.width},height={args.height},framerate={args.fps}/1",
        "!", "fdsink", "fd=1", "sync=false",
    ]

    print("[rx-yolo] GST:", " ".join(pipeline), flush=True)

    return subprocess.Popen(
        pipeline,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=10**8,
        preexec_fn=os.setsid,
    )



# LIVE_DROP_ARCH_PATCH
def main() -> int:
    import argparse
    import json
    import os
    import signal
    import subprocess
    import threading
    import time
    from pathlib import Path

    import cv2
    import numpy as np

    parser = argparse.ArgumentParser(description="Low-latency RTP camera RX + YOLOX live/drop-old-frames")
    parser.add_argument("--port", type=int, default=6000)
    parser.add_argument("--latency-ms", type=int, default=80)
    parser.add_argument("--width", type=int, default=424)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--yolo-exp-file", type=Path, required=True)
    parser.add_argument("--yolo-ckpt", type=Path, required=True)
    parser.add_argument("--yolo-device", default="gpu", choices=["cpu", "gpu"])
    parser.add_argument("--yolo-conf", type=float, default=0.25)
    parser.add_argument("--yolo-nms", type=float, default=0.45)
    parser.add_argument("--yolo-tsize", type=int, default=192)
    parser.add_argument("--process-fps", type=float, default=10.0)
    parser.add_argument("--window-name", default="raspi-rx-yolo")
    parser.add_argument("--yolo-fp16", action="store_true")
    args = parser.parse_args()

    stop = {"value": False}
    lock = threading.Lock()

    shared = {
        "latest_frame": None,
        "latest_frame_ts": 0.0,
        "last_yolo_frame": None,
        "last_yolo_ts": 0.0,
        "person": False,
        "count": 0,
        "detections": [],
        "status": "starting",
        "error": None,
        "frames_rx": 0,
        "frames_drop": 0,
    }

    frame_size = int(args.width * args.height * 3)

    def write_json(payload):
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        tmp = args.json_out.with_name(f".{args.json_out.name}.tmp.{os.getpid()}.{time.time_ns()}")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, args.json_out)

    def publish(status=None, error=None):
        with lock:
            payload = {
                "status": status or shared["status"],
                "input_source": "raspi_rtp_h264_pipe",
                "person_detected": bool(shared["person"]),
                "count": int(shared["count"]),
                "detections": shared["detections"],
                "frame_width": int(args.width),
                "frame_height": int(args.height),
                "error": error if error is not None else shared["error"],
                "timestamp": time.time(),
                "frames_rx": int(shared["frames_rx"]),
                "frames_drop": int(shared["frames_drop"]),
                "last_frame_age_sec": time.time() - shared["latest_frame_ts"] if shared["latest_frame_ts"] else None,
                "last_yolo_age_sec": time.time() - shared["last_yolo_ts"] if shared["last_yolo_ts"] else None,
            }
        try:
            write_json(payload)
        except Exception as exc:
            print(f"[rx-yolo][WARN] JSON write failed: {exc}", flush=True)

    gst_cmd = [
        "gst-launch-1.0", "-q",
        "udpsrc", f"port={args.port}",
        "caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
        "!", "rtpjitterbuffer", f"latency={args.latency_ms}", "drop-on-latency=true",
        "!", "rtph264depay",
        "!", "queue", "max-size-buffers=1", "leaky=downstream",
        "!", "h264parse",
        "!", "avdec_h264",
        "!", "videoconvert",
        "!", "queue", "max-size-buffers=1", "leaky=downstream",
        "!", "videoscale",
        "!", "videorate",
        "!", f"video/x-raw,format=BGR,width={args.width},height={args.height},framerate={args.fps}/1",
        "!", "fdsink", "fd=1", "sync=false",
    ]

    print("[rx-yolo] GST:", " ".join(gst_cmd), flush=True)

    proc = subprocess.Popen(
        gst_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        preexec_fn=os.setsid,
    )

    def stderr_reader():
        if proc.stderr is None:
            return
        for line in iter(proc.stderr.readline, b""):
            if not line:
                break
            txt = line.decode(errors="replace").strip()
            if txt:
                print("[gst]", txt, flush=True)

    def reader_thread():
        print("[rx-yolo] reader thread started; keeping only latest frame", flush=True)
        if proc.stdout is None:
            return

        while not stop["value"]:
            buf = proc.stdout.read(frame_size)
            if not buf or len(buf) < frame_size:
                time.sleep(0.005)
                continue

            frame = np.frombuffer(buf, dtype=np.uint8).reshape((args.height, args.width, 3)).copy()

            with lock:
                if shared["latest_frame"] is not None:
                    shared["frames_drop"] += 1
                shared["latest_frame"] = frame
                shared["latest_frame_ts"] = time.time()
                shared["frames_rx"] += 1
                if shared["status"] in ("starting", "loading_yolo"):
                    shared["status"] = "receiving"

    def yolo_thread():
        try:
            with lock:
                shared["status"] = "loading_yolo"
            publish("loading_yolo")

            # Reutiliza la clase/funciones del fichero si existen.
            # Si no existen, importamos el motor YOLO validado del script combinado antiguo.
            engine_cls = globals().get("YoloEngine") or globals().get("LocalYoloEngine")

            if engine_cls is None:
                import sys as _sys
                from pathlib import Path as _Path

                _go2_root = _Path(__file__).resolve().parents[2]
                if str(_go2_root) not in _sys.path:
                    _sys.path.insert(0, str(_go2_root))

                from video_file_tx_rx_yolo_usb import LocalYoloEngine as engine_cls

            # Compatibilidad de nombres de argumentos entre rx_camera_rtp_yolox_pipe.py
            # y video_file_tx_rx_yolo_usb.py.
            if not hasattr(args, "camera_json_out"):
                args.camera_json_out = args.json_out
            if not hasattr(args, "yolo_process_fps"):
                args.yolo_process_fps = args.process_fps
            if not hasattr(args, "yolo_fuse"):
                args.yolo_fuse = False

            engine = engine_cls(args)

            period = 1.0 / max(0.1, float(args.process_fps))
            last = 0.0
            last_log = 0.0

            with lock:
                shared["status"] = "online"

            while not stop["value"]:
                now = time.time()
                if now - last < period:
                    time.sleep(0.005)
                    continue

                with lock:
                    frame = None if shared["latest_frame"] is None else shared["latest_frame"].copy()

                if frame is None:
                    time.sleep(0.01)
                    continue

                last = now

                try:
                    result_frame, person, count = engine.process_frame(frame)
                    detections = []
                    # Si engine escribe JSON por su cuenta, bien; pero aquí mantenemos estado común.
                    # Intentamos leer detections del JSON si existe, sin bloquear.
                    try:
                        if args.json_out.exists():
                            old = json.loads(args.json_out.read_text())
                            detections = old.get("detections", [])
                    except Exception:
                        detections = []

                    with lock:
                        shared["last_yolo_frame"] = result_frame
                        shared["last_yolo_ts"] = time.time()
                        shared["person"] = bool(person)
                        shared["count"] = int(count)
                        shared["detections"] = detections
                        shared["status"] = "online"
                        shared["error"] = None

                    publish("online")

                    if now - last_log >= 1.0:
                        with lock:
                            age = time.time() - shared["latest_frame_ts"] if shared["latest_frame_ts"] else -1
                            print(
                                f"[rx-yolo] person={shared['person']} count={shared['count']} "
                                f"rx={shared['frames_rx']} drop={shared['frames_drop']} frame_age={age:.3f}s",
                                flush=True,
                            )
                        last_log = now

                except Exception as exc:
                    with lock:
                        shared["status"] = "inference_error"
                        shared["error"] = str(exc)
                    print(f"[rx-yolo][WARN] inference failed: {exc}", flush=True)
                    publish("inference_error", str(exc))
                    time.sleep(0.1)

        except Exception as exc:
            with lock:
                shared["status"] = "fault"
                shared["error"] = str(exc)
            print(f"[rx-yolo][ERROR] yolo thread: {exc}", flush=True)
            publish("fault", str(exc))

    def handle_signal(signum, frame):
        stop["value"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    threading.Thread(target=stderr_reader, daemon=True).start()
    threading.Thread(target=reader_thread, daemon=True).start()
    threading.Thread(target=yolo_thread, daemon=True).start()

    cv2.namedWindow(args.window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(args.window_name, 640, 480)

    last_publish = 0.0

    try:
        while not stop["value"]:
            with lock:
                raw = None if shared["latest_frame"] is None else shared["latest_frame"].copy()
                yolo = None if shared["last_yolo_frame"] is None else shared["last_yolo_frame"].copy()
                yolo_age = time.time() - shared["last_yolo_ts"] if shared["last_yolo_ts"] else 999.0
                status = shared["status"]
                person = shared["person"]
                count = shared["count"]
                rx = shared["frames_rx"]
                drop = shared["frames_drop"]

            if raw is None:
                time.sleep(0.01)
                continue

            # Vídeo siempre actual. Si hay anotación reciente, usamos frame anotado.
            max_yolo_age = max(0.35, 2.0 / max(0.1, float(args.process_fps)))
            shown = yolo if (yolo is not None and yolo_age <= max_yolo_age) else raw

            cv2.rectangle(shown, (0, 0), (shown.shape[1], 24), (0, 0, 0), -1)
            cv2.putText(
                shown,
                f"LIVE | YOLO={status} person={person} count={count} rx={rx} drop={drop}",
                (6, 17),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

            cv2.imshow(args.window_name, shown)

            if time.time() - last_publish >= 1.0:
                publish()
                last_publish = time.time()

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                stop["value"] = True
                break

    finally:
        stop["value"] = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            cv2.destroyWindow(args.window_name)
        except Exception:
            pass

    publish("offline")
    return 0

if __name__ == "__main__":
    main()
