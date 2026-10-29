import json
import os
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def gui_window(tmp_path_factory):
    from udp_probe.gui import ProbeApp

    home = tmp_path_factory.mktemp("gui-home")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "home", lambda: home)
        try:
            app = ProbeApp()
        except tk.TclError as error:
            pytest.skip(f"A graphical display is required: {error}")
    try:
        app.update()
        yield app
    finally:
        app.server.close()
        app.destroy()


@pytest.fixture
def probe_app(gui_window, tmp_path):
    from udp_probe.config import ProbeConfig

    app = gui_window
    app.settings_path = tmp_path / ".udp-rate-probe/settings.json"
    for name, value in ProbeConfig().to_dict().items():
        app.variables[name].set(str(value))
    app.connected = False
    app.running = False
    app.records.clear()
    app.table.delete(*app.table.get_children())
    app._buttons()
    app._chart()
    return app


def test_gui_controls_results_exports_and_layout(probe_app, tmp_path, monkeypatch):
    app = probe_app
    assert str(app.start_button["state"]) == "disabled"
    app._event("connected", {"ip": "192.168.1.20", "name": "Test phone"})
    assert str(app.start_button["state"]) == "normal"
    assert app._config().payload_size == 1200
    result = {
        "direction": "downlink",
        "phase": "verify",
        "target_mbps": 80,
        "offered_mbps": 79.8,
        "goodput_mbps": 79.5,
        "loss_percent": 0.38,
        "jitter_ms": 0.12,
        "status": "PASS",
        "passed": True,
    }
    app._event("result", result)
    report = {
        "outcome": "completed",
        "records": [result],
        "recommendations": {"downlink": result},
        "config": {"mode": "auto"},
    }
    app._event("finished", report)
    assert app.downlink_value.get() == "80.00"
    assert "72.00" in app.summary.get()
    assert len(app.table.get_children()) == 1
    for extension in ("json", "csv"):
        path = tmp_path / f"export.{extension}"
        monkeypatch.setattr(
            "udp_probe.gui.filedialog.asksaveasfilename",
            lambda destination=path, **kwargs: str(destination),
        )
        app._export()
        assert path.is_file()
        if extension == "json":
            assert json.loads(path.read_text())["outcome"] == "completed"
        else:
            assert "goodput_mbps" in path.read_text()
    app.update()
    assert app.stop_button.winfo_ismapped()
    assert app.stop_button.winfo_rooty() + app.stop_button.winfo_height() <= (
        app.winfo_rooty() + app.winfo_height()
    )
    screenshot = os.environ.get("UDP_PROBE_GUI_SCREENSHOT")
    if screenshot:
        from PIL import ImageGrab

        app.lift()
        app.update()
        left, top = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab(bbox=(left, top, left + app.winfo_width(), top + app.winfo_height())).save(
            screenshot
        )
    app._save_settings()
    assert (tmp_path / ".udp-rate-probe/settings.json").is_file()


def test_chart_compares_actual_sent_and_received_across_directions(probe_app):
    app = probe_app
    app.records.extend(
        [
            {
                "direction": "downlink",
                "target_mbps": 1000,
                "offered_mbps": 100,
                "goodput_mbps": 96,
                "passed": True,
            },
            {
                "direction": "downlink",
                "target_mbps": 1000,
                "offered_mbps": 200,
                "goodput_mbps": 100,
                "passed": False,
            },
            {
                "direction": "uplink",
                "target_mbps": 1000,
                "offered_mbps": 50,
                "goodput_mbps": 0,
                "passed": False,
            },
        ]
    )
    app._chart()
    sent_lines = app.canvas.find_withtag("sent-rate")
    received_lines = app.canvas.find_withtag("received-rate")
    assert len(sent_lines) == len(received_lines) == 1
    sent = app.canvas.coords(sent_lines[0])
    received = app.canvas.coords(received_lines[0])
    assert len(sent) == len(received) == 6
    assert sent[::2] == received[::2]
    assert all(
        sent_y < received_y for sent_y, received_y in zip(sent[1::2], received[1::2], strict=True)
    )
    assert app.canvas.itemcget(sent_lines[0], "fill") != app.canvas.itemcget(
        received_lines[0], "fill"
    )
    assert len(app.canvas.find_withtag("sent-rate-sample")) == 3
    assert len(app.canvas.find_withtag("received-rate-sample")) == 3
    assert len(app.canvas.find_withtag("failed-trial")) == 4
    axis_labels = [
        app.canvas.itemcget(item, "text") for item in app.canvas.find_withtag("rate-axis-label")
    ]
    assert "230.0" in axis_labels
    assert [
        app.canvas.itemcget(item, "text") for item in app.canvas.find_withtag("trial-axis-label")
    ] == ["1D", "2D", "3U"]


@pytest.mark.parametrize("previous_threshold", [1, 0, 20])
def test_saved_settings_cannot_override_five_percent_threshold(probe_app, previous_threshold):
    app = probe_app
    app.settings_path.parent.mkdir(parents=True)
    app.settings_path.write_text(
        json.dumps({"loss_limit": str(previous_threshold), "start_mbps": "20"}), encoding="utf-8"
    )
    app._load_settings()
    assert app._config().loss_limit == 5
    assert app._config().start_mbps == 20
    for running in (True, False):
        app.running = running
        app._buttons()
        assert str(app.loss_threshold_entry["state"]) == "readonly"
