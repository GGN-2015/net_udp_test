import sys


def main():
    try:
        from .gui import ProbeApp
    except ModuleNotFoundError as error:
        if error.name not in {"tkinter", "_tkinter"}:
            raise
        print(
            "Tkinter is required. On Windows, install Python with Tcl/Tk support. "
            "On Ubuntu/Debian, install python3-tk; on Fedora, install python3-tkinter.",
            file=sys.stderr,
        )
        return 1
    ProbeApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
