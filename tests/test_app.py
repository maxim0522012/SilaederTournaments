import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from werkzeug.security import generate_password_hash
from flask_migrate import upgrade

TEMP_DIR = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["TENNIS_DB_PATH"] = os.path.join(TEMP_DIR.name, "test.db")
os.environ["SECRET_KEY"] = "test-secret-that-is-long-enough"
os.environ["ADMIN_PASSWORD"] = "test-admin-password-123"
os.environ["CRM_OIDC_ENABLED"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["ALLOW_LOCAL_USER_LOGIN"] = "true"

from app import (QR_BASE_URL, ROOT, app, calculate_server_ratings, clear_login_failures, db,
                 seed_database, send_external_notification, update_user_from_oidc)  # noqa: E402

with app.app_context():
    upgrade(directory=str(ROOT / "migrations"))
    seed_database()


class ServerFlowTest(unittest.TestCase):
    def setUp(self):
        with db() as connection:
            connection.execute("DELETE FROM oidc_identities")
            connection.execute("DELETE FROM account_link_tokens")
            connection.execute("DELETE FROM match_challenges")
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
                ("u4", "Мария", "Волкова", "8В", "maria", "maria-password-123"),
                ("u5", "Артём", "Кузнецов", "10А", "artem", "artem-password-123"),
                ("u6", "София", "Лебедева", "9Б", "sofia", "sofia-password-123"),
                ("u7", "Даниил", "Морозов", "8А", "daniil", "daniil-password-123"),
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
        claims = {"first_name": "Ева", "last_name": "Новикова", "class_name": "", "role": "user", "email": "eva.lk@example.org"}
        with db() as connection:
            connection.execute("UPDATE users SET status='inactive' WHERE id='u8'")
            update_user_from_oidc(connection, "u8", claims)
            updated = connection.execute("SELECT status,email FROM users WHERE id='u8'").fetchone()
            self.assertEqual(updated["status"], "inactive")
            self.assertEqual(updated["email"], "eva.lk@example.org")

    def test_local_login_saves_notification_email(self):
        client = app.test_client()
        response = self.post(client, "/api/login", {
            "login": "maxim", "password": "maxim-password-123", "email": "maxim.local@example.org",
        })
        self.assertEqual(response.status_code, 200)
        state = client.get("/api/state").get_json()
        current = next(user for user in state["users"] if user["id"] == state["currentUserId"])
        self.assertTrue(state["localLoginEnabled"])
        self.assertEqual(current["email"], "maxim.local@example.org")

    def test_external_notification_uses_basic_auth_idempotency_and_retries(self):
        response = Mock(status_code=202, headers={})
        response.json.return_value = {"id": 7, "status": "queued", "idempotent_replay": False}
        with patch("app.EXTERNAL_NOTIFICATIONS_ENABLED", True), \
                patch("app.CRM_NOTIFICATION_CLIENT_ID", "tennis"), \
                patch("app.CRM_NOTIFICATION_CLIENT_SECRET", "secret"), \
                patch("app.http_requests.post", return_value=response) as post:
            result = send_external_notification("790be3dd-4b7a-4ab4-94ce-82d44bcfd06f", "tennis.match.r-1", "Матч", "Проверьте результат")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(post.call_args.kwargs["auth"], ("tennis", "secret"))
        self.assertEqual(post.call_args.kwargs["headers"]["Idempotency-Key"], "tennis.match.r-1")

        unavailable = Mock(status_code=503, headers={})
        unavailable.json.return_value = {"message": "queue unavailable"}
        replay = Mock(status_code=200, headers={})
        replay.json.return_value = {"id": 7, "status": "queued", "idempotent_replay": True}
        with patch("app.EXTERNAL_NOTIFICATIONS_ENABLED", True), \
                patch("app.CRM_NOTIFICATION_CLIENT_ID", "tennis"), \
                patch("app.CRM_NOTIFICATION_CLIENT_SECRET", "secret"), \
                patch("app.time.sleep"), \
                patch("app.http_requests.post", side_effect=[unavailable, replay]) as retry_post:
            retried = send_external_notification("790be3dd-4b7a-4ab4-94ce-82d44bcfd06f", "tennis.match.r-1", "Матч", "Проверьте результат")
        self.assertTrue(retried["idempotent_replay"])
        self.assertEqual(retry_post.call_count, 2)
        self.assertEqual(retry_post.call_args_list[0].kwargs["json"], retry_post.call_args_list[1].kwargs["json"])

    def test_challenge_flow_and_admin_security_alerts(self):
        challenger = app.test_client()
        opponent = app.test_client()
        admin = app.test_client()
        self.assertEqual(self.login(challenger, "maxim", "maxim-password-123").status_code, 200)
        self.assertEqual(self.login(opponent, "eva", "eva-password-123").status_code, 200)
        scheduled_at = int(__import__("time").time() * 1000) + 24 * 60 * 60 * 1000
        created = self.post(challenger, "/api/challenges", {
            "opponent": "u8", "scheduledAt": scheduled_at, "message": "После уроков",
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        challenge_id = created.get_json()["id"]
        incoming = opponent.get("/api/state").get_json()["challenges"]
        self.assertEqual(incoming[0]["message"], "После уроков")
        self.assertEqual(self.post(opponent, f"/api/challenges/{challenge_id}/accept").status_code, 200)
        self.assertEqual(opponent.get("/api/state").get_json()["challenges"][0]["status"], "accepted")

        for score in (4, 4, 4, 6):
            request_created = self.post(challenger, "/api/requests", {
                "opponent": "u8", "scoreRequester": 11, "scoreOpponent": score,
            })
            self.assertEqual(request_created.status_code, 200)
            self.assertEqual(self.post(opponent, f"/api/requests/{request_created.get_json()['id']}/accept").status_code, 200)
        self.assertEqual(self.login(admin, "admin", "test-admin-password-123").status_code, 200)
        alerts = admin.get("/api/state").get_json()["securityAlerts"]
        self.assertTrue(any(alert["kind"] == "frequent_pair" for alert in alerts))
        self.assertTrue(any(alert["kind"] == "repeated_score" for alert in alerts))

    def test_match_request_notification_confirmation_and_local_qr(self):
        requester = app.test_client()
        opponent = app.test_client()
        self.assertEqual(self.login(requester, "maxim", "maxim-password-123").status_code, 200)
        created = self.post(requester, "/api/requests", {"opponent": "u8", "scoreRequester": 11, "scoreOpponent": 7})
        self.assertEqual(created.status_code, 200)
        request_id = created.get_json()["id"]
        confirmation_token = created.get_json()["token"]

        with db() as connection:
            connection.execute(
                "INSERT INTO oidc_identities(issuer,subject,user_id,created_at) VALUES(?,?,?,?)",
                ("https://lk.silaeder.ru", "790be3dd-4b7a-4ab4-94ce-82d44bcfd06f", "u8", 1_700_000_000_000),
            )

        qr = requester.get(f"/api/requests/{request_id}/qr")
        self.assertEqual(qr.status_code, 200)
        self.assertTrue(qr.content_type.startswith("image/svg+xml"))
        self.assertNotIn(b"api.qrserver.com", qr.data)
        self.assertEqual(qr.headers["Content-Location"], f"{QR_BASE_URL}/confirm/{confirmation_token}")

        with patch("app.send_external_notification", return_value={"status": "queued", "idempotent_replay": False}) as send_notification:
            notified = self.post(requester, f"/api/requests/{request_id}/notify")
            self.assertEqual(notified.status_code, 200)
            self.assertTrue(notified.get_json()["queued"])
            self.assertEqual(notified.get_json()["notificationStatus"], "queued")
            send_notification.assert_called_once()
            self.assertEqual(send_notification.call_args.args[0], "790be3dd-4b7a-4ab4-94ce-82d44bcfd06f")
            self.assertEqual(send_notification.call_args.args[1], f"school-sport.tennis.match-request.{request_id}")
            self.assertIn(f"/confirm/{confirmation_token}", send_notification.call_args.args[4])
        self.assertEqual(opponent.get(f"/confirm/{confirmation_token}").status_code, 200)
        with opponent.session_transaction() as confirmation_session:
            self.assertEqual(confirmation_session["confirmation_token"], confirmation_token)
        self.assertEqual(self.login(opponent, "eva", "eva-password-123").status_code, 200)
        opponent_state = opponent.get("/api/state").get_json()
        item = next(item for item in opponent_state["requests"] if item["id"] == request_id)
        self.assertEqual(item["token"], "")
        self.assertEqual(self.post(opponent, f"/api/requests/{request_id}/accept").status_code, 200)

    def test_chess_matches_support_draws_and_have_separate_elo(self):
        import time

        requester = app.test_client()
        opponent = app.test_client()
        self.assertEqual(self.login(requester, "maxim", "maxim-password-123").status_code, 200)
        self.assertEqual(self.login(opponent, "eva", "eva-password-123").status_code, 200)

        for score_requester, score_opponent in ((2, 0), (1, 1)):
            created = self.post(requester, "/api/requests", {
                "sport": "chess", "opponent": "u8",
                "scoreRequester": score_requester, "scoreOpponent": score_opponent,
            })
            self.assertEqual(created.status_code, 200, created.get_json())
            accepted = self.post(opponent, f"/api/requests/{created.get_json()['id']}/accept")
            self.assertEqual(accepted.status_code, 200, accepted.get_json())

        invalid = self.post(requester, "/api/requests", {
            "sport": "chess", "opponent": "u8", "scoreRequester": 1, "scoreOpponent": 0,
        })
        self.assertEqual(invalid.status_code, 400)
        state = requester.get("/api/state").get_json()
        chess_matches = [match for match in state["matches"] if match["sport"] == "chess"]
        self.assertEqual(len(chess_matches), 2)
        self.assertTrue(any(match["scoreOne"] == match["scoreTwo"] for match in chess_matches))
        with db() as connection:
            self.assertEqual(calculate_server_ratings(connection, "tennis")["u1"], 1000)
            self.assertEqual(calculate_server_ratings(connection, "chess")["u1"], 1011)

        admin = app.test_client()
        self.assertEqual(self.login(admin, "admin", "test-admin-password-123").status_code, 200)
        future = int(time.time() * 1000)
        tournament = self.post(admin, "/api/admin/tournaments", {
            "sport": "chess", "name": "Шахматный кубок", "description": "Тест",
            "registrationDeadline": future + 3_600_000,
            "startAt": future + 7_200_000, "maxPlayers": 2,
        })
        tournament_id = tournament.get_json()["id"]
        self.assertEqual(self.post(requester, f"/api/tournaments/{tournament_id}/join").status_code, 200)
        self.assertEqual(self.post(opponent, f"/api/tournaments/{tournament_id}/join").status_code, 200)
        self.assertEqual(self.post(admin, f"/api/admin/tournaments/{tournament_id}/start").status_code, 200)
        tournament_state = next(item for item in admin.get("/api/state").get_json()["tournaments"]
                                if item["id"] == tournament_id)
        tournament_match = next(item for item in tournament_state["matches"] if item["status"] == "pending")
        draw = self.post(requester, "/api/requests", {
            "sport": "chess", "opponent": "u8", "scoreRequester": 1, "scoreOpponent": 1,
            "tournamentMatchId": tournament_match["id"],
        })
        self.assertEqual(draw.status_code, 400)
        self.assertIn("побед", draw.get_json()["error"])

    def test_lichess_pkce_link_sync_and_disconnect(self):
        client = app.test_client()
        self.assertEqual(self.login(client, "maxim", "maxim-password-123").status_code, 200)
        started = client.get("/auth/lichess/login")
        self.assertEqual(started.status_code, 302)
        authorization = urlsplit(started.location)
        parameters = parse_qs(authorization.query)
        self.assertEqual(authorization.netloc, "lichess.org")
        self.assertEqual(parameters["code_challenge_method"], ["S256"])
        self.assertEqual(parameters["client_id"], ["sport.silaeder.ru"])
        self.assertIn("code_challenge", parameters)
        with client.session_transaction() as oauth_session:
            flow = dict(oauth_session["lichess_oauth"])
            self.assertNotIn(flow["verifier"], started.location)

        token_response = Mock(status_code=200)
        token_response.json.return_value = {"access_token": "test_access_token", "token_type": "bearer"}
        profile_response = Mock(status_code=200)
        profile_response.json.return_value = {
            "id": "maximchess", "username": "MaximChess",
            "perfs": {"rapid": {"rating": 1720}, "blitz": {"rating": 1655}, "classical": {"rating": 1810}},
        }
        revoke_response = Mock(status_code=204)
        with patch("app.http_requests.post", return_value=token_response) as token_post, \
                patch("app.http_requests.get", return_value=profile_response), \
                patch("app.http_requests.delete", return_value=revoke_response) as token_delete:
            callback = client.get(f"/auth/lichess/callback?code=Valid_Code&state={flow['state']}")
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(token_post.call_args.kwargs["json"]["code_verifier"], flow["verifier"])
        token_delete.assert_called_once()
        state = client.get("/api/state").get_json()
        player = next(user for user in state["users"] if user["id"] == "u1")
        self.assertEqual(player["lichess"]["username"], "MaximChess")
        self.assertEqual(player["lichess"]["rapid"], 1720)
        with db() as connection:
            columns = [row["name"] for row in connection.execute("PRAGMA table_info(lichess_accounts)")]
            self.assertNotIn("access_token", columns)

        refreshed_profile = Mock(status_code=200)
        refreshed_profile.json.return_value = {
            "id": "maximchess", "username": "MaximChess",
            "perfs": {"rapid": {"rating": 1731}, "blitz": {"rating": 1660}},
        }
        with patch("app.http_requests.get", return_value=refreshed_profile):
            synced = self.post(client, "/api/lichess/sync")
        self.assertEqual(synced.status_code, 200)
        state = client.get("/api/state").get_json()
        player = next(user for user in state["users"] if user["id"] == "u1")
        self.assertEqual(player["lichess"]["rapid"], 1731)
        self.assertEqual(self.post(client, "/api/lichess/disconnect").status_code, 200)
        state = client.get("/api/state").get_json()
        player = next(user for user in state["users"] if user["id"] == "u1")
        self.assertNotIn("lichess", player)

    def test_same_players_can_record_multiple_matches_in_one_day(self):
        requester = app.test_client()
        opponent = app.test_client()
        self.assertEqual(self.login(requester, "maxim", "maxim-password-123").status_code, 200)
        self.assertEqual(self.login(opponent, "eva", "eva-password-123").status_code, 200)

        for score_requester, score_opponent in ((11, 7), (8, 11)):
            created = self.post(requester, "/api/requests", {
                "opponent": "u8",
                "scoreRequester": score_requester,
                "scoreOpponent": score_opponent,
            })
            self.assertEqual(created.status_code, 200)
            accepted = self.post(opponent, f"/api/requests/{created.get_json()['id']}/accept")
            self.assertEqual(accepted.status_code, 200)

        with db() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM matches WHERE active=1 AND "
                "((player_one='u1' AND player_two='u8') OR (player_one='u8' AND player_two='u1'))"
            ).fetchone()[0]
            self.assertEqual(count, 2)

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
            "u4": ("maria", "maria-password-123"),
            "u5": ("artem", "artem-password-123"),
            "u6": ("sofia", "sofia-password-123"),
            "u7": ("daniil", "daniil-password-123"),
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
        self.assertEqual(len(upper_matches), 4)

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
        self.assertEqual(len(lower_matches), 2)
        self.assertEqual(
            {player for match in lower_matches for player in (match["playerOne"], match["playerTwo"])},
            losers,
        )

        saw_final = False
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
        self.assertFalse(any(match["status"] == "bye" for match in tournament["matches"]))
        stage_counts = {}
        for match in tournament["matches"]:
            stage_counts[match["stage"]] = stage_counts.get(match["stage"], 0) + 1
        self.assertEqual(stage_counts, {"upper": 7, "lower": 6, "final": 1})

        ordered_matches = sorted(tournament["matches"], key=lambda match: (match["sequence"], match["position"]))

        def next_match(player_id, source):
            return next((match for match in ordered_matches
                         if match["sequence"] > source["sequence"]
                         and player_id in {match["playerOne"], match["playerTwo"]}), None)

        for match in ordered_matches:
            if match["stage"] == "upper":
                winner_next = next_match(match["winner"], match)
                loser_next = next_match(match["loser"], match)
                self.assertIsNotNone(winner_next)
                self.assertIn(winner_next["stage"], {"upper", "final"})
                self.assertIsNotNone(loser_next)
                self.assertEqual(loser_next["stage"], "lower")
            elif match["stage"] == "lower":
                winner_next = next_match(match["winner"], match)
                self.assertIsNotNone(winner_next)
                self.assertIn(winner_next["stage"], {"lower", "final"})
                self.assertIsNone(next_match(match["loser"], match))

        decisive = max(
            (match for match in ordered_matches if match["stage"] in {"final", "reset"}),
            key=lambda match: (match["sequence"], match["position"]),
        )
        lower_final = max(
            (match for match in ordered_matches if match["stage"] == "lower"),
            key=lambda match: (match["sequence"], match["position"]),
        )
        self.assertEqual(tournament["podium"], {
            "first": tournament["championId"],
            "second": decisive["loser"],
            "third": lower_final["loser"],
        })


if __name__ == "__main__":
    unittest.main()
