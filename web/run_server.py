#!/usr/bin/env python3
"""
Rehandle 협업 스케줄링 웹 — 실행 스크립트

비교군(Reha_Crane_baseline.ipynb)과 협업 모델(Reha_Crane_predetermined.ipynb)을
같은 인스턴스로 돌리고 결과를 비교·보관한다. 노트북은 ../model 에서 그대로 읽는다.

로컬(개인 PC)
    python run_server.py                       # http://127.0.0.1:8766

연구실 공용 서버 (내부망 공유)
    python run_server.py --share               # 0.0.0.0 바인딩 + 토큰 자동 생성
    python run_server.py --share --token abc   # 토큰 직접 지정

Gurobi 가 설치된 파이썬(= 노트북을 돌리던 conda 환경)으로 실행하세요.
외부 패키지는 필요 없습니다.
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import limits  # noqa: E402
from app.server import create_server, BASE_DIR  # noqa: E402
from app.registry import build_registry, default_notebook_dir  # noqa: E402

TOKEN_FILE = os.path.join(BASE_DIR, "data", ".access_token")


def resolve_token(args) -> str:
    if args.no_token:
        return ""
    if args.token:
        return args.token
    if not args.share:
        return ""                      # 로컬 전용이면 인증 불필요
    # 공유 모드: 토큰을 파일에 보관해 재시작해도 같은 값을 쓴다
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8") as f:
            saved = f.read().strip()
        if saved:
            return saved
    token = secrets.token_urlsafe(12)
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


def lan_ip() -> str:
    """
    이 머신의 내부망 IP 를 추정한다.

    서버에 외부 인터넷이 없을 수 있으므로(내부망 전용 랩 서버) 어떤 경우에도
    예외를 밖으로 내보내지 않는다. 못 찾으면 안내 문구를 돌려준다.
    """
    # 1) 기본 경로로 나가는 인터페이스 주소 (실제로 패킷을 보내지는 않는다)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        pass

    # 2) 호스트 이름으로 조회
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # 3) 인터페이스에 붙은 주소 중 루프백이 아닌 것
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                return ip
    except Exception:
        pass

    return "<서버IP>"


# ----------------------------------------------------------------------------
# 포트 충돌 처리 — 이전에 띄운 서버가 살아 있으면 정리하고 다시 띄운다
# ----------------------------------------------------------------------------
def port_free(host: str, port: int) -> bool:
    """ThreadingHTTPServer 와 같은 조건(SO_REUSEADDR)으로 bind 가 되는지 본다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _sh(cmd: list[str]) -> str:
    import subprocess
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def port_owners(port: int) -> list[tuple[int, str]]:
    """그 포트에서 LISTEN 중인 프로세스 [(pid, 명령줄)] (macOS / Linux, lsof 사용)."""
    out = []
    for tok in _sh(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).split():
        if tok.isdigit() and int(tok) != os.getpid():
            out.append((int(tok), _sh(["ps", "-o", "command=", "-p", tok])))
    return out


def active_jobs(port: int, token: str) -> int | None:
    """이전 서버에 실행/대기 중인 작업 수. 못 물어보면 None."""
    import json
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/jobs",
                                 headers={"X-Auth-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            jobs = json.loads(r.read().decode("utf-8")).get("jobs", [])
        return sum(1 for j in jobs if j.get("status") in ("queued", "running"))
    except Exception:
        return None


def stop_pid(pid: int, wait: float = 6.0) -> bool:
    import signal
    import time
    for sig, limit in ((signal.SIGINT, wait / 2), (signal.SIGTERM, wait / 2),
                       (getattr(signal, "SIGKILL", signal.SIGTERM), 2.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        t_end = time.time() + limit
        while time.time() < t_end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.2)
    return False


def resolve_port(host: str, port: int, token: str, replace: bool) -> int:
    """
    포트가 비어 있으면 그대로. 막혀 있으면
      - 이전에 띄운 이 웹 서버(run_server.py)이고 돌고 있는 실험이 없으면 → 종료하고 같은 포트 재사용
      - 실험이 돌고 있거나 다른 프로그램이면 → 건드리지 않고 다음 빈 포트 사용
    """
    import time
    if port_free(host, port):
        return port
    owners = port_owners(port)
    ours = [(p, c) for p, c in owners if "run_server.py" in c]
    others = [(p, c) for p, c in owners if "run_server.py" not in c]

    if ours and replace:
        n = active_jobs(port, token)
        if n:
            print(f"⚠️  포트 {port} 에 이전 서버(PID {', '.join(str(p) for p, _ in ours)})가 "
                  f"실험 {n}개를 돌리는 중이라 종료하지 않습니다.")
        else:
            for pid, _ in ours:
                ok = stop_pid(pid)
                print(f"이전 서버(PID {pid})가 포트 {port} 를 잡고 있어 "
                      + ("종료했습니다." if ok else "종료하지 못했습니다."))
            for _ in range(25):
                if port_free(host, port):
                    return port
                time.sleep(0.2)
    elif ours:
        print(f"포트 {port} 에 이전 서버(PID {', '.join(str(p) for p, _ in ours)})가 떠 있습니다 (--keep-old).")
    for pid, cmd in others:
        print(f"포트 {port} 는 다른 프로그램이 사용 중입니다: PID {pid}  {cmd[:90]}")
    if not owners:
        print(f"포트 {port} 가 사용 중입니다 (사용 중인 프로세스를 확인하지 못했습니다).")

    for p in range(port + 1, port + 30):
        if port_free(host, p):
            print(f"→ 포트 {p} 로 띄웁니다.")
            return p
    raise SystemExit(f"포트 {port}~{port + 29} 가 모두 사용 중입니다. --port 로 다른 번호를 지정하세요.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Predetermined rehandle cooperation web")
    ap.add_argument("--host", default=None, help="바인딩 주소 (기본 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--share", action="store_true",
                    help="내부망에 공개 (0.0.0.0 바인딩 + 토큰 인증)")
    ap.add_argument("--token", default=None, help="접속 토큰 직접 지정")
    ap.add_argument("--no-token", action="store_true", help="토큰 인증 끄기")
    ap.add_argument("--notebooks", default=None, help="노트북 폴더 (기본: ../model)")
    ap.add_argument("--data", default=None, help="결과 저장 폴더 (기본: ./data)")
    ap.add_argument("--realdata", default=None, help="실데이터 CSV 폴더 (기본: ../data)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--keep-old", action="store_true",
                    help="같은 포트에 이전 서버가 떠 있어도 종료하지 않고 다른 포트로 띄우기")
    args = ap.parse_args()

    host = args.host or ("0.0.0.0" if args.share else "127.0.0.1")
    token = resolve_token(args)
    notebook_dir = args.notebooks or default_notebook_dir(BASE_DIR)
    data_dir = args.data or os.path.join(BASE_DIR, "data")

    print("=" * 72)
    print("Rehandle 협업 스케줄링 Web")
    print("=" * 72)
    print(f"python     : {sys.executable}")
    print(f"notebooks  : {notebook_dir}")
    print(f"data       : {data_dir}")
    print(f"자원 제한   : 기본 {limits.DEFAULT_THREADS} 스레드 / 상한 {limits.MAX_THREADS} "
          f"(이 머신 {os.cpu_count()} 스레드), 최대 {limits.MAX_TIME_LIMIT}초")
    print("-" * 72)
    for key, method in build_registry(notebook_dir).items():
        ok, reason = method.available()
        mark = "OK " if ok else "-- "
        src = (os.path.basename(method.notebook_path) if getattr(method, "notebook_path", None)
               else (method.solver or "python"))
        print(f"{mark} {key:<14} {method.label}  <- {src}" + ("" if ok else f"   ({reason})"))
    print("-" * 72)

    args.port = resolve_port(host, args.port, token, replace=not args.keep_old)
    try:
        httpd = create_server(host, args.port, BASE_DIR, notebook_dir, data_dir, token, args.realdata)
    except OSError as exc:
        if exc.errno not in (48, 98):        # EADDRINUSE (macOS 48 / Linux 98)
            raise
        args.port = resolve_port(host, args.port, token, replace=not args.keep_old)
        httpd = create_server(host, args.port, BASE_DIR, notebook_dir, data_dir, token, args.realdata)
    rd = httpd.RequestHandlerClass.realdata
    st = rd.status()
    print(f"실데이터    : {rd.source_dir} → "
          + (", ".join(st["files"]) + (" (색인 있음)" if st["indexed"] else " (처음 가져올 때 색인 생성)")
             if st["available"] else "CSV 없음 — 실데이터 가져오기 비활성"))

    if args.share:
        ip = lan_ip()
        url = f"http://{ip}:{args.port}"
        print(f"내부망 공개 중 → {url}")
        if ip == "<서버IP>":
            print("  (IP 자동 감지 실패 — 'hostname -I' 로 확인한 주소를 쓰세요)")
        if token:
            print(f"접속 토큰    : {token}")
            print(f"바로 열기    : {url}/?token={token}")
            print(f"(토큰 파일   : {TOKEN_FILE})")
        else:
            print("⚠️  토큰 없이 공개되었습니다. 내부망 누구나 접속·실행할 수 있습니다.")
    else:
        url = f"http://127.0.0.1:{args.port}"
        print(f"서버 실행 중 → {url}")
        print("(VS Code Remote-SSH 로 접속했다면 포트가 자동 포워딩되어 "
              "이 주소를 개인 PC 브라우저에서 열 수 있습니다)")
    print("Ctrl+C 로 종료")

    if not args.no_browser and not args.share:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
