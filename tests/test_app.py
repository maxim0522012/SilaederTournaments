import os
import tempfile
import unittest

from werkzeug.security import generate_password_hash

TEMP_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["TENNIS_DB_PATH"] = os.path.join(TEMP_DIR.name, "test.db")
os.environ["SECRET_KEY"] = "test-secret-that-is-long-enough"
os.environ["ADMIN_PASSWORD"] = "test-admin-password-123"
os.environ["CRM_OIDC_ENABLED"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"

from app import app, clear_login_failures, db, update_user_from_oidc  # noqa: E402


class ServerFlowTest(unittest.TestCase):
    def setUp(self):
        with db() as connection:
            connection.execute("DELETE FROM requests")
            connection.execute("DELETE FROM matches")
            connection.execute("DELETE FROM users WHERE role!='admin'")
            created = 1_700_000_000_000
            users = [
                ("u1", "Максим", "Орлов", "10Б", "maxim", "maxim-password-123"),
                ("u8", "Ева", "Новикова", "7Б", "eva", "eva-password-123"),
            ]
            connection.executemany(
                "INSERT INTO users(id,first_name,last_name,class_name,login,password_hash,role,status,is_player,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(uid, first, last, cls, login, generate_password_hash(password), "user", "active", 1, created)
                 for uid, first, last, cls, login, password in users],
            )
        clear_login_failures(("127.0.0.1", "admin"))

    def csrf(self, client):
        return client.get("/api/state").get_json()["csrfToken"]

    def post(self, client, path, payload=None, token=None, origin=None):
        headers = {"X-CSRF-Token": token or self.csrf(client)}
        if origin:
            headers["Origin"] = origin
        return client.post(path, json=payload or {}, headers=headers)

    def login(self, client, login, password):
        return self.post(client, "/api/login", {"login": login, "password": password})

    def test_oidc_refresh_preserves_inactive_status(self):
        claims = {"first_name": "Ева", "last_name": "Новикова", "class_name": "", "role": "user", "email": ""}
        with db() as connection:
            connection.execute("UPDATE users SET status='inactive' WHERE id='u8'")
            update_user_from_oidc(connection, "u8", claims)
            status = connection.execute("SELECT status FROM users WHERE id='u8'").fetchone()["status"]
            self.assertEqual(status, "inactive")

    def test_match_request_notification_confirmation_and_local_qr(self):
        requester = app.test_client()
        opponent = app.test_client()
        self.assertEqual(self.login(requester, "maxim", "maxim-password-123").status_code, 200)
        created = self.post(requester, "/api/requests", {"opponent": "u8", "scoreRequester": 11, "scoreOpponent": 7})
        self.assertEqual(created.status_code, 200)
        request_id = created.get_json()["id"]

        qr = requester.get(f"/api/requests/{request_id}/qr")
        self.assertEqual(qr.status_code, 200)
        self.assertTrue(qr.content_type.startswith("image/svg+xml"))
        self.assertNotIn(b"api.qrserver.com", qr.data)

        self.assertEqual(self.post(requester, f"/api/requests/{request_id}/notify").status_code, 200)
        self.assertEqual(self.login(opponent, "eva", "eva-password-123").status_code, 200)
        opponent_state = opponent.get("/api/state").get_json()
        item = next(item for item in opponent_state["requests"] if item["id"] == request_id)
        self.assertEqual(item["token"], "")
        self.assertEqual(self.post(opponent, f"/api/requests/{request_id}/accept").status_code, 200)

    def test_public_state_minimizes_personal_data(self):
        public = app.test_client().get("/api/state").get_json()
        player = next(user for user in public["users"] if user["id"] == "u1")
        for private_field in ("className", "login", "email", "role", "createdAt"):
            self.assertNotIn(private_field, player)

    def test_csrf_origin_and_security_headers(self):
        admin = app.test_client()
        self.assertEqual(self.login(admin, "admin", "test-admin-password-123").status_code, 200)
        token = self.csrf(admin)
        blocked = self.post(admin, "/api/admin/users/u1/deactivate", token=token, origin="https://evil.example")
        self.assertEqual(blocked.status_code, 403)
        missing = admin.post("/api/admin/users/u1/deactivate", json={})
        self.assertEqual(missing.status_code, 403)
        response = admin.get("/")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])

    def test_login_rate_limit(self):
        client = app.test_client()
        for _ in range(5):
            self.assertEqual(self.login(client, "admin", "incorrect-password").status_code, 400)
        limited = self.login(client, "admin", "test-admin-password-123")
        self.assertEqual(limited.status_code, 429)


if __name__ == "__main__":
    unittest.main()
