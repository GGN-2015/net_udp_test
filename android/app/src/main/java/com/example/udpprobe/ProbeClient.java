package com.example.udpprobe;

import android.os.Build;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

final class ProbeClient implements AutoCloseable {
    interface Listener {
        void state(String message, boolean connected);
        void log(String message);
        void trial(JSONObject message);
        void result(JSONObject message);
        void complete(JSONObject message);
    }

    private static final class Run {
        final long id;
        final ProbeConfig config;
        final DatagramEngine udp;
        final AtomicBoolean cancelled = new AtomicBoolean();
        final AtomicBoolean working = new AtomicBoolean();
        final ExecutorService executor = Executors.newSingleThreadExecutor();
        long trial;
        double rate;
        double duration;
        String direction;

        Run(long id, ProbeConfig config, DatagramEngine udp) {
            this.id = id;
            this.config = config;
            this.udp = udp;
        }

        void close() {
            cancelled.set(true);
            udp.close();
            executor.shutdownNow();
            try {
                executor.awaitTermination(2, TimeUnit.SECONDS);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
            }
        }
    }

    private final Listener listener;
    private final Socket socket = new Socket();
    private final AtomicBoolean closed = new AtomicBoolean();
    private OutputStream output;
    private volatile Run run;
    private long session;
    private boolean welcomed;

    ProbeClient(Listener listener) {
        this.listener = listener;
    }

    void connect(String host, int port) {
        new Thread(() -> connectionLoop(host, port), "tcp-control").start();
    }

    private void connectionLoop(String host, int port) {
        try {
            listener.state("Connecting to " + host + ":" + port + "…", false);
            socket.connect(new InetSocketAddress(host, port), 5000);
            socket.setTcpNoDelay(true);
            socket.setKeepAlive(true);
            socket.setSoTimeout(15000);
            synchronized (this) {
                output = socket.getOutputStream();
            }
            send(new JSONObject().put("type", "hello").put("version", 1)
                    .put("name", Build.MANUFACTURER + " " + Build.MODEL));
            try (BufferedInputStream input = new BufferedInputStream(socket.getInputStream())) {
                while (!closed.get()) {
                    handle(readMessage(input), socket.getInetAddress());
                }
            }
        } catch (IOException | JSONException | IllegalArgumentException error) {
            if (!closed.get()) {
                listener.log("Connection ended: " + error.getMessage());
            }
        } finally {
            close();
            listener.state("Disconnected", false);
        }
    }

    private JSONObject readMessage(BufferedInputStream input) throws IOException, JSONException {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        for (int count = 0; count < 65536; count++) {
            int value = input.read();
            if (value == -1) {
                throw new EOFException("Host disconnected.");
            }
            if (value == '\n') {
                JSONObject message = new JSONObject(bytes.toString(StandardCharsets.UTF_8.name()));
                message.getString("type");
                return message;
            }
            bytes.write(value);
        }
        throw new IOException("Control message is too large.");
    }

    private synchronized void send(JSONObject message) throws IOException {
        if (closed.get() || output == null) {
            throw new IOException("TCP connection is closed.");
        }
        byte[] bytes = (message.toString() + "\n").getBytes(StandardCharsets.UTF_8);
        if (bytes.length > 65536) {
            throw new IOException("Control message is too large.");
        }
        output.write(bytes);
        output.flush();
    }

    private void handle(JSONObject message, InetAddress host) throws JSONException, IOException {
        String type = message.getString("type");
        if (type.equals("ping")) {
            send(new JSONObject().put("type", "pong"));
            return;
        }
        if (type.equals("welcome")) {
            if (welcomed || message.getInt("version") != 1 || message.getLong("session") <= 0) {
                throw new IOException("Unsupported or repeated host welcome.");
            }
            welcomed = true;
            session = message.getLong("session");
            listener.state("Connected · ready for host", true);
            listener.log("TCP connected. Start the probe from the desktop.");
            return;
        }
        if (!welcomed) {
            throw new IOException("Expected host welcome.");
        }
        long runId = message.getLong("run");
        if (type.equals("configure")) {
            if (run != null) {
                run.close();
                run = null;
            }
            try {
                ProbeConfig config = new ProbeConfig(message);
                Run current = new Run(runId, config, new DatagramEngine(host, config, session));
                run = current;
                if (closed.get()) {
                    current.close();
                    return;
                }
                listener.log("UDP phone :" + config.phonePort + " ↔ host :" + config.hostPort
                        + " · " + config.payloadSize + " bytes");
                send(new JSONObject().put("type", "configured").put("run", runId));
            } catch (IOException | JSONException error) {
                sendError(runId, error.getMessage());
            }
            return;
        }
        if (type.equals("stop")) {
            Run current = run;
            if (current != null && current.id == runId) {
                current.close();
                run = null;
            }
            send(new JSONObject().put("type", "stopped").put("run", runId));
            listener.state("Connected · ready for host", true);
            return;
        }
        Run current = run;
        if (current == null || current.id != runId || current.cancelled.get()) {
            return;
        }
        switch (type) {
            case "trial_begin":
                if (current.working.get()) {
                    throw new IOException("Previous UDP trial is still running.");
                }
                current.trial = message.getLong("trial");
                current.rate = message.getDouble("target_mbps");
                current.duration = message.getDouble("duration");
                current.direction = message.getString("direction");
                if (!Double.isFinite(current.rate) || !Double.isFinite(current.duration)
                        || current.rate < current.config.minRate || current.rate > current.config.maxRate
                        || current.duration < 0.5 || current.duration > 30
                        || !(current.direction.equals("downlink") || current.direction.equals("uplink"))) {
                    throw new IOException("Invalid trial parameters.");
                }
                current.udp.begin(current.trial, current.rate, current.duration, current.config.payloadSize);
                listener.trial(message);
                send(new JSONObject().put("type", "trial_ready").put("run", runId)
                        .put("trial", current.trial));
                break;
            case "send":
            case "trial_end":
                if (message.getLong("trial") != current.trial || !current.working.compareAndSet(false, true)
                        || (type.equals("send") != current.direction.equals("uplink"))) {
                    throw new IOException("Invalid or overlapping trial command.");
                }
                current.executor.execute(() -> work(current, type.equals("send")));
                break;
            case "result":
                listener.result(message);
                break;
            case "complete":
                listener.complete(message);
                break;
            default:
                throw new IOException("Unknown control message: " + type);
        }
    }

    private void work(Run current, boolean sending) {
        try {
            JSONObject report;
            if (sending) {
                report = current.udp.send(current.trial, current.rate, current.duration,
                        current.config.payloadSize, current.cancelled);
                report.put("type", "sender_report");
            } else {
                Thread.sleep(current.config.drainMs);
                report = current.udp.snapshot().put("type", "receiver_report");
            }
            report.put("run", current.id).put("trial", current.trial);
            current.working.set(false);
            if (!current.cancelled.get()) {
                send(report);
            }
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
        } catch (IOException | JSONException error) {
            if (!current.cancelled.get()) {
                listener.log("UDP worker failed: " + error.getMessage());
                close();
            }
        } finally {
            current.working.set(false);
        }
    }

    private void sendError(long runId, String text) throws JSONException, IOException {
        listener.log("Probe error: " + text);
        send(new JSONObject().put("type", "error").put("run", runId).put("message", text));
    }

    void requestStop() {
        Run current = run;
        if (current != null) {
            current.cancelled.set(true);
        }
        new Thread(() -> {
            try {
                send(new JSONObject().put("type", "stop_request"));
            } catch (IOException | JSONException error) {
                close();
            }
        }, "stop-request").start();
    }

    @Override
    public void close() {
        closed.set(true);
        try {
            socket.close();
        } catch (IOException ignored) {
        }
        Run current = run;
        if (current != null) {
            current.close();
        }
    }
}
