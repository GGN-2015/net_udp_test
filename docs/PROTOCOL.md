# UDP Rate Probe Protocol v1

The desktop is the TCP server and test controller. The Android app is the TCP
client. Both UDP sockets use IPv4 and remain bound for one probe run. All numeric
rates are decimal Mbps. One TCP connection can carry multiple runs.

## TCP framing

Messages are UTF-8 JSON objects, one per newline, with a maximum frame length of
65,536 bytes including the newline. Every message has a string `type`. Messages
must use finite numbers. Writes are serialized to prevent overlapping frames.
The host accepts one client and requires a valid hello within five seconds.

The phone sends:

```json
{"type":"hello","version":1,"name":"Manufacturer Model"}
```

The host replies with a random positive 63-bit session ID:

```json
{"type":"welcome","version":1,"session":123456789}
```

Use exact 64-bit integer JSON decoding for session, run, and trial IDs. All IDs
are positive and fit signed Java `long`. Session IDs are also carried in UDP
packets. Run IDs identify TCP messages only. A new random run ID is used on each
start, and every trial gets its own random trial ID.
Each three-trial rate test also gets a random positive 63-bit `group` ID, used
only in TCP messages and exported reports.

The host sends `{"type":"ping"}` every two seconds; the phone answers
`{"type":"pong"}`. Idle TCP read/heartbeat expiry is 15 seconds. The TCP reader
continues responding during UDP work.

## Run and trial sequence

Messages in this table carry `run`, except `hello`, `welcome`, `ping`, `pong`, and
`stop_request`. Trial messages also carry `trial`.

| Sender | Type | Fields / action |
| --- | --- | --- |
| Host | `configure` | All `ProbeConfig` fields: UDP ports, mode, direction, rate bounds, duration, size, loss, search budget, drain, tolerance |
| Phone | `configured` | Sent after the phone successfully binds its UDP socket |
| Host | `trial_begin` | `trial`, `direction`, `target_mbps`, `duration`, `phase`, `group`, `repetition`, `repetitions`; reset the receiver counters before acknowledging |
| Phone | `trial_ready` | Receiver and sender state prepared |
| Host | UDP downlink data | Only after `trial_ready` |
| Host | `trial_end` | Downlink sender has finished; phone waits `drain_ms` then snapshots |
| Phone | `receiver_report` | `received`, `duplicates`, `reordered`, `jitter_ms` |
| Host | `send` | For uplink only, after the host's receiver is ready |
| Phone | UDP uplink data | Paced until the requested monotonic duration expires |
| Phone | `sender_report` | `sent`, `errors`, `elapsed` in actual seconds; host waits `drain_ms` and snapshots its receiver |
| Host | `result` | One three-trial median-loss summary, including representative metrics, `direction`, `phase`, `status`, `passed`, and group metadata |
| Host | `complete` | `recommendations`: direction → verified result or `null`; empty for fixed mode |
| Either indirectly | `stop` | Host-to-phone cancellation and UDP cleanup |
| Phone | `stopped` | Acknowledge cleanup after the receiver and worker terminate |
| Phone | `stop_request` | User cancellation request; the host cancels and sends `stop` |
| Phone | `error` | `run`, `message`; abort the run and release sockets |

The host always sends `stop` and waits for `stopped` when finishing a run,
including successful completion. New runs are enabled only after cleanup. Late
messages with another run or trial ID are ignored. TCP loss cancels UDP work and
releases sockets. A peer that fails cleanup acknowledgement is disconnected.

For each target rate and phase, the host repeats the `trial_begin` through
sender/receiver-report exchange exactly three times with distinct trial IDs.
The `group` stays the same, `repetition` is `1`, `2`, then `3`, and `repetitions`
is `3`. Each trial resets UDP counters and sequence numbers. A single `result`
is sent after all three exchanges; the next search decision follows this result.
Fixed mode and longer verification phases use the same grouping rule. An
incomplete group produces no `result`. These optional metadata fields and the
existing trial exchanges are compatible with the Android v1 client, which
ignores unknown fields and displays the grouped `result`.

For downlink, the desktop owns the sender counters and the phone reports receive
counters. For uplink the roles reverse. TCP carries the sender's actual count,
so loss is not estimated from how many packets were planned.

`result` fields include `target_mbps`, `offered_mbps`, `goodput_mbps`,
`loss_percent`, `sent`, `received`, `send_errors`, `elapsed`, `duplicates`,
`reordered`, `jitter_ms`, `status`, `passed`, `direction`, `phase`, and `trial`.
Group metadata includes `group`, `repetitions: 3`, `sample_count: 3`,
`aggregation: "median_loss"`, `sample_trials` (three trial IDs), and
`representative_trial`. `loss_percent` is the median of the three individual
loss percentages. All other measurement fields come from that median-loss
trial, whose ID is also `trial`; ties use the first matching trial. There is no
single `repetition` on the grouped result.

Statuses are `PASS`, `LOSS`, and `SENDER LIMITED`. Phases are `search`, `verify`,
and `fixed`. If any of the three trials fails sender pacing or reports socket
send errors, the grouped status is `SENDER LIMITED`; otherwise the median loss
determines `PASS` or `LOSS`. See the README for formulas and search/verification
rules.

The host fixes `loss_limit` at `5.0` in `configure` and evaluates loss strictly:
median `loss_percent < 5.0` satisfies the loss criterion, while median
`loss_percent >= 5.0` fails. The field remains in the existing configuration
message for compatibility with the Android client. The host chart compares
`offered_mbps` (actual sent) with `goodput_mbps` (unique valid received), both
from the representative trial and using its sender duration.

The host's JSON report keeps raw trials in `samples` and grouped results in
`records`, joined by `group`. Raw trials include `repetition` and their individual
loss and status. Completed trials in an interrupted group remain in `samples`
without a corresponding `records` entry. CSV exports keep one row per raw trial
and append the group's median loss and decision, leaving these columns empty
for incomplete groups.

## UDP wire format

Each UDP payload is exactly the configured `payload_size` bytes. The first
36 bytes form this network-byte-order (big-endian) header:

| Offset | Length | Field |
| --- | --- | --- |
| 0 | 4 | ASCII `URP1` (`55 52 50 31`) |
| 4 | 1 | Version `1` |
| 5 | 3 | Reserved zero bytes |
| 8 | 8 | Session ID, unsigned 64-bit, positive signed-long range |
| 16 | 8 | Trial ID, unsigned 64-bit, positive signed-long range |
| 24 | 4 | Sequence number, unsigned 32-bit, starts at zero each trial |
| 28 | 8 | Sender monotonic timestamp in nanoseconds |
| 36 | remaining | Padding bytes |

Python struct format: `!4sB3xQQIQ`.

The receiver accepts only the TCP peer's IPv4 address, negotiated UDP source
port, valid magic/version, current session/trial IDs, expected datagram size, and
a bounded sequence range. The range is sized to 1.5 times the planned packet
count plus 4096. A bitmap counts unique packets without retaining every packet.
Duplicates do not contribute goodput. A previously unseen sequence below the
highest observed sequence increments the reorder counter.

Send pacing uses monotonic elapsed time, batches of at most 64 packets, and
short waits of at most one millisecond. Reusable packet buffers avoid per-packet
payload allocation. All data traffic is unicast between the negotiated peers.

## Scope and trust

This protocol serves trusted local benchmarking. TCP is plaintext and has no
identity authentication. Session IDs isolate test traffic, not attackers. Bind
the host to a trusted interface or restrict access with the host firewall.
