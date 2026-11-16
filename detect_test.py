# detect_test.py — 1단계: 사진에서 고양이(cat)만 찾아 점수와 가운데 점 출력
from pathlib import Path

from ultralytics import YOLO

BASE = Path(__file__).resolve().parent      # 이 파일이 있는 폴더 = C:\Project\Cat_Care
IMAGE_DIR = BASE / "CatCare_Image"          # 시험용 사진 폴더
RUNS_DIR = BASE / "runs"                    # 결과 저장 폴더 (노트북 전체 YOLO 설정과 상관없이 여기로)
CAT = 15                                    # COCO 데이터셋의 cat 번호 (계획서)

model = YOLO(str(BASE / "yolo11n.pt"))      # YOLO11n 모델 (계획서)

results = model.predict(
    source=str(IMAGE_DIR),        # 폴더 안 사진 전부
    classes=[CAT],                # 고양이만 찾기
    conf=0.4,                     # 점수 0.4 미만은 버림 (계획서, 예시 값)
    save=True,                    # 박스 그린 사진 저장
    project=str(RUNS_DIR),        # 저장 위치를 Cat_Care\runs로 직접 지정
    name="detect_test",           # → C:\Project\Cat_Care\runs\detect_test
    exist_ok=True,                # 다시 실행해도 같은 폴더에 덮어쓰기
    verbose=False,                # YOLO 자체 출력은 끄고 아래에서 직접 출력
)

for r in results:
    name = Path(r.path).name                     # 사진 파일 이름
    if len(r.boxes) == 0:
        print(f"{name}: 고양이 없음")
        continue
    for box in r.boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()    # 박스 왼쪽 위 ~ 오른쪽 아래 (픽셀)
        conf = float(box.conf[0])                # 점수 (0~1)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2    # 박스 가운데 점 (계획서: 구역 판정에 사용)
        print(f"{name}: cat {conf:.2f}  center=({cx:.0f}, {cy:.0f})")

print(f"\n결과 사진 저장 위치: {results[0].save_dir if results else RUNS_DIR}")
