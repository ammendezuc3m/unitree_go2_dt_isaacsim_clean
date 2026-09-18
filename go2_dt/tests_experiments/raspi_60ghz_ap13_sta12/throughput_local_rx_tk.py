#!/usr/bin/env python3
import argparse
import time
from collections import deque

import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


def read_rx_bytes(iface: str) -> int:
    with open(f"/sys/class/net/{iface}/statistics/rx_bytes", "r") as f:
        return int(f.read().strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iface", default="enP7s7")
    parser.add_argument("--scale-max", type=float, default=140.0)
    parser.add_argument("--window", type=int, default=240)
    parser.add_argument("--refresh", type=float, default=1.0)
    parser.add_argument("--smooth-samples", type=int, default=3)
    args = parser.parse_args()

    root = tk.Tk()
    root.title("60 GHz Live Throughput")

    fig = Figure(figsize=(9, 4.8), dpi=100)
    ax = fig.add_subplot(111)

    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    samples = deque(maxlen=max(2, args.window))
    smooth = deque(maxlen=max(1, args.smooth_samples))

    last_t = time.time()
    last_rx = read_rx_bytes(args.iface)
    start_t = last_t

    def update():
        nonlocal last_t, last_rx

        now = time.time()
        rx = read_rx_bytes(args.iface)
        dt = max(1e-6, now - last_t)

        mbps_raw = ((rx - last_rx) * 8.0) / (dt * 1_000_000.0)
        mbps_raw = max(0.0, mbps_raw)

        smooth.append(mbps_raw)
        mbps = sum(smooth) / len(smooth)

        elapsed = now - start_t
        samples.append((elapsed, mbps))

        last_t = now
        last_rx = rx

        xs = [x for x, _ in samples]
        ys = [y for _, y in samples]

        ax.clear()
        ax.set_title("60 GHz Live Throughput", fontsize=16, fontweight="bold", pad=12)
        ax.text(
            0.02,
            0.94,
            f"RX Throughput: {mbps:.1f} Mbps",
            transform=ax.transAxes,
            fontsize=12,
            fontweight="bold",
            color="tab:blue",
        )

        ax.plot(xs, ys, linewidth=2.0, color="tab:blue")
        ax.set_ylim(0, args.scale_max)
        ax.set_ylabel("Mbps")
        ax.grid(True, alpha=0.25)

        if xs:
            ax.set_xlim(max(0, xs[-1] - args.window), max(args.window, xs[-1]))

        ax.set_xlabel(
            f"Local RX on {args.iface} | samples: {len(samples)}/{args.window} | refresh: {args.refresh:.1f}s"
        )

        fig.tight_layout()
        canvas.draw_idle()
        root.after(int(args.refresh * 1000), update)

    update()
    root.mainloop()


if __name__ == "__main__":
    main()
