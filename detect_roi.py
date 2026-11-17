# detect_roi.py — 1-4 추가 진단: 작게 찍힌 고양이를 찾는 방법 비교 (YOLO11n · YOLO26n · YOLO11s)
#
#   ① 전체 사진, imgsz 640  (지금까지 방식, 기준)
#   ② 전체 사진, imgsz 1280 (사진을 크게 넣기)
#   ③ 구역(ROI)만 잘라서 imgsz 640 (밥그릇·화장실 주변만 크게 보기)
#
# 결과 사진: C:\Project\Cat_Care\runs\detect_roi\<모델>\
#   - <사진>_full640.jpg, <사진>_full1280.jpg : 전체 사진 결과
#   - <사진>_<구역>.jpg                        : 구역만 잘라서 본 결과
#   - zones\<사진>.jpg                          : 구역 위치 확인용 (빨강=밥그릇, 파랑=화장실)
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from ultralytics import YOLO

BASE = Path(__file__).resolve().parent
IMAGE_DIR = BASE / "CatCare_Image"
OUT = BASE / "runs" / "detect_roi"
MODELS = ["yolo11n", "yolo26n", "yolo11s"]
CAT, DOG = 15, 16
CONF = 0.1          # 진단용 낮은 기준 (결과표에서 0.4 기준도 함께 봄)
MARGIN = 0.15       # 구역을 자를 때 사방으로 여유를 둘 비율

# 구역: 사진 크기에 대한 비율 (x1, y1, x2, y2) — 계획서처럼 0~1로 저장
# 사진마다 찍은 위치가 달라서 각도별로 따로 정함
ZONES = {
    "main": {"bowl": (0.33, 0.32, 0.515, 0.513), "litter": (0.65, 0.40, 0.80, 0.533)},   # house_main 각도
    "far":  {"bowl": (0.32, 0.587, 0.53, 0.84),  "litter": (0.65, 0.667, 0.825, 0.88)},  # house7~9 각도
}
VIEW = {"house_main.jpg": "main", "house7.jpg": "far", "house8.jpg": "far", "house9.jpg": "far"}
# 정답 (사진을 직접 보고 확인): 고양이가 어느 구역에 있는지
TRUTH = {"house_main.jpg": "구역 밖(소파 앞)", "house7.jpg": "화장실 안",
         "house8.jpg": "화장실 옆", "house9.jpg": "구역 밖(화면 아래)"}


def load_bgr(path):
    """사진을 회전 정보까지 반영해 읽고, YOLO가 쓰는 BGR 배열로 바꾼다."""
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.ascontiguousarray(np.array(im)[:, :, ::-1])


def best(r, cls):
    return max((float(b.conf[0]) for b in r.boxes if int(b.cls[0]) == cls), default=0.0)


def crop(img, zone):
    h, w = img.shape[:2]
    x1, y1, x2, y2 = zone
    mx, my = (x2 - x1) * MARGIN, (y2 - y1) * MARGIN
    a, b = int(max(0, x1 - mx) * w), int(max(0, y1 - my) * h)
    c, d = int(min(1, x2 + mx) * w), int(min(1, y2 + my) * h)
    return img[b:d, a:c]


images = sorted(p for p in IMAGE_DIR.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
imgs = {p.name: load_bgr(p) for p in images}

# 구역 위치 확인용 사진
(OUT / "zones").mkdir(parents=True, exist_ok=True)
for name, view in VIEW.items():
    im = imgs[name].copy()
    h, w = im.shape[:2]
    for z, (x1, y1, x2, y2) in ZONES[view].items():
        color = (0, 0, 255) if z == "bowl" else (255, 0, 0)
        cv2.rectangle(im, (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h)), color, 12)
    cv2.imwrite(str(OUT / "zones" / name), im)

full = {}   # full[모델][크기][사진] = 고양이 최고 점수
roi = {}    # roi[모델][사진][구역] = (고양이, 강아지) 최고 점수
times = {}  # times[모델][방식] = [ms...]

for m in MODELS:
    model = YOLO(str(BASE / f"{m}.pt"))
    out = OUT / m
    out.mkdir(parents=True, exist_ok=True)
    full[m], roi[m], times[m] = {640: {}, 1280: {}}, {}, {"full640": [], "full1280": [], "roi": []}

    for name, img in imgs.items():
        for size in (640, 1280):
            r = model.predict(img, imgsz=size, conf=CONF, verbose=False)[0]
            full[m][size][name] = best(r, CAT)
            times[m][f"full{size}"].append(r.speed["inference"])
            cv2.imwrite(str(out / f"{Path(name).stem}_full{size}.jpg"), r.plot())

    for name, view in VIEW.items():
        roi[m][name] = {}
        for z, zone in ZONES[view].items():
            r = model.predict(crop(imgs[name], zone), imgsz=640, conf=CONF, verbose=False)[0]
            roi[m][name][z] = (best(r, CAT), best(r, DOG))
            times[m]["roi"].append(r.speed["inference"])
            cv2.imwrite(str(out / f"{Path(name).stem}_{z}.jpg"), r.plot())

# ---------------- 결과 출력 ----------------
print("===== ①② 전체 사진: 고양이 최고 점수 (0 = 못 찾음) =====")
print(f"{'사진':16s}" + "".join(f"{m + '@' + str(s):>15s}" for m in MODELS for s in (640, 1280)))
for name in imgs:
    print(f"{name:16s}" + "".join(f"{full[m][s][name]:15.2f}" for m in MODELS for s in (640, 1280)))
for m in MODELS:
    for s in (640, 1280):
        n = sum(v >= 0.4 for k, v in full[m][s].items() if k != "dog.jpg")
        print(f"{m} imgsz {s}: 0.4 이상 {n}장 / 고양이 사진 {len(imgs) - 1}장")

print("\n===== ③ 구역만 잘라서: (고양이, 강아지) 최고 점수 =====")
for name in VIEW:
    print(f"{name} — 정답: {TRUTH[name]}")
    for m in MODELS:
        txt = "  ".join(f"{z}: cat {c:.2f} / dog {d:.2f}" for z, (c, d) in roi[m][name].items())
        print(f"   {m:8s} {txt}")

print("\n===== 평균 처리 시간 (노트북 CPU, ms) =====")
for m in MODELS:
    print(f"{m}: " + ", ".join(f"{k} {sum(v) / len(v):.0f}" for k, v in times[m].items()))
