import base64
import math
import hashlib
import io
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import qrcode
import qrcode.image.svg
import requests as http_requests
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_file, send_from_directory, session
from flask_migrate import Migrate, upgrade
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from schema import schema_db

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.environ.get("TENNIS_DB_PATH", ROOT / "tennis.db"))
ELO_START = 1000
ELO_K = 24

app = Flask(__name__, static_folder=None)
if os.environ.get("TRUST_PROXY_HEADERS", "false").lower() == "true":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
EXTERNAL_URL = os.environ.get("EXTERNAL_URL", os.environ.get("APP_URL", "http://127.0.0.1:5000")).rstrip("/")
APP_URL = EXTERNAL_URL
QR_BASE_URL = (os.environ.get("QR_BASE_URL") or EXTERNAL_URL).rstrip("/")
COOKIE_SECURE_SETTING = os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SEED_DEMO_DATA = os.environ.get("SEED_DEMO_DATA", "false").lower() == "true"
ALLOW_LOCAL_USER_LOGIN = os.environ.get("ALLOW_LOCAL_USER_LOGIN", "false").lower() == "true"
OIDC_ISSUER = os.environ.get("CRM_OIDC_ISSUER", "https://lk.silaeder.ru").rstrip("/")
CRM_BASE_URL = os.environ.get("CRM_BASE_URL", OIDC_ISSUER).rstrip("/")
CRM_NOTIFICATION_CLIENT_ID = (os.environ.get("CRM_CLIENT_ID") or os.environ.get("CRM_OIDC_CLIENT_ID") or "").strip()
CRM_NOTIFICATION_CLIENT_SECRET = (os.environ.get("CRM_CLIENT_SECRET") or os.environ.get("CRM_OIDC_CLIENT_SECRET") or "").strip()
EXTERNAL_NOTIFICATIONS_ENABLED = bool(CRM_NOTIFICATION_CLIENT_ID and CRM_NOTIFICATION_CLIENT_SECRET)
OIDC_ENABLED = os.environ.get("CRM_OIDC_ENABLED", "false").lower() == "true" and all(
    os.environ.get(key) for key in ("SECRET_KEY", "CRM_OIDC_CLIENT_ID", "CRM_OIDC_CLIENT_SECRET")
)
OIDC_REDIRECT_URI = os.environ.get("CRM_OIDC_REDIRECT_URI", f"{EXTERNAL_URL}/auth/silaeder/callback")
OIDC_LOGOUT_REDIRECT_URI = os.environ.get("CRM_OIDC_POST_LOGOUT_REDIRECT_URI", f"{EXTERNAL_URL}/auth/silaeder/logout/callback")
LICHESS_BASE_URL = os.environ.get("LICHESS_BASE_URL", "https://lichess.org").rstrip("/")
LICHESS_CLIENT_ID = os.environ.get("LICHESS_CLIENT_ID", "sport.silaeder.ru").strip()
LICHESS_REDIRECT_URI = os.environ.get("LICHESS_REDIRECT_URI", f"{EXTERNAL_URL}/auth/lichess/callback")
app.config.update(
    MAX_CONTENT_LENGTH=32 * 1024,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(COOKIE_SECURE_SETTING == "true" if COOKIE_SECURE_SETTING else APP_URL.startswith("https://")),
    SESSION_COOKIE_NAME="tennis_session",
    SESSION_REFRESH_EACH_REQUEST=False,
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_PATH.resolve().as_posix()}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)
schema_db.init_app(app)
migrate = Migrate(app, schema_db, compare_type=True, render_as_batch=True)

LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
login_failures = {}
login_failures_lock = threading.Lock()
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))

oauth = OAuth(app)
if OIDC_ENABLED:
    oauth.register(
        name="silaeder",
        client_id=os.environ["CRM_OIDC_CLIENT_ID"],
        client_secret=os.environ["CRM_OIDC_CLIENT_SECRET"],
        server_metadata_url=f"{OIDC_ISSUER}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email roles", "code_challenge_method": "S256"},
    )


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def now_ms():
    return int(time.time() * 1000)


def day_key(timestamp):
    return time.strftime("%Y-%m-%d", time.localtime(timestamp / 1000))


def valid_score(a, b):
    high, low = max(a, b), min(a, b)
    if a == b or high < 11:
        return False
    return high == 11 if low < 10 else high - low == 2


def valid_sport(value):
    return value if value in {"tennis", "chess"} else None


def valid_match_score(sport, a, b, tournament=False):
    if sport == "tennis":
        return valid_score(a, b)
    return (a, b) in ({(2, 0), (0, 2)} if tournament else {(2, 0), (1, 1), (0, 2)})


def clean_text(value, field, max_length, required=True):
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"Поле «{field}» обязательно.")
    if len(text) > max_length:
        raise ValueError(f"Поле «{field}» не должно быть длиннее {max_length} символов.")
    return text


def clean_email(value):
    email = str(value or "").strip().lower()
    if not email:
        return ""
    if len(email) > 254 or email.count("@") != 1 or any(character.isspace() or ord(character) < 32 for character in email):
        raise ValueError("Укажите корректный адрес электронной почты.")
    local_part, domain = email.split("@", 1)
    if not local_part or not domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("Укажите корректный адрес электронной почты.")
    return email


def login_key(login):
    return request.remote_addr or "unknown", str(login or "").strip().lower()[:64]


def login_is_limited(key):
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    with login_failures_lock:
        recent = [stamp for stamp in login_failures.get(key, []) if stamp >= cutoff]
        login_failures[key] = recent
        return len(recent) >= LOGIN_MAX_FAILURES


def record_login_failure(key):
    with login_failures_lock:
        login_failures.setdefault(key, []).append(time.time())


def clear_login_failures(key):
    with login_failures_lock:
        login_failures.pop(key, None)


def confirmation_return_path():
    token = session.get("confirmation_token", "")
    if token and len(token) <= 128 and all(character.isalnum() or character in "-_" for character in token):
        return f"/confirm/{token}"
    return "/#home"


def seed_database():
    with db() as connection:
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            if len(ADMIN_PASSWORD) < 12:
                raise RuntimeError("Для первого запуска задайте ADMIN_PASSWORD длиной не менее 12 символов.")
            seed_users = [("admin", "Администратор", "Школы", "", "admin", ADMIN_PASSWORD, "admin", 0)]
            if SEED_DEMO_DATA:
                seed_users.extend([
                    ("u1", "Максим", "Орлов", "10Б", "maxim", secrets.token_urlsafe(24), "user", 1),
                    ("u2", "Анна", "Белова", "9А", "anna", secrets.token_urlsafe(24), "user", 1),
                    ("u3", "Илья", "Соколов", "11А", "ilya", secrets.token_urlsafe(24), "user", 1),
                    ("u4", "Мария", "Волкова", "8В", "maria", secrets.token_urlsafe(24), "user", 1),
                    ("u5", "Артём", "Кузнецов", "10А", "artem", secrets.token_urlsafe(24), "user", 1),
                    ("u6", "София", "Лебедева", "9Б", "sofia", secrets.token_urlsafe(24), "user", 1),
                    ("u7", "Даниил", "Морозов", "8А", "daniil", secrets.token_urlsafe(24), "user", 1),
                    ("u8", "Ева", "Новикова", "7Б", "eva", secrets.token_urlsafe(24), "user", 1),
                ])
            created = now_ms() - 20 * 86_400_000
            connection.executemany(
                "INSERT INTO users(id,first_name,last_name,class_name,login,password_hash,role,status,is_player,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(uid, first, last, cls, login, generate_password_hash(password), role, "active", player, created)
                 for uid, first, last, cls, login, password, role, player in seed_users],
            )
            if SEED_DEMO_DATA:
                scores = [("u1","u2",11,8),("u3","u5",13,11),("u4","u6",7,11),("u1","u3",11,9),
                          ("u2","u4",11,5),("u5","u7",11,6),("u6","u8",11,4),("u1","u4",12,10),
                          ("u2","u3",9,11),("u5","u6",8,11)]
                for index, (p1, p2, s1, s2) in enumerate(scores):
                    connection.execute("INSERT INTO matches(id,player_one,player_two,score_one,score_two,confirmed_by,created_at) VALUES(?,?,?,?,?,?,?)",
                                       (f"m{index+1}", p1, p2, s1, s2, p2, now_ms()-(10-index)*86_400_000))
        admin = connection.execute("SELECT id,password_hash FROM users WHERE role='admin' ORDER BY created_at LIMIT 1").fetchone()
        if admin and len(ADMIN_PASSWORD) >= 12 and not check_password_hash(admin["password_hash"], ADMIN_PASSWORD):
            connection.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(ADMIN_PASSWORD), admin["id"]))
        connection.execute("DELETE FROM oidc_sessions")
        connection.execute("PRAGMA optimize")


LOCAL_DEMO_USERS = [
    ("demo-01", "Алексей", "Смирнов", "7А", "student01"),
    ("demo-02", "Мария", "Иванова", "7Б", "student02"),
    ("demo-03", "Дмитрий", "Кузнецов", "8А", "student03"),
    ("demo-04", "Анна", "Попова", "8Б", "student04"),
    ("demo-05", "Михаил", "Соколов", "9А", "student05"),
    ("demo-06", "Елена", "Лебедева", "9Б", "student06"),
    ("demo-07", "Иван", "Козлов", "10А", "student07"),
    ("demo-08", "София", "Новикова", "10Б", "student08"),
    ("demo-09", "Артём", "Морозов", "11А", "student09"),
    ("demo-10", "Полина", "Волкова", "11Б", "student10"),
    ("demo-11", "Никита", "Павлов", "8В", "student11"),
    ("demo-12", "Дарья", "Фёдорова", "9В", "student12"),
]


def seed_local_demo_users():
    if not ALLOW_LOCAL_USER_LOGIN:
        raise RuntimeError("Локальные тестовые аккаунты отключены. Установите ALLOW_LOCAL_USER_LOGIN=true.")
    password_hash = generate_password_hash("123456")
    with db() as connection:
        for user_id, first_name, last_name, class_name, login_name in LOCAL_DEMO_USERS:
            existing = connection.execute("SELECT id FROM users WHERE login=?", (login_name,)).fetchone()
            if existing:
                connection.execute(
                    """UPDATE users SET first_name=?,last_name=?,class_name=?,password_hash=?,role='user',
                       status='active',is_player=1 WHERE id=?""",
                    (first_name, last_name, class_name, password_hash, existing["id"]),
                )
            else:
                connection.execute(
                    """INSERT INTO users(
                         id,first_name,last_name,class_name,login,password_hash,role,status,is_player,created_at
                       ) VALUES(?,?,?,?,?,?,'user','active',1,?)""",
                    (user_id, first_name, last_name, class_name, login_name, password_hash, now_ms()),
                )
        connection.execute("PRAGMA optimize")


def user_row(user_id):
    if not user_id:
        return None
    with db() as connection:
        return connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def current_user():
    return user_row(session.get("user_id"))


def require_user(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["status"] != "active":
            return jsonify(error="Требуется подтверждённый аккаунт."), 401
        return handler(user, *args, **kwargs)
    return wrapped


def require_admin(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or user["role"] != "admin":
            return jsonify(error="Недостаточно прав."), 403
        return handler(user, *args, **kwargs)
    return wrapped


def serialize_user(row, viewer):
    can_see_full = viewer and (viewer["role"] == "admin" or viewer["id"] == row["id"])
    data = {"id": row["id"], "firstName": row["first_name"], "lastName": row["last_name"],
            "status": row["status"], "isPlayer": bool(row["is_player"])}
    if can_see_full:
        data.update(className=row["class_name"], login=row["login"],
                    email=row["email"] if "email" in row.keys() else "",
                    role=row["role"], createdAt=row["created_at"])
    return data


def lichess_rating(profile, category):
    value = (profile.get("perfs") or {}).get(category) or {}
    rating = value.get("rating")
    return int(rating) if isinstance(rating, (int, float)) else None


def serialize_lichess_account(row):
    return {
        "id": row["lichess_id"], "username": row["username"],
        "rapid": row["rapid_rating"], "blitz": row["blitz_rating"],
        "classical": row["classical_rating"], "syncedAt": row["synced_at"],
        "url": f"https://lichess.org/@/{row['username']}",
    }


def update_lichess_account(connection, user_id, profile):
    lichess_id = str(profile.get("id") or "").strip().lower()
    username = str(profile.get("username") or "").strip()
    if not lichess_id or not username or len(lichess_id) > 50 or len(username) > 50:
        raise ValueError("Lichess не передал корректный профиль.")
    owner = connection.execute(
        "SELECT user_id FROM lichess_accounts WHERE lichess_id=?", (lichess_id,)
    ).fetchone()
    if owner and owner["user_id"] != user_id:
        raise ValueError("Этот профиль Lichess уже подключён к другому школьному аккаунту.")
    timestamp = now_ms()
    connection.execute(
        """INSERT INTO lichess_accounts(
             user_id,lichess_id,username,rapid_rating,blitz_rating,classical_rating,connected_at,synced_at
           ) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET
             lichess_id=excluded.lichess_id,username=excluded.username,
             rapid_rating=excluded.rapid_rating,blitz_rating=excluded.blitz_rating,
             classical_rating=excluded.classical_rating,synced_at=excluded.synced_at""",
        (user_id, lichess_id, username, lichess_rating(profile, "rapid"),
         lichess_rating(profile, "blitz"), lichess_rating(profile, "classical"), timestamp, timestamp),
    )


def lichess_request_error(response):
    if response.status_code == 429:
        return "Lichess временно ограничил запросы. Попробуйте снова через минуту."
    return "Lichess временно недоступен. Попробуйте позже."


def calculate_server_ratings(connection, sport="tennis"):
    ratings = {row["id"]: ELO_START for row in connection.execute("SELECT id FROM users WHERE is_player=1")}
    rows = connection.execute("SELECT player_one,player_two,score_one,score_two FROM matches WHERE active=1 AND sport=? ORDER BY created_at", (sport,)).fetchall()
    for match in rows:
        if match["player_one"] not in ratings or match["player_two"] not in ratings:
            continue
        rating_one, rating_two = ratings[match["player_one"]], ratings[match["player_two"]]
        expected_one = 1 / (1 + 10 ** ((rating_two - rating_one) / 400))
        actual_one = 0.5 if match["score_one"] == match["score_two"] else (1 if match["score_one"] > match["score_two"] else 0)
        delta = round(ELO_K * (actual_one - expected_one))
        ratings[match["player_one"]] += delta
        ratings[match["player_two"]] -= delta
    return ratings


def suspicious_activity(connection):
    """Build review hints for admins without automatically punishing players."""
    alerts = []
    cutoff_day = now_ms() - 24 * 60 * 60 * 1000
    cutoff_week = now_ms() - 7 * 24 * 60 * 60 * 1000
    frequent = connection.execute(
        """SELECT CASE WHEN requester<opponent THEN requester ELSE opponent END AS first_id,
                  CASE WHEN requester<opponent THEN opponent ELSE requester END AS second_id,
                  sport,COUNT(*) AS total
             FROM requests WHERE status='accepted' AND resolved_at>=?
            GROUP BY sport,first_id,second_id HAVING COUNT(*)>=4 ORDER BY total DESC""",
        (cutoff_day,),
    ).fetchall()
    for row in frequent:
        alerts.append({"kind": "frequent_pair", "severity": "high", "title": "Частые матчи одной пары",
                       "details": f"За 24 часа подтверждено матчей: {row['total']}",
                       "userIds": [row["first_id"], row["second_id"]], "count": row["total"], "sport": row["sport"]})

    repeated = connection.execute(
        """SELECT CASE WHEN requester<opponent THEN requester ELSE opponent END AS first_id,
                  CASE WHEN requester<opponent THEN opponent ELSE requester END AS second_id,
                  MIN(score_requester,score_opponent) AS low_score,
                  MAX(score_requester,score_opponent) AS high_score,sport,COUNT(*) AS total
             FROM requests WHERE status='accepted' AND resolved_at>=?
            GROUP BY sport,first_id,second_id,low_score,high_score HAVING COUNT(*)>=3 ORDER BY total DESC""",
        (cutoff_week,),
    ).fetchall()
    for row in repeated:
        alerts.append({"kind": "repeated_score", "severity": "medium", "title": "Повторяющийся счёт",
                       "details": f"Счёт {row['high_score']}:{row['low_score']} повторился {row['total']} раза за 7 дней",
                       "userIds": [row["first_id"], row["second_id"]], "count": row["total"], "sport": row["sport"]})

    fast = connection.execute(
        """SELECT requester,opponent,sport,COUNT(*) AS total FROM requests
            WHERE status='accepted' AND resolved_at>=? AND resolved_at-created_at<=30000
            GROUP BY sport,requester,opponent HAVING COUNT(*)>=3 ORDER BY total DESC""",
        (cutoff_week,),
    ).fetchall()
    for row in fast:
        alerts.append({"kind": "fast_confirmation", "severity": "medium", "title": "Слишком быстрые подтверждения",
                       "details": f"Подтверждений быстрее 30 секунд: {row['total']}",
                       "userIds": [row["requester"], row["opponent"]], "count": row["total"], "sport": row["sport"]})
    return alerts[:20]


def create_tournament_round(connection, tournament_id, stage, players, initial=False):
    ordered = sorted(players, key=lambda row: (row["seed"] or 10_000, row["joined_at"]))
    if initial:
        bracket_size = 1 << (len(ordered) - 1).bit_length()
        ordered.extend([None] * (bracket_size - len(ordered)))
    sequence = connection.execute(
        "SELECT COALESCE(MAX(sequence),0)+1 FROM tournament_matches WHERE tournament_id=?", (tournament_id,)
    ).fetchone()[0]
    round_number = connection.execute(
        "SELECT COALESCE(MAX(round_number),0)+1 FROM tournament_matches WHERE tournament_id=? AND stage=?",
        (tournament_id, stage),
    ).fetchone()[0]
    created = now_ms()
    position = 1
    while len(ordered) > 1:
        player_one = ordered.pop(0)
        player_two = ordered.pop(-1)
        if player_one is None:
            player_one, player_two = player_two, player_one
        if player_two is None:
            connection.execute(
                """INSERT INTO tournament_matches(
                     id,tournament_id,sequence,stage,round_number,position,player_one,player_two,
                     winner,loser,status,result_match_id,created_at,completed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (f"tm-{secrets.token_hex(8)}", tournament_id, sequence, stage, round_number, position,
                 player_one["user_id"], None, player_one["user_id"], None, "bye", None, created, created),
            )
            position += 1
            continue
        connection.execute(
            """INSERT INTO tournament_matches(
                 id,tournament_id,sequence,stage,round_number,position,player_one,player_two,
                 winner,loser,status,result_match_id,created_at,completed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"tm-{secrets.token_hex(8)}", tournament_id, sequence, stage, round_number, position,
             player_one["user_id"], player_two["user_id"], None, None, "pending", None, created, None),
        )
        position += 1
    if ordered:
        player = ordered[0]
        connection.execute(
            """INSERT INTO tournament_matches(
                 id,tournament_id,sequence,stage,round_number,position,player_one,player_two,
                 winner,loser,status,result_match_id,created_at,completed_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"tm-{secrets.token_hex(8)}", tournament_id, sequence, stage, round_number, position,
             player["user_id"], None, player["user_id"], None, "bye", None, created, created),
        )


def next_double_elimination_round(upper, lower, last_stage):
    """Return the next bracket and its players for a standard double-elimination tournament."""
    if upper and lower:
        if len(upper) == 1 and len(lower) == 1:
            return "final", [upper[0], lower[0]]
        if len(upper) > 1 and len(lower) > 1:
            # After an upper round its losers must enter the lower bracket. When both
            # brackets have the same size, the lower bracket is reduced once more
            # before the next upper round so that nobody receives an extra life.
            stage = "lower" if last_stage == "upper" or len(lower) >= len(upper) else "upper"
            return stage, lower if stage == "lower" else upper
        if len(upper) > 1:
            return "upper", upper
        return "lower", lower
    if len(upper) > 1:
        return "upper", upper
    if len(lower) > 1:
        return "lower", lower
    return None, upper or lower


def advance_tournament(connection, tournament_id):
    tournament = connection.execute("SELECT * FROM tournaments WHERE id=?", (tournament_id,)).fetchone()
    if not tournament or tournament["status"] != "active":
        return
    if connection.execute(
        "SELECT 1 FROM tournament_matches WHERE tournament_id=? AND status='pending' LIMIT 1", (tournament_id,)
    ).fetchone():
        return
    last = connection.execute(
        "SELECT stage,winner FROM tournament_matches WHERE tournament_id=? ORDER BY sequence DESC LIMIT 1", (tournament_id,)
    ).fetchone()
    last_stage = last["stage"] if last else None
    if last_stage in {"final", "reset"} and last["winner"]:
        connection.execute(
            "UPDATE tournaments SET status='completed',champion_id=?,completed_at=? WHERE id=?",
            (last["winner"], now_ms(), tournament_id),
        )
        return

    active_players = connection.execute(
        """SELECT * FROM tournament_participants
           WHERE tournament_id=? AND eliminated=0 ORDER BY seed,joined_at""", (tournament_id,)
    ).fetchall()
    if len(active_players) == 1:
        connection.execute(
            "UPDATE tournaments SET status='completed',champion_id=?,completed_at=? WHERE id=?",
            (active_players[0]["user_id"], now_ms(), tournament_id),
        )
        return
    if not active_players:
        connection.execute("UPDATE tournaments SET status='cancelled',completed_at=? WHERE id=?", (now_ms(), tournament_id))
        return

    upper = [player for player in active_players if player["losses"] == 0]
    lower = [player for player in active_players if player["losses"] == 1]
    stage, players = next_double_elimination_round(upper, lower, last_stage)
    if stage is None:
        winner = players[0]
        connection.execute(
            "UPDATE tournaments SET status='completed',champion_id=?,completed_at=? WHERE id=?",
            (winner["user_id"], now_ms(), tournament_id),
        )
        return
    create_tournament_round(connection, tournament_id, stage, players, initial=last_stage is None)


def tournament_podium(tournament, bracket):
    if tournament["status"] != "completed" or not tournament["champion_id"]:
        return None
    decisive_matches = [item for item in bracket if item["stage"] in {"final", "reset"} and item["status"] == "completed"]
    lower_matches = [item for item in bracket if item["stage"] == "lower" and item["status"] == "completed"]
    decisive = max(decisive_matches, key=lambda item: (item["sequence"], item["position"]), default=None)
    lower_final = max(lower_matches, key=lambda item: (item["sequence"], item["position"]), default=None)
    return {
        "first": tournament["champion_id"],
        "second": decisive["loser"] if decisive else None,
        "third": lower_final["loser"] if lower_final else None,
    }


def serialize_tournaments(connection):
    tournaments = []
    for row in connection.execute("SELECT * FROM tournaments ORDER BY start_at DESC,created_at DESC").fetchall():
        participants = connection.execute(
            "SELECT * FROM tournament_participants WHERE tournament_id=? ORDER BY COALESCE(seed,10000),joined_at", (row["id"],)
        ).fetchall()
        bracket = connection.execute(
            "SELECT * FROM tournament_matches WHERE tournament_id=? ORDER BY sequence,position", (row["id"],)
        ).fetchall()
        podium = tournament_podium(row, bracket)
        tournaments.append({
            "id": row["id"], "name": row["name"], "description": row["description"],
            "sport": row["sport"],
            "registrationDeadline": row["registration_deadline"], "startAt": row["start_at"],
            "maxPlayers": row["max_players"], "status": row["status"],
            "championId": row["champion_id"], "podium": podium, "createdAt": row["created_at"],
            "participants": [{"userId": item["user_id"], "seed": item["seed"], "losses": item["losses"],
                              "eliminated": bool(item["eliminated"]), "joinedAt": item["joined_at"]}
                             for item in participants],
            "matches": [{"id": item["id"], "sequence": item["sequence"], "stage": item["stage"],
                         "roundNumber": item["round_number"], "position": item["position"],
                         "playerOne": item["player_one"], "playerTwo": item["player_two"],
                         "winner": item["winner"], "loser": item["loser"], "status": item["status"],
                         "resultMatchId": item["result_match_id"], "createdAt": item["created_at"],
                         "completedAt": item["completed_at"]}
                        for item in bracket],
        })
    return tournaments


@app.get("/")
@app.get("/confirm/<token>")
def index(token=None):
    if token and len(token) <= 128 and all(character.isalnum() or character in "-_" for character in token):
        session["confirmation_token"] = token
    return send_from_directory(ROOT, "index.html", max_age=0)


@app.get("/<path:filename>")
def assets(filename):
    if filename in {"app.js", "styles.css"}:
        return send_from_directory(ROOT, filename, max_age=0)
    return "Not found", 404


@app.before_request
def validate_unsafe_request():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    origin = request.headers.get("Origin")
    allowed_origins = {APP_URL, request.host_url.rstrip("/")}
    if origin and origin.rstrip("/") not in allowed_origins:
        return jsonify(error="Запрос с другого сайта отклонён."), 403
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        return jsonify(error="Защитный токен устарел. Обновите страницу."), 403
    return None


@app.after_request
def security_headers(response):
    if request.path in {"/", "/index.html", "/app.js", "/styles.css"} or request.path.startswith(("/confirm/", "/api/", "/auth/")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.errorhandler(413)
def request_too_large(error):
    return jsonify(error="Запрос слишком большой."), 413


@app.get("/static/<path:filename>")
def static_assets(filename):
    return send_from_directory(ROOT / "static", filename)


@app.get("/api/state")
def state():
    viewer = current_user()
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    with db() as connection:
        if viewer and viewer["role"] == "admin":
            user_rows = connection.execute("SELECT * FROM users ORDER BY created_at").fetchall()
            match_rows = connection.execute("SELECT * FROM matches ORDER BY created_at").fetchall()
        else:
            viewer_id = viewer["id"] if viewer else ""
            user_rows = connection.execute("""
                SELECT * FROM users WHERE
                  (status='active' AND is_player=1) OR id=? OR id IN (
                    SELECT player_one FROM matches WHERE active=1
                    UNION SELECT player_two FROM matches WHERE active=1
                    UNION SELECT user_id FROM tournament_participants
                  )
                ORDER BY created_at
            """, (viewer_id,)).fetchall()
            match_rows = connection.execute("SELECT * FROM matches WHERE active=1 ORDER BY created_at").fetchall()
        users = [serialize_user(row, viewer) for row in user_rows]
        lichess_accounts = {
            row["user_id"]: serialize_lichess_account(row)
            for row in connection.execute("SELECT * FROM lichess_accounts").fetchall()
        }
        for serialized_user in users:
            if serialized_user["id"] in lichess_accounts:
                serialized_user["lichess"] = lichess_accounts[serialized_user["id"]]
        matches = [dict(row) for row in match_rows]
        requests_rows = []
        challenge_rows = []
        security_alerts = []
        if viewer:
            if viewer["role"] == "admin":
                rows = connection.execute("SELECT * FROM requests ORDER BY created_at").fetchall()
                challenge_rows = connection.execute("SELECT * FROM match_challenges ORDER BY created_at DESC").fetchall()
                security_alerts = suspicious_activity(connection)
            else:
                rows = connection.execute("SELECT * FROM requests WHERE requester=? OR opponent=? ORDER BY created_at", (viewer["id"], viewer["id"])).fetchall()
                challenge_rows = connection.execute(
                    "SELECT * FROM match_challenges WHERE challenger=? OR opponent=? ORDER BY created_at DESC",
                    (viewer["id"], viewer["id"]),
                ).fetchall()
            requests_rows = [dict(row) for row in rows]
        tournaments = serialize_tournaments(connection)
    matches_json = []
    for m in matches:
        item = {"id":m["id"],"playerOne":m["player_one"],"playerTwo":m["player_two"],
                "scoreOne":m["score_one"],"scoreTwo":m["score_two"],
                "sport":m["sport"],
                "createdAt":m["created_at"],"active":bool(m["active"]),
                "tournamentMatchId":m.get("tournament_match_id") if isinstance(m, dict) else m["tournament_match_id"]}
        if viewer and viewer["role"] == "admin":
            item["confirmedBy"] = m["confirmed_by"]
        if m["dispute_user"] and viewer and (viewer["role"] == "admin" or viewer["id"] in {m["player_one"], m["player_two"]}):
            item["dispute"] = {"userId":m["dispute_user"],"reason":m["dispute_reason"]}
        matches_json.append(item)
    requests_json = [{"id":r["id"],"token":r["token"] if viewer and viewer["id"] == r["requester"] else "",
                      "requester":r["requester"],"opponent":r["opponent"],
                      "scoreRequester":r["score_requester"],"scoreOpponent":r["score_opponent"],
                      "status":r["status"],"notified":bool(r["notified"]),"createdAt":r["created_at"],
                      "tournamentMatchId":r.get("tournament_match_id"),"sport":r["sport"]} for r in requests_rows]
    challenges_json = [{"id":r["id"], "challenger":r["challenger"], "opponent":r["opponent"],
                        "scheduledAt":r["scheduled_at"], "message":r["message"], "status":r["status"],
                        "createdAt":r["created_at"], "resolvedAt":r["resolved_at"], "sport":r["sport"]} for r in challenge_rows]
    return jsonify(users=users, matches=matches_json, requests=requests_json, challenges=challenges_json,
                   securityAlerts=security_alerts, tournaments=tournaments,
                   currentUserId=viewer["id"] if viewer else None,
                   oidcEnabled=OIDC_ENABLED, oidcSession=bool(session.get("oidc_login")),
                   localLoginEnabled=ALLOW_LOCAL_USER_LOGIN,
                   lichessEnabled=bool(LICHESS_CLIENT_ID),
                   externalNotificationsEnabled=EXTERNAL_NOTIFICATIONS_ENABLED,
                   csrfToken=csrf_token, authMessage=session.pop("auth_message", None))


def normalized_oidc_claims(userinfo):
    roles = userinfo.get("roles") or userinfo.get("role") or []
    if isinstance(roles, str):
        roles = [item.strip() for item in roles.replace(",", " ").split()]
    roles = {str(item).lower() for item in roles}
    if "admin" in roles:
        local_role = "admin"
    elif "teacher" in roles:
        local_role = "teacher"
    elif "student" in roles:
        local_role = "user"
    else:
        raise ValueError("Вход разрешён только ученикам, учителям и администраторам ЛК Силаэдра.")

    first_name = str(userinfo.get("given_name") or "").strip()
    last_name = str(userinfo.get("family_name") or "").strip()
    if not first_name:
        parts = str(userinfo.get("name") or "Пользователь Силаэдра").strip().split(maxsplit=1)
        first_name, last_name = parts[0], parts[1] if len(parts) > 1 else ""
    return {
        "issuer": OIDC_ISSUER,
        "subject": str(userinfo.get("sub") or "").strip()[:255],
        "first_name": first_name[:80],
        "last_name": (last_name or "—")[:80],
        "email": str(userinfo.get("email") or "").strip().lower()[:254],
        "class_name": str(userinfo.get("class_name") or userinfo.get("class") or userinfo.get("grade") or "").strip()[:20],
        "role": local_role,
    }


def update_user_from_oidc(connection, user_id, claims):
    values = (claims["first_name"], claims["last_name"], claims["class_name"], claims["role"], claims["email"] or None, user_id)
    try:
        connection.execute("UPDATE users SET first_name=?,last_name=?,class_name=?,role=?,email=?,status=CASE WHEN status='pending' THEN 'active' ELSE status END WHERE id=?", values)
    except sqlite3.IntegrityError:
        connection.execute("UPDATE users SET first_name=?,last_name=?,class_name=?,role=?,status=CASE WHEN status='pending' THEN 'active' ELSE status END WHERE id=?", values[:4] + (user_id,))


class ExternalNotificationError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def notification_recipient_sub(connection, user_id):
    row = connection.execute(
        "SELECT subject FROM oidc_identities WHERE user_id=? AND issuer=? ORDER BY created_at DESC LIMIT 1",
        (user_id, OIDC_ISSUER),
    ).fetchone()
    return row["subject"] if row else None


def safe_notification_url(path):
    url = f"{EXTERNAL_URL}{path}"
    parsed = urlsplit(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
        return url
    return None


def send_external_notification(recipient_sub, event_key, title, message, url=None):
    if not EXTERNAL_NOTIFICATIONS_ENABLED:
        raise ExternalNotificationError("API уведомлений ЛК не настроен.")
    payload = {"recipient_sub": recipient_sub, "title": title.strip(), "message": message.strip()}
    if url:
        payload["url"] = url
    headers = {"Idempotency-Key": event_key}
    last_error = None
    for attempt in range(3):
        try:
            response = http_requests.post(
                f"{CRM_BASE_URL}/api/external/notifications",
                auth=(CRM_NOTIFICATION_CLIENT_ID, CRM_NOTIFICATION_CLIENT_SECRET),
                headers=headers,
                json=payload,
                timeout=10,
            )
        except http_requests.RequestException as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.2 * (2 ** attempt) + secrets.randbelow(100) / 1000)
                continue
            raise ExternalNotificationError("ЛК Силаэдра временно недоступен.") from None
        if response.status_code in {200, 202}:
            try:
                return response.json()
            except ValueError:
                return {"status": "queued", "idempotent_replay": response.status_code == 200}
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 2:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = min(max(float(retry_after), 0), 2)
                except ValueError:
                    delay = 0.2 * (2 ** attempt) + secrets.randbelow(100) / 1000
                time.sleep(delay)
                continue
        try:
            error_message = response.json().get("message")
        except ValueError:
            error_message = None
        raise ExternalNotificationError(error_message or "ЛК Силаэдра отклонил уведомление.", response.status_code)
    raise ExternalNotificationError(str(last_error) if last_error else "Не удалось поставить уведомление в очередь.")


@app.get("/auth/lichess/login")
@require_user
def lichess_login(user):
    if not LICHESS_CLIENT_ID:
        session["auth_message"] = "Подключение Lichess не настроено."
        return redirect("/#home")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    oauth_state = secrets.token_urlsafe(32)
    session["lichess_oauth"] = {
        "user_id": user["id"], "verifier": verifier, "state": oauth_state,
        "expires_at": now_ms() + 10 * 60 * 1000,
    }
    parameters = urlencode({
        "response_type": "code", "client_id": LICHESS_CLIENT_ID,
        "redirect_uri": LICHESS_REDIRECT_URI, "scope": "",
        "code_challenge_method": "S256", "code_challenge": challenge,
        "state": oauth_state,
    })
    return redirect(f"{LICHESS_BASE_URL}/oauth?{parameters}")


@app.get("/auth/lichess/callback")
def lichess_callback():
    flow = session.pop("lichess_oauth", None)
    user = current_user()
    supplied_state = request.args.get("state", "")
    code = request.args.get("code", "")
    if request.args.get("error"):
        session["auth_message"] = "Подключение Lichess отменено."
        return redirect("/#home")
    if (not flow or not user or user["id"] != flow.get("user_id") or
            flow.get("expires_at", 0) < now_ms() or not supplied_state or
            not secrets.compare_digest(supplied_state, flow.get("state", ""))):
        session["auth_message"] = "Сессия подключения Lichess устарела. Начните привязку заново."
        return redirect("/#home")
    if not code or len(code) > 200 or not all(character.isalnum() or character == "_" for character in code):
        session["auth_message"] = "Lichess вернул некорректный код авторизации."
        return redirect("/#home")

    access_token = ""
    try:
        token_response = http_requests.post(
            f"{LICHESS_BASE_URL}/api/token",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={
                "grant_type": "authorization_code", "redirect_uri": LICHESS_REDIRECT_URI,
                "client_id": LICHESS_CLIENT_ID, "code": code,
                "code_verifier": flow["verifier"],
            }, timeout=10,
        )
        if token_response.status_code != 200:
            raise ValueError(lichess_request_error(token_response))
        access_token = str(token_response.json().get("access_token") or "")
        if not access_token or len(access_token) > 300:
            raise ValueError("Lichess не выдал токен для проверки профиля.")
        profile_response = http_requests.get(
            f"{LICHESS_BASE_URL}/api/account",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=10,
        )
        if profile_response.status_code != 200:
            raise ValueError(lichess_request_error(profile_response))
        profile = profile_response.json()
        with db() as connection:
            update_lichess_account(connection, user["id"], profile)
        session["auth_message"] = f"Профиль Lichess @{profile['username']} успешно подключён."
    except (http_requests.RequestException, ValueError, KeyError) as error:
        app.logger.warning("Lichess account link failed: %s", type(error).__name__)
        session["auth_message"] = str(error) if isinstance(error, ValueError) else "Не удалось подключиться к Lichess. Попробуйте позже."
    finally:
        if access_token:
            try:
                http_requests.delete(
                    f"{LICHESS_BASE_URL}/api/token",
                    headers={"Authorization": f"Bearer {access_token}"}, timeout=5,
                )
            except http_requests.RequestException:
                app.logger.warning("Lichess token revocation failed")
    return redirect("/#home")


@app.post("/api/lichess/sync")
@require_user
def sync_lichess_account(user):
    with db() as connection:
        account = connection.execute(
            "SELECT * FROM lichess_accounts WHERE user_id=?", (user["id"],)
        ).fetchone()
    if not account:
        return jsonify(error="Сначала подключите профиль Lichess."), 404
    try:
        response = http_requests.get(
            f"{LICHESS_BASE_URL}/api/user/{quote(account['username'], safe='')}",
            headers={"Accept": "application/json"}, timeout=10,
        )
        if response.status_code != 200:
            return jsonify(error=lichess_request_error(response)), 503
        with db() as connection:
            update_lichess_account(connection, user["id"], response.json())
    except http_requests.RequestException:
        return jsonify(error="Не удалось связаться с Lichess."), 503
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(ok=True)


@app.post("/api/lichess/disconnect")
@require_user
def disconnect_lichess_account(user):
    with db() as connection:
        connection.execute("DELETE FROM lichess_accounts WHERE user_id=?", (user["id"],))
    return jsonify(ok=True)


@app.get("/auth/silaeder/login")
def silaeder_login():
    if not OIDC_ENABLED:
        session["auth_message"] = "Школьный вход ещё не настроен администратором."
        return redirect("/#home")
    external_host = urlsplit(EXTERNAL_URL).hostname
    request_host = urlsplit(f"//{request.host}").hostname
    if request_host != external_host:
        return redirect(f"{EXTERNAL_URL}/auth/silaeder/login")
    return oauth.silaeder.authorize_redirect(OIDC_REDIRECT_URI)


@app.get("/auth/silaeder/callback")
def silaeder_callback():
    if not OIDC_ENABLED:
        return redirect("/#home")
    try:
        token = oauth.silaeder.authorize_access_token()
        userinfo = token.get("userinfo") or oauth.silaeder.userinfo(token=token)
        claims = normalized_oidc_claims(userinfo)
        if not claims["subject"]:
            raise ValueError("ЛК не передал идентификатор пользователя.")
        with db() as connection:
            identity = connection.execute("SELECT user_id FROM oidc_identities WHERE issuer=? AND subject=?", (claims["issuer"], claims["subject"])).fetchone()
            if identity:
                user_id = identity["user_id"]
                update_user_from_oidc(connection, user_id, claims)
            else:
                existing = connection.execute("SELECT id FROM users WHERE email IS NOT NULL AND lower(email)=lower(?)", (claims["email"],)).fetchone() if claims["email"] else None
                if existing:
                    if not EXTERNAL_NOTIFICATIONS_ENABLED:
                        session["auth_message"] = "Аккаунт с таким email уже существует. Для безопасной привязки администратору нужно настроить API уведомлений ЛК."
                        return redirect("/#home")
                    raw_token = secrets.token_urlsafe(32)
                    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                    connection.execute("DELETE FROM account_link_tokens WHERE expires_at<?", (now_ms(),))
                    connection.execute("INSERT OR REPLACE INTO account_link_tokens VALUES(?,?,?,?,?,?)", (token_hash, existing["id"], claims["issuer"], claims["subject"], json.dumps(claims, ensure_ascii=False), now_ms() + 30 * 60 * 1000))
                    send_external_notification(
                        claims["subject"], f"tennis.account-link.{token_hash[:32]}",
                        "Подтвердите привязку аккаунта",
                        "Подтвердите привязку существующего аккаунта к ЛК Силаэдра. Ссылка действует 30 минут.",
                        safe_notification_url(f"/auth/silaeder/link/{raw_token}"),
                    )
                    session["auth_message"] = "Уведомление для безопасной привязки отправлено через ЛК Силаэдра. Оно действует 30 минут."
                    return redirect("/#home")
                user_id = f"u-{secrets.token_hex(8)}"
                login = f"oidc-{hashlib.sha256((claims['issuer'] + claims['subject']).encode()).hexdigest()[:16]}"
                connection.execute("INSERT INTO users(id,first_name,last_name,class_name,login,password_hash,role,status,is_player,created_at,email) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (user_id, claims["first_name"], claims["last_name"], claims["class_name"], login, generate_password_hash(secrets.token_urlsafe(32)), claims["role"], "active", 1, now_ms(), claims["email"] or None))
                connection.execute("INSERT INTO oidc_identities VALUES(?,?,?,?)", (claims["issuer"], claims["subject"], user_id, now_ms()))
        return_path = confirmation_return_path()
        session.clear()
        session.permanent = True
        session["user_id"] = user_id
        session["oidc_login"] = True
        return redirect(return_path)
    except Exception as error:
        app.logger.exception("OIDC login failed")
        session["auth_message"] = "Не удалось выполнить школьный вход. Попробуйте ещё раз или обратитесь к администратору."
        return redirect(confirmation_return_path())


@app.get("/auth/silaeder/link/<raw_token>")
def silaeder_link(raw_token):
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    with db() as connection:
        link = connection.execute("SELECT * FROM account_link_tokens WHERE token_hash=? AND expires_at>=?", (token_hash, now_ms())).fetchone()
        if not link:
            session["auth_message"] = "Ссылка привязки недействительна или уже истекла."
            return redirect("/#home")
        claims = json.loads(link["claims_json"])
        connection.execute("INSERT OR IGNORE INTO oidc_identities VALUES(?,?,?,?)", (link["issuer"], link["subject"], link["user_id"], now_ms()))
        update_user_from_oidc(connection, link["user_id"], claims)
        connection.execute("DELETE FROM account_link_tokens WHERE token_hash=?", (token_hash,))
    return_path = confirmation_return_path()
    session.clear()
    session.permanent = True
    session["user_id"] = link["user_id"]
    session["oidc_login"] = True
    session["auth_message"] = "Аккаунт ЛК Силаэдра успешно привязан."
    return redirect(return_path)


@app.post("/auth/silaeder/logout")
def silaeder_logout():
    session.clear()
    if OIDC_ENABLED:
        try:
            metadata = oauth.silaeder.load_server_metadata()
            endpoint = metadata.get("end_session_endpoint")
            if endpoint:
                params = {"post_logout_redirect_uri": OIDC_LOGOUT_REDIRECT_URI}
                return jsonify(redirect=f"{endpoint}?{urlencode(params)}")
        except Exception:
            pass
    return jsonify(redirect="/#home")


@app.get("/auth/silaeder/logout/callback")
def silaeder_logout_callback():
    session.clear()
    return redirect("/#home")


@app.post("/api/login")
def login():
    data=request.get_json(silent=True) or {}
    try:
        login_name = clean_text(data.get("login"), "Логин", 64)
        password = clean_text(data.get("password"), "Пароль", 256)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    key = login_key(login_name)
    if login_is_limited(key):
        response = jsonify(error="Слишком много попыток входа. Повторите через 15 минут.")
        response.status_code = 429
        response.headers["Retry-After"] = str(LOGIN_WINDOW_SECONDS)
        return response
    with db() as connection:
        user=connection.execute("SELECT * FROM users WHERE lower(login)=lower(?)",(login_name,)).fetchone()
    password_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    if not check_password_hash(password_hash, password):
        record_login_failure(key)
        return jsonify(error="Неверный логин или пароль."),400
    clear_login_failures(key)
    if user["role"] == "admin" and check_password_hash(user["password_hash"], "admin"):
        return jsonify(error="Стандартный пароль администратора отключён. Задайте ADMIN_PASSWORD."), 403
    if user["status"] in {"inactive","rejected"}:
        return jsonify(error="Аккаунт недоступен. Обратитесь к администратору."),403
    if OIDC_ENABLED and user["role"] != "admin" and not ALLOW_LOCAL_USER_LOGIN:
        return jsonify(error="Для учеников и учителей используйте вход через ЛК Силаэдра."),403
    if ALLOW_LOCAL_USER_LOGIN and user["role"] != "admin" and data.get("email"):
        try:
            email = clean_email(data.get("email"))
            with db() as connection:
                connection.execute("UPDATE users SET email=? WHERE id=?",(email,user["id"]))
        except ValueError as error:
            return jsonify(error=str(error)),400
        except sqlite3.IntegrityError:
            return jsonify(error="Эта почта уже используется другим аккаунтом."),409
    session.clear()
    session.permanent = True
    session["user_id"]=user["id"]
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear(); return jsonify(ok=True)


@app.post("/api/register")
def register():
    if OIDC_ENABLED:
        return jsonify(error="Регистрация выполняется через ЛК Силаэдра."),410
    data=request.get_json(silent=True) or {}
    try:
        first_name = clean_text(data.get("firstName"), "Имя", 80)
        last_name = clean_text(data.get("lastName"), "Фамилия", 80)
        class_name = clean_text(data.get("className"), "Класс", 20)
        login_name = clean_text(data.get("login"), "Логин", 64)
        password = clean_text(data.get("password"), "Пароль", 256)
        if len(password) < 12:
            raise ValueError("Пароль должен содержать не менее 12 символов.")
    except ValueError as error:
        return jsonify(error=str(error)), 400
    uid=f"u-{secrets.token_hex(8)}"
    try:
        with db() as connection:
            connection.execute("INSERT INTO users(id,first_name,last_name,class_name,login,password_hash,role,status,is_player,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (uid,first_name,last_name,class_name.upper(),login_name,generate_password_hash(password),"user","pending",1,now_ms()))
    except sqlite3.IntegrityError:
        return jsonify(error="Этот логин уже занят."),409
    session["user_id"]=uid
    return jsonify(ok=True)


@app.get("/api/users/search")
@require_user
def search_users(viewer):
    try:
        raw_query = clean_text(request.args.get("q", ""), "Поиск", 80, required=False)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    query=f"%{raw_query}%"
    with db() as connection:
        rows=connection.execute("SELECT * FROM users WHERE status='active' AND is_player=1 AND id!=? AND (first_name LIKE ? OR last_name LIKE ? OR class_name LIKE ?) LIMIT 8",(viewer["id"],query,query,query)).fetchall()
    return jsonify(users=[serialize_user(row,None) for row in rows])


@app.post("/api/admin/tournaments")
@require_admin
def create_tournament(admin):
    data = request.get_json(silent=True) or {}
    sport = valid_sport(data.get("sport") or "tennis")
    if not sport:
        return jsonify(error="Неизвестный вид спорта."), 400
    try:
        name = clean_text(data.get("name"), "Название", 100)
        description = clean_text(data.get("description"), "Описание", 600, required=False)
        registration_deadline = int(data.get("registrationDeadline"))
        start_at = int(data.get("startAt"))
        max_players = int(data.get("maxPlayers"))
    except (TypeError, ValueError) as error:
        message = str(error) if isinstance(error, ValueError) and str(error).startswith("Поле") else "Проверьте даты и количество участников."
        return jsonify(error=message), 400
    if max_players < 2 or max_players > 32:
        return jsonify(error="Количество участников должно быть от 2 до 32."), 400
    if registration_deadline <= now_ms() or start_at <= registration_deadline:
        return jsonify(error="Регистрация должна завершаться в будущем и раньше начала турнира."), 400
    tournament_id = f"t-{secrets.token_hex(8)}"
    with db() as connection:
        connection.execute(
            """INSERT INTO tournaments(
                 id,name,description,registration_deadline,start_at,max_players,status,created_by,created_at,sport
               ) VALUES(?,?,?,?,?,?, 'registration',?,?,?)""",
            (tournament_id, name, description, registration_deadline, start_at, max_players, admin["id"], now_ms(), sport),
        )
    return jsonify(id=tournament_id)


@app.post("/api/tournaments/<tournament_id>/<action>")
@require_user
def tournament_registration(user, tournament_id, action):
    if action not in {"join", "leave"}:
        return jsonify(error="Неизвестное действие."), 400
    if not user["is_player"]:
        return jsonify(error="Для участия нужен профиль игрока."), 403
    with db() as connection:
        tournament = connection.execute("SELECT * FROM tournaments WHERE id=?", (tournament_id,)).fetchone()
        if not tournament:
            return jsonify(error="Турнир не найден."), 404
        if tournament["status"] != "registration":
            return jsonify(error="Регистрация на этот турнир закрыта."), 409
        if action == "join":
            if now_ms() >= tournament["registration_deadline"]:
                return jsonify(error="Срок регистрации уже закончился."), 409
            count = connection.execute(
                "SELECT COUNT(*) FROM tournament_participants WHERE tournament_id=?", (tournament_id,)
            ).fetchone()[0]
            if count >= tournament["max_players"]:
                return jsonify(error="Все места на турнир уже заняты."), 409
            try:
                connection.execute(
                    "INSERT INTO tournament_participants(tournament_id,user_id,joined_at) VALUES(?,?,?)",
                    (tournament_id, user["id"], now_ms()),
                )
            except sqlite3.IntegrityError:
                return jsonify(error="Вы уже зарегистрированы на этот турнир."), 409
        else:
            result = connection.execute(
                "DELETE FROM tournament_participants WHERE tournament_id=? AND user_id=?", (tournament_id, user["id"])
            )
            if not result.rowcount:
                return jsonify(error="Вы не зарегистрированы на этот турнир."), 404
    return jsonify(ok=True)


@app.post("/api/admin/tournaments/<tournament_id>/<action>")
@require_admin
def admin_tournament(admin, tournament_id, action):
    if action not in {"start", "cancel"}:
        return jsonify(error="Неизвестное действие."), 400
    with db() as connection:
        tournament = connection.execute("SELECT * FROM tournaments WHERE id=?", (tournament_id,)).fetchone()
        if not tournament:
            return jsonify(error="Турнир не найден."), 404
        if tournament["status"] != "registration":
            return jsonify(error="Это действие уже недоступно для турнира."), 409
        if action == "cancel":
            connection.execute(
                "UPDATE tournaments SET status='cancelled',completed_at=? WHERE id=?", (now_ms(), tournament_id)
            )
            return jsonify(ok=True)

        participants = connection.execute(
            "SELECT * FROM tournament_participants WHERE tournament_id=? ORDER BY joined_at", (tournament_id,)
        ).fetchall()
        if len(participants) < 2:
            return jsonify(error="Для запуска нужны хотя бы два участника."), 409
        ratings = calculate_server_ratings(connection, tournament["sport"])
        ordered = sorted(participants, key=lambda row: (-ratings.get(row["user_id"], ELO_START), row["joined_at"]))
        for seed, participant in enumerate(ordered, 1):
            connection.execute(
                "UPDATE tournament_participants SET seed=?,losses=0,eliminated=0 WHERE tournament_id=? AND user_id=?",
                (seed, tournament_id, participant["user_id"]),
            )
        connection.execute("UPDATE tournaments SET status='active' WHERE id=?", (tournament_id,))
        advance_tournament(connection, tournament_id)
    return jsonify(ok=True)


@app.post("/api/requests")
@require_user
def create_request(user):
    data=request.get_json(silent=True) or {}; opponent=data.get("opponent")
    sport=valid_sport(data.get("sport") or "tennis")
    if not sport: return jsonify(error="Неизвестный вид спорта."),400
    tournament_match_id=data.get("tournamentMatchId") or None
    if not isinstance(opponent, str) or len(opponent) > 80:
        return jsonify(error="Некорректный соперник."), 400
    if tournament_match_id is not None and (not isinstance(tournament_match_id, str) or len(tournament_match_id) > 80):
        return jsonify(error="Некорректный турнирный матч."), 400
    try: s1,s2=int(data.get("scoreRequester")),int(data.get("scoreOpponent"))
    except (TypeError,ValueError): return jsonify(error="Некорректный счёт."),400
    if opponent==user["id"]: return jsonify(error="Проверьте соперника."),400
    if not user["is_player"]:
        return jsonify(error="Для подачи результата нужен профиль игрока."),403
    with db() as connection:
        if tournament_match_id:
            tournament_match=connection.execute(
                """SELECT tm.*,t.status AS tournament_status FROM tournament_matches tm
                   JOIN tournaments t ON t.id=tm.tournament_id WHERE tm.id=?""", (tournament_match_id,)
            ).fetchone()
            if not tournament_match or tournament_match["status"]!="pending" or tournament_match["tournament_status"]!="active":
                return jsonify(error="Турнирный матч не найден или уже завершён."),404
            tournament_sport=connection.execute("SELECT sport FROM tournaments WHERE id=?",(tournament_match["tournament_id"],)).fetchone()["sport"]
            if sport != tournament_sport:
                return jsonify(error="Вид спорта не совпадает с турниром."),400
            if user["id"] not in {tournament_match["player_one"],tournament_match["player_two"]}:
                return jsonify(error="Подать результат могут только участники этого матча."),403
            expected_opponent = tournament_match["player_two"] if user["id"] == tournament_match["player_one"] else tournament_match["player_one"]
            if opponent != expected_opponent:
                return jsonify(error="Для турнирного матча выбран неверный соперник."),400
            if connection.execute(
                "SELECT 1 FROM requests WHERE tournament_match_id=? AND status='pending'", (tournament_match_id,)
            ).fetchone():
                return jsonify(error="Для этого турнирного матча уже подан результат."),409
        if not valid_match_score(sport,s1,s2,bool(tournament_match_id)):
            message="В турнирных шахматах должна быть определена победа." if sport=="chess" and tournament_match_id else "Проверьте результат матча."
            return jsonify(error=message),400
        target=connection.execute("SELECT * FROM users WHERE id=? AND status='active' AND is_player=1",(opponent,)).fetchone()
        if not target: return jsonify(error="Игрок не найден."),404
        rid=f"r-{secrets.token_hex(8)}"; token=secrets.token_urlsafe(24)
        connection.execute(
            """INSERT INTO requests(
                 id,token,requester,opponent,score_requester,score_opponent,created_at,tournament_match_id,sport
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (rid,token,user["id"],opponent,s1,s2,now_ms(),tournament_match_id,sport),
        )
    return jsonify(id=rid,token=token)


@app.post("/api/requests/<rid>/notify")
@require_user
def notify_request(user,rid):
    with db() as connection:
        row=connection.execute(
            """SELECT r.*,opponent.first_name AS opponent_first_name,
                      opponent.last_name AS opponent_last_name
                 FROM requests r JOIN users opponent ON opponent.id=r.opponent
                WHERE r.id=? AND r.requester=? AND r.status='pending'""",
            (rid,user["id"]),
        ).fetchone()
        if not row:
            return jsonify(error="Заявка не найдена."),404
        connection.execute("UPDATE requests SET notified=1 WHERE id=?",(rid,))
        recipient_sub = notification_recipient_sub(connection, row["opponent"])
    if not recipient_sub:
        return jsonify(ok=True, notificationStatus="no_oidc", queued=False)
    if row["sport"] == "chess":
        notification_score = "½:½" if row["score_requester"] == row["score_opponent"] else (
            "1:0" if row["score_requester"] > row["score_opponent"] else "0:1"
        )
    else:
        notification_score = f"{row['score_requester']}:{row['score_opponent']}"
    try:
        result = send_external_notification(
            recipient_sub,
            f"school-sport.{row['sport']}.match-request.{rid}",
            "Подтвердите результат матча",
            f"Ваш соперник указал результат матча: {notification_score}. "
            "Подтвердите или отклоните его на сайте.",
            safe_notification_url(f"/confirm/{row['token']}"),
        )
        return jsonify(ok=True, notificationStatus="queued", queued=True,
                       idempotentReplay=bool(result.get("idempotent_replay")))
    except ExternalNotificationError as error:
        app.logger.warning("External match notification failed with status %s", error.status_code)
        return jsonify(ok=True, notificationStatus="not_configured" if not EXTERNAL_NOTIFICATIONS_ENABLED else "failed",
                       queued=False)


@app.get("/api/requests/<rid>/qr")
@require_user
def request_qr(user, rid):
    with db() as connection:
        row = connection.execute("SELECT token FROM requests WHERE id=? AND requester=? AND status='pending'", (rid, user["id"])).fetchone()
    if not row:
        return jsonify(error="Заявка не найдена."), 404
    confirmation_url = f"{QR_BASE_URL}/confirm/{row['token']}"
    image = qrcode.make(confirmation_url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    output = io.BytesIO()
    image.save(output)
    output.seek(0)
    response = send_file(output, mimetype="image/svg+xml", max_age=0)
    response.headers["Content-Location"] = confirmation_url
    return response


def resolve_match_request(connection, request_id, opponent_id, action):
    if action not in {"accept", "reject"}:
        raise ValueError("Неизвестное действие.")
    row = connection.execute(
        "SELECT * FROM requests WHERE id=? AND opponent=? AND status='pending'", (request_id, opponent_id)
    ).fetchone()
    if not row:
        raise LookupError("Заявка не найдена или уже обработана.")
    tournament_match = None
    if action == "accept":
        match_id = f"m-{secrets.token_hex(8)}"
        if row["tournament_match_id"]:
            tournament_match = connection.execute(
                """SELECT tm.*,t.status AS tournament_status FROM tournament_matches tm
                   JOIN tournaments t ON t.id=tm.tournament_id WHERE tm.id=?""", (row["tournament_match_id"],)
            ).fetchone()
            if not tournament_match or tournament_match["status"] != "pending" or tournament_match["tournament_status"] != "active":
                raise ValueError("Турнирный матч уже закрыт.")
        connection.execute(
            """INSERT INTO matches(
                 id,player_one,player_two,score_one,score_two,confirmed_by,created_at,active,
                 dispute_user,dispute_reason,tournament_match_id,sport
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (match_id,row["requester"],row["opponent"],row["score_requester"],row["score_opponent"],
             opponent_id,now_ms(),1,None,None,row["tournament_match_id"],row["sport"]),
        )
        if tournament_match:
            winner = row["requester"] if row["score_requester"] > row["score_opponent"] else row["opponent"]
            loser = row["opponent"] if winner == row["requester"] else row["requester"]
            connection.execute(
                """UPDATE tournament_matches SET winner=?,loser=?,status='completed',result_match_id=?,completed_at=?
                   WHERE id=? AND status='pending'""",
                (winner,loser,match_id,now_ms(),row["tournament_match_id"]),
            )
            connection.execute(
                """UPDATE tournament_participants
                   SET losses=losses+1,eliminated=CASE WHEN losses+1>=2 THEN 1 ELSE 0 END
                   WHERE tournament_id=? AND user_id=?""",
                (tournament_match["tournament_id"],loser),
            )
            advance_tournament(connection,tournament_match["tournament_id"])
    connection.execute(
        "UPDATE requests SET status=?,resolved_at=? WHERE id=?",
        ("accepted" if action == "accept" else "rejected", now_ms(), request_id),
    )
    return row


@app.post("/api/requests/<rid>/<action>")
@require_user
def resolve_request(user,rid,action):
    with db() as connection:
        try:
            resolve_match_request(connection, rid, user["id"], action)
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 409 if "закрыт" in str(error) else 400
    return jsonify(ok=True)


@app.post("/api/challenges")
@require_user
def create_challenge(user):
    data = request.get_json(silent=True) or {}
    opponent = data.get("opponent")
    sport = valid_sport(data.get("sport") or "tennis")
    if not sport:
        return jsonify(error="Неизвестный вид спорта."), 400
    try:
        scheduled_at = int(data.get("scheduledAt"))
        message = clean_text(data.get("message"), "Комментарий", 250, required=False)
    except (TypeError, ValueError) as error:
        return jsonify(error=str(error) if isinstance(error, ValueError) else "Укажите дату и время матча."), 400
    if not user["is_player"] or opponent == user["id"] or not isinstance(opponent, str) or len(opponent) > 80:
        return jsonify(error="Выберите другого активного игрока."), 400
    if scheduled_at < now_ms() - 5 * 60 * 1000 or scheduled_at > now_ms() + 90 * 24 * 60 * 60 * 1000:
        return jsonify(error="Дата матча должна быть в пределах ближайших 90 дней."), 400
    with db() as connection:
        target = connection.execute(
            "SELECT * FROM users WHERE id=? AND status='active' AND is_player=1", (opponent,)
        ).fetchone()
        if not target:
            return jsonify(error="Игрок не найден."), 404
        challenge_id = f"c-{secrets.token_hex(8)}"
        connection.execute(
            "INSERT INTO match_challenges(id,challenger,opponent,scheduled_at,message,created_at,sport) VALUES(?,?,?,?,?,?,?)",
            (challenge_id, user["id"], opponent, scheduled_at, message, now_ms(), sport),
        )
    with db() as connection:
        recipient_sub = notification_recipient_sub(connection, opponent)
    if recipient_sub:
        try:
            send_external_notification(
                recipient_sub, f"school-sport.{sport}.challenge.{challenge_id}", "Вызов на матч",
                f"{user['first_name']} {user['last_name']} предлагает сыграть в {'шахматы' if sport == 'chess' else 'настольный теннис'}. Откройте сайт, чтобы принять или отклонить вызов.",
                safe_notification_url("/#home"),
            )
        except ExternalNotificationError as error:
            app.logger.warning("External challenge notification failed with status %s", error.status_code)
    return jsonify(id=challenge_id)


@app.post("/api/challenges/<challenge_id>/<action>")
@require_user
def resolve_challenge(user, challenge_id, action):
    if action not in {"accept", "reject", "cancel"}:
        return jsonify(error="Неизвестное действие."), 400
    with db() as connection:
        row = connection.execute("SELECT * FROM match_challenges WHERE id=? AND status='pending'", (challenge_id,)).fetchone()
        if not row:
            return jsonify(error="Вызов не найден или уже обработан."), 404
        if action == "cancel" and row["challenger"] != user["id"]:
            return jsonify(error="Отменить вызов может только его автор."), 403
        if action in {"accept", "reject"} and row["opponent"] != user["id"]:
            return jsonify(error="Ответить на вызов может только соперник."), 403
        status = {"accept": "accepted", "reject": "rejected", "cancel": "cancelled"}[action]
        connection.execute("UPDATE match_challenges SET status=?,resolved_at=? WHERE id=?", (status, now_ms(), challenge_id))
    return jsonify(ok=True)


@app.get("/api/confirm/<token>")
def request_by_token(token):
    with db() as connection:
        row=connection.execute("SELECT id FROM requests WHERE token=? AND status='pending'",(token,)).fetchone()
    return jsonify(id=row["id"] if row else None)


@app.post("/api/admin/users/<uid>/<action>")
@require_admin
def admin_user(admin,uid,action):
    mapping={"approve":"active","reject":"rejected","activate":"active","deactivate":"inactive"}
    if action not in mapping:return jsonify(error="Неизвестное действие."),400
    with db() as connection:
        result = connection.execute("UPDATE users SET status=? WHERE id=? AND role!='admin'",(mapping[action],uid))
    if not result.rowcount:
        return jsonify(error="Пользователь не найден или его статус нельзя изменить."),404
    return jsonify(ok=True)


@app.post("/api/matches/<mid>/dispute")
@require_user
def dispute_match(user,mid):
    try:
        reason=clean_text((request.get_json(silent=True) or {}).get("reason"), "Комментарий", 1000)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    with db() as connection:
        match=connection.execute("SELECT * FROM matches WHERE id=? AND active=1",(mid,)).fetchone()
        if not match or user["id"] not in {match["player_one"],match["player_two"]}: return jsonify(error="Матч не найден."),404
        connection.execute("UPDATE matches SET dispute_user=?,dispute_reason=? WHERE id=?",(user["id"],reason,mid))
    return jsonify(ok=True)


@app.post("/api/admin/matches/<mid>/<action>")
@require_admin
def admin_match(admin,mid,action):
    with db() as connection:
        if action=="cancel":
            match = connection.execute("SELECT tournament_match_id FROM matches WHERE id=?", (mid,)).fetchone()
            if match and match["tournament_match_id"]:
                return jsonify(error="Результат турнирного матча нельзя отменить после продвижения сетки."),409
            connection.execute("UPDATE matches SET active=0 WHERE id=?",(mid,))
        elif action=="resolve": connection.execute("UPDATE matches SET dispute_user=NULL,dispute_reason=NULL WHERE id=?",(mid,))
        else:return jsonify(error="Неизвестное действие."),400
    return jsonify(ok=True)


@app.cli.command("seed-data")
def seed_data_command():
    """Create the first administrator and optional demo data after migrations."""
    seed_database()


@app.cli.command("seed-local-demo")
def seed_local_demo_command():
    """Create local-only student accounts with a shared development password."""
    seed_local_demo_users()
    print("Создано 12 локальных учеников: student01–student12, пароль 123456")


if __name__ == "__main__":
    with app.app_context():
        upgrade(directory=str(ROOT / "migrations"))
        seed_database()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
