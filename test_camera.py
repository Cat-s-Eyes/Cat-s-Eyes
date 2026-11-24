# test_camera.py — USB 카메라가 보이는지 확인
#
# 이 파이는 모니터 없이 씁니다. 카메라 창(cv2.imshow)은 노트북에 뜨지 않습니다.
#
#   python3 test_camera.py
#       runs/camera_test.jpg 한 장을 저장합니다.
#       Cursor에서 이 파일을 열면 됩니다.
#       사진이라 Git 에는 올리지 않습니다 (runs/ 가 .gitignore 에 있음).
#
#   python3 test_camera.py --record
#       videos/camera_날짜시간.mp4 로 녹화합니다. 멈추려면 Ctrl+C.
#       길이를 정하려면 --seconds 20
#       미리보기가 켜져 있으면 카메라를 같이 쓸 수 없으니 먼저 Ctrl+C 로 끄세요.
#       영상은 Git 에 올리지 않습니다 (videos/ 가 .gitignore 에 있음).
#
#   python3 test_camera.py --preview
#       브라우저로 실시간 화면을 봅니다. 끝나면 Ctrl+C.
#       같은 Wi-Fi:  화면에 나오는 http://<파이 IP>:8765
#       SSH만 될 때, 노트북에서:
#         ssh -L 8765:127.0.0.1:8765 bandiyesol@<파이 IP>
#         브라우저에서 http://127.0.0.1:8765
#
# 기본 장치는 /dev/video0 (USB HD camera). 화면이 이상하면 --device 로 바꿉니다.
import argparse
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STILL = ROOT / "runs" / "camera_test.jpg"
PAGE = """<!doctype html>
<meta charset="utf-8">
<title>카메라</title>
<style>
  body { margin: 0; background: #111; color: #eee; font-family: sans-serif; }
  p { margin: 12px; }
  img { width: 100%; max-width: 640px; background: #000; }
</style>
<p>USB 카메라. 멈추면 새로고침하고, 다 보면 파이에서 Ctrl+C 로 끄세요.</p>
<img src="/stream" alt="camera">
""".encode()


def require_ffmpeg():
    if shutil.which("ffmpeg"):
        return
    raise SystemExit("ffmpeg 가 없습니다.  sudo apt install ffmpeg")


def ffmpeg_input(device):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "v4l2", "-framerate", "5", "-video_size", "640x480",
        "-i", device,
    ]


# 5fps로 약 3초. 카메라를 연 직후는 노출이 낮아 어둡다.
STILL_FRAMES = 15


def capture_still(device, dest):
    """노출이 맞춰진 뒤의 마지막 한 장을 dest 에 저장한다."""
    require_ffmpeg()
    if not Path(device).exists():
        raise SystemExit(f"{device} 가 없습니다. USB 카메라가 꽂혀 있는지 확인하세요.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    pattern = dest.parent / "_cam_tmp_%02d.jpg"
    for old in dest.parent.glob("_cam_tmp_*.jpg"):
        old.unlink()
    try:
        proc = subprocess.run(
            ffmpeg_input(device) + ["-frames:v", str(STILL_FRAMES), str(pattern)],
            capture_output=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("카메라가 20초 안에 사진을 주지 않았습니다. 미리보기가 켜져 있으면 끄고 다시 찍으세요.")
    frames = sorted(dest.parent.glob("_cam_tmp_*.jpg"))
    if proc.returncode != 0 or not frames:
        detail = proc.stderr.decode(errors="replace").strip()
        if "Device or resource busy" in detail or "Device busy" in detail:
            detail = "카메라가 다른 프로그램에 잡혀 있습니다. 미리보기를 Ctrl+C 로 끈 뒤 다시 찍으세요."
        raise SystemExit(detail or "카메라에서 사진을 읽지 못했습니다.")
    frames[-1].replace(dest)
    for frame in frames[:-1]:
        frame.unlink(missing_ok=True)
    print(f"저장했습니다. Cursor에서 이 파일을 여세요:\n  {dest}")


def record(device, seconds):
    """Ctrl+C 할 때까지, 또는 seconds 초 동안 mp4 로 저장한다."""
    require_ffmpeg()
    if not Path(device).exists():
        raise SystemExit(f"{device} 가 없습니다. USB 카메라가 꽂혀 있는지 확인하세요.")
    dest = ROOT / "videos" / f"camera_{datetime.now():%Y%m%d_%H%M%S}.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "v4l2", "-framerate", "15", "-video_size", "640x480",
        "-i", device,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an",
    ]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd.append(str(dest))
    if seconds:
        print(f"{seconds}초 동안 녹화합니다.")
    else:
        print("녹화합니다. 멈추려면 Ctrl+C")
    print(f"  {dest}")
    try:
        proc = subprocess.run(cmd)
    except KeyboardInterrupt:
        proc = None
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"저장했습니다. Cursor에서 이 파일을 열면 재생됩니다.\n  {dest}")
        return
    detail = ""
    if proc is not None and proc.returncode not in (0, 255):
        detail = " 미리보기가 켜져 있으면 먼저 Ctrl+C 로 끄세요."
    raise SystemExit("영상이 저장되지 않았습니다." + detail)


class Preview:
    def __init__(self, device):
        self.device = device
        self.jpg = None
        self.lock = threading.Lock()
        self.error = None
        self._stop = threading.Event()
        self.proc = None

    def start(self):
        thread = threading.Thread(target=self._read, daemon=True)
        thread.start()
        return thread

    def _read(self):
        self.proc = subprocess.Popen(
            ffmpeg_input(self.device) + ["-q:v", "8", "-f", "mjpeg", "pipe:1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc = self.proc
        buf = b""
        try:
            while not self._stop.is_set():
                chunk = proc.stdout.read(8192)
                if not chunk:
                    err = proc.stderr.read().decode(errors="replace").strip()
                    self.error = err or "카메라 읽기가 끝났습니다."
                    break
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9")
                    if start == -1 or end == -1 or end < start:
                        if start > 0:
                            buf = buf[start:]
                        elif start == -1 and len(buf) > 200_000:
                            buf = b""
                        break
                    frame = buf[start:end + 2]
                    buf = buf[end + 2:]
                    with self.lock:
                        self.jpg = frame
        finally:
            self._kill()

    def _kill(self):
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)

    def stop(self):
        self._stop.set()
        self._kill()


def serve(preview, port):
    owner = preview

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._bytes(200, "text/html; charset=utf-8", PAGE)
            elif self.path == "/shot.jpg":
                jpg = self._latest()
                if jpg is None:
                    self._bytes(503, "text/plain; charset=utf-8", "아직 프레임이 없습니다.".encode())
                else:
                    self._bytes(200, "image/jpeg", jpg)
            elif self.path == "/stream":
                self.send_response(200)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while True:
                        jpg = self._latest()
                        if jpg is None:
                            time.sleep(0.2)
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n")
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                        time.sleep(0.2)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._bytes(404, "text/plain; charset=utf-8", b"not found")

        def _latest(self):
            with owner.lock:
                return owner.jpg

        def _bytes(self, code, content_type, body):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    addresses = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), port, socket.AF_INET):
            addresses.append(info[4][0])
    except socket.gaierror:
        addresses = []
    addresses.append("127.0.0.1")
    seen = []
    for addr in addresses:
        if addr not in seen:
            seen.append(addr)
    print("브라우저에서 엽니다. 같은 Wi-Fi가 아니면 SSH 포트 포워딩을 쓰세요.")
    for addr in seen:
        print(f"  http://{addr}:{port}")
    print("끄려면 Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n미리보기를 종료합니다.")
    finally:
        preview.stop()
        httpd.shutdown()


def main():
    parser = argparse.ArgumentParser(description="USB 카메라 확인")
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--preview", action="store_true", help="브라우저로 실시간 화면")
    parser.add_argument("--record", action="store_true", help="mp4로 녹화. Ctrl+C로 종료")
    parser.add_argument("--seconds", type=int, default=0, help="녹화 길이(초). 0이면 Ctrl+C까지")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.record and args.preview:
        raise SystemExit("녹화와 미리보기는 카메라를 같이 쓸 수 없습니다. 하나만 실행하세요.")
    if args.record:
        record(args.device, args.seconds)
        return
    if args.preview:
        require_ffmpeg()
        if not Path(args.device).exists():
            raise SystemExit(f"{args.device} 가 없습니다. USB 카메라가 꽂혀 있는지 확인하세요.")
        preview = Preview(args.device)
        preview.start()
        deadline = time.monotonic() + 5
        while preview.jpg is None and preview.error is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if preview.jpg is None:
            preview.stop()
            raise SystemExit(preview.error or "5초 안에 카메라 화면을 받지 못했습니다.")
        serve(preview, args.port)
        return
    capture_still(args.device, STILL)


if __name__ == "__main__":
    main()
