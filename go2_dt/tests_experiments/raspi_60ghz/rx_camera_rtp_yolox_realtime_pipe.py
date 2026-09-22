#!/usr/bin/env python3
import argparse
import os
import signal
import subprocess
import sys
import time
import threading
from pathlib import Path

GO2_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(GO2_ROOT))

from video_file_tx_rx_yolo_usb import LocalYoloEngine, atomic_write_json, build_camera_state


def build_gst_cmd(rx_port: int, latency_ms: int, width: int, height: int, fps: int):
    pipeline = [
        "gst-launch-1.0", "-q",
        "udpsrc", f"port={rx_port}",
        "caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
        "!", "rtpjitterbuffer", f"latency={latency_ms}", "drop-on-latency=true",
        "!", "rtph264depay",
        "!", "h264parse",
        "!", "avdec_h264",
        "!", "videoconvert",
        "!", "videoscale",
        "!", f"video/x-raw,format=BGR,width={width},height={height},framerate={fps}/1",
        "!", "queue", "max-size-buffers=1", "leaky=downstream",
        "!", "fdsink", "fd=1", "sync=false",
    ]
    return pipeline


def read_exact(pipe, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = pipe.read(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def main():
    parser = argparse.ArgumentParser(description="Realtime RTP/H264 RX via gst-launch pipe + YOLOX")

    parser.add_argument("--rx-port", type=int, default=6000)
    parser.add_argument("--latency-ms", type=int, default=80)

    # Forzamos tamaño estable para leer frames RAW.
    parser.add_argument("--width", type=int, default=424)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument("--fps", type=int, default=15)

    parser.add_argument("--camera-json-out", type=Path, default=GO2_ROOT / "camera_yolo/outputs/live_camera_state.json")

    parser.add_argument("--yolo-exp-file", type=Path, default=GO2_ROOT / "camera_yolo/YOLOX/exps/default/yolox_s.py")
    parser.add_argument("--yolo-ckpt", type=Path, default=GO2_ROOT / "camera_yolo/Weights/yolox_s.pth")
    parser.add_argument("--yolo-device", default="gpu", choices=["cpu", "gpu"])
    parser.add_argument("--yolo-conf", type=float, default=0.25)
    parser.add_argument("--yolo-nms", type=float, default=0.45)
    parser.add_argument("--yolo-person-min-score", type=float, default=0.55)
    parser.add_argument("--yolo-person-min-height-ratio", type=float, default=0.35)
    parser.add_argument("--yolo-person-min-area-ratio", type=float, default=0.04)
    parser.add_argument("--yolo-person-min-aspect-ratio", type=float, default=1.15)
    parser.add_argument("--yolo-tsize", type=int, default=256)
    parser.add_argument("--yolo-process-fps", type=float, default=5.0)
    parser.add_argument("--yolo-fp16", action="store_true")
    parser.add_argument("--yolo-fuse", action="store_true")

    parser.add_argument("--window-name", default="Raspi RTP YOLO realtime pipe")
    parser.add_argument("--window-width", type=int, default=850)
    parser.add_argument("--window-height", type=int, default=520)
    parser.add_argument("--verbose", action="store_true")

    args, unknown = parser.parse_known_args()
    if unknown:
        print(f"[RX-YOLO][WARN] Ignorando argumentos no usados: {unknown}", flush=True)

    # Compatibilidad con LocalYoloEngine del script antiguo.
    args.yolo_camera_width = args.width
    args.yolo_camera_height = args.height
    args.yolo_camera_fps = args.fps
    args.yolo_window_name = args.window_name
    args.yolo_window_width = args.window_width
    args.yolo_window_height = args.window_height

    import cv2
    import numpy as np

    if not args.yolo_exp_file.exists():
        raise FileNotFoundError(f"No existe YOLO exp file: {args.yolo_exp_file}")
    if not args.yolo_ckpt.exists():
        raise FileNotFoundError(f"No existe YOLO ckpt: {args.yolo_ckpt}")

    args.camera_json_out.parent.mkdir(parents=True, exist_ok=True)

    gst_cmd = build_gst_cmd(args.rx_port, args.latency_ms, args.width, args.height, args.fps)

    print("============================================================", flush=True)
    print("[RX-YOLO-PIPE] Receptor RTP/H264 realtime por gst-launch pipe", flush=True)
    print(f"[RX-YOLO-PIPE] Puerto RX: {args.rx_port}", flush=True)
    print(f"[RX-YOLO-PIPE] Tamaño RAW: {args.width}x{args.height} BGR @ {args.fps} FPS", flush=True)
    print(f"[RX-YOLO-PIPE] JSON: {args.camera_json_out}", flush=True)
    print(f"[RX-YOLO-PIPE] YOLO device={args.yolo_device} tsize={args.yolo_tsize} fp16={args.yolo_fp16}", flush=True)
    print("[RX-YOLO-PIPE] CMD:", " ".join(gst_cmd), flush=True)
    print("============================================================", flush=True)

    shared = {
        "latest_frame": None,
        "last_yolo_frame": None,
        "last_yolo_ts": 0.0,
        "detected": False,
        "count": 0,
        "status": "starting",
        "error": None,
        "frame_w": args.width,
        "frame_h": args.height,
    }

    lock = threading.Lock()
    stop = {"value": False}

    def write_state(status=None, error=None):
        with lock:
            payload = build_camera_state(
                status=status or shared["status"],
                input_source="raspi_rtp_h264_yolox_realtime_pipe",
                person_detected=shared["detected"],
                count=shared["count"],
                frame_width=shared["frame_w"],
                frame_height=shared["frame_h"],
                detections=[],
                error=error if error is not None else shared["error"],
            )
        atomic_write_json(args.camera_json_out, payload)

    def yolo_worker():
        try:
            with lock:
                shared["status"] = "loading_yolo"
            write_state()

            print("[YOLO] Cargando modelo...", flush=True)
            engine = LocalYoloEngine(args)

            with lock:
                shared["status"] = "yolo_ready"
                shared["error"] = None
            write_state()

            print("[YOLO] Modelo listo. Inferencia sobre latest_frame.", flush=True)

            frame_period = 1.0 / max(0.1, float(args.yolo_process_fps))
            last_process = 0.0
            last_log = 0.0

            while not stop["value"]:
                now = time.time()

                if now - last_process < frame_period:
                    time.sleep(0.005)
                    continue

                with lock:
                    frame = None if shared["latest_frame"] is None else shared["latest_frame"].copy()

                if frame is None:
                    time.sleep(0.01)
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
                        shared["frame_h"], shared["frame_w"] = frame.shape[:2]

                    if now - last_log >= 1.0:
                        print(f"[YOLO] online | person={detected} count={count}", flush=True)
                        write_state()
                        last_log = now

                except Exception as exc:
                    msg = str(exc)
                    print(f"[YOLO][WARN] Inferencia falló: {msg}", flush=True)
                    with lock:
                        shared["status"] = "inference_error"
                        shared["error"] = msg
                    write_state(error=msg)
                    time.sleep(0.2)

        except Exception as exc:
            msg = str(exc)
            print(f"[YOLO][ERROR] Worker roto: {msg}", flush=True)
            with lock:
                shared["status"] = "fault"
                shared["error"] = msg
            write_state(error=msg)

    proc = None

    try:
        proc = subprocess.Popen(
            gst_cmd,
            stdout=subprocess.PIPE,
            stderr=None if args.verbose else subprocess.DEVNULL,
            bufsize=10**8,
            preexec_fn=os.setsid,
        )

        frame_size = args.width * args.height * 3

        cv2.namedWindow(args.window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(args.window_name, args.window_width, args.window_height)

        worker = threading.Thread(target=yolo_worker, daemon=True)
        worker.start()

        frames = 0
        last_fps_t = time.time()
        last_json_t = time.time()
        rx_fps = 0.0

        while True:
            raw = read_exact(proc.stdout, frame_size)

            if raw is None:
                raise RuntimeError("gst-launch dejó de entregar frames RAW por stdout")

            frame = np.frombuffer(raw, dtype=np.uint8).reshape((args.height, args.width, 3)).copy()

            frames += 1
            now = time.time()

            if now - last_fps_t >= 1.0:
                rx_fps = frames / (now - last_fps_t)
                frames = 0
                last_fps_t = now

            with lock:
                shared["latest_frame"] = frame.copy()
                shared["frame_h"], shared["frame_w"] = frame.shape[:2]
                status = shared["status"]
                error = shared["error"]
                detected = shared["detected"]
                count = shared["count"]
                yolo_frame = shared["last_yolo_frame"]
                yolo_age = time.time() - shared["last_yolo_ts"] if shared["last_yolo_ts"] else 999.0

            max_yolo_age = max(0.75, 2.0 / max(0.1, float(args.yolo_process_fps)))
            if yolo_frame is not None and yolo_age <= max_yolo_age:
                shown = yolo_frame.copy()
            else:
                shown = frame.copy()

            label = f"RX {rx_fps:.1f} FPS | YOLO={status} | person={detected} count={count}"
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

            cv2.imshow(args.window_name, shown)

            if now - last_json_t >= 1.0:
                write_state()
                last_json_t = now

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q"), ord("Q")):
                break

            if proc.poll() is not None:
                raise RuntimeError(f"gst-launch terminó con código {proc.returncode}")

    except KeyboardInterrupt:
        print("[RX-YOLO-PIPE] Ctrl+C", flush=True)

    except Exception as exc:
        msg = str(exc)
        print(f"[RX-YOLO-PIPE][ERROR] {msg}", flush=True)
        with lock:
            shared["status"] = "fault"
            shared["error"] = msg
        write_state(error=msg)
        return 1

    finally:
        stop["value"] = True

        if proc is not None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                pass
            time.sleep(0.2)
            try:
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass

        try:
            cv2.destroyWindow(args.window_name)
        except Exception:
            pass

        with lock:
            shared["status"] = "offline"
            shared["detected"] = False
            shared["count"] = 0
        write_state()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
