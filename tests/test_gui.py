import json
import os
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")


def test_gui_controls_results_exports_and_layout(tmp_path, monkeypatch):
    from udp_probe.gui import ProbeApp

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    try:
        app = ProbeApp()
    except tk.TclError as error:
        pytest.skip(f"A graphical display is required: {error}")
    try:
        app.update()
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
            ImageGrab.grab(
                bbox=(left, top, left + app.winfo_width(), top + app.winfo_height())
            ).save(screenshot)
        app._save_settings()
        assert (tmp_path / ".udp-rate-probe/settings.json").is_file()
    finally:
        app.server.close()
        app.destroy()
