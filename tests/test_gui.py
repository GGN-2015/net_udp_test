import csv
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
    app.samples.clear()
    app.report = None
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
        "group": 123,
        "sample_count": 3,
    }
    samples = [
        {
            **{key: value for key, value in result.items() if key != "sample_count"},
            "trial": index,
            "repetition": index,
            "repetitions": 3,
            "loss_percent": loss,
            "status": "PASS" if loss < 5 else "LOSS",
            "passed": loss < 5,
        }
        for index, loss in enumerate((0.1, 0.38, 10), 1)
    ]
    app._event(
        "trial",
        {
            "direction": "downlink",
            "phase": "verify",
            "rate": 80,
            "duration": 3,
            "repetition": 2,
            "repetitions": 3,
        },
    )
    assert "trial 2/3" in app.trial_status.get()
    for sample in samples:
        app._event("sample", sample)
    assert app.records == []
    app._event("result", result)
    report = {
        "outcome": "completed",
        "records": [result],
        "samples": samples,
        "recommendations": {"downlink": result},
        "config": {"mode": "auto"},
    }
    app._event("finished", report)
    assert app.downlink_value.get() == "80.00"
    assert "72.00" in app.summary.get()
    assert len(app.table.get_children()) == 1
    assert app.table.heading("loss", "text") == "Median loss %"
    assert "1 rate tests, 3 individual trials" in app.trial_status.get()
    for extension in ("json", "csv"):
        path = tmp_path / f"export.{extension}"
        monkeypatch.setattr(
            "udp_probe.gui.filedialog.asksaveasfilename",
            lambda destination=path, **kwargs: str(destination),
        )
        app._export()
        assert path.is_file()
        if extension == "json":
            exported = json.loads(path.read_text())
            assert exported["outcome"] == "completed"
            assert exported["samples"] == samples
            assert exported["records"] == [result]
        else:
            with path.open(newline="") as output:
                rows = list(csv.DictReader(output))
            assert len(rows) == 3
            assert [float(row["loss_percent"]) for row in rows] == [0.1, 0.38, 10]
            assert all(row["median_loss_percent"] == "0.38" for row in rows)
            assert all(row["group_status"] == "PASS" for row in rows)
            assert all(row["group_passed"] == "True" for row in rows)
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


def test_partial_group_can_be_exported_without_a_median(probe_app, tmp_path, monkeypatch):
    app = probe_app
    app.running = True
    app._event("running", {})
    sample = {
        "group": 123,
        "trial": 1,
        "direction": "downlink",
        "target_mbps": 80,
        "repetition": 1,
        "repetitions": 3,
        "loss_percent": 2,
        "status": "PASS",
    }
    app._event("sample", sample)
    assert str(app.export_button["state"]) == "disabled"
    app._event(
        "finished",
        {
            "outcome": "stopped",
            "records": [],
            "samples": [sample],
            "recommendations": {},
            "config": {"mode": "fixed"},
        },
    )
    assert str(app.export_button["state"]) == "normal"
    assert not app.table.get_children()
    path = tmp_path / "partial.csv"
    monkeypatch.setattr("udp_probe.gui.filedialog.asksaveasfilename", lambda **kwargs: str(path))
    app._export()
    with path.open(newline="") as output:
        rows = list(csv.DictReader(output))
    assert len(rows) == 1
    assert rows[0]["loss_percent"] == "2"
    for field in ("median_loss_percent", "group_status", "group_passed"):
        assert rows[0][field] == ""
    app._event("running", {})
    assert app.samples == app.records == []
    assert str(app.export_button["state"]) == "disabled"


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
