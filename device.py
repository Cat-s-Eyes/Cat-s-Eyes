# device.py — A: OLED·LED 알림, 스위치로 끄기
#
# 알림 줄은 alerts.py 가 alerts 표에 넣는다. 이 파일은 그 줄을 보여주고 끈다.
#   - acked_at 이 비어 있으면 아직 안 끈 알림 → OLED 문구 + LED 깜빡임
#   - 스위치를 짧게 누르면 acked_at 에 끈 시각 (db.iso 형식)
#   - type 이 clean 이면 cleanings 에도 한 줄 (cleaned_at = 끈 시각)
#
# 핀은 빵판 수정본 기준 (README 5-3 과 다름)
#   LED GPIO17 (물리 11), 스위치 GPIO27 (물리 13), OLED I2C 0x3C
#
#   python3 device.py                 평소 화면. 밥 시간·청소 조건이면 알림
#   python3 device.py --demo meal     밥 알림을 한 줄 넣고 시험
#   python3 device.py --demo clean    청소 알림을 한 줄 넣고 시험
#                                     (끄면 청소 기록이 생겨 화장실 횟수가 0부터)
#   python3 device.py --check         OLED·LED·스위치만 확인하고 종료


import argparse
import queue
import time
from datetime import datetime
from pathlib import Path

import smbus
from gpiozero import Button, LED
from PIL import Image, ImageDraw, ImageFont

from alerts import check_clean, check_meal
from db import _write, connect, count_litter, count_meals, init_db, iso



PIN_LED = 17
PIN_BUTTON = 27
OLED_ADDR = 0x3C
OLED_WIDTH = 128
OLED_HEIGHT = 64

FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path.home() / ".local/share/fonts/NanumGothic.ttf",
    Path(__file__).resolve().parent / "fonts" / "NanumGothic.ttf",
]

# SSD1306 128x64. 전하 펌프를 켜야 파이 3.3V 만으로 화면이 나온다.
_INIT = (
    0xAE,
    0xD5, 0x80,
    0xA8, 0x3F,
    0xD3, 0x00,
    0x40,
    0x8D, 0x14,
    0x20, 0x02,
    0xA0,             # 좌우 반전 없음. A1 이면 이 모듈에서 글자가 뒤집힌다
    0xC0,             # 위아래 반전 없음. C8 이면 180도 뒤집힌다
    0xDA, 0x12,
    0x81, 0xCF,
    0xD9, 0xF1,
    0xDB, 0x40,
    0xA4,
    0xA6,
    0x2E,
    0xAF,
)


class SSD1306:
    """adafruit 라이브러리 없이 I2C 로 128x64 OLED 에 그린다."""

    def __init__(self, bus_n=1, addr=OLED_ADDR):
        try:
            self.bus = smbus.SMBus(bus_n)
        except FileNotFoundError as exc:
            raise SystemExit("I2C 가 꺼져 있습니다. sudo raspi-config 에서 I2C 를 켜세요.") from exc
        self.addr = addr
        try:
            for value in _INIT:
                self.command(value)
        except OSError as exc:
            raise SystemExit(
                f"OLED(0x{addr:02x})에 쓰지 못했습니다. 핀 1·3·5·GND 연결을 확인하세요."
            ) from exc
        self.clear()

    def command(self, value):
        self.bus.write_byte_data(self.addr, 0x00, value)

    def clear(self):
        blank = Image.new("1", (OLED_WIDTH, OLED_HEIGHT), 0)
        self.show(blank)

    def show(self, image):
        pages = _pack(image)
        for page in range(8):
            self.command(0xB0 | page)
            self.command(0x00)
            self.command(0x10)
            chunk = pages[page * OLED_WIDTH:(page + 1) * OLED_WIDTH]
            for offset in range(0, OLED_WIDTH, 16):
                self.bus.write_i2c_block_data(self.addr, 0x40, list(chunk[offset:offset + 16]))

    def close(self):
        try:
            self.clear()
            self.command(0xAE)
        except OSError:
            pass
        self.bus.close()


def _pack(image):
    """Pillow 이미지를 SSD1306 페이지 버퍼(가로 128, 세로 8비트)로 바꾼다."""
    img = image.convert("1")
    packed = img.tobytes()
    stride = OLED_WIDTH // 8
    buf = bytearray(OLED_WIDTH * (OLED_HEIGHT // 8))
    for y in range(OLED_HEIGHT):
        row = packed[y * stride:(y + 1) * stride]
        for x in range(OLED_WIDTH):
            if row[x // 8] & (0x80 >> (x % 8)):
                buf[(y // 8) * OLED_WIDTH + x] |= 1 << (y % 8)
    return buf


_fonts = {}
_font_warned = False


def load_font(size):
    global _font_warned
    if size in _fonts:
        return _fonts[size]
    for path in FONT_CANDIDATES:
        if path.is_file():
            font = ImageFont.truetype(str(path), size)
            _fonts[size] = font
            return font
    if not _font_warned:
        print("한글 글꼴이 없습니다.  sudo apt install fonts-nanum")
        _font_warned = True
    font = ImageFont.load_default()
    _fonts[size] = font
    return font


def unacked(conn):
    rows = conn.execute(
        "SELECT id, type, created_at FROM alerts WHERE acked_at IS NULL ORDER BY created_at"
    ).fetchall()
    return [dict(row) for row in rows]


def ack_oldest(conn):
    """안 끈 알림 중 가장 오래된 것을 끈다. clean 이면 청소 기록도 남긴다."""
    row = conn.execute(
        "SELECT id, type FROM alerts WHERE acked_at IS NULL ORDER BY created_at, id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    when = iso(datetime.now())
    try:
        conn.execute(
            "UPDATE alerts SET acked_at = ? WHERE id = ? AND acked_at IS NULL",
            (when, row["id"]),
        )
        if row["type"] == "clean":
            conn.execute("INSERT INTO cleanings (cleaned_at) VALUES (?)", (when,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"알림을 껐습니다. type={row['type']} acked_at={when}")
    return row["type"]


def render(conn, alerts, now, notice):
    image = Image.new("1", (OLED_WIDTH, OLED_HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    title = load_font(18)
    body = load_font(14)
    if alerts:
        kind = alerts[0]["type"]
        draw.text((2, 4), "화장실 청소" if kind == "clean" else "밥 시간!", font=title, fill=1)
        draw.text((2, 30), "눌러서 확인", font=body, fill=1)
        if len(alerts) > 1:
            draw.text((2, 48), f"알림 {len(alerts)}개", font=body, fill=1)
    elif notice:
        draw.text((2, 22), notice, font=title, fill=1)
    else:
        draw.text((2, 2), now.strftime("%H:%M:%S"), font=title, fill=1)
        draw.text((2, 26), f"밥 {count_meals(conn)}회", font=body, fill=1)
        draw.text((2, 46), f"화장실 {count_litter(conn)}회", font=body, fill=1)
    return image


def hardware_check():
    """OLED 에 한 줄을 그리고 LED 를 두 번 깜빡인 뒤 스위치 상태를 출력한다."""
    oled = SSD1306()
    led = LED(PIN_LED)
    button = Button(PIN_BUTTON, pull_up=True, bounce_time=0.05)
    try:
        image = Image.new("1", (OLED_WIDTH, OLED_HEIGHT), 0)
        draw = ImageDraw.Draw(image)
        font = load_font(16)
        draw.text((2, 8), "OLED 확인", font=font, fill=1)
        draw.text((2, 36), datetime.now().strftime("%H:%M:%S"), font=font, fill=1)
        oled.show(image)
        led.on()
        time.sleep(0.3)
        led.off()
        time.sleep(0.2)
        led.on()
        time.sleep(0.3)
        led.off()
        state = "눌림" if button.is_pressed else "떼짐"
        print(f"OLED에 'OLED 확인'을 보냈습니다. LED가 두 번 깜빡여야 합니다. 스위치는 지금 {state}입니다.")
        if button.is_pressed:
            print("스위치가 계속 눌린 것으로 보입니다. 버튼을 90도 돌려 꽂아 보세요.")
        time.sleep(3)
    finally:
        led.off()
        button.close()
        led.close()
        oled.close()


def run(demo=None):
    conn = connect()
    init_db(conn)
    if demo:
        _write(conn, "INSERT INTO alerts (type, created_at) VALUES (?, ?)", (demo, iso(datetime.now())))
        print(f"시험 알림을 넣었습니다. type={demo}")
        if demo == "clean":
            print("이 알림을 스위치로 끄면 cleanings 에 청소 시각이 기록됩니다.")

    oled = SSD1306()
    led = LED(PIN_LED)
    button = Button(PIN_BUTTON, pull_up=True, bounce_time=0.05, hold_time=1)
    events = queue.Queue()
    held = {"value": False}

    def on_held():
        held["value"] = True
        events.put("long")

    def on_released():
        if held["value"]:
            held["value"] = False
            return
        events.put("short")

    button.when_held = on_held
    button.when_released = on_released

    blinking = False
    notice = ""
    notice_until = 0.0
    last_check = 0.0
    print("알림 화면을 시작했습니다. 끝내려면 Ctrl+C")
    try:
        while True:
            now = datetime.now()
            if time.monotonic() - last_check >= 1:
                check_meal(conn, now)
                check_clean(conn, now)
                last_check = time.monotonic()
            while not events.empty():
                kind = events.get()
                if kind == "short":
                    ack_oldest(conn)
                elif kind == "long" and not unacked(conn):
                    notice = "레이저 대기"
                    notice_until = time.monotonic() + 2
            if notice and time.monotonic() >= notice_until:
                notice = ""
            alerts = unacked(conn)
            if alerts and not blinking:
                led.blink(on_time=0.5, off_time=0.5)
                blinking = True
            elif not alerts and blinking:
                led.off()
                blinking = False
            text = notice if time.monotonic() < notice_until else ""
            oled.show(render(conn, alerts, now, text))
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        led.off()
        button.close()
        led.close()
        oled.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="OLED·LED 알림")
    parser.add_argument("--demo", choices=("meal", "clean"), help="시험용 알림을 하나 넣고 실행")
    parser.add_argument("--check", action="store_true", help="OLED, LED, 스위치만 확인하고 종료")
    args = parser.parse_args()
    if args.check:
        hardware_check()
    else:
        run(args.demo)


if __name__ == "__main__":
    main()
