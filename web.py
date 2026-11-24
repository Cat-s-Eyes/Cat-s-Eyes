# web.py — A: 조회·설정 웹
#
#   .venv/bin/python web.py
#   같은 Wi-Fi에서 http://<파이 IP>:5000
#
# 설정 이름과 형식은 db.py 와 맞춘다.
#   meal_times "08:00,18:00" / clean_threshold "5" / stay_seconds "20"
#   play_time "14:00" / play_minutes "5"
# 구역 좌표는 사진 크기 대비 0~1. name 은 bowl, litter.
import sys
from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, g, jsonify, render_template, request, send_file

from db import clean_remaining, connect, count_litter, count_meals, get_routine, get_setting, init_db, set_setting
from test_camera import capture_still

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "data" / "snapshot.jpg"
FALLBACK_SHOT = ROOT / "runs" / "camera_test.jpg"
SETTING_KEYS = ("meal_times", "play_time", "play_minutes", "clean_threshold", "stay_seconds")
ZONE_NAMES = ("bowl", "litter")

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


def db():
    if "conn" not in g:
        g.conn = connect()
        init_db(g.conn)
    return g.conn


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def unacked(conn):
    rows = conn.execute(
        "SELECT id, type, created_at FROM alerts WHERE acked_at IS NULL ORDER BY created_at"
    ).fetchall()
    return [dict(row) for row in rows]


def settings_dict(conn):
    return {key: get_setting(conn, key) for key in SETTING_KEYS}


def zones_dict(conn):
    rows = conn.execute("SELECT name, x1, y1, x2, y2 FROM zones WHERE name IN ('bowl', 'litter')").fetchall()
    return {row["name"]: dict(row) for row in rows}


def parse_hhmm(text):
    text = text.strip()
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError:
        raise ValueError(f"시각 형식이 아닙니다: {text}") from None
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def parse_meal_times(text):
    parts = [part.strip() for part in str(text).split(",")]
    parts = [part for part in parts if part]
    if not parts:
        raise ValueError("밥 시간을 하나 이상 적어 주세요. 예: 08:00,18:00")
    return ",".join(parse_hhmm(part) for part in parts)


def parse_positive(text, label, limit):
    try:
        number = int(str(text).strip())
    except ValueError:
        raise ValueError(f"{label}은 숫자로 적어 주세요.") from None
    if not 1 <= number <= limit:
        raise ValueError(f"{label}은 1부터 {limit}까지입니다.")
    return str(number)


def parse_settings(body):
    if not isinstance(body, dict):
        raise ValueError("설정 내용이 없습니다.")
    return {
        "meal_times": parse_meal_times(body.get("meal_times", "")),
        "play_time": parse_hhmm(str(body.get("play_time", ""))),
        "play_minutes": parse_positive(body.get("play_minutes", ""), "놀이 시간", 180),
        "clean_threshold": parse_positive(body.get("clean_threshold", ""), "청소 기준", 50),
        "stay_seconds": parse_positive(body.get("stay_seconds", ""), "머문 시간", 600),
    }


def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def parse_zones(body):
    if not isinstance(body, dict):
        raise ValueError("구역 내용이 없습니다.")
    raw = body.get("zones", body)
    found = {}
    for name in ZONE_NAMES:
        box = raw.get(name) if isinstance(raw, dict) else None
        if not box:
            continue
        try:
            x1, x2 = sorted((clamp01(box["x1"]), clamp01(box["x2"])))
            y1, y2 = sorted((clamp01(box["y1"]), clamp01(box["y2"])))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{name} 좌표가 잘못되었습니다.") from None
        if x2 - x1 < 0.01 or y2 - y1 < 0.01:
            raise ValueError("구역이 너무 작습니다. 사진 위에서 조금 더 크게 드래그하세요.")
        found[name] = {"x1": round(x1, 4), "y1": round(y1, 4), "x2": round(x2, 4), "y2": round(y2, 4)}
    if not found:
        raise ValueError("밥그릇이나 화장실 구역을 먼저 그려 주세요.")
    return found


def save_zones(conn, zones):
    try:
        for name, box in zones.items():
            conn.execute(
                "INSERT INTO zones (name, x1, y1, x2, y2) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET x1 = excluded.x1, y1 = excluded.y1, "
                "x2 = excluded.x2, y2 = excluded.y2",
                (name, box["x1"], box["y1"], box["x2"], box["y2"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def snapshot_file():
    if SNAPSHOT.is_file():
        return SNAPSHOT
    if FALLBACK_SHOT.is_file():
        return FALLBACK_SHOT
    return None


def parse_day(text):
    if not text:
        return date.today()
    try:
        return date.fromisoformat(text)
    except ValueError:
        abort(400, description="날짜는 YYYY-MM-DD 형식입니다.")


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/settings")
def settings_page():
    return render_template("settings.html")


@app.get("/api/status")
def api_status():
    conn = db()
    config = settings_dict(conn)
    return jsonify({
        "meal_count": count_meals(conn),
        "litter_count": count_litter(conn),
        "clean_remaining": clean_remaining(conn),
        "clean_threshold": config["clean_threshold"],
        "meal_times": config["meal_times"],
        "play_time": config["play_time"],
        "play_minutes": config["play_minutes"],
        "alerts": unacked(conn),
    })


@app.get("/api/routine")
def api_routine():
    day = parse_day(request.args.get("date"))
    return jsonify({"date": day.isoformat(), "items": get_routine(db(), day)})


@app.get("/api/settings")
def api_settings():
    return jsonify(settings_dict(db()))


@app.post("/api/settings")
def api_save_settings():
    try:
        values = parse_settings(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    conn = db()
    for key, value in values.items():
        set_setting(conn, key, value)
    return jsonify(values)


@app.get("/api/zones")
def api_zones():
    return jsonify({"zones": zones_dict(db())})


@app.post("/api/zones")
def api_save_zones():
    try:
        zones = parse_zones(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    conn = db()
    save_zones(conn, zones)
    return jsonify({"zones": zones_dict(conn)})


@app.get("/snapshot.jpg")
def snapshot():
    path = snapshot_file()
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=0)


@app.post("/api/snapshot")
def take_snapshot():
    try:
        capture_still("/dev/video0", SNAPSHOT)
    except SystemExit as exc:
        message = exc.code if isinstance(exc.code, str) else "사진을 찍지 못했습니다."
        return jsonify({"error": message}), 503
    return jsonify({"ok": True})


def main():
    print("웹 서버를 시작합니다. 같은 Wi-Fi에서 http://<파이 IP>:5000")
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    sys.exit(main())
