import math
import hashlib
import json
import os
import secrets
import smtplib
import sqlite3
import time
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB_PATH = Path(os.environ.get("TENNIS_DB_PATH", ROOT / "tennis.db"))
ELO_START = 1000
ELO_K = 24

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:5000").rstrip("/")
OIDC_ISSUER = os.environ.get("CRM_OIDC_ISSUER", "https://lk.silaeder.ru").rstrip("/")
OIDC_ENABLED = os.environ.get("CRM_OIDC_ENABLED", "false").lower() == "true" and all(
    os.environ.get(key) for key in ("SECRET_KEY", "CRM_OIDC_CLIENT_ID", "CRM_OIDC_CLIENT_SECRET")
)
OIDC_REDIRECT_URI = os.environ.get("CRM_OIDC_REDIRECT_URI", f"{APP_URL}/auth/silaeder/callback")
OIDC_LOGOUT_REDIRECT_URI = os.environ.get("CRM_OIDC_POST_LOGOUT_REDIRECT_URI", f"{APP_URL}/auth/silaeder/logout/callback")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=APP_URL.startswith("https://"))

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


def init_db():
    with db() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
          class_name TEXT NOT NULL DEFAULT '', login TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
          status TEXT NOT NULL DEFAULT 'pending', is_player INTEGER NOT NULL DEFAULT 1,
          created_at INTEGER NOT NULL, email TEXT
        );
        CREATE TABLE IF NOT EXISTS matches (
          id TEXT PRIMARY KEY, player_one TEXT NOT NULL, player_two TEXT NOT NULL,
          score_one INTEGER NOT NULL, score_two INTEGER NOT NULL,
          confirmed_by TEXT, created_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          dispute_user TEXT, dispute_reason TEXT,
          FOREIGN KEY(player_one) REFERENCES users(id), FOREIGN KEY(player_two) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS requests (
          id TEXT PRIMARY KEY, token TEXT NOT NULL UNIQUE, requester TEXT NOT NULL,
          opponent TEXT NOT NULL, score_requester INTEGER NOT NULL, score_opponent INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', notified INTEGER NOT NULL DEFAULT 0,
          created_at INTEGER NOT NULL, resolved_at INTEGER,
          FOREIGN KEY(requester) REFERENCES users(id), FOREIGN KEY(opponent) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_matches_created ON matches(created_at);
        CREATE INDEX IF NOT EXISTS idx_requests_opponent_status ON requests(opponent, status);
        CREATE TABLE IF NOT EXISTS oidc_identities (
          issuer TEXT NOT NULL, subject TEXT NOT NULL, user_id TEXT NOT NULL,
          created_at INTEGER NOT NULL, PRIMARY KEY(issuer, subject),
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS account_link_tokens (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, issuer TEXT NOT NULL,
          subject TEXT NOT NULL, claims_json TEXT NOT NULL, expires_at INTEGER NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS oidc_sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, id_token TEXT,
          created_at INTEGER NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "email" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(lower(email)) WHERE email IS NOT NULL AND email != ''")
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            seed_users = [
                ("admin", "Администратор", "Школы", "", "admin", "admin", "admin", 0),
                ("u1", "Максим", "Орлов", "10Б", "maxim", "123456", "user", 1),
                ("u2", "Анна", "Белова", "9А", "anna", "123456", "user", 1),
                ("u3", "Илья", "Соколов", "11А", "ilya", "123456", "user", 1),
                ("u4", "Мария", "Волкова", "8В", "maria", "123456", "user", 1),
                ("u5", "Артём", "Кузнецов", "10А", "artem", "123456", "user", 1),
                ("u6", "София", "Лебедева", "9Б", "sofia", "123456", "user", 1),
                ("u7", "Даниил", "Морозов", "8А", "daniil", "123456", "user", 1),
                ("u8", "Ева", "Новикова", "7Б", "eva", "123456", "user", 1),
            ]
            created = now_ms() - 20 * 86_400_000
            connection.executemany(
                "INSERT INTO users(id,first_name,last_name,class_name,login,password_hash,role,status,is_player,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(uid, first, last, cls, login, generate_password_hash(password), role, "active", player, created)
                 for uid, first, last, cls, login, password, role, player in seed_users],
            )
            scores = [("u1","u2",11,8),("u3","u5",13,11),("u4","u6",7,11),("u1","u3",11,9),
                      ("u2","u4",11,5),("u5","u7",11,6),("u6","u8",11,4),("u1","u4",12,10),
                      ("u2","u3",9,11),("u5","u6",8,11)]
            for index, (p1, p2, s1, s2) in enumerate(scores):
                connection.execute("INSERT INTO matches(id,player_one,player_two,score_one,score_two,confirmed_by,created_at) VALUES(?,?,?,?,?,?,?)",
                                   (f"m{index+1}", p1, p2, s1, s2, p2, now_ms()-(10-index)*86_400_000))


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
    return {"id": row["id"], "firstName": row["first_name"],
            "lastName": row["last_name"],
            "className": row["class_name"], "login": row["login"] if can_see_full else "",
            "email": row["email"] if can_see_full and "email" in row.keys() else "",
            "role": row["role"], "status": row["status"], "isPlayer": bool(row["is_player"]),
            "createdAt": row["created_at"]}


@app.get("/")
@app.get("/confirm/<token>")
def index(token=None):
    return send_from_directory(ROOT, "index.html", max_age=0)


@app.get("/<path:filename>")
def assets(filename):
    if filename in {"app.js", "styles.css"}:
        return send_from_directory(ROOT, filename, max_age=0)
    return "Not found", 404


@app.after_request
def disable_frontend_cache(response):
    """Always serve the current UI while the school server is being updated."""
    if request.path in {"/", "/index.html", "/app.js", "/styles.css"} or request.path.startswith("/confirm/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/static/<path:filename>")
def static_assets(filename):
    return send_from_directory(ROOT / "static", filename)


@app.get("/api/state")
def state():
    viewer = current_user()
    with db() as connection:
        users = [serialize_user(row, viewer) for row in connection.execute("SELECT * FROM users ORDER BY created_at")]
        matches = [dict(row) for row in connection.execute("SELECT * FROM matches ORDER BY created_at")]
        requests_rows = []
        if viewer:
            if viewer["role"] == "admin":
                rows = connection.execute("SELECT * FROM requests ORDER BY created_at").fetchall()
            else:
                rows = connection.execute("SELECT * FROM requests WHERE requester=? OR opponent=? ORDER BY created_at", (viewer["id"], viewer["id"])).fetchall()
            requests_rows = [dict(row) for row in rows]
    matches_json = [{"id":m["id"],"playerOne":m["player_one"],"playerTwo":m["player_two"],"scoreOne":m["score_one"],"scoreTwo":m["score_two"],"confirmedBy":m["confirmed_by"],"createdAt":m["created_at"],"active":bool(m["active"]),
                     **({"dispute":{"userId":m["dispute_user"],"reason":m["dispute_reason"]}} if m["dispute_user"] else {})} for m in matches]
    requests_json = [{"id":r["id"],"token":r["token"],"requester":r["requester"],"opponent":r["opponent"],"scoreRequester":r["score_requester"],"scoreOpponent":r["score_opponent"],"status":r["status"],"notified":bool(r["notified"]),"createdAt":r["created_at"]} for r in requests_rows]
    return jsonify(users=users, matches=matches_json, requests=requests_json,
                   currentUserId=viewer["id"] if viewer else None,
                   oidcEnabled=OIDC_ENABLED, oidcSession=bool(session.get("oidc_login")),
                   authMessage=session.pop("auth_message", None))


def normalized_oidc_claims(userinfo):
    roles = userinfo.get("roles") or userinfo.get("role") or []
    if isinstance(roles, str):
        roles = [item.strip() for item in roles.replace(",", " ").split()]
    roles = {str(item).lower() for item in roles}
    if "admin" in roles or "teacher" in roles:
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
        "subject": str(userinfo.get("sub") or "").strip(),
        "first_name": first_name,
        "last_name": last_name or "—",
        "email": str(userinfo.get("email") or "").strip().lower(),
        "class_name": str(userinfo.get("class_name") or userinfo.get("class") or userinfo.get("grade") or "").strip(),
        "role": local_role,
    }


def update_user_from_oidc(connection, user_id, claims):
    values = (claims["first_name"], claims["last_name"], claims["class_name"], claims["role"], claims["email"] or None, user_id)
    try:
        connection.execute("UPDATE users SET first_name=?,last_name=?,class_name=?,role=?,email=?,status=CASE WHEN status='pending' THEN 'active' ELSE status END WHERE id=?", values)
    except sqlite3.IntegrityError:
        connection.execute("UPDATE users SET first_name=?,last_name=?,class_name=?,role=?,status=CASE WHEN status='pending' THEN 'active' ELSE status END WHERE id=?", values[:4] + (user_id,))


def mail_configured():
    return all(os.environ.get(key) for key in ("MAIL_HOST", "MAIL_FROM"))


def send_link_email(recipient, raw_token):
    message = EmailMessage()
    message["Subject"] = "Подтверждение входа в рейтинг настольного тенниса"
    message["From"] = os.environ["MAIL_FROM"]
    message["To"] = recipient
    message.set_content(f"Для привязки аккаунта ЛК Силаэдра перейдите по ссылке в течение 30 минут:\n\n{APP_URL}/auth/silaeder/link/{raw_token}\n")
    host = os.environ["MAIL_HOST"]
    port = int(os.environ.get("MAIL_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if os.environ.get("MAIL_USE_TLS", "true").lower() == "true":
            smtp.starttls()
        if os.environ.get("MAIL_USERNAME"):
            smtp.login(os.environ["MAIL_USERNAME"], os.environ.get("MAIL_PASSWORD", ""))
        smtp.send_message(message)


@app.get("/auth/silaeder/login")
def silaeder_login():
    if not OIDC_ENABLED:
        session["auth_message"] = "Школьный вход ещё не настроен администратором."
        return redirect("/#home")
    if request.host_url.rstrip("/") != APP_URL:
        return redirect(f"{APP_URL}/auth/silaeder/login")
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
                    if not mail_configured():
                        session["auth_message"] = "Аккаунт с таким email уже существует. Для безопасной привязки администратору нужно настроить отправку почты."
                        return redirect("/#home")
                    raw_token = secrets.token_urlsafe(32)
                    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                    connection.execute("DELETE FROM account_link_tokens WHERE expires_at<?", (now_ms(),))
                    connection.execute("INSERT OR REPLACE INTO account_link_tokens VALUES(?,?,?,?,?,?)", (token_hash, existing["id"], claims["issuer"], claims["subject"], json.dumps(claims, ensure_ascii=False), now_ms() + 30 * 60 * 1000))
                    send_link_email(claims["email"], raw_token)
                    session["auth_message"] = "Мы отправили на ваш email ссылку для безопасной привязки аккаунта. Она действует 30 минут."
                    return redirect("/#home")
                user_id = f"u-{secrets.token_hex(8)}"
                login = f"oidc-{hashlib.sha256((claims['issuer'] + claims['subject']).encode()).hexdigest()[:16]}"
                connection.execute("INSERT INTO users(id,first_name,last_name,class_name,login,password_hash,role,status,is_player,created_at,email) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (user_id, claims["first_name"], claims["last_name"], claims["class_name"], login, generate_password_hash(secrets.token_urlsafe(32)), claims["role"], "active", 1, now_ms(), claims["email"] or None))
                connection.execute("INSERT INTO oidc_identities VALUES(?,?,?,?)", (claims["issuer"], claims["subject"], user_id, now_ms()))
        oidc_session_id = secrets.token_urlsafe(24)
        with db() as connection:
            connection.execute("INSERT INTO oidc_sessions VALUES(?,?,?,?)", (oidc_session_id, user_id, token.get("id_token"), now_ms()))
        session.clear()
        session["user_id"] = user_id
        session["oidc_login"] = True
        session["oidc_session_id"] = oidc_session_id
        return redirect("/#home")
    except Exception as error:
        session["auth_message"] = f"Не удалось выполнить школьный вход: {error}"
        return redirect("/#home")


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
    session.clear()
    session["user_id"] = link["user_id"]
    session["oidc_login"] = True
    session["auth_message"] = "Аккаунт ЛК Силаэдра успешно привязан."
    return redirect("/#home")


@app.get("/auth/silaeder/logout")
def silaeder_logout():
    oidc_session_id = session.get("oidc_session_id")
    id_token = None
    if oidc_session_id:
        with db() as connection:
            oidc_session = connection.execute("SELECT id_token FROM oidc_sessions WHERE id=?", (oidc_session_id,)).fetchone()
            id_token = oidc_session["id_token"] if oidc_session else None
            connection.execute("DELETE FROM oidc_sessions WHERE id=?", (oidc_session_id,))
    session.clear()
    if OIDC_ENABLED:
        try:
            metadata = oauth.silaeder.load_server_metadata()
            endpoint = metadata.get("end_session_endpoint")
            if endpoint:
                params = {"post_logout_redirect_uri": OIDC_LOGOUT_REDIRECT_URI}
                if id_token:
                    params["id_token_hint"] = id_token
                return redirect(f"{endpoint}?{urlencode(params)}")
        except Exception:
            pass
    return redirect("/#home")


@app.get("/auth/silaeder/logout/callback")
def silaeder_logout_callback():
    session.clear()
    return redirect("/#home")


@app.post("/api/login")
def login():
    data=request.get_json() or {}
    with db() as connection:
        user=connection.execute("SELECT * FROM users WHERE lower(login)=lower(?)",(data.get("login", ""),)).fetchone()
    if not user or not check_password_hash(user["password_hash"],data.get("password", "")):
        return jsonify(error="Неверный логин или пароль."),400
    if user["status"] in {"inactive","rejected"}:
        return jsonify(error="Аккаунт недоступен. Обратитесь к администратору."),403
    if OIDC_ENABLED and user["role"] != "admin":
        return jsonify(error="Для учеников и учителей используйте вход через ЛК Силаэдра."),403
    session.clear()
    session["user_id"]=user["id"]
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear(); return jsonify(ok=True)


@app.post("/api/register")
def register():
    if OIDC_ENABLED:
        return jsonify(error="Регистрация выполняется через ЛК Силаэдра."),410
    data=request.get_json() or {}
    required=["firstName","lastName","className","login","password"]
    if any(not str(data.get(key,"")).strip() for key in required) or len(data["password"])<6:
        return jsonify(error="Заполните все поля. Пароль — не короче 6 символов."),400
    uid=f"u-{secrets.token_hex(8)}"
    try:
        with db() as connection:
            connection.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?)",(uid,data["firstName"].strip(),data["lastName"].strip(),data["className"].strip().upper(),data["login"].strip(),generate_password_hash(data["password"]),"user","pending",1,now_ms()))
    except sqlite3.IntegrityError:
        return jsonify(error="Этот логин уже занят."),409
    session["user_id"]=uid
    return jsonify(ok=True)


@app.get("/api/users/search")
@require_user
def search_users(viewer):
    query=f"%{request.args.get('q','').strip()}%"
    with db() as connection:
        rows=connection.execute("SELECT * FROM users WHERE status='active' AND is_player=1 AND id!=? AND (first_name LIKE ? OR last_name LIKE ? OR class_name LIKE ?) LIMIT 8",(viewer["id"],query,query,query)).fetchall()
    return jsonify(users=[serialize_user(row,None) for row in rows])


def same_pair_today(connection,p1,p2,include_requests=True):
    start=time.mktime(time.localtime()[:3]+(0,0,0,0,0,-1))*1000
    if connection.execute("SELECT 1 FROM matches WHERE active=1 AND created_at>=? AND ((player_one=? AND player_two=?) OR (player_one=? AND player_two=?))",(start,p1,p2,p2,p1)).fetchone():
        return True
    return include_requests and bool(connection.execute("SELECT 1 FROM requests WHERE status='pending' AND created_at>=? AND ((requester=? AND opponent=?) OR (requester=? AND opponent=?))",(start,p1,p2,p2,p1)).fetchone())


@app.post("/api/requests")
@require_user
def create_request(user):
    data=request.get_json() or {}; opponent=data.get("opponent")
    try: s1,s2=int(data.get("scoreRequester")),int(data.get("scoreOpponent"))
    except (TypeError,ValueError): return jsonify(error="Некорректный счёт."),400
    if opponent==user["id"] or not valid_score(s1,s2): return jsonify(error="Проверьте соперника и счёт матча."),400
    with db() as connection:
        target=connection.execute("SELECT * FROM users WHERE id=? AND status='active' AND is_player=1",(opponent,)).fetchone()
        if not target: return jsonify(error="Игрок не найден."),404
        if same_pair_today(connection,user["id"],opponent): return jsonify(error="Для этой пары уже есть матч или заявка сегодня."),409
        rid=f"r-{secrets.token_hex(8)}"; token=secrets.token_urlsafe(24)
        connection.execute("INSERT INTO requests(id,token,requester,opponent,score_requester,score_opponent,created_at) VALUES(?,?,?,?,?,?,?)",(rid,token,user["id"],opponent,s1,s2,now_ms()))
    return jsonify(id=rid,token=token)


@app.post("/api/requests/<rid>/notify")
@require_user
def notify_request(user,rid):
    with db() as connection:
        result=connection.execute("UPDATE requests SET notified=1 WHERE id=? AND requester=? AND status='pending'",(rid,user["id"]))
    return (jsonify(ok=True) if result.rowcount else (jsonify(error="Заявка не найдена."),404))


@app.post("/api/requests/<rid>/<action>")
@require_user
def resolve_request(user,rid,action):
    if action not in {"accept","reject"}: return jsonify(error="Неизвестное действие."),400
    with db() as connection:
        row=connection.execute("SELECT * FROM requests WHERE id=? AND opponent=? AND status='pending'",(rid,user["id"])).fetchone()
        if not row: return jsonify(error="Заявка не найдена или уже обработана."),404
        if action=="accept":
            if same_pair_today(connection,row["requester"],row["opponent"],False): return jsonify(error="Эта пара уже сыграла сегодня."),409
            connection.execute("INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?)",(f"m-{secrets.token_hex(8)}",row["requester"],row["opponent"],row["score_requester"],row["score_opponent"],user["id"],now_ms(),1,None,None))
        connection.execute("UPDATE requests SET status=?,resolved_at=? WHERE id=?",("accepted" if action=="accept" else "rejected",now_ms(),rid))
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
    reason=(request.get_json() or {}).get("reason","").strip()
    with db() as connection:
        match=connection.execute("SELECT * FROM matches WHERE id=? AND active=1",(mid,)).fetchone()
        if not match or user["id"] not in {match["player_one"],match["player_two"]}: return jsonify(error="Матч не найден."),404
        connection.execute("UPDATE matches SET dispute_user=?,dispute_reason=? WHERE id=?",(user["id"],reason,mid))
    return jsonify(ok=True)


@app.post("/api/admin/matches/<mid>/<action>")
@require_admin
def admin_match(admin,mid,action):
    with db() as connection:
        if action=="cancel": connection.execute("UPDATE matches SET active=0 WHERE id=?",(mid,))
        elif action=="resolve": connection.execute("UPDATE matches SET dispute_user=NULL,dispute_reason=NULL WHERE id=?",(mid,))
        else:return jsonify(error="Неизвестное действие."),400
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
