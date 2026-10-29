package com.example.udpprobe;

import org.json.JSONException;
import org.json.JSONObject;

final class ProbeConfig {
    final int hostPort;
    final int phonePort;
    final int payloadSize;
    final int drainMs;
    final double minRate;
    final double maxRate;

    ProbeConfig(JSONObject message) throws JSONException {
        hostPort = message.getInt("host_udp_port");
        phonePort = message.getInt("phone_udp_port");
        payloadSize = message.getInt("payload_size");
        drainMs = message.getInt("drain_ms");
        minRate = message.getDouble("min_mbps");
        maxRate = message.getDouble("max_mbps");
        if (hostPort < 1024 || hostPort > 65535 || phonePort < 1024 || phonePort > 65535
                || payloadSize < 128 || payloadSize > 1400 || drainMs < 100 || drainMs > 3000
                || !Double.isFinite(minRate) || !Double.isFinite(maxRate)
                || minRate < 0.01 || maxRate > 1000 || maxRate < minRate) {
            throw new JSONException("Invalid probe configuration from host.");
        }
    }
}
