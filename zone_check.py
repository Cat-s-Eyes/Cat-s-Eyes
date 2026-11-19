# zone_check.py — 2단계: 구역 판정 (YOLO26n + 구역 자르기)
#
# 사진 1장마다 아래 순서로 "고양이가 밥그릇 / 화장실 / 구역 밖 중 어디 있는지" 판정한다.
#   1. 구역(0~1 비율)에 여유(MARGIN)를 붙여 잘라 낸다
#   2. 잘라 낸 사진에서 YOLO26n으로 고양이·강아지를 찾는다
#   3. 찾은 박스를 원래 사진 좌표로 바꾼다
#   4. 고양이 박스마다 검사:  점수 0.4 이상?  →  가운데 점이 구역 안?  →  같은 자리에 강아지 점수가 더 높지 않음?
#   5. 통과한 고양이가 있으면 그 구역에 "고양이 있음"
#
# 결과 사진: C:\Project\Cat_Care\runs\zone_check\<사진>.jpg
#   빨강 사각형 = 밥그릇 구역, 파랑 = 화장실 구역, 회색 = 잘라 낸 범위(여유 포함)
#   초록 박스 = 고양이로 인정, 주황 = 탈락(이유 표시), 보라 = 강아지
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from ultralytics import YOLO

BASE = Path(__file__).resolve().parent
IMAGE_DIR = BASE / "CatCare_Image"
OUT = BASE / "runs" / "zone_check"
MODEL = "yolo26n"
CAT, DOG = 15, 16
CONF_CAT = 0.4      # 고양이로 인정할 최소 점수 (계획서 예시값)
CONF_MIN = 0.1      # 강아지와 비교하려고 낮은 점수 박스까지 받아 둠
MARGIN = 0.15       # 구역을 자를 때 사방으로 붙이는 여유 비율
SAME_SPOT = 0.5     # 고양이·강아지 박스가 이만큼 겹치면(IoU) 같은 자리로 봄

# 구역: 사진 크기에 대한 비율 (x1, y1, x2, y2). 나중에는 DB zones 표에서 읽음
ZONES = {
    "main": {"bowl": (0.33, 0.32, 0.515, 0.513), "litter": (0.65, 0.40, 0.80, 0.533)},   # house_main 각도
    "far":  {"bowl": (0.32, 0.587, 0.53, 0.84),  "litter": (0.65, 0.667, 0.825, 0.88)},  # house7~9 각도
}
VIEW = {"house_main.jpg": "main", "house7.jpg": "far", "house8.jpg": "far", "house9.jpg": "far"}
# 정답 (사진을 직접 보고 확인). None = 구역 밖
TRUTH = {"house_main.jpg": None, "house7.jpg": "litter", "house8.jpg": "litter", "house9.jpg": None}
COLOR = {"bowl": (0, 0, 255), "litter": (255, 0, 0)}   # BGR


def load_bgr(path):
    """사진을 회전 정보까지 반영해 읽고, YOLO가 쓰는 BGR 배열로 바꾼다."""
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.ascontiguousarray(np.array(im)[:, :, ::-1])


def to_pixels(shape, zone):
    """0~1 비율 구역 → 픽셀 좌표"""
    h, w = shape[:2]
    x1, y1, x2, y2 = zone
    return x1 * w, y1 * h, x2 * w, y2 * h


def crop_area(shape, zone):
    """구역에 여유를 붙인 잘라 낼 범위 (픽셀, 사진 밖으로 나가지 않게)"""
    h, w = shape[:2]
    x1, y1, x2, y2 = zone
    mx, my = (x2 - x1) * MARGIN, (y2 - y1) * MARGIN
    return (int(max(0, x1 - mx) * w), int(max(0, y1 - my) * h),
            int(min(1, x2 + mx) * w), int(min(1, y2 + my) * h))


def iou(p, q):
    """두 박스가 겹치는 정도 (0 = 안 겹침, 1 = 완전히 같음)"""
    ix = max(0, min(p[2], q[2]) - max(p[0], q[0]))
    iy = max(0, min(p[3], q[3]) - max(p[1], q[1]))
    inter = ix * iy
    union = (p[2] - p[0]) * (p[3] - p[1]) + (q[2] - q[0]) * (q[3] - q[1]) - inter
    return inter / union if union else 0.0


def check_zone(model, img, zone):
    """구역 하나를 판정한다.
    반환: (인정된 고양이 점수 또는 None, 고양이 후보 목록, 강아지 목록, 처리 시간 ms)
    후보 = (점수, 박스, 판정 문구)   박스는 원래 사진 좌표"""
    a, b, c, d = crop_area(img.shape, zone)
    r = model.predict(img[b:d, a:c], imgsz=640, conf=CONF_MIN, classes=[CAT, DOG], verbose=False)[0]

    cats, dogs = [], []
    for bx in r.boxes:
        x1, y1, x2, y2 = bx.xyxy[0].tolist()
        item = (float(bx.conf[0]), (x1 + a, y1 + b, x2 + a, y2 + b))   # 잘라 낸 좌표 → 원래 좌표
        (cats if int(bx.cls[0]) == CAT else dogs).append(item)

    zx1, zy1, zx2, zy2 = to_pixels(img.shape, zone)
    found, checked = None, []
    for conf, box in sorted(cats, reverse=True):          # 점수 높은 순
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if conf < CONF_CAT:
            why = "low"                                    # 점수 낮음
        elif not (zx1 <= cx <= zx2 and zy1 <= cy <= zy2):
            why = "out"                                    # 가운데 점이 여유 부분에 있음
        elif any(iou(box, db) >= SAME_SPOT and dc > conf for dc, db in dogs):
            why = "dog"                                    # 같은 자리 강아지 점수가 더 높음
        else:
            why = "OK"
            if found is None:
                found = conf
        checked.append((conf, box, why))
    return found, checked, dogs, r.speed["inference"]


def draw(img, results):
    im = img.copy()
    t = max(2, img.shape[1] // 400)                        # 사진 크기에 맞춘 선 두께
    fs = img.shape[1] / 1600                               # 글자 크기
    for z, (zone, (found, checked, dogs, _)) in results.items():
        a, b, c, d = crop_area(img.shape, zone)
        cv2.rectangle(im, (a, b), (c, d), (160, 160, 160), t // 2)
        x1, y1, x2, y2 = map(int, to_pixels(img.shape, zone))
        cv2.rectangle(im, (x1, y1), (x2, y2), COLOR[z], t)
        label = f"{z}: {'CAT ' + format(found, '.2f') if found else 'none'}"
        cv2.putText(im, label, (x1, y1 - 3 * t), cv2.FONT_HERSHEY_SIMPLEX, fs, COLOR[z], t)
        for conf, box in dogs:
            p, q = (int(box[0]), int(box[1])), (int(box[2]), int(box[3]))
            cv2.rectangle(im, p, q, (200, 0, 200), t)
            cv2.putText(im, f"dog {conf:.2f}", (p[0], q[1] + 8 * t), cv2.FONT_HERSHEY_SIMPLEX, fs, (200, 0, 200), t)
        for conf, box, why in checked:
            color = (0, 200, 0) if why == "OK" else (0, 140, 255)
            p, q = (int(box[0]), int(box[1])), (int(box[2]), int(box[3]))
            cv2.rectangle(im, p, q, color, t)
            cv2.circle(im, ((p[0] + q[0]) // 2, (p[1] + q[1]) // 2), 3 * t, color, -1)   # 가운데 점
            cv2.putText(im, f"cat {conf:.2f} {why}", (p[0], p[1] - 2 * t), cv2.FONT_HERSHEY_SIMPLEX, fs, color, t)
    return im


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(BASE / f"{MODEL}.pt"))
    name_ko = {"bowl": "밥그릇", "litter": "화장실", None: "구역 밖"}
    rows, times = [], []

    for name, view in VIEW.items():
        img = load_bgr(IMAGE_DIR / name)
        results = {}
        for z, zone in ZONES[view].items():
            results[z] = (zone, check_zone(model, img, zone))
            times.append(results[z][1][3])
        cv2.imwrite(str(OUT / name), draw(img, results))

        # 최종 위치: 인정된 구역 중 점수가 가장 높은 곳 (없으면 구역 밖)
        hits = {z: res[0] for z, (_, res) in results.items() if res[0] is not None}
        where = max(hits, key=hits.get) if hits else None
        rows.append((name, results, where))

        print(f"\n[{name}] 정답: {name_ko[TRUTH[name]]}")
        for z, (_, (found, checked, dogs, _)) in results.items():
            cats_txt = ", ".join(f"cat {c:.2f} {w}" for c, _, w in checked) or "고양이 박스 없음"
            dogs_txt = ", ".join(f"dog {c:.2f}" for c, _ in dogs)
            print(f"   {name_ko[z]}: {cats_txt}" + (f" | {dogs_txt}" if dogs_txt else ""))

    print("\n===== 판정 결과 (YOLO26n + 구역 자르기, 기준 0.4) =====")
    print(f"{'사진':16s}{'정답':>8s}{'판정':>8s}  결과")
    for name, _, where in rows:
        ok = "맞음" if where == TRUTH[name] else "틀림"
        print(f"{name:16s}{name_ko[TRUTH[name]]:>8s}{name_ko[where]:>8s}  {ok}")
    n_ok = sum(w == TRUTH[n] for n, _, w in rows)
    print(f"정답 {n_ok}/{len(rows)}장 · 구역 하나당 평균 {sum(times) / len(times):.0f} ms (노트북 CPU)")
    print(f"결과 사진: {OUT}")
