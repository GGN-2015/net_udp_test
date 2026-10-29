import os
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest
from test_integration import configuration


@pytest.fixture(scope="module")
def java_phone_classpath(tmp_path_factory):
    json_jar = os.environ.get("UDP_PROBE_JSON_JAR")
    if not json_jar or not Path(json_jar).is_file():
        pytest.skip(
            "Set UDP_PROBE_JSON_JAR to an org.json JAR to test the actual Android network core."
        )
    java_home = os.environ.get("JAVA_HOME")
    javac = str(Path(java_home) / "bin" / "javac") if java_home else shutil.which("javac")
    java = str(Path(java_home) / "bin" / "java") if java_home else shutil.which("java")
    if not javac or not java:
        pytest.skip("A JDK is required for cross-language tests.")
    output = tmp_path_factory.mktemp("java-classes")
    root = Path(__file__).resolve().parents[1]
    core = root / "android/app/src/main/java/com/example/udpprobe"
    sources = [
        core / name for name in ("ProbeConfig.java", "DatagramEngine.java", "ProbeClient.java")
    ]
    sources.extend((root / "tests/java").rglob("*.java"))
    subprocess.run(
        [javac, "-encoding", "UTF-8", "-cp", json_jar, "-d", str(output), *map(str, sources)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return java, os.pathsep.join((str(output), json_jar))


@pytest.fixture
def java_phone(host, java_phone_classpath, tmp_path):
    _, events, port = host
    java, classpath = java_phone_classpath
    with (tmp_path / "java-phone.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [java, "-cp", classpath, "com.example.udpprobe.HeadlessPhone", str(port)],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            events.wait("connected")
            yield process
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def test_actual_android_core_bidirectional_and_repeat(host, java_phone):
    server, events, _ = host
    config = configuration()
    for _ in range(2):
        server.start_probe(config)
        result = events.wait("finished")
        assert result["outcome"] == "completed", result
        assert len(result["records"]) == 2
        assert all(record["passed"] for record in result["records"]), result


def test_actual_android_core_stop_restart_and_verify(host, java_phone):
    server, events, _ = host
    config = configuration()
    server.start_probe(replace(config, direction="uplink", duration=10))
    events.wait("trial")
    time.sleep(0.15)
    java_phone.stdin.write(b"s")
    java_phone.stdin.flush()
    assert events.wait("finished")["outcome"] == "stopped"
    server.start_probe(replace(config, direction="downlink", mode="auto", max_steps=2))
    result = events.wait("finished")
    assert result["outcome"] == "completed", result
    assert result["recommendations"]["downlink"]["passed"], result
