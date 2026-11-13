"""docker/nginx.conf 的規則：用真的 nginx 容器，web 換成回顯路徑的假服務。沒有 docker 就略過。"""
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
NGINX = next(line.split("image:")[1].strip() for line in COMPOSE.splitlines() if "nginx-unprivileged" in line)
CLIENT = "curlimages/curl:8.21.0"
# 假的 web：回 {"path": ..., "host": ...}
ECHO = """
import json, http.server
class H(http.server.BaseHTTPRequestHandler):
    def _go(self):
        body = json.dumps({"path": self.path, "host": self.headers.get("Host")}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    do_GET = do_POST = _go
http.server.ThreadingHTTPServer(("0.0.0.0", 8787), H).serve_forever()
"""

pytestmark = pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(
    ["docker", "info"], check=False, capture_output=True).returncode != 0, reason="沒有 docker")


def docker(*args: str, check: bool = True) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check).stdout.strip()


@pytest.fixture(scope="module")
def gate():
    """lan 網路（私有位址）與 wan 網路（TEST-NET-3，模擬公網來源）都接到 nginx。"""
    tag = uuid.uuid4().hex[:8]
    lan, wan, names = f"t-lan-{tag}", f"t-wan-{tag}", []
    docker("network", "create", "--subnet", "192.168.250.0/24", lan)
    docker("network", "create", "--subnet", "203.0.113.0/24", wan)
    try:
        names.append(docker("run", "-d", "--network", lan, "--network-alias", "web", "python:3.11-alpine",
                            "python", "-c", ECHO))
        names.append(nginx := docker("run", "-d", "--network", lan, "--read-only", "--tmpfs", "/tmp",
                                     "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                                     "-v", f"{ROOT / 'docker' / 'nginx.conf'}:/etc/nginx/conf.d/default.conf:ro", NGINX))
        docker("network", "connect", wan, nginx)
        ip = {n: docker("inspect", "-f", f"{{{{(index .NetworkSettings.Networks \"{n}\").IPAddress}}}}", nginx) for n in (lan, wan)}
        for _ in range(50):
            if docker("run", "--rm", "--network", lan, CLIENT, "-s", "-o", "/dev/null", "-w", "%{http_code}",
                      f"http://{ip[lan]}:8080/x", check=False) == "200":
                break
        yield lan, wan, ip
    finally:
        for name in names:
            docker("rm", "-f", name, check=False)
        docker("network", "rm", lan, wan, check=False)


def curl(gate, net: str, url: str, *args: str) -> tuple[int, str]:
    lan, wan, ip = gate
    network = lan if net == "lan" else wan
    url = url.replace("NGINX", ip[network])
    out = docker("run", "--rm", "--network", network, CLIENT, "-s", "--path-as-is", "-w", "\n%{http_code}", *args, url, check=False)
    body, _, code = out.rpartition("\n")
    return int(code), body


@pytest.mark.parametrize("path", ["/api/auth", "/api/auth/token", "/api/auth/", "//api/auth", "/api/./auth"])
def test_tunnel_blocks_auth_api(gate, path):
    code, body = curl(gate, "lan", f"http://NGINX:8080{path}")
    assert code == 404 and json.loads(body) == {"error": "not found"}


def test_tunnel_auth_page_is_not_found(gate):
    code, body = curl(gate, "lan", "http://NGINX:8080/auth")
    assert code == 200 and json.loads(body)["path"] == "/not-found"  # 前端顯示找不到


def test_tunnel_session_and_passthrough(gate):
    assert curl(gate, "lan", "http://NGINX:8080/api/session") == (200, '{"local": false}')
    for path in ("/api/auth-health", "/api/today", "/auth-not-really", "/reports/2026-01-01"):
        code, body = curl(gate, "lan", f"http://NGINX:8080{path}")
        assert code == 200 and json.loads(body)["path"] == path, path


def test_lan_reaches_auth(gate):
    for path in ("/auth", "/api/auth", "/api/session"):
        code, body = curl(gate, "lan", f"http://NGINX:8788{path}")
        assert code == 200 and json.loads(body)["path"] == path, path
    code, _ = curl(gate, "lan", "http://NGINX:8788/api/auth/token", "-X", "POST", "-H", "Origin: http://192.168.1.20:8788")
    assert code == 200


def test_lan_rejects_public_source(gate):
    assert curl(gate, "wan", "http://NGINX:8788/auth")[0] == 403


@pytest.mark.parametrize("host", ["evil.example", "raspi.lan", "8.8.8.8"])
def test_lan_rejects_names(gate, host):
    """DNS rebinding：Host 不是私有 IP 或 localhost 就拒絕。"""
    assert curl(gate, "lan", "http://NGINX:8788/auth", "-H", f"Host: {host}")[0] == 421


def test_lan_rejects_cross_site(gate):
    code, _ = curl(gate, "lan", "http://NGINX:8788/api/auth/revoke", "-X", "POST", "-H", "Origin: https://evil.example")
    assert code == 403
