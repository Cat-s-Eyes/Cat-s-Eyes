# detect_check.py — 1-3단계: 고양이를 놓친 원인 진단 + 모델 비교 (YOLO11n · YOLO26n · YOLO11s)
#
# - 점수 기준을 0.1까지 낮추고, 고양이만 찾는 제한도 풀어서 실행한다.
#   → 놓친 고양이가 "점수가 낮았는지 / 다른 동물로 착각했는지 / 아예 못 봤는지" 확인
# - 결과 사진: C:\Project\Cat_Care\runs\detect_check\<모델 이름>\
from pathlib import Path

from ultralytics import YOLO

BASE = Path(__file__).resolve().parent
IMAGE_DIR = BASE / "CatCare_Image"
RUNS_DIR = BASE / "runs" / "detect_check"
MODELS = ["yolo11n", "yolo26n", "yolo11s"]
CAT = 15            # COCO cat 번호 (계획서)
LOW_CONF = 0.1      # 진단용 낮은 점수 기준
PLAN_CONF = 0.4     # 계획서 기준 (예시 값)

images = sorted(p for p in IMAGE_DIR.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
summary = {}        # summary[모델][사진] = 고양이 최고 점수 (없으면 0)

for m in MODELS:
    model = YOLO(str(BASE / f"{m}.pt"))          # 없으면 이 폴더로 자동 다운로드
    print(f"\n===== {m} =====")
    summary[m] = {}
    times = []
    for img in images:
        r = model.predict(
            source=str(img), conf=LOW_CONF, save=True,
            project=str(RUNS_DIR), name=m, exist_ok=True, verbose=False,
        )[0]
        times.append(r.speed["inference"])
        cat_confs = [float(b.conf[0]) for b in r.boxes if int(b.cls[0]) == CAT]
        best_cat = max(cat_confs, default=0.0)
        summary[m][img.name] = best_cat
        others = sorted(
            ((r.names[int(b.cls[0])], float(b.conf[0])) for b in r.boxes if int(b.cls[0]) != CAT),
            key=lambda x: -x[1],
        )[:4]
        other_txt = ", ".join(f"{n} {c:.2f}" for n, c in others) or "-"
        cat_txt = f"cat {best_cat:.2f}" if best_cat else "cat 없음"
        print(f"{img.name:16s} {cat_txt:10s} | 그 밖에: {other_txt}")
    print(f"평균 추론 시간: {sum(times) / len(times):.0f} ms (노트북 CPU 기준)")

# 모델별 비교표
print("\n===== 고양이 최고 점수 비교 (0 = 못 찾음) =====")
print(f"{'사진':16s}" + "".join(f"{m:>10s}" for m in MODELS))
for img in images:
    print(f"{img.name:16s}" + "".join(f"{summary[m][img.name]:10.2f}" for m in MODELS))
for th in (PLAN_CONF, 0.25):
    print(f"점수 {th} 이상으로 찾은 사진 수: " + ", ".join(
        f"{m} {sum(v >= th for v in summary[m].values())}장" for m in MODELS))
