# stay_tracker.py — 3단계: 머문 시간 계산 (구역마다 따로)
#
# 사진 1장마다 구역 판정 결과(bowl / litter / None=구역 밖)를 넣으면,
# 구역마다 '밖 → 안 → 놓침 → 안 또는 밖' 상태를 관리하고, 방문이 끝나면 기록 1개를 돌려준다.
#
#   - 들어온 시각(entered_at): 처음 구역 안에 보인 사진의 시각
#   - 끊김 허용 3초 (계획서 예시): 마지막으로 본 뒤 3초 안에 다시 보이면 같은 방문
#   - 3초 넘게 안 보이면 방문 끝: 나간 시각(left_at) = 마지막으로 본 시각
#   - 프로그램 종료 시: 안에 있던 방문은 그때까지(마지막으로 본 시각)로 저장
#
# ⚠️ 사진 처리 간격이 끊김 허용(3초)보다 길면 매 사진이 따로 방문으로 나뉜다 → 간격은 1~2초로 유지
#
# 실행하면: ① 가짜 시간 기록으로 규칙 시험  ② 실제 사진을 시간 순서로 흉내 내서 YOLO26n까지 연결 시험
from datetime import datetime, timedelta

GAP_S = {"bowl": 3.0, "litter": 3.0}   # 끊김 허용(초). 화장실은 영상을 보고 조정 (계획서)


class ZoneTracker:
    """구역 하나의 상태 관리. state: out(밖) / in(안) / lost(놓침)"""

    def __init__(self, zone, gap_s=3.0):
        self.zone = zone
        self.gap = timedelta(seconds=gap_s)
        self.state = "out"
        self.entered_at = self.last_seen = None

    def update(self, seen, now):
        """사진 1장 판정 결과를 넣는다. 방문이 끝났으면 기록(dict), 아니면 None."""
        done = None
        if self.state != "out" and now - self.last_seen > self.gap:
            done = self._finish()                  # 너무 오래 안 보였음 → 앞 방문 끝
        if seen:
            if self.state == "out":
                self.entered_at = now              # 새 방문 시작
            self.state, self.last_seen = "in", now
        elif self.state == "in":
            self.state = "lost"                    # 잠깐 놓침 (아직 같은 방문)
        return done

    def close(self):
        """프로그램 종료 시: 진행 중인 방문을 저장하고 끝낸다."""
        return self._finish() if self.state != "out" else None

    def _finish(self):
        visit = {"zone": self.zone, "entered_at": self.entered_at, "left_at": self.last_seen,
                 "duration_s": (self.last_seen - self.entered_at).total_seconds()}
        self.state, self.entered_at, self.last_seen = "out", None, None
        return visit


class StayTracker:
    """밥그릇·화장실 구역을 한꺼번에 관리. where = 'bowl' / 'litter' / None(구역 밖)"""

    def __init__(self, gaps=GAP_S):
        self.zones = {z: ZoneTracker(z, g) for z, g in gaps.items()}

    def update(self, where, now):
        visits = [t.update(where == z, now) for z, t in self.zones.items()]
        return [v for v in visits if v]

    def close(self):
        return [v for v in (t.close() for t in self.zones.values()) if v]


def fmt(v):
    """계획서 예시 형식: bowl 08:02:10 ~ 08:02:55, 45초"""
    return f"{v['zone']} {v['entered_at']:%H:%M:%S} ~ {v['left_at']:%H:%M:%S}, {v['duration_s']:.0f}초"


def run(timeline, start, step_s=1.0):
    """timeline: 사진마다 구역 판정 결과 목록. step_s초 간격으로 넣고 끝나면 close."""
    tr, out = StayTracker(), []
    for i, where in enumerate(timeline):
        out += tr.update(where, start + timedelta(seconds=i * step_s))
    return out + tr.close()


def frames(*parts):
    """('bowl', 46), (None, 15) … → 1초 간격 판정 목록"""
    return [w for w, n in parts for _ in range(n)]


# ---------------- ① 가짜 시간 기록으로 규칙 시험 ----------------
def test_rules():
    T0 = datetime(2026, 11, 19, 8, 2, 0)
    B, L = "bowl", "litter"
    cases = [
        # (이름, 판정 목록, 사진 간격, 기대 결과 [(구역, 시작 초, 끝 초)])
        ("정상 방문 45초 (계획서 예시)", frames((None, 10), (B, 46), (None, 15)), 1, [(B, 10, 55)]),
        ("1장 놓침 → 한 번으로", frames((B, 21), (None, 1), (B, 18), (None, 5)), 1, [(B, 0, 39)]),
        ("경계: 마지막 본 뒤 3초째 다시 보임 → 한 번", frames((B, 21), (None, 2), (B, 10), (None, 5)), 1, [(B, 0, 32)]),
        ("경계: 마지막 본 뒤 4초째 다시 보임 → 두 번", frames((B, 21), (None, 3), (B, 10), (None, 5)), 1, [(B, 0, 20), (B, 24, 33)]),
        ("5초 놓침 → 두 번", frames((B, 21), (None, 5), (B, 15), (None, 5)), 1, [(B, 0, 20), (B, 26, 40)]),
        ("밥그릇 → 화장실 이동", frames((B, 11), (None, 1), (L, 19), (None, 5)), 1, [(B, 0, 10), (L, 12, 30)]),
        ("안에 있는 채로 프로그램 종료", frames((None, 50), (B, 11)), 1, [(B, 50, 60)]),
        ("한 장만 잡힘 (오인식 가능)", frames((None, 5), (B, 1), (None, 10)), 1, [(B, 5, 5)]),
        ("사진 간격 2초 (파이가 느릴 때)", frames((B, 21), (None, 3)), 2, [(B, 0, 40)]),
    ]
    ok = 0
    for name, tl, step, want in cases:
        got = run(tl, T0, step)
        exp = [(z, T0 + timedelta(seconds=a), T0 + timedelta(seconds=b)) for z, a, b in want]
        passed = [(v["zone"], v["entered_at"], v["left_at"]) for v in got] == exp
        ok += passed
        print(f"{'✅' if passed else '❌'} {name}")
        for v in got:
            print(f"      {fmt(v)}")
    print(f"→ {ok}/{len(cases)}개 통과")

    # 주의 사례: 사진 간격이 끊김 허용(3초)보다 길 때
    got = run(frames((B, 11)), T0, 4)
    print(f"\n⚠️ 사진 간격 4초로 40초 머문 경우 → 방문 {len(got)}개로 나뉨 (간격은 3초보다 짧아야 함)")


# ---------------- ② 실제 사진을 시간 순서로 흉내 ----------------
def test_photos():
    from ultralytics import YOLO
    import zone_check as zc

    model = YOLO(str(zc.BASE / f"{zc.MODEL}.pt"))
    where_of = {}
    for name in ("house9.jpg", "house7.jpg", "house8.jpg"):   # 같은 각도(far) 사진
        img = zc.load_bgr(zc.IMAGE_DIR / name)
        hits = {}
        for z, zone in zc.ZONES[zc.VIEW[name]].items():
            found = zc.check_zone(model, img, zone)[0]
            if found is not None:
                hits[z] = found
        where_of[name] = max(hits, key=hits.get) if hits else None
        print(f"   {name}: {where_of[name] or '구역 밖'}")

    # 1초에 1장: 밖 3장 → 화장실 6장 → 밖 2장(놓침) → 화장실 4장 → 밖 6장
    seq = ["house9.jpg"] * 3 + ["house7.jpg"] * 6 + ["house9.jpg"] * 2 + ["house8.jpg"] * 4 + ["house9.jpg"] * 6
    got = run([where_of[n] for n in seq], datetime(2026, 11, 19, 8, 0, 0))
    print("   기대: litter 08:00:03 ~ 08:00:14, 11초 (중간 2초 놓침은 한 번으로)")
    for v in got:
        print(f"   결과: {fmt(v)}")


if __name__ == "__main__":
    print("===== ① 규칙 시험 (가짜 시간 기록) =====")
    test_rules()
    print("\n===== ② 실제 사진으로 흉내 (YOLO26n + 구역 판정 + 머문 시간) =====")
    test_photos()
