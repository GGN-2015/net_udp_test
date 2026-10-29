package com.example.udpprobe;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.BitSet;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.LockSupport;

final class DatagramEngine implements AutoCloseable {
    private static final int MAGIC = 0x55525031;
    private static final int HEADER_SIZE = 36;
    private final DatagramSocket socket;
    private final InetAddress host;
    private final int hostPort;
    private final long session;
    private final Thread receiver;
    private final AtomicBoolean closed = new AtomicBoolean();
    private long trial = -1;
    private int payloadSize;
    private int maxPackets;
    private BitSet seen = new BitSet();
    private long received;
    private long duplicates;
    private long reordered;
    private long highest = -1;
    private long lastTransit;
    private boolean hasTransit;
    private double jitterNs;

    DatagramEngine(InetAddress host, ProbeConfig config, long session) throws IOException {
        this.host = host;
        this.hostPort = config.hostPort;
        this.session = session;
        socket = new DatagramSocket(null);
        try {
            socket.setReceiveBufferSize(4 * 1024 * 1024);
            socket.setSendBufferSize(4 * 1024 * 1024);
            socket.bind(new InetSocketAddress(config.phonePort));
            socket.setSoTimeout(100);
        } catch (IOException error) {
            socket.close();
            throw error;
        }
        receiver = new Thread(this::receiveLoop, "udp-receiver");
        receiver.start();
    }

    synchronized void begin(long trial, double rate, double duration, int size) {
        this.trial = trial;
        payloadSize = size;
        maxPackets = (int) Math.ceil(rate * 1_000_000 / 8 / size * duration * 1.5) + 4096;
        seen = new BitSet(maxPackets);
        received = duplicates = reordered = 0;
        highest = -1;
        jitterNs = 0;
        hasTransit = false;
    }

    private void receiveLoop() {
        byte[] bytes = new byte[65536];
        ByteBuffer buffer = ByteBuffer.wrap(bytes).order(ByteOrder.BIG_ENDIAN);
        DatagramPacket packet = new DatagramPacket(bytes, bytes.length);
        while (!closed.get()) {
            try {
                packet.setLength(bytes.length);
                socket.receive(packet);
                long receivedNs = System.nanoTime();
                if (!packet.getAddress().equals(host) || packet.getPort() != hostPort
                        || packet.getLength() < HEADER_SIZE || buffer.getInt(0) != MAGIC
                        || buffer.get(4) != 1 || buffer.getLong(8) != session) {
                    continue;
                }
                synchronized (this) {
                    if (buffer.getLong(16) != trial || packet.getLength() != payloadSize) {
                        continue;
                    }
                    long sequence = Integer.toUnsignedLong(buffer.getInt(24));
                    if (sequence >= maxPackets) {
                        continue;
                    }
                    if (seen.get((int) sequence)) {
                        duplicates++;
                        continue;
                    }
                    seen.set((int) sequence);
                    received++;
                    if (sequence < highest) {
                        reordered++;
                    }
                    highest = Math.max(highest, sequence);
                    long transit = receivedNs - buffer.getLong(28);
                    if (hasTransit) {
                        jitterNs += (Math.abs(transit - lastTransit) - jitterNs) / 16;
                    }
                    lastTransit = transit;
                    hasTransit = true;
                }
            } catch (SocketTimeoutException ignored) {
            } catch (IOException error) {
                return;
            }
        }
    }

    synchronized JSONObject snapshot() throws JSONException {
        return new JSONObject().put("received", received).put("duplicates", duplicates)
                .put("reordered", reordered).put("jitter_ms", jitterNs / 1_000_000);
    }

    JSONObject send(long trial, double rate, double duration, int size, AtomicBoolean cancel)
            throws JSONException {
        byte[] bytes = new byte[size];
        ByteBuffer buffer = ByteBuffer.wrap(bytes).order(ByteOrder.BIG_ENDIAN);
        buffer.putInt(0, MAGIC);
        buffer.put(4, (byte) 1);
        buffer.putLong(8, session);
        buffer.putLong(16, trial);
        DatagramPacket packet = new DatagramPacket(bytes, bytes.length, host, hostPort);
        double packetsPerSecond = rate * 1_000_000 / 8 / size;
        long start = System.nanoTime();
        long sent = 0;
        long errors = 0;
        while (!cancel.get() && !closed.get() && !Thread.currentThread().isInterrupted()) {
            double elapsed = (System.nanoTime() - start) / 1_000_000_000.0;
            if (elapsed >= duration) {
                break;
            }
            long due = Math.min((long) (elapsed * packetsPerSecond) + 1, sent + 64);
            if (due <= sent) {
                long wait = (long) ((sent / packetsPerSecond - elapsed) * 1_000_000_000);
                LockSupport.parkNanos(Math.min(Math.max(wait, 1), 1_000_000));
                continue;
            }
            for (long remaining = due - sent; remaining > 0; remaining--) {
                if (cancel.get() || (System.nanoTime() - start) / 1_000_000_000.0 >= duration) {
                    break;
                }
                buffer.putInt(24, (int) sent);
                buffer.putLong(28, System.nanoTime());
                try {
                    socket.send(packet);
                    sent++;
                } catch (IOException error) {
                    if (!cancel.get() && !closed.get()) {
                        errors++;
                        LockSupport.parkNanos(1_000_000);
                    }
                    break;
                }
            }
        }
        return new JSONObject().put("sent", sent).put("errors", errors)
                .put("elapsed", Math.max((System.nanoTime() - start) / 1_000_000_000.0, 0.000001));
    }

    @Override
    public void close() {
        closed.set(true);
        socket.close();
        if (Thread.currentThread() != receiver) {
            try {
                receiver.join(1000);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
            }
        }
    }
}
