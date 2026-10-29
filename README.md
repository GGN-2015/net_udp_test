# UDP Rate Probe

A Python desktop host for Windows/Linux and a native Android companion that find
the highest sustainable UDP send rate between a computer and a phone. Both GUIs
and all project documentation are in English.

The phone connects to the host over TCP first. The desktop then controls both UDP
ports, direction, packet size, rates, start, and stop. Measurements run in worker
threads, so both interfaces remain responsive.

## Features

- **Automatic rate search:** increase the offered rate, bracket failure, bisect
  the interval, then verify a passing candidate with a longer trial.
- **Fixed-rate mode:** measure a selected rate without searching.
- **Downlink and uplink:** host → phone, phone → host, or both sequentially.
- **Measured results:** actual offered rate, received goodput, loss, duplicate
  packets, reordering, interarrival jitter, and sender errors.
- **Honest pacing results:** a slow sender is reported as `SENDER LIMITED` rather
  than mistaken for a passing high-rate link.
- **Desktop dashboard:** trial chart, results table, confirmed rates, suggested
  10% headroom, JSON/CSV export, and saved settings.
- **Connection lifecycle:** heartbeat, peer/version checks, cancellation from
  either endpoint, disconnect handling, and repeated runs on one TCP connection.

## Layout

```text
pyproject.toml              Python package and desktop entry point
uv.lock                     Locked Python dependencies for reproducible environments
.python-version             Default project interpreter (Python 3.12)
src/udp_probe/             Desktop GUI, controller, UDP engine, and metrics
android/                   Android Studio / Gradle APK project and wrapper
tests/                     Unit, real-socket, and optional Java interop tests
docs/PROTOCOL.md            TCP messages and UDP wire format
```

## 1. Run the desktop host

Requires **Python 3.10+ with Tkinter** and [uv](https://docs.astral.sh/uv/).
The application has no runtime Python dependencies; uv manages the project
environment, development tools, and lockfile. The default project interpreter
is Python 3.12, selected by `.python-version`.

### Windows (PowerShell)

Install uv and the project interpreter with `uv python install 3.12`, then run
from the repository root:

```powershell
uv python install 3.12
uv sync
uv run udp-rate-probe
```

### Linux

Install uv, then run from the repository root:

```bash
uv python install 3.12
uv sync
uv run udp-rate-probe
```

A graphical desktop/X11/Wayland session is required. If using a system Python
instead, install its matching Tkinter package (`python3-tk` on Ubuntu/Debian or
`python3-tkinter` on Fedora), then select it with
`uv sync --python /path/to/python3.12`.

After synchronization, `uv run python -m udp_probe` is an alternative launch
command. `uv run` uses the editable project environment created by `uv sync`.

## 2. Build and install the Android app

Requires **Android 8.0+ (API 26)** on the phone. The project uses Java 17,
Android Gradle Plugin 8.7.3, Gradle 8.9, and compile/target SDK 35. It uses native
Android views and no third-party Android runtime dependencies.

Open `android/` in Android Studio, install SDK platform 35 and the requested build
tools, then select **Build APK(s)** or run the Gradle wrapper.

Windows:

```powershell
$env:JAVA_HOME = "C:\path\to\jdk-17"
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
cd android
.\gradlew.bat assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

Linux:

```bash
export JAVA_HOME=/path/to/jdk-17
export ANDROID_HOME="$HOME/Android/Sdk"
cd android
sh gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

You can also set `sdk.dir` in the ignored `android/local.properties` file.
The first Gradle build downloads dependencies. The installable debug APK is
`android/app/build/outputs/apk/debug/app-debug.apk`. Release builds require your
own signing configuration; no private signing keys are included.

## 3. Connect and probe

1. Connect the computer and phone to the same LAN/Wi-Fi. A wired computer on the
   same router also works. Use a network without wireless client isolation.
2. In the desktop GUI, leave **Bind IPv4** as `0.0.0.0` (all interfaces), choose
   the **TCP port** (default `5001`), and click **Start listening**.
3. In the phone app, enter the computer's actual LAN IPv4 address and the same
   TCP port. Click **Connect**. The desktop displays the connected phone.
   `0.0.0.0` is a bind address; do not enter it on the phone.
4. On the desktop, configure **Host UDP port** (`5002`) and **Phone UDP port**
   (`5003`), direction, rates, trial duration, and acceptable loss.
5. Click **Start probe** on the desktop. Keep the Android app visible. Both
   endpoints show results after each trial. **Stop probe** on either endpoint
   cancels the run and leaves TCP connected for another run.
6. Read the confirmed rate cards after an automatic run. Use **Export results**
   for the full JSON report or a CSV of individual trials.

The host controls the phone's UDP port through TCP. The phone only needs to know
the host IPv4 address and TCP port beforehand. Each endpoint binds its configured
UDP port for both sending and receiving. The TCP client uses an OS-selected local
ephemeral port. TCP and UDP port numbers may be identical because they are
separate transports.

The Android app keeps the display awake while visible. It disconnects when sent
to the background, preventing unattended traffic. Reconnect after returning.
Only one phone is accepted at a time.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| TCP port | 5001 | Host control listener; match this on the phone |
| Host / phone UDP port | 5002 / 5003 | Each endpoint's UDP bind and peer destination |
| Direction | both | Downlink, then uplink, using separate searches |
| Probe mode | auto | Automatic search or one fixed-rate trial per direction |
| Minimum / start / maximum | 1 / 10 / 200 Mbps | Search bounds; start is also the fixed-mode target |
| Trial duration | 3 seconds | Each search or fixed-rate measurement |
| Datagram bytes | 1200 | Total UDP payload, including the 36-byte probe header |
| Allowed loss | 1% | Maximum lost unique packets for a passing trial |
| Max search trials | 10 | Search budget per direction, excluding verification |
| Drain time | 400 ms | Receive grace period after the sender finishes |
| Search tolerance | 0.05 | Stop when the pass/fail interval is within 5% |

Ports must be 1024–65535. Rates must satisfy
`0.01 <= minimum <= start <= maximum <= 1000 Mbps`; duration is 0.5–30 seconds,
datagram size 128–1400 bytes, loss 0–20%, drain time 100–3000 ms, search budget
1–30, and tolerance 0.01–0.25. A requested rate is a target, not a guarantee.

Desktop settings are stored in `~/.udp-rate-probe/settings.json`. The Android app
remembers the last host and TCP port in private preferences.

## How the best rate is determined

For each selected direction:

1. Test the start rate. Double after passing trials until a failure or the
   configured maximum; halve after failures until a passing rate or the minimum.
2. Once both a passing and failing bound exist, bisect until the interval is
   within the configured tolerance or the search budget is exhausted.
3. Re-test the highest passing candidate for
   `min(30, max(3, trial_duration * 1.5))` seconds. If it fails, try up to two
   lower previously passing candidates. Only a passing verification produces a
   confirmed recommendation. Otherwise the GUI shows **No pass**.

A trial passes when it receives at least one unique packet, loss is within the
configured threshold, there are no socket send errors, and the actual offered
rate is within 10% of the target (or one packet's rate, whichever is larger, to
handle low-rate quantization). A target that the sender cannot pace is marked
`SENDER LIMITED` and treated as a failing bound.

Rates use decimal Mbps and count UDP payload bytes, including the probe header:

```text
offered Mbps = sent_packets * datagram_bytes * 8 / actual_sender_seconds / 1e6
goodput Mbps = unique_received_packets * datagram_bytes * 8 / actual_sender_seconds / 1e6
loss %       = (sent_packets - unique_received_packets) / sent_packets * 100
```

Sequence numbers deduplicate packets and identify reordering. Jitter is an
exponentially smoothed interarrival transit variation (`1/16` update), computed
using monotonic send/receive timestamps. Clock synchronization is unnecessary
for this variation metric; the application does not report one-way latency.
Packets arriving after the drain window are counted as lost.

The result describes the tested devices, payload size, direction, and current
network conditions. It includes endpoint CPU, socket buffers, and OS scheduling
limits. This is an application-level paced test with small packet batches, not
hardware line-rate pacing or Wi-Fi PHY capacity. Test several times for variable
Wi-Fi conditions, and use the suggested 10% headroom as an operating starting
point. Reaching the configured maximum means that maximum passed, not that the
link's absolute limit was found. Both directions are sequential, not a
simultaneous full-duplex benchmark.

## Firewall and connectivity

Allow inbound TCP on the host control port and inbound UDP on the host UDP port.
The phone must be reachable on its configured UDP port from the computer.

Example Windows firewall rules, from an elevated PowerShell on a private LAN:

```powershell
New-NetFirewallRule -DisplayName "UDP Probe TCP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5001 -Profile Private -RemoteAddress LocalSubnet
New-NetFirewallRule -DisplayName "UDP Probe UDP" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5002 -Profile Private -RemoteAddress LocalSubnet
```

Example Ubuntu UFW rules; replace the subnet with your actual trusted LAN:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 5001 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 5002 proto udp
```

| Symptom | Check |
| --- | --- |
| TCP connection fails | Correct host LAN IPv4, listener active, matching TCP port, firewall, same LAN |
| TCP connects but UDP has 100% loss | UDP firewall rules, guest/client isolation, VPN routing, selected UDP ports |
| Address already in use | Choose another UDP/TCP port or close the process using it |
| Sender limited | Lower the maximum, increase datagram size, close CPU-heavy workloads |
| Tkinter missing | Install a Tcl/Tk-enabled Python or your Linux Tkinter package |
| App disconnects | Keep it visible; reconnect after switching apps or losing Wi-Fi |
| Gradle cannot find SDK | Configure `ANDROID_HOME` or `android/local.properties` |

Use this tool on trusted networks you own or have permission to test. TCP control
is plaintext and unauthenticated; do not expose its listener to the public
Internet. UDP session/trial IDs and peer-address checks reject unrelated packets
but are not authentication. The probe can saturate the tested link and consume
phone battery. No cloud service, account, analytics, or Internet traffic is
required during measurement.

## Development and verification

The `dev` dependency group is installed by default. Commit `uv.lock` alongside
dependency changes; use `uv sync --locked` to require the checked-in resolution.

```bash
uv sync
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
cd android
sh gradlew assembleDebug lintDebug
```

On Windows use `gradlew.bat` in place of `sh gradlew`.

Tests cover configuration, search convergence, rate classification, loss,
duplicates/reordering, control framing, actual loopback UDP traffic, both
directions, repeated runs, stop/restart, reconnect, version rejection, and port
conflicts. Tests bind only loopback addresses.

Optional JVM interoperability tests compile and run the **actual Android TCP
client and UDP engine** against the Python host. Supply a Java 17+ JDK and an
`org.json` JAR, such as `org.json:json:20240303`, and set `UDP_PROBE_JSON_JAR` to
its absolute path before running pytest. Only `android.os.Build` is stubbed for
the device name; the TCP protocol, pacing, and UDP receiver are the production
Java classes. Without that environment variable these tests are skipped.

```powershell
$env:UDP_PROBE_JSON_JAR = "C:\path\to\json-20240303.jar"
uv run pytest -q
```

Android compilation/lint and JVM interoperability validate the implementation;
real-phone Wi-Fi throughput and Android screen behavior should be checked on
the target device. The desktop GUI can be smoke-tested on any display with
Tkinter installed.

## License

MIT. See `LICENSE`. The generated Gradle wrapper retains its upstream license.
