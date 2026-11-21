# alerts.py — 5·6단계: 알림 만들기 (B는 alerts 표에 한 줄 넣는 것까지, 표시·끄기는 A)
#
#   - check_meal(conn, now)  : 밥 시간이면 'meal' 알림 한 줄 (5단계)
#   - check_clean(conn, now) : 마지막 청소 뒤 화장실 횟수가 기준 이상이면 'clean' 알림 한 줄 (6단계)
#
# camera_ai.py가 사진을 처리할 때마다 한 번씩 부른다.
# 알림을 끄고 cleanings에 청소 기록을 넣는 것은 A(device.py) → 그 뒤로 화장실 횟수는 0부터 다시 셈
from datetime import datetime, timedelta

from db import _write, count_litter, get_setting, iso

MEAL_WINDOW = timedelta(minutes=10)   # 밥 시각부터 이 시간 안에만 알림 (계획서 예시 10분)


def meal_slots(conn, now):
    """오늘의 밥 시각 목록. meal_times = "08:00,18:00" → [오늘 08:00, 오늘 18:00]"""
    slots = []
    for text in get_setting(conn, "meal_times").split(","):
        text = text.strip()
        try:
            hm = datetime.strptime(text, "%H:%M").time()
        except ValueError:
            continue                                  # 잘못 적힌 값은 건너뜀
        slots.append(datetime.combine(now.date(), hm))
    return slots


def check_meal(conn, now=None):
    """지금이 밥 시각 ~ +10분 사이이고, 그 시각 이후 만든 meal 알림이 없으면 한 줄 넣는다.
    알림을 만들었으면 True."""
    now = now or datetime.now()
    for slot in meal_slots(conn, now):
        if not (slot <= now < slot + MEAL_WINDOW):
            continue
        made = conn.execute("SELECT 1 FROM alerts WHERE type = 'meal' AND created_at >= ? LIMIT 1",
                            (iso(slot),)).fetchone()
        if made:
            continue                                  # 이번 밥 시각 알림은 이미 만듦
        _write(conn, "INSERT INTO alerts (type, created_at, acked_at) VALUES ('meal', ?, NULL)", (iso(now),))
        return True
    return False


def check_clean(conn, now=None):
    """마지막 청소 뒤 화장실 간 횟수 ≥ clean_threshold 이고,
    마지막 청소 뒤 만든 clean 알림이 없으면 한 줄 넣는다. 알림을 만들었으면 True."""
    now = now or datetime.now()
    if count_litter(conn) < int(get_setting(conn, "clean_threshold")):
        return False
    made = conn.execute("SELECT 1 FROM alerts WHERE type = 'clean' "
                        "AND created_at > COALESCE((SELECT MAX(cleaned_at) FROM cleanings), '') LIMIT 1").fetchone()
    if made:
        return False                                  # 이번 청소 주기의 알림은 이미 만듦
    _write(conn, "INSERT INTO alerts (type, created_at, acked_at) VALUES ('clean', ?, NULL)", (iso(now),))
    return True


# ---------------- 시험 (임시 DB 사용, data\cat_care.db는 건드리지 않음) ----------------
def test_meal():
    import tempfile
    from pathlib import Path

    from db import connect, init_db, set_setting

    def count(conn):
        return conn.execute("SELECT COUNT(*) FROM alerts WHERE type = 'meal'").fetchone()[0]

    D = datetime(2026, 11, 20)
    at = lambda h, m: D.replace(hour=h, minute=m)                       # 오늘 h:m
    # (이름, 시각, 만들어져야 하는지, 시험 뒤 meal 알림 총 개수)
    steps = [
        ("07:59 밥 시간 전", at(7, 59), False, 0),
        ("08:00 밥 시간", at(8, 0), True, 1),
        ("08:05 같은 밥 시간 다시 확인 (중복 방지)", at(8, 5), False, 1),
        ("08:09:59 아직 10분 안 (중복 방지)", at(8, 9).replace(second=59), False, 1),
        ("08:10 10분 지남", at(8, 10), False, 1),
        ("18:03 두 번째 밥 시간", at(18, 3), True, 2),
        ("18:04 같은 밥 시간 다시 확인", at(18, 4), False, 2),
        ("다음 날 08:01", at(8, 1) + timedelta(days=1), True, 3),
    ]
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "test.db")
        init_db(conn)
        print("===== ① 하루 흐름 (meal_times = 08:00,18:00) =====")
        for name, now, want_made, want_total in steps:
            made, total = check_meal(conn, now), count(conn)
            ok = made == want_made and total == want_total
            results.append(ok)
            print(f"{'✅' if ok else '❌'} {name}: {'알림 생김' if made else '없음'} (총 {total}개)")
        conn.close()

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "test.db")
        init_db(conn)
        print("\n===== ② 프로그램을 늦게 켬 =====")
        made = check_meal(conn, at(9, 30))
        results.append(not made)
        print(f"{'✅' if not made else '❌'} 09:30에 처음 켬 → 지난 08:00 알림 {'뜸' if made else '안 뜸'}")

        print("\n===== ③ A가 알림을 끈 뒤 (acked_at 채움) =====")
        check_meal(conn, at(18, 0))
        conn.execute("UPDATE alerts SET acked_at = ? WHERE type = 'meal'", (iso(at(18, 1)),))
        conn.commit()
        made = check_meal(conn, at(18, 2))
        results.append(not made)
        print(f"{'✅' if not made else '❌'} 18:01에 끔 → 18:02에 다시 확인해도 새 알림 {'생김' if made else '안 생김'}")
        conn.close()

    with tempfile.TemporaryDirectory() as tmp:          # 앞 시험의 18:00 알림이 섞이지 않게 새 DB
        conn = connect(Path(tmp) / "test.db")
        init_db(conn)
        print("\n===== ④ 설정에 잘못된 값이 섞임 =====")
        set_setting(conn, "meal_times", " 12:30 , 8시, 25:00,")
        slots = [f"{s:%H:%M}" for s in meal_slots(conn, D)]
        made = check_meal(conn, at(12, 31))
        ok = slots == ["12:30"] and made
        results.append(ok)
        print(f"{'✅' if ok else '❌'} ' 12:30 , 8시, 25:00,' → 읽은 시각 {slots}, 12:31 알림 {'생김' if made else '없음'}")
        conn.close()

    print(f"\n→ {sum(results)}/{len(results)}개 통과")
    return results


def test_clean():
    import tempfile
    from pathlib import Path

    from db import connect, init_db, save_visit, set_setting

    T = datetime(2026, 11, 20, 9, 0, 0)
    clock = [T]                                          # 가짜 시계: 방문할 때마다 10분씩 흐름

    def visit(conn, seconds):
        """화장실 방문 1개 저장 → 그 시각에 check_clean. (알림 생겼는지, 횟수) 돌려줌"""
        start = clock[0]
        end = start + timedelta(seconds=seconds)
        save_visit(conn, {"zone": "litter", "entered_at": start, "left_at": end, "duration_s": float(seconds)})
        clock[0] += timedelta(minutes=10)
        return check_clean(conn, end), count_litter(conn)

    def total(conn):
        return conn.execute("SELECT COUNT(*) FROM alerts WHERE type = 'clean'").fetchone()[0]

    results = []

    def check(name, ok, detail):
        results.append(ok)
        print(f"{'✅' if ok else '❌'} {name}: {detail}")

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "test.db")
        init_db(conn)
        print("===== ① 기준까지 (clean_threshold = 5, stay_seconds = 20) =====")
        made = [visit(conn, 25)[0] for _ in range(4)]
        check("화장실 4회", not any(made) and total(conn) == 0, f"알림 {total(conn)}개")
        made, n = visit(conn, 10)
        check("10초 짧은 방문 (횟수 안 셈)", not made and n == 4, f"횟수 {n}, 알림 {total(conn)}개")
        made, n = visit(conn, 25)
        check("5회째", made and total(conn) == 1, f"횟수 {n}, 알림 {'생김' if made else '없음'} (총 {total(conn)}개)")
        made, n = visit(conn, 25)
        check("6회째 (아직 청소 안 함, 중복 방지)", not made and total(conn) == 1, f"횟수 {n}, 총 {total(conn)}개")

        print("\n===== ② A가 알림을 끄고 청소 기록을 넣음 =====")
        conn.execute("UPDATE alerts SET acked_at = ? WHERE type = 'clean'", (iso(clock[0]),))
        _write(conn, "INSERT INTO cleanings (cleaned_at) VALUES (?)", (iso(clock[0]),))
        clock[0] += timedelta(minutes=10)
        n = count_litter(conn)
        made = check_clean(conn, clock[0])
        check("청소 직후", n == 0 and not made, f"횟수 {n}, 알림 {'생김' if made else '없음'}")

        print("\n===== ③ 청소 뒤 다시 5회 =====")
        made = [visit(conn, 25)[0] for _ in range(4)]
        check("청소 뒤 4회", not any(made) and total(conn) == 1, f"총 {total(conn)}개 (그대로)")
        made, n = visit(conn, 25)
        check("청소 뒤 5회째", made and total(conn) == 2, f"횟수 {n}, 알림 {'생김' if made else '없음'} (총 {total(conn)}개)")
        conn.close()

    with tempfile.TemporaryDirectory() as tmp:          # 새 DB에서 기준만 바꿔 봄
        conn = connect(Path(tmp) / "test.db")
        init_db(conn)
        print("\n===== ④ 웹에서 기준을 2회로 낮춤 (계획서 시험 방법) =====")
        set_setting(conn, "clean_threshold", 2)
        made1, _ = visit(conn, 25)
        made2, n = visit(conn, 25)
        check("기준 2회", not made1 and made2 and total(conn) == 1,
              f"1회째 {'알림' if made1 else '없음'}, 2회째 {'알림 생김' if made2 else '없음'}")
        conn.close()

    print(f"\n→ {sum(results)}/{len(results)}개 통과")
    return results


if __name__ == "__main__":
    print("################ 밥 시간 알림 ################")
    test_meal()
    print("\n################ 청소 알림 ################")
    test_clean()
