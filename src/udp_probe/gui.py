import csv
import json
import queue
import socket
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import ProbeConfig
from .host import HostServer

BLUE = "#2563eb"
GREEN = "#059669"
BACKGROUND = "#f1f5f9"
INK = "#0f172a"
MUTED = "#64748b"


class ProbeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("UDP Rate Probe | Desktop Host")
        self.geometry("1190x890")
        self.minsize(1000, 740)
        self.configure(bg=BACKGROUND)
        self.events = queue.Queue()
        self.server = HostServer(lambda kind, data: self.events.put((kind, data)))
        self.variables = {}
        self.probe_widgets = []
        self.connection_widgets = []
        self.records = []
        self.report = None
        self.connected = False
        self.listening = False
        self.running = False
        self.current_trial = None
        self.settings_path = Path.home() / ".udp-rate-probe" / "settings.json"
        self._style()
        self._build()
        self._load_settings()
        self._buttons()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(80, self._poll)

    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=BACKGROUND, foreground=INK)
        style.configure("Card.TFrame", background="white")
        style.configure("Card.TLabel", background="white")
        style.configure("Muted.TLabel", background="white", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background="white", font=("Segoe UI", 12, "bold"))
        style.configure(
            "Metric.TLabel", background="white", foreground=BLUE, font=("Segoe UI", 23, "bold")
        )
        style.configure("TEntry", fieldbackground="white", padding=5)
        style.configure("TCombobox", fieldbackground="white", padding=4)
        style.configure("TButton", padding=(12, 7))
        style.configure("Accent.TButton", background=BLUE, foreground="white")
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#94a3b8")])
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=28)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), padding=6)

    def _build(self):
        header = tk.Frame(self, bg=INK, padx=24, pady=17)
        header.pack(fill="x")
        tk.Label(
            header, text="UDP RATE PROBE", bg=INK, fg="white", font=("Segoe UI", 20, "bold")
        ).pack(anchor="w")
        tk.Label(
            header,
            text="TCP control  /  UDP measurement  /  Android companion",
            bg=INK,
            fg="#94a3b8",
            font=("Segoe UI", 10),
        ).pack(anchor="w")
        connection = ttk.Frame(self, style="Card.TFrame", padding=12)
        connection.pack(fill="x", padx=18, pady=(14, 10))
        self._inline_entry(connection, "bind_ip", "Bind IPv4", "0.0.0.0", 16)
        self._inline_entry(connection, "tcp_port", "TCP port", "5001", 7)
        self.listen_button = ttk.Button(connection, text="Start listening", command=self._listen)
        self.listen_button.pack(side="left", padx=6)
        self.disconnect_button = ttk.Button(
            connection, text="Disconnect phone", command=self.server.disconnect
        )
        self.disconnect_button.pack(side="left", padx=6)
        self.connection_status = tk.StringVar(value="Not listening")
        ttk.Label(connection, textvariable=self.connection_status, style="Card.TLabel").pack(
            side="right", padx=8
        )

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=18)
        settings_panel = ttk.Frame(body, style="Card.TFrame", padding=16)
        settings_panel.pack(side="left", fill="y", padx=(0, 12))
        ttk.Label(settings_panel, text="Probe settings", style="Title.TLabel").pack(
            anchor="w", pady=(0, 12)
        )
        controls = ttk.Frame(settings_panel, style="Card.TFrame")
        controls.pack(side="bottom", fill="x", pady=(12, 0))
        scroll_area = ttk.Frame(settings_panel, style="Card.TFrame")
        scroll_area.pack(fill="both", expand=True)
        settings_canvas = tk.Canvas(scroll_area, bg="white", highlightthickness=0, height=100)
        settings_scrollbar = ttk.Scrollbar(
            scroll_area, orient="vertical", command=settings_canvas.yview
        )
        settings_scrollbar.pack(side="right", fill="y")
        settings_canvas.pack(side="left", fill="both", expand=True)
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings = ttk.Frame(settings_canvas, style="Card.TFrame")
        settings_window = settings_canvas.create_window((0, 0), window=settings, anchor="nw")
        settings.bind(
            "<Configure>",
            lambda event: settings_canvas.configure(
                scrollregion=settings_canvas.bbox("all"), width=settings.winfo_reqwidth()
            ),
        )
        settings_canvas.bind(
            "<Configure>",
            lambda event: settings_canvas.itemconfigure(settings_window, width=event.width),
        )
        fields = [
            ("host_udp_port", "Host UDP port", "5002", None),
            ("phone_udp_port", "Phone UDP port", "5003", None),
            ("direction", "Direction", "both", ("both", "downlink", "uplink")),
            ("mode", "Probe mode", "auto", ("auto", "fixed")),
            ("min_mbps", "Minimum (Mbps)", "1", None),
            ("start_mbps", "Start / fixed (Mbps)", "10", None),
            ("max_mbps", "Maximum (Mbps)", "200", None),
            ("duration", "Trial duration (s)", "3", None),
            ("payload_size", "Datagram bytes", "1200", None),
            ("loss_limit", "Allowed loss (%)", "1", None),
            ("max_steps", "Max search trials", "10", None),
            ("drain_ms", "Drain time (ms)", "400", None),
            ("tolerance", "Search tolerance", "0.05", None),
        ]
        for row, (name, label, default, options) in enumerate(fields, 1):
            variable = self.variables[name] = tk.StringVar(value=default)
            ttk.Label(settings, text=label, style="Card.TLabel").grid(
                row=row, column=0, sticky="w", pady=4, padx=(0, 12)
            )
            if options:
                widget = ttk.Combobox(
                    settings, textvariable=variable, values=options, state="readonly", width=11
                )
            else:
                widget = ttk.Entry(settings, textvariable=variable, width=13)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
            self.probe_widgets.append(widget)
        ttk.Label(
            settings,
            text="Downlink: host → phone\nUplink: phone → host\nBoth: sequential, not simultaneous",
            style="Muted.TLabel",
        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=12)
        self.start_button = ttk.Button(
            controls, text="Start probe", style="Accent.TButton", command=self._start
        )
        self.start_button.pack(fill="x", pady=(0, 6))
        self.stop_button = ttk.Button(controls, text="Stop probe", command=self._stop)
        self.stop_button.pack(fill="x")
        ttk.Label(
            controls,
            text="Use only on networks you own\nor have permission to test.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(12, 0))

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        cards = ttk.Frame(right)
        cards.pack(fill="x")
        self.downlink_value = tk.StringVar(value="—")
        self.uplink_value = tk.StringVar(value="—")
        for title, variable in (
            ("CONFIRMED DOWNLINK", self.downlink_value),
            ("CONFIRMED UPLINK", self.uplink_value),
        ):
            card = ttk.Frame(cards, style="Card.TFrame", padding=14)
            card.pack(side="left", fill="both", expand=True, padx=(0, 6))
            ttk.Label(card, text=title, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=variable, style="Metric.TLabel").pack(anchor="w", pady=3)
            ttk.Label(card, text="Sustainable target · Mbps", style="Muted.TLabel").pack(anchor="w")

        chart = ttk.Frame(right, style="Card.TFrame", padding=(12, 8))
        chart.pack(fill="x", pady=10)
        ttk.Label(chart, text="Received goodput by trial", style="Title.TLabel").pack(anchor="w")
        self.canvas = tk.Canvas(chart, height=155, bg="white", highlightthickness=0)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda event: self._chart())
        self.trial_status = tk.StringVar(value="Connect your phone, then start a probe.")
        ttk.Label(right, textvariable=self.trial_status, wraplength=700).pack(
            anchor="w", pady=(0, 8)
        )
        table_frame = ttk.Frame(right)
        table_frame.pack(fill="both", expand=True)
        columns = (
            "direction",
            "phase",
            "target",
            "offered",
            "received",
            "loss",
            "jitter",
            "status",
        )
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=6)
        for name, title, width in zip(
            columns,
            (
                "Direction",
                "Phase",
                "Target",
                "Sent Mbps",
                "Rx Mbps",
                "Loss %",
                "Jitter ms",
                "Result",
            ),
            (77, 60, 66, 74, 74, 60, 68, 130),
            strict=True,
        ):
            self.table.heading(name, text=title)
            self.table.column(name, width=width, minwidth=50, anchor="center")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.tag_configure("PASS", foreground=GREEN)
        self.table.tag_configure("LOSS", foreground="#dc2626")
        self.table.tag_configure("SENDER LIMITED", foreground="#b45309")
        footer = ttk.Frame(right)
        footer.pack(fill="x", pady=8)
        self.summary = tk.StringVar(value="No measurements yet.")
        ttk.Label(footer, textvariable=self.summary, wraplength=540).pack(
            side="left", fill="x", expand=True
        )
        self.export_button = ttk.Button(footer, text="Export results", command=self._export)
        self.export_button.pack(side="right", padx=(8, 0))

        self.log = tk.Text(
            self,
            height=4,
            bg=INK,
            fg="#cbd5e1",
            font=("Consolas", 9),
            relief="flat",
            state="disabled",
            padx=12,
            pady=7,
        )
        self.log.pack(fill="x", padx=18, pady=(10, 14))
        try:
            addresses = sorted(
                {
                    item[4][0]
                    for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                }
            )
        except OSError:
            addresses = ["127.0.0.1"]
        self._log(
            "Host IPv4 candidates: "
            + ", ".join(addresses)
            + ". Use your LAN/Wi-Fi adapter address."
        )

    def _inline_entry(self, parent, name, label, default, width):
        ttk.Label(parent, text=label, style="Card.TLabel").pack(side="left", padx=(0, 5))
        self.variables[name] = tk.StringVar(value=default)
        entry = ttk.Entry(parent, textvariable=self.variables[name], width=width)
        entry.pack(side="left", padx=(0, 12))
        self.connection_widgets.append(entry)

    def _buttons(self):
        self.listen_button.configure(text="Stop listening" if self.listening else "Start listening")
        self.disconnect_button.configure(state="normal" if self.connected else "disabled")
        self.start_button.configure(
            state="normal" if self.connected and not self.running else "disabled"
        )
        self.stop_button.configure(state="normal" if self.running else "disabled")
        self.export_button.configure(
            state="normal" if self.records and not self.running else "disabled"
        )
        for widget in self.probe_widgets:
            widget.configure(
                state="disabled"
                if self.running
                else ("readonly" if isinstance(widget, ttk.Combobox) else "normal")
            )
        for widget in self.connection_widgets:
            widget.configure(state="disabled" if self.listening else "normal")

    def _listen(self):
        if self.listening:
            self.server.close()
            self.listening = False
            self.connection_status.set("Not listening")
        else:
            try:
                self.server.listen(
                    self.variables["bind_ip"].get().strip(), int(self.variables["tcp_port"].get())
                )
            except (OSError, ValueError) as error:
                messagebox.showerror("Cannot listen", str(error))
                return
            self.listening = True
            self.connection_status.set("Listening · waiting for phone")
            self._save_settings()
        self._buttons()

    def _config(self):
        defaults = ProbeConfig().to_dict()
        values = {
            name: type(default)(self.variables[name].get()) for name, default in defaults.items()
        }
        return ProbeConfig(**values).validate()

    def _start(self):
        try:
            self.server.start_probe(self._config())
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot start probe", str(error))
            return
        self.running = True
        self._save_settings()
        self._buttons()

    def _stop(self):
        self.server.stop_probe()
        self.trial_status.set("Stopping… waiting for both endpoints to release UDP sockets.")

    def _poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._event(kind, data)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _event(self, kind, data):
        if kind == "listening":
            self._log(f"TCP listening on {data['bind_ip']}:{data['tcp_port']}.")
        elif kind == "connected":
            self.connected = True
            self.connection_status.set(f"Connected · {data['ip']}")
            self._log(f"Connected to {data['name']} ({data['ip']}).")
        elif kind == "disconnected":
            self.connected = False
            self.connection_status.set(
                "Listening · waiting for phone" if self.listening else "Not listening"
            )
            self._log("Phone disconnected.")
        elif kind == "running":
            self.records.clear()
            self.report = None
            self.table.delete(*self.table.get_children())
            self.downlink_value.set("—")
            self.uplink_value.set("—")
            self.summary.set("Probe running. Results are UDP payload rates, not Wi-Fi PHY rates.")
            self._chart()
        elif kind == "trial":
            self.current_trial = data
            self.trial_status.set(
                f"{data['direction'].title()} · {data['phase']} · "
                f"target {data['rate']:.2f} Mbps · {data['duration']:.1f} s"
            )
        elif kind == "result":
            self.records.append(data)
            item = self.table.insert(
                "",
                "end",
                values=(
                    data["direction"],
                    data["phase"],
                    f"{data['target_mbps']:.2f}",
                    f"{data['offered_mbps']:.2f}",
                    f"{data['goodput_mbps']:.2f}",
                    f"{data['loss_percent']:.2f}",
                    f"{data['jitter_ms']:.2f}",
                    data["status"],
                ),
                tags=(data["status"],),
            )
            self.table.see(item)
            self._chart()
            self._log(
                f"{data['direction']} {data['target_mbps']:.2f} Mbps: "
                f"{data['status']}, loss {data['loss_percent']:.2f}%."
            )
        elif kind == "finished":
            self.running = False
            self.report = data
            self.trial_status.set(
                f"Probe {data['outcome']}. {len(data['records'])} trials recorded."
            )
            notes = []
            if data["outcome"] == "completed":
                for direction, variable in (
                    ("downlink", self.downlink_value),
                    ("uplink", self.uplink_value),
                ):
                    result = data["recommendations"].get(direction)
                    if result:
                        variable.set(f"{result['target_mbps']:.2f}")
                        notes.append(
                            f"{direction.title()} with 10% headroom: "
                            f"{result['target_mbps'] * 0.9:.2f} Mbps"
                        )
                    elif direction in data["recommendations"]:
                        variable.set("No pass")
                self.summary.set(
                    ". ".join(notes)
                    or "No confirmed auto-rate recommendation. Inspect trial results."
                )
                if data["config"]["mode"] == "fixed":
                    self.summary.set(
                        "Fixed-rate measurement complete. Use auto mode to find a sustainable rate."
                    )
            else:
                self.summary.set("Partial results only; no completed-run recommendation.")
            self._log(f"Probe {data['outcome']}.")
        elif kind in {"error", "log"}:
            self._log(data["message"])
            if kind == "error":
                self.trial_status.set("Probe error: " + data["message"])
        self._buttons()

    def _chart(self):
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 250)
        height = 155
        left, top, bottom, right = 48, 22, height - 24, width - 12
        maximum = max((record["goodput_mbps"] for record in self.records), default=1) * 1.15
        maximum = max(maximum, 0.1)
        for tick in range(4):
            vertical = bottom - (bottom - top) * tick / 3
            self.canvas.create_line(left, vertical, right, vertical, fill="#e2e8f0")
            self.canvas.create_text(
                left - 6,
                vertical,
                anchor="e",
                text=f"{maximum * tick / 3:.1f}",
                fill=MUTED,
                font=("Segoe UI", 8),
            )
        if not self.records:
            self.canvas.create_text(width / 2, 75, text="Measurements appear here", fill=MUTED)
        points = {"downlink": [], "uplink": []}
        for index, record in enumerate(self.records):
            horizontal = left + (right - left) * index / max(1, len(self.records) - 1)
            vertical = bottom - (bottom - top) * record["goodput_mbps"] / maximum
            points[record["direction"]].extend((horizontal, vertical))
            color = BLUE if record["direction"] == "downlink" else GREEN
            self.canvas.create_oval(
                horizontal - 3,
                vertical - 3,
                horizontal + 3,
                vertical + 3,
                fill=color if record["passed"] else "#dc2626",
                outline="",
            )
        for direction, coordinates in points.items():
            if len(coordinates) >= 4:
                self.canvas.create_line(
                    *coordinates, fill=BLUE if direction == "downlink" else GREEN, width=2
                )
        self.canvas.create_text(
            left, 8, anchor="w", text="Downlink", fill=BLUE, font=("Segoe UI", 8)
        )
        self.canvas.create_text(
            left + 70,
            8,
            anchor="w",
            text="Uplink  ·  Red dot: failed",
            fill=GREEN,
            font=("Segoe UI", 8),
        )
        self.canvas.create_text(
            right, height - 7, anchor="e", text="Trial order →", fill=MUTED, font=("Segoe UI", 8)
        )

    def _log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "100.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _export(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=f"udp-probe-{datetime.now():%Y%m%d-%H%M%S}.json",
            filetypes=(("JSON report", "*.json"), ("CSV trials", "*.csv")),
        )
        if not filename:
            return
        try:
            with open(filename, "w", encoding="utf-8", newline="") as output:
                if filename.lower().endswith(".csv"):
                    writer = csv.DictWriter(output, fieldnames=list(self.records[0]))
                    writer.writeheader()
                    writer.writerows(self.records)
                else:
                    json.dump(self.report, output, indent=2, allow_nan=False)
            self._log(f"Exported {filename}")
        except OSError as error:
            messagebox.showerror("Export failed", str(error))

    def _load_settings(self):
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            for name, variable in self.variables.items():
                if name in settings:
                    variable.set(str(settings[name]))
        except (OSError, ValueError, TypeError):
            pass

    def _save_settings(self):
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps(
                    {name: variable.get() for name, variable in self.variables.items()}, indent=2
                ),
                encoding="utf-8",
            )
        except OSError as error:
            self._log(f"Settings were not saved: {error}")

    def _close(self):
        self._save_settings()
        self.server.close()
        self.destroy()
