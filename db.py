# db.py — 4단계: DB 기록 (SQLite cat_care.db)
#
# 세 프로그램(camera_ai.py · device.py · web.py)이 함께 쓰는 DB를 만들고 다루는 함수 모음
#   - init_db()          : 테이블 만들기 + 설정 기본값 넣기 (이미 있으면 그대로 둠)
#   - save_visit()       : 머문 시간 기록 1개를 visits에 저장 (stay_seconds 이상이면 counted=1)
#   - count_meals()      : 그날 밥 먹은 횟수
#   - count_litter()     : 마지막 청소 뒤 화장실 간 횟수
#   - clean_remaining()  : 청소까지 남은 횟수
#   - get_routine()      : 그날 하루 루틴 (웹에서 사용, min_seconds 미만은 빼고 돌려줌)
#
# 시각은 TEXT, ISO 형식 '2026-11-20T08:02:10' (9주차 DB 수업과 같음)
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data" / "cat_care.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    zone        TEXT    NOT NULL,   -- 'bowl' | 'litter'
    entered_at  TEXT    NOT NULL,   -- 들어온 시각
    left_at     TEXT    NOT NULL,   -- 나간 시각
    duration_s  REAL    NOT NULL,   -- 머문 시간(초)
    counted     INTEGER NOT NULL    -- 1: N초 이상 → 횟수에 포함
);
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS zones (
    name  TEXT PRIMARY KEY,         -- 'bowl' | 'litter'
    x1 REAL NOT NULL, y1 REAL NOT NULL,
    x2 REAL NOT NULL, y2 REAL NOT NULL   -- 사진 크기에 대한 비율 (0~1)
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,      -- 'meal' | 'clean'
    created_at  TEXT NOT NULL,
    acked_at    TEXT                -- 스위치로 끈 시각 (안 껐으면 NULL)
);
CREATE TABLE IF NOT EXISTS cleanings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cleaned_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS play_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    end_reason  TEXT                -- 'timeout' | 'switch' | 'error' | 'web'
);
"""

# 설정 기본값 (계획서 예시값). 값이 없을 때만 넣음 → 웹에서 바꾼 값은 지키기
DEFAULTS = {"meal_times": "08:00,18:00", "play_time": "14:00", "play_minutes": "5",
            "clean_threshold": "5", "stay_seconds": "20"}

ROUTINE_MIN_S = 2   # 하루 루틴 화면에서 숨길 짧은 방문 기준(초). 저장은 모두 함 (11-19 결정 C)


def iso(t):
    """datetime → '2026-11-20T08:02:10'"""
    return t.isoformat(timespec="seconds")


def connect(path=DB_PATH):
    """세 프로그램이 같이 쓰므로 잠깐 기다릴 수 있게(timeout=5) 열고, WAL을 켠다."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _write(conn, sql, params=()):
    """쓰기 1번: 성공하면 commit, 예외가 나면 rollback (9주차 방식)"""
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise


def init_db(conn):
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", DEFAULTS.items())
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS[key]


def set_setting(conn, key, value):
    """원래는 웹(A)이 저장함. 여기서는 시험용"""
    _write(conn, "INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def save_visit(conn, visit):
    """stay_tracker가 돌려준 방문 기록 1개를 저장. stay_seconds 이상이면 counted=1"""
    stay = float(get_setting(conn, "stay_seconds"))     # 웹에서 바꿀 수 있어서 매번 읽음
    counted = 1 if visit["duration_s"] >= stay else 0
    _write(conn, "INSERT INTO visits (zone, entered_at, left_at, duration_s, counted) VALUES (?, ?, ?, ?, ?)",
           (visit["zone"], iso(visit["entered_at"]), iso(visit["left_at"]), visit["duration_s"], counted))
    return counted


def _day_range(day):
    start = datetime.combine(day, datetime.min.time())
    return iso(start), iso(start + timedelta(days=1))


def count_meals(conn, day=None):
    """그날 밥 먹은 횟수 (계획서 5-4)"""
    a, b = _day_range(day or date.today())
    return conn.execute("SELECT COUNT(*) FROM visits WHERE zone = 'bowl' AND counted = 1 "
                        "AND entered_at >= ? AND entered_at < ?", (a, b)).fetchone()[0]


def count_litter(conn):
    """마지막 청소 뒤 화장실 간 횟수 (계획서 5-4)"""
    return conn.execute("SELECT COUNT(*) FROM visits WHERE zone = 'litter' AND counted = 1 "
                        "AND entered_at > COALESCE((SELECT MAX(cleaned_at) FROM cleanings), '')").fetchone()[0]


def clean_remaining(conn):
    """청소까지 남은 횟수 = clean_threshold − 마지막 청소 뒤 화장실 간 횟수 (0보다 작으면 0)"""
    return max(0, int(get_setting(conn, "clean_threshold")) - count_litter(conn))


def get_routine(conn, day=None, min_seconds=ROUTINE_MIN_S):
    """그날 하루 루틴 목록 (웹 /api/routine 용). min_seconds 미만 방문은 빼고 돌려줌.
    전부 보려면 min_seconds=0"""
    a, b = _day_range(day or date.today())
    rows = conn.execute("SELECT zone, entered_at, left_at, duration_s, counted FROM visits "
                        "WHERE entered_at >= ? AND entered_at < ? AND duration_s >= ? "
                        "ORDER BY entered_at", (a, b, min_seconds)).fetchall()
    return [dict(r) for r in rows]


# ---------------- 시험 (임시 DB 사용, data\cat_care.db는 건드리지 않음) ----------------
if __name__ == "__main__":
    import tempfile
    from stay_tracker import frames, run

    def check(name, got, want):
        print(f"{'✅' if got == want else '❌'} {name}: {got}" + ("" if got == want else f" (기대 {want})"))
        return got == want

    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(Path(tmp) / "test.db")
        results = []

        print("===== ① 테이블·기본값 =====")
        init_db(conn)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                             "AND name != 'sqlite_sequence' ORDER BY name")]
        results.append(check("테이블 6개", tables,
                             ["alerts", "cleanings", "play_sessions", "settings", "visits", "zones"]))
        results.append(check("WAL 모드", conn.execute("PRAGMA journal_mode").fetchone()[0], "wal"))
        results.append(check("기본값 stay_seconds", get_setting(conn, "stay_seconds"), "20"))
        set_setting(conn, "clean_threshold", 3)
        init_db(conn)                                   # 다시 실행해도 바꾼 값 유지되는지
        results.append(check("init 다시 해도 바꾼 값 유지", get_setting(conn, "clean_threshold"), "3"))
        set_setting(conn, "clean_threshold", 5)

        print("\n===== ② stay_tracker 결과를 저장 =====")
        T = datetime(2026, 11, 20, 8, 0, 0)
        B, L = "bowl", "litter"
        # 하루 흉내: 밥 45초 → 화장실 30초 → 밥 3초 → 1장 오인식(밥 0초) → 화장실 25초 (10초 간격으로 띄움)
        tl = frames((None, 2), (B, 46), (None, 10), (L, 31), (None, 10), (B, 4), (None, 10),
                    (B, 1), (None, 10), (L, 26), (None, 10))
        for v in run(tl, T):
            c = save_visit(conn, v)
            print(f"   저장: {v['zone']:6s} {v['entered_at']:%H:%M:%S} {v['duration_s']:4.0f}초 → counted={c}")
        save_visit(conn, {"zone": B, "entered_at": T - timedelta(hours=10),       # 어제 밤 방문
                          "left_at": T - timedelta(hours=10) + timedelta(seconds=40), "duration_s": 40.0})

        print("\n===== ③ 횟수 계산 =====")
        results.append(check("오늘 밥 먹은 횟수 (어제 것·짧은 것 제외)", count_meals(conn, T.date()), 1))
        results.append(check("화장실 간 횟수 (청소 기록 없음)", count_litter(conn), 2))
        results.append(check("청소까지 남은 횟수 (5 − 2)", clean_remaining(conn), 3))
        # 첫 화장실(08:00:58) 뒤, 두 번째(08:02:04) 전인 08:02:00에 청소했다고 가정
        _write(conn, "INSERT INTO cleanings (cleaned_at) VALUES (?)", (iso(T + timedelta(minutes=2)),))
        results.append(check("청소 뒤 화장실 간 횟수", count_litter(conn), 1))
        results.append(check("청소 뒤 남은 횟수 (5 − 1)", clean_remaining(conn), 4))

        print("\n===== ④ 하루 루틴 (웹용 get_routine) =====")
        full = get_routine(conn, T.date(), min_seconds=0)
        short = get_routine(conn, T.date())
        results.append(check("전부 (min_seconds=0)", len(full), 5))
        results.append(check(f"웹 화면 ({ROUTINE_MIN_S}초 미만 숨김)", len(short), 4))
        for r in short:
            mark = "→ 횟수 +1" if r["counted"] else ""
            print(f"   {r['entered_at'][11:16]} · {r['zone']:6s} · {r['duration_s']:.0f}초 머묾 {mark}")
        conn.close()

    print(f"\n→ {sum(results)}/{len(results)}개 통과")
