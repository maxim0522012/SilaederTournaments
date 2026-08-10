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
            connection.execute("DELETE FROM tournament_matches")
            connection.execute("DELETE FROM tournament_participants")
            connection.execute("DELETE FROM tournaments")
            connection.execute("DELETE FROM matches")
            connection.execute("DELETE FROM users WHERE role!='admin'")
            created = 1_700_000_000_000
            users = [
                ("u1", "Максим", "Орлов", "10Б", "maxim", "maxim-password-123"),
                ("u8", "Ева", "Новикова", "7Б", "eva", "eva-password-123"),
                ("u2", "Анна", "Белова", "9А", "anna", "anna-password-123"),
                ("u3", "Илья", "Соколов", "11А", "ilya", "ilya-password-123"),
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

    def test_admin_creates_tournament_and_results_generate_lower_bracket(self):
        import time

        players = {}
        credentials = {
            "u1": ("maxim", "maxim-password-123"),
            "u8": ("eva", "eva-password-123"),
            "u2": ("anna", "anna-password-123"),
            "u3": ("ilya", "ilya-password-123"),
        }
        for user_id, (login, password) in credentials.items():
            client = app.test_client()
            self.assertEqual(self.login(client, login, password).status_code, 200)
            players[user_id] = client

        future = int(time.time() * 1000)
        forbidden = self.post(players["u1"], "/api/admin/tournaments", {
            "name": "Кубок школы", "description": "Тест", "registrationDeadline": future + 3_600_000,
            "startAt": future + 7_200_000, "maxPlayers": 8,
        })
        self.assertEqual(forbidden.status_code, 403)

        admin = app.test_client()
        self.assertEqual(self.login(admin, "admin", "test-admin-password-123").status_code, 200)
        created = self.post(admin, "/api/admin/tournaments", {
            "name": "Кубок школы", "description": "Двойное выбывание",
            "registrationDeadline": future + 3_600_000, "startAt": future + 7_200_000, "maxPlayers": 8,
        })
        self.assertEqual(created.status_code, 200)
        tournament_id = created.get_json()["id"]

        for client in players.values():
            self.assertEqual(self.post(client, f"/api/tournaments/{tournament_id}/join").status_code, 200)
        self.assertEqual(self.post(admin, f"/api/admin/tournaments/{tournament_id}/start").status_code, 200)

        tournament = next(item for item in admin.get("/api/state").get_json()["tournaments"] if item["id"] == tournament_id)
        upper_matches = [item for item in tournament["matches"] if item["stage"] == "upper" and item["status"] == "pending"]
        self.assertEqual(len(upper_matches), 2)

        losers = set()
        for tournament_match in upper_matches:
            requester_id, opponent_id = tournament_match["playerOne"], tournament_match["playerTwo"]
            created_result = self.post(players[requester_id], "/api/requests", {
                "opponent": opponent_id, "scoreRequester": 11, "scoreOpponent": 7,
                "tournamentMatchId": tournament_match["id"],
            })
            self.assertEqual(created_result.status_code, 200)
            request_id = created_result.get_json()["id"]
            self.assertEqual(self.post(players[opponent_id], f"/api/requests/{request_id}/accept").status_code, 200)
            losers.add(opponent_id)

        tournament = next(item for item in admin.get("/api/state").get_json()["tournaments"] if item["id"] == tournament_id)
        lower_matches = [item for item in tournament["matches"] if item["stage"] == "lower" and item["status"] == "pending"]
        self.assertEqual(len(lower_matches), 1)
        self.assertEqual({lower_matches[0]["playerOne"], lower_matches[0]["playerTwo"]}, losers)

        saw_final = False
        saw_reset = False
        for _ in range(12):
            tournament = next(item for item in admin.get("/api/state").get_json()["tournaments"] if item["id"] == tournament_id)
            if tournament["status"] == "completed":
                break
            pending_matches = [item for item in tournament["matches"] if item["status"] == "pending"]
            self.assertTrue(pending_matches)
            losses = {item["userId"]: item["losses"] for item in tournament["participants"]}
            for tournament_match in pending_matches:
                if tournament_match["stage"] == "final":
                    saw_final = True
                    requester_id = next(uid for uid in (tournament_match["playerOne"], tournament_match["playerTwo"]) if losses[uid] == 1)
                    opponent_id = tournament_match["playerTwo"] if requester_id == tournament_match["playerOne"] else tournament_match["playerOne"]
                else:
                    saw_reset = saw_reset or tournament_match["stage"] == "reset"
                    requester_id, opponent_id = tournament_match["playerOne"], tournament_match["playerTwo"]
                result = self.post(players[requester_id], "/api/requests", {
                    "opponent": opponent_id, "scoreRequester": 11, "scoreOpponent": 7,
                    "tournamentMatchId": tournament_match["id"],
                })
                self.assertEqual(result.status_code, 200)
                self.assertEqual(self.post(players[opponent_id], f"/api/requests/{result.get_json()['id']}/accept").status_code, 200)

        tournament = next(item for item in admin.get("/api/state").get_json()["tournaments"] if item["id"] == tournament_id)
        self.assertEqual(tournament["status"], "completed")
        self.assertIsNotNone(tournament["championId"])
        self.assertTrue(saw_final)
        self.assertTrue(saw_reset)


if __name__ == "__main__":
    unittest.main()
