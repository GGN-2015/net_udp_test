package com.example.udpprobe;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

import java.util.Locale;

public final class MainActivity extends Activity {
    private static final int INK = Color.rgb(15, 23, 42);
    private static final int MUTED = Color.rgb(100, 116, 139);
    private static final int BLUE = Color.rgb(37, 99, 235);
    private EditText hostInput;
    private EditText portInput;
    private Button connectButton;
    private Button stopButton;
    private TextView stateText;
    private TextView trialText;
    private TextView goodputText;
    private TextView lossText;
    private TextView summaryText;
    private TextView logText;
    private ProbeClient client;
    private boolean connected;
    private boolean connecting;
    private int generation;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(INK);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        LinearLayout header = column();
        header.setPadding(dp(22), dp(24), dp(22), dp(22));
        header.setBackgroundColor(INK);
        header.addView(text("UDP RATE PROBE", 25, Color.WHITE, true));
        header.addView(text("ANDROID COMPANION  /  TCP + UDP", 11, Color.rgb(148, 163, 184), false));
        root.addView(header);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(241, 245, 249));
        LinearLayout content = column();
        content.setPadding(dp(16), dp(16), dp(16), dp(24));
        scroll.addView(content);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        LinearLayout connection = card(content, "Connect to desktop");
        connection.addView(text("Enter the host's LAN IPv4 address. Start listening on the desktop first.", 13, MUTED, false));
        hostInput = new EditText(this);
        hostInput.setHint("Host IPv4 address, e.g. 192.168.1.10");
        hostInput.setSingleLine(true);
        hostInput.setInputType(InputType.TYPE_CLASS_PHONE);
        connection.addView(hostInput, new LinearLayout.LayoutParams(-1, -2));
        portInput = new EditText(this);
        portInput.setHint("TCP control port");
        portInput.setSingleLine(true);
        portInput.setInputType(InputType.TYPE_CLASS_NUMBER);
        connection.addView(portInput, new LinearLayout.LayoutParams(-1, -2));
        SharedPreferences preferences = getPreferences(MODE_PRIVATE);
        hostInput.setText(preferences.getString("host", ""));
        portInput.setText(preferences.getString("port", "5001"));
        LinearLayout actions = new LinearLayout(this);
        connectButton = new Button(this);
        connectButton.setText("Connect");
        connectButton.setTextColor(Color.WHITE);
        connectButton.setBackgroundTintList(android.content.res.ColorStateList.valueOf(BLUE));
        connectButton.setOnClickListener(view -> toggleConnection());
        actions.addView(connectButton, new LinearLayout.LayoutParams(0, -2, 1));
        stopButton = new Button(this);
        stopButton.setText("Stop probe");
        stopButton.setEnabled(false);
        stopButton.setOnClickListener(view -> {
            if (client != null) {
                client.requestStop();
                trialText.setText("Stop requested. Waiting for the host…");
            }
        });
        actions.addView(stopButton, new LinearLayout.LayoutParams(0, -2, 1));
        connection.addView(actions);
        stateText = text("Disconnected", 13, MUTED, true);
        connection.addView(stateText);

        LinearLayout live = card(content, "Live measurement");
        trialText = text("Connect, then start the probe from the desktop.", 14, MUTED, false);
        live.addView(trialText);
        goodputText = text("— Mbps", 36, BLUE, true);
        live.addView(goodputText);
        live.addView(text("Received UDP payload rate", 12, MUTED, false));
        lossText = text("Loss —   |   Jitter —", 14, INK, false);
        live.addView(lossText);

        LinearLayout recommendation = card(content, "Confirmed sustainable rates");
        summaryText = text("No completed auto probe yet.\nDownlink: host → phone\nUplink: phone → host", 15, INK, false);
        recommendation.addView(summaryText);
        recommendation.addView(text("Both directions are tested separately. Keep this app visible and the phone on the same LAN as the host.", 12, MUTED, false));

        LinearLayout activity = card(content, "Session activity");
        logText = text("Ready. Use only on networks you are authorized to test.\n", 12, MUTED, false);
        logText.setTypeface(Typeface.MONOSPACE);
        logText.setTextIsSelectable(true);
        activity.addView(logText);
        setContentView(root);
        root.requestApplyInsets();
    }

    private void toggleConnection() {
        if (client != null && (connected || connecting)) {
            disconnect();
            return;
        }
        String host = hostInput.getText().toString().trim();
        int port;
        try {
            String[] octets = host.split("\\.", -1);
            if (octets.length != 4) {
                throw new IllegalArgumentException();
            }
            for (String octet : octets) {
                if (!octet.matches("[0-9]{1,3}") || Integer.parseInt(octet) > 255) {
                    throw new IllegalArgumentException();
                }
            }
            port = Integer.parseInt(portInput.getText().toString());
            if (port < 1024 || port > 65535) {
                throw new IllegalArgumentException();
            }
        } catch (IllegalArgumentException error) {
            stateText.setText("Enter a valid IPv4 address and TCP port (1024–65535).");
            return;
        }
        getPreferences(MODE_PRIVATE).edit().putString("host", host).putString("port", String.valueOf(port)).apply();
        int currentGeneration = ++generation;
        connecting = true;
        hostInput.setEnabled(false);
        portInput.setEnabled(false);
        connectButton.setText("Cancel");
        client = new ProbeClient(new ProbeClient.Listener() {
            private void update(Runnable action) {
                runOnUiThread(() -> {
                    if (currentGeneration == generation && !isDestroyed()) {
                        action.run();
                    }
                });
            }

            @Override
            public void state(String message, boolean ready) {
                update(() -> {
                    connected = ready;
                    if (ready || message.equals("Disconnected")) {
                        connecting = false;
                    }
                    stateText.setText(message);
                    stateText.setTextColor(ready ? Color.rgb(5, 150, 105) : MUTED);
                    connectButton.setText(ready ? "Disconnect" : (connecting ? "Cancel" : "Connect"));
                    stopButton.setEnabled(ready);
                    hostInput.setEnabled(!ready && !connecting);
                    portInput.setEnabled(!ready && !connecting);
                });
            }

            @Override
            public void log(String message) {
                update(() -> appendLog(message));
            }

            @Override
            public void trial(JSONObject message) {
                update(() -> {
                    trialText.setText(String.format(Locale.US, "%s · %s\nTarget %.2f Mbps · %.1f s",
                            message.optString("direction"), message.optString("phase"),
                            message.optDouble("target_mbps"), message.optDouble("duration")));
                    summaryText.setText("Probe in progress. Recommendations appear after verification.");
                    stopButton.setEnabled(true);
                });
            }

            @Override
            public void result(JSONObject message) {
                update(() -> {
                    goodputText.setText(String.format(Locale.US, "%.2f Mbps", message.optDouble("goodput_mbps")));
                    lossText.setText(String.format(Locale.US, "Loss %.2f%%  |  Jitter %.2f ms\n%s",
                            message.optDouble("loss_percent"), message.optDouble("jitter_ms"), message.optString("status")));
                    appendLog(String.format(Locale.US, "%s %.2f Mbps → %s (loss %.2f%%)", message.optString("direction"),
                            message.optDouble("target_mbps"), message.optString("status"), message.optDouble("loss_percent")));
                });
            }

            @Override
            public void complete(JSONObject message) {
                update(() -> {
                    JSONObject rates = message.optJSONObject("recommendations");
                    StringBuilder summary = new StringBuilder();
                    for (String direction : new String[]{"downlink", "uplink"}) {
                        JSONObject result = rates == null ? null : rates.optJSONObject(direction);
                        if (result != null) {
                            summary.append(String.format(Locale.US, "%s: %.2f Mbps\nWith 10%% headroom: %.2f Mbps\n\n",
                                    direction, result.optDouble("target_mbps"), result.optDouble("target_mbps") * 0.9));
                        } else if (rates != null && rates.has(direction)) {
                            summary.append(direction).append(": no confirmed passing rate\n");
                        }
                    }
                    summaryText.setText(summary.length() > 0 ? summary.toString().trim()
                            : "Fixed-rate trial completed. See live measurement and the desktop report.");
                    trialText.setText("Probe complete. Ready for another run.");
                    stopButton.setEnabled(false);
                });
            }
        });
        client.connect(host, port);
    }

    private void disconnect() {
        ProbeClient previous = client;
        if (previous != null) {
            new Thread(previous::close, "disconnect").start();
        }
    }

    @Override
    protected void onStop() {
        super.onStop();
        disconnect();
    }

    private void appendLog(String message) {
        String existing = logText.getText().toString();
        if (existing.length() > 6000) {
            existing = existing.substring(existing.length() - 4000);
        }
        logText.setText(existing + message + "\n");
    }

    private LinearLayout column() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    private LinearLayout card(LinearLayout parent, String title) {
        LinearLayout card = column();
        card.setPadding(dp(18), dp(16), dp(18), dp(16));
        GradientDrawable background = new GradientDrawable();
        background.setColor(Color.WHITE);
        background.setCornerRadius(dp(14));
        card.setBackground(background);
        LinearLayout.LayoutParams parameters = new LinearLayout.LayoutParams(-1, -2);
        parameters.bottomMargin = dp(14);
        parent.addView(card, parameters);
        card.addView(text(title, 18, INK, true));
        return card;
    }

    private TextView text(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setPadding(0, dp(4), 0, dp(4));
        if (bold) {
            view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        }
        return view;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
