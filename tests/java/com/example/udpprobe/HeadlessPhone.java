package com.example.udpprobe;

import org.json.JSONObject;

public final class HeadlessPhone {
    public static void main(String[] args) throws Exception {
        ProbeClient client = new ProbeClient(new ProbeClient.Listener() {
            public void state(String message, boolean connected) {
                System.out.println(message);
            }

            public void log(String message) {
                System.out.println(message);
            }

            public void trial(JSONObject message) {
                System.out.println("TRIAL " + message);
            }

            public void result(JSONObject message) {
                System.out.println("RESULT " + message);
            }

            public void complete(JSONObject message) {
                System.out.println("COMPLETE " + message);
            }
        });
        client.connect("127.0.0.1", Integer.parseInt(args[0]));
        while (System.in.read() != -1) {
            client.requestStop();
        }
        client.close();
    }
}
