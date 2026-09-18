#!/usr/bin/env python3
import argparse
import subprocess
import time
import tkinter as tk
from dataclasses import dataclass
from collections import deque

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=3",
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
    "-o", "StrictHostKeyChecking=accept-new",
]

@dataclass
class Counters:
    t: float
    tx: int
    rx: int

def read_local_iface(iface: str) -> Counters:
    with open("/proc/net/dev", "r", encoding="utf-8") as f:
        for line in f:
            if ":" not in line:
                continue
            name, data = line.split(":", 1)
            if name.strip() == iface:
                fields = data.split()
                rx = int(fields[0])
                tx = int(fields[8])
                return Counters(time.time(), tx, rx)
    raise RuntimeError(f"Local interface not found: {iface}")

def read_remote_iface(host: str, user: str, iface: str) -> Counters:
    """
    Read remote interface counters using a REMOTE timestamp.

    Important:
    The old version used time.time() locally after the SSH command returned.
    If one SSH read was delayed and the next one was not, Mbps showed artificial
    dips/spikes because dt included SSH jitter instead of only counter time.

    Here, tx/rx bytes and timestamp are sampled on the STA itself.
    /proc/uptime is used because it exists on OpenWrt/BusyBox.
    """
    cmd = (
        "awk '{print $1}' /proc/uptime; "
        f"cat /sys/class/net/{iface}/statistics/tx_bytes; "
        f"cat /sys/class/net/{iface}/statistics/rx_bytes"
    )

    p = subprocess.run(
        ["ssh", *SSH_OPTS, f"{user}@{host}", cmd],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    if p.returncode != 0:
        raise RuntimeError(f"SSH read failed for {host}:{iface}: {p.stderr.strip()}")

    lines = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    if len(lines) < 3:
        raise RuntimeError(f"Invalid counter output from {host}:{iface}: {p.stdout!r}")

    remote_t = float(lines[0])
    tx = int(lines[1])
    rx = int(lines[2])
    return Counters(remote_t, tx, rx)

def mbps(prev: Counters, curr: Counters):
    dt = curr.t - prev.t
    if dt <= 0:
        return 0.0, 0.0

    tx = max(0.0, (curr.tx - prev.tx) * 8.0 / dt / 1_000_000.0)
    rx = max(0.0, (curr.rx - prev.rx) * 8.0 / dt / 1_000_000.0)
    return tx, rx

class ThroughputPlot:
    def __init__(self, args):
        self.args = args

        self.root = tk.Tk()
        self.root.title("60 GHz Live Throughput")
        self.root.geometry(f"{args.width}x{args.height}")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.canvas = tk.Canvas(self.root, bg="white", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # Histórico real. Cuantos más samples, más se comprime visualmente el eje X.
        self.history = deque(maxlen=args.samples)

        self.local_prev = None
        self.sta_prev = None

        self.running = True
        self.last_error = ""

        self.start_time = time.time()

        self.init_counters()
        self.root.after(1000, self.update_loop)

    def init_counters(self):
        self.local_prev = read_local_iface(self.args.local_iface)
        self.sta_prev = read_remote_iface(
            self.args.sta_host,
            self.args.sta_user,
            self.args.sta_radio_iface,
        )

    def close(self):
        self.running = False
        self.root.destroy()

    def smoothed_value(self, key: str, current: float) -> float:
        n = max(1, int(getattr(self.args, "smooth_samples", 1)))
        if n <= 1 or not self.history:
            return current

        recent = []
        for sample in list(self.history)[-(n - 1):]:
            if key in sample:
                recent.append(float(sample[key]))

        values = recent + [float(current)]
        return sum(values) / max(1, len(values))

    def update_loop(self):
        if not self.running:
            return

        try:
            local_curr = read_local_iface(self.args.local_iface)
            sta_curr = read_remote_iface(
                self.args.sta_host,
                self.args.sta_user,
                self.args.sta_radio_iface,
            )

            _, pc_rx_raw = mbps(self.local_prev, local_curr)
            _, sta_rx_raw = mbps(self.sta_prev, sta_curr)

            pc_rx = self.smoothed_value("pc_video_rx_raw", pc_rx_raw)
            sta_rx = self.smoothed_value("radio_rx_raw", sta_rx_raw)

            self.local_prev = local_curr
            self.sta_prev = sta_curr

            now = time.time()
            self.history.append({
                "t": now - self.start_time,
                "radio_rx": sta_rx,
                "pc_video_rx": pc_rx,
                "radio_rx_raw": sta_rx_raw,
                "pc_video_rx_raw": pc_rx_raw,
            })

            self.last_error = ""

        except Exception as exc:
            self.last_error = str(exc)

        self.draw()
        self.root.after(int(self.args.interval * 1000), self.update_loop)

    def layout(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        return {
            "w": w,
            "h": h,
            "left": 80,
            "right": 35,
            "top": 135,
            "bottom": 70,
        }

    def draw_series(self, key, color):
        if len(self.history) < 2:
            return

        c = self.canvas
        l = self.layout()

        w = l["w"]
        h = l["h"]
        left = l["left"]
        right = l["right"]
        top = l["top"]
        bottom = l["bottom"]

        plot_w = max(1, w - left - right)
        plot_h = max(1, h - top - bottom)
        scale = self.args.scale_max

        data = list(self.history)

        # Histórico acumulado/comprimido:
        # - Al principio ocupa poca anchura.
        # - A medida que entran puntos, se va expandiendo.
        # - Cuando llega al máximo de samples, se comporta como scrolling history.
        n = len(data)
        max_n = self.args.samples

        points = []
        for i, sample in enumerate(data):
            x = left + (i / max(1, max_n - 1)) * plot_w
            y = top + plot_h - min(sample[key], scale) / scale * plot_h
            points.extend([x, y])

        if len(points) >= 4:
            c.create_line(*points, fill=color, width=3, smooth=True)

        # Punto actual
        last = data[-1]
        x_last = left + ((n - 1) / max(1, max_n - 1)) * plot_w
        y_last = top + plot_h - min(last[key], scale) / scale * plot_h
        c.create_oval(x_last - 4, y_last - 4, x_last + 4, y_last + 4, fill=color, outline=color)

    def draw(self):
        c = self.canvas
        c.delete("all")

        l = self.layout()
        w = l["w"]
        h = l["h"]
        left = l["left"]
        right = l["right"]
        top = l["top"]
        bottom = l["bottom"]

        plot_w = max(1, w - left - right)
        plot_h = max(1, h - top - bottom)
        scale = self.args.scale_max

        latest_radio = self.history[-1]["radio_rx"] if self.history else 0.0
        latest_pc = self.history[-1]["pc_video_rx"] if self.history else 0.0

        # Title
        c.create_text(
            25,
            18,
            anchor="w",
            text="60 GHz Live Throughput",
            fill="black",
            font=("Sans", 18, "bold"),
        )

        c.create_text(
            25,
            47,
            anchor="w",
            text="Historical view of radio link load and received video traffic",
            fill="#333333",
            font=("Sans", 11),
        )

        # Legend: separated, no overlap
        legend_x = 35
        legend_y1 = 82
        legend_y2 = 107

        c.create_line(legend_x, legend_y1, legend_x + 55, legend_y1, fill="#d62728", width=4)
        c.create_text(
            legend_x + 70,
            legend_y1,
            anchor="w",
            text=f"Radio Link Throughput: {latest_radio:.1f} Mbps",
            fill="#d62728",
            font=("Sans", 12, "bold"),
        )

        c.create_line(legend_x, legend_y2, legend_x + 55, legend_y2, fill="#1f77b4", width=4)
        c.create_text(
            legend_x + 70,
            legend_y2,
            anchor="w",
            text=f"PC Video Throughput: {latest_pc:.1f} Mbps",
            fill="#1f77b4",
            font=("Sans", 12, "bold"),
        )

        # Plot frame
        c.create_rectangle(left, top, left + plot_w, top + plot_h, outline="#444444")

        # Y grid
        step = 50
        value = 0
        while value <= scale:
            y = top + plot_h - value / scale * plot_h
            c.create_line(left, y, left + plot_w, y, fill="#eeeeee")
            c.create_text(left - 10, y, anchor="e", text=str(value), fill="#555555", font=("Sans", 9))
            value += step

        c.create_text(25, top - 10, anchor="w", text="Mbps", fill="#555555", font=("Sans", 10))

        # X grid: histórico
        if self.args.samples >= 30:
            for frac in (0.25, 0.50, 0.75, 1.00):
                x = left + frac * plot_w
                c.create_line(x, top, x, top + plot_h, fill="#f3f3f3")

        # Series
        self.draw_series("radio_rx", "#d62728")
        self.draw_series("pc_video_rx", "#1f77b4")

        # Bottom text
        filled = len(self.history)
        total_seconds = int(filled * self.args.interval)

        if self.last_error:
            c.create_text(
                left,
                h - 45,
                anchor="w",
                text=f"ERROR: {self.last_error}",
                fill="red",
                font=("Sans", 10, "bold"),
            )
        else:
            c.create_text(
                left,
                h - 45,
                anchor="w",
                text="Radio Link Throughput = video + iperf load over the 60 GHz link",
                fill="#333333",
                font=("Sans", 10),
            )

        c.create_text(
            left + plot_w / 2,
            h - 23,
            anchor="center",
            text=f"Historical samples: {filled}/{self.args.samples} | visible time window: ~{int(self.args.samples * self.args.interval)} s | refresh: {self.args.interval:.1f} s",
            fill="#555555",
            font=("Sans", 10),
        )

    def run(self):
        self.root.mainloop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-iface", default="enP7s7")
    parser.add_argument("--sta-host", default="192.168.1.12")
    parser.add_argument("--sta-user", default="root")
    parser.add_argument("--sta-radio-iface", default="wlan0")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--smooth-samples", type=int, default=5)

    # Más samples = más histórico y más compresión visual.
    parser.add_argument("--samples", type=int, default=240)

    parser.add_argument("--scale-max", type=float, default=650.0)
    parser.add_argument("--width", type=int, default=1050)
    parser.add_argument("--height", type=int, default=620)

    # Kept for compatibility with previous launch commands.
    parser.add_argument("--ap-host", default="192.168.1.13")
    parser.add_argument("--ap-user", default="root")
    parser.add_argument("--target-video-mbps", type=float, default=20.0)
    parser.add_argument("--target-iperf-mbps", type=float, default=500.0)

    args = parser.parse_args()

    app = ThroughputPlot(args)
    app.run()

if __name__ == "__main__":
    main()
