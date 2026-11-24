# test_servo.py — 서보가 움직이는지 확인
#
# 빵판 수정본 핀
#   좌우(팬) GPIO12, 물리 핀 32
#   상하(틸트) GPIO13, 물리 핀 33
#
# gpiozero Servo 는 이 파이에서 펄스 폭을 정수 %로 잘라 1.5ms 가 1.4ms 가 된다.
# 그래서 lgpio.tx_servo 로 마이크로초를 그대로 보낸다.
#
# 서보가 하나만 꽂혀 있으면 좌우·상하 핀 중 어디에 있는지 모를 수 있다.
# 기본은 둘을 순서대로 크게 움직인다. 동시에 돌리지는 않는다.
#
#   python3 test_servo.py           좌우 다음 상하
#   python3 test_servo.py pan       좌우만
#   python3 test_servo.py tilt      상하만
#   python3 test_servo.py check     고장인지 선이 틀린지 핀을 하나씩 확인
import argparse
import time

import lgpio

SERVOS = {
    "pan": (12, "좌우"),
    "tilt": (13, "상하"),
}

# SG90 이 확실히 다르게 가는 폭. 기계 끝(500·2500)까지는 가지 않는다.
SWEEP = (
    (1500, "가운데"),
    (1000, "한쪽"),
    (2000, "반대쪽"),
    (1500, "가운데"),
)


class ServoLine:
    def __init__(self, pin):
        self.pin = pin
        self.handle = lgpio.gpiochip_open(0)
        rc = lgpio.gpio_claim_output(self.handle, pin, 0)
        if rc < 0:
            lgpio.gpiochip_close(self.handle)
            raise SystemExit(f"GPIO{pin} 을 열지 못했습니다 ({rc}).")

    def pulse(self, micros):
        rc = lgpio.tx_servo(self.handle, self.pin, int(micros), 50)
        if rc < 0:
            raise SystemExit(f"GPIO{self.pin} 펄스 실패 ({rc}).")

    def close(self):
        lgpio.tx_servo(self.handle, self.pin, 0, 50)
        lgpio.gpio_write(self.handle, self.pin, 0)
        lgpio.gpio_free(self.handle, self.pin)
        lgpio.gpiochip_close(self.handle)


def move(name, pin):
    print(f"\n지금 {name} 입니다. GPIO{pin}, 물리 핀 {32 if pin == 12 else 33}")
    line = ServoLine(pin)
    try:
        for micros, label in SWEEP:
            print(f"  {label} ({micros}µs)")
            line.pulse(micros)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n중간에 멈춥니다.")
        line.close()
        raise
    line.close()
    print(f"{name} 신호를 끊었습니다.")


# 그림의 32·33번 외에, 헤더를 세다 보면 꽂히기 쉬운 핀.
CHECKS = (
    (12, "물리 핀 32 (좌우)"),
    (13, "물리 핀 33 (상하)"),
    (18, "물리 핀 12"),
    (19, "물리 핀 35"),
)


def ask(text):
    try:
        return input(text).strip().lower()
    except EOFError:
        raise SystemExit(0)


def check():
    """신호가 서보까지 가는지, 뿔이 잠기는지로 구분한다."""
    print("서보 줄 한가운데 검은 커넥터가 끝까지 꽂혀 있는지 먼저 눌러 주세요.")
    print("노란 줄끼리, 빨간 줄끼리, 갈색 줄끼리 맞아야 합니다.")
    print()
    print("지금 신호는 꺼져 있습니다. 흰 뿔을 손가락으로 살살 돌려 보세요.")
    print("그냥 돌아가면 정상입니다. 서보는 신호가 없을 때 힘이 없습니다.")
    ask("돌려 봤으면 Enter > ")
    print()
    print("이제 핀을 하나씩 켭니다. 그 핀에 선이 연결되어 있고 서보가 살아 있으면")
    print("뿔이 손으로 잘 안 돌아갈 정도로 버티거나, 각도가 바뀝니다.")
    for pin, label in CHECKS:
        print(f"\n{label} 에 신호를 넣는 중입니다.")
        line = ServoLine(pin)
        try:
            line.pulse(1000)
            time.sleep(1.0)
            line.pulse(2000)
            answer = ask("지금 신호가 켜져 있습니다. 뿔이 잠겼거나 각도가 바뀌었나요? y / n > ")
        finally:
            line.close()
        if answer.startswith("y"):
            print(f"서보는 정상입니다. 노란 신호선이 {label} 쪽에 연결되어 있습니다.")
            return
    print("\n네 곳 모두 반응이 없으면, 노란 선이 그 핀들에 안 닿았거나 서보에 5V가 없습니다.")
    print("마지막 확인: 노란 선만 빼 두고, 빨간 선을 뽑았다가 5V 빨간 줄에 다시 꽂아 보세요.")
    print("꽂는 순간 딸깍 하면 서보는 살아 있고, 전원 줄은 맞습니다. 신호선만 다시 맞추면 됩니다.")
    print("딸깍도 없고 뿔도 계속 헛돌면, 빨간·갈색이 전원까지 안 가거나 서보가 고장인 쪽입니다.")


def main():
    parser = argparse.ArgumentParser(description="서보 동작 확인")
    parser.add_argument("which", nargs="?", choices=("pan", "tilt", "both", "check"), default="both")
    args = parser.parse_args()
    if args.which == "check":
        try:
            check()
        except KeyboardInterrupt:
            print("\n종료합니다.")
        return
    order = ("pan", "tilt") if args.which == "both" else (args.which,)
    print("한 번에 하나만 움직입니다. 멈추려면 Ctrl+C")
    print("2초 뒤에 시작합니다.")
    try:
        time.sleep(2)
        for key in order:
            pin, name = SERVOS[key]
            move(name, pin)
    except KeyboardInterrupt:
        print("\n종료합니다.")
    print("\n둘 다 가만히 있으면 노란 신호선이 핀 32·33 과 같은 빵판 열에 있는지,")
    print("빨간 선이 5V 빨간 줄(가운데에서 끊기지 않은 쪽)에 있는지 확인하세요.")


if __name__ == "__main__":
    main()
