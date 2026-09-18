#!/usr/bin/env python3
import argparse
import time
from collections import deque

import tkinter as tk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


def read_rx_bytes(iface: str) -> int:
    with open("/proc/net/dev", "r", encoding="utf-8") as f:
        for line in f:
            if ":" not in line:
                continue
            name, data = line.split(":", 1)
            if name.strip() == iface:
                return int(data.split()[0])
    raise RuntimeError(f"No existe la interfaz {iface}")


def main():
    ap = argparse.ArgumentParser(description="Very smoothed local RX throughput plot")
    ap.add_argument("--local-iface", "--iface", dest="local_iface", default="enP7s7")
    ap.add_argument("--sta-host", default="")
    ap.add_argument("--scale-max", type=float, default=500.0)
    ap.add_argument("--samples", type=int, default=240)
    ap.add_argument("--interval", type=float, default=1.0)

    # Media móvil larga.
    ap.add_argument("--smooth-samples", type=int, default=30)

    # EMA fuerte: cuanto menor, más recta.
    ap.add_argument("--ema-alpha", type=float, default=0.08)

    # Limita cuánto puede cambiar visualmente cada muestra respecto al valor suavizado anterior.
    ap.add_argument("--max-step-mbps", type=float, default=8.0)

    ap.add_argument("--title", default="60 GHz Live Throughput")
    args, unknown = ap.parse_known_args()

    iface = args.local_iface
    samples = max(10, int(args.samples))
    interval = max(0.1, float(args.interval))
    smooth_n = max(1, int(args.smooth_samples))
    alpha = max(0.01, min(1.0, float(args.ema_alpha)))
    max_step = max(0.0, float(args.max_step_mbps))

    raw_window = deque(maxlen=smooth_n)
    plotted_values = deque(maxlen=samples)

    ema_value = None

    root = tk.Tk()
    root.title(args.title)

    fig = Figure(figsize=(10, 5), dpi=100)
    ax = fig.add_subplot(111)
    ax.set_title(args.title, fontsize=16, fontweight="bold")
    ax.set_ylabel("Mbps")
    ax.set_xlabel(
        f"Local RX on {iface} | samples: 0/{samples} | "
        f"MA={smooth_n} | EMA alpha={alpha}"
    )
    ax.set_ylim(0, args.scale_max)
    ax.grid(True, alpha=0.25)

    line, = ax.plot([], [], linewidth=2)
    text = ax.text(
        0.02,
        0.95,
        "RX Throughput: -- Mbps",
        transform=ax.transAxes,
        va="top",
        fontsize=11,
        fontweight="bold",
    )

    canvas = FigureCanvasTkAgg(fig, master=root)
    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    last_bytes = read_rx_bytes(iface)
    last_time = time.time()

    def update():
        nonlocal last_bytes, last_time, ema_value

        try:
            now_bytes = read_rx_bytes(iface)
            now_time = time.time()

            dt = max(1e-6, now_time - last_time)
            raw_mbps = ((now_bytes - last_bytes) * 8.0) / dt / 1_000_000.0

            last_bytes = now_bytes
            last_time = now_time

            if raw_mbps < 0:
                raw_mbps = 0.0

            raw_window.append(raw_mbps)
            moving_avg = sum(raw_window) / len(raw_window)

            if ema_value is None:
                ema_value = moving_avg
            else:
                candidate = alpha * moving_avg + (1.0 - alpha) * ema_value

                if max_step > 0:
                    upper = ema_value + max_step
                    lower = ema_value - max_step
                    candidate = max(lower, min(upper, candidate))

                ema_value = candidate

            plotted_values.append(ema_value)

            xs = list(range(len(plotted_values)))
            ys = list(plotted_values)

            line.set_data(xs, ys)
            ax.set_xlim(0, max(samples, len(ys), 10))
            ax.set_ylim(0, args.scale_max)

            text.set_text(f"RX Throughput: {ema_value:.1f} Mbps")
            ax.set_xlabel(
                f"Local RX on {iface} | samples: {len(ys)}/{samples} | "
                f"refresh: {interval:.1f}s | MA={smooth_n} | EMA alpha={alpha} | step≤{max_step:.1f} Mbps"
            )

            canvas.draw_idle()

        except Exception as exc:
            text.set_text(f"RX Throughput: ERROR: {exc}")
            canvas.draw_idle()

        root.after(int(interval * 1000), update)

    root.after(int(interval * 1000), update)
    root.mainloop()


if __name__ == "__main__":
    main()
