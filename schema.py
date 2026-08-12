from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, MetaData, text


metadata = MetaData(naming_convention={
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})
schema_db = SQLAlchemy(metadata=metadata)


class User(schema_db.Model):
    __tablename__ = "users"

    id = schema_db.Column(schema_db.Text, primary_key=True, nullable=True)
    first_name = schema_db.Column(schema_db.Text, nullable=False)
    last_name = schema_db.Column(schema_db.Text, nullable=False)
    class_name = schema_db.Column(schema_db.Text, nullable=False, server_default="")
    login = schema_db.Column(schema_db.Text, nullable=False)
    password_hash = schema_db.Column(schema_db.Text, nullable=False)
    role = schema_db.Column(schema_db.Text, nullable=False, server_default="user")
    status = schema_db.Column(schema_db.Text, nullable=False, server_default="pending")
    is_player = schema_db.Column(schema_db.Integer, nullable=False, server_default="1")
    created_at = schema_db.Column(schema_db.Integer, nullable=False)
    email = schema_db.Column(schema_db.Text)

    # SQLite cannot reflect expression indexes reliably. The case-insensitive
    # email index is therefore maintained explicitly by the Alembic migration.
    __table_args__ = (Index("idx_users_login", "login", unique=True),)


class Match(schema_db.Model):
    __tablename__ = "matches"

    id = schema_db.Column(schema_db.Text, primary_key=True, nullable=True)
    player_one = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    player_two = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    score_one = schema_db.Column(schema_db.Integer, nullable=False)
    score_two = schema_db.Column(schema_db.Integer, nullable=False)
    confirmed_by = schema_db.Column(schema_db.Text)
    created_at = schema_db.Column(schema_db.Integer, nullable=False)
    active = schema_db.Column(schema_db.Integer, nullable=False, server_default="1")
    dispute_user = schema_db.Column(schema_db.Text)
    dispute_reason = schema_db.Column(schema_db.Text)
    tournament_match_id = schema_db.Column(schema_db.Text)

    __table_args__ = (Index("idx_matches_created", "created_at"),)


class MatchRequest(schema_db.Model):
    __tablename__ = "requests"

    id = schema_db.Column(schema_db.Text, primary_key=True, nullable=True)
    token = schema_db.Column(schema_db.Text, nullable=False)
    requester = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    opponent = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    score_requester = schema_db.Column(schema_db.Integer, nullable=False)
    score_opponent = schema_db.Column(schema_db.Integer, nullable=False)
    status = schema_db.Column(schema_db.Text, nullable=False, server_default="pending")
    notified = schema_db.Column(schema_db.Integer, nullable=False, server_default="0")
    created_at = schema_db.Column(schema_db.Integer, nullable=False)
    resolved_at = schema_db.Column(schema_db.Integer)
    tournament_match_id = schema_db.Column(schema_db.Text)

    __table_args__ = (
        Index("idx_requests_token", "token", unique=True),
        Index("idx_requests_opponent_status", "opponent", "status"),
        Index(
            "idx_requests_tournament_pending",
            "tournament_match_id",
            unique=True,
            sqlite_where=text("tournament_match_id IS NOT NULL AND status='pending'"),
        ),
    )


class MatchChallenge(schema_db.Model):
    __tablename__ = "match_challenges"

    id = schema_db.Column(schema_db.Text, primary_key=True, nullable=True)
    challenger = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    opponent = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    scheduled_at = schema_db.Column(schema_db.Integer, nullable=False)
    message = schema_db.Column(schema_db.Text, nullable=False, server_default="")
    status = schema_db.Column(schema_db.Text, nullable=False, server_default="pending")
    created_at = schema_db.Column(schema_db.Integer, nullable=False)
    resolved_at = schema_db.Column(schema_db.Integer)

    __table_args__ = (
        Index("idx_match_challenges_opponent_status", "opponent", "status"),
        Index("idx_match_challenges_challenger_status", "challenger", "status"),
    )


class OidcIdentity(schema_db.Model):
    __tablename__ = "oidc_identities"

    issuer = schema_db.Column(schema_db.Text, primary_key=True)
    subject = schema_db.Column(schema_db.Text, primary_key=True)
    user_id = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    created_at = schema_db.Column(schema_db.Integer, nullable=False)


class AccountLinkToken(schema_db.Model):
    __tablename__ = "account_link_tokens"

    token_hash = schema_db.Column(schema_db.Text, primary_key=True, nullable=True)
    user_id = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    issuer = schema_db.Column(schema_db.Text, nullable=False)
    subject = schema_db.Column(schema_db.Text, nullable=False)
    claims_json = schema_db.Column(schema_db.Text, nullable=False)
    expires_at = schema_db.Column(schema_db.Integer, nullable=False)


class OidcSession(schema_db.Model):
    __tablename__ = "oidc_sessions"

    id = schema_db.Column(schema_db.Text, primary_key=True, nullable=True)
    user_id = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    id_token = schema_db.Column(schema_db.Text)
    created_at = schema_db.Column(schema_db.Integer, nullable=False)


class Tournament(schema_db.Model):
    __tablename__ = "tournaments"

    id = schema_db.Column(schema_db.Text, primary_key=True, nullable=True)
    name = schema_db.Column(schema_db.Text, nullable=False)
    description = schema_db.Column(schema_db.Text, nullable=False, server_default="")
    registration_deadline = schema_db.Column(schema_db.Integer, nullable=False)
    start_at = schema_db.Column(schema_db.Integer, nullable=False)
    max_players = schema_db.Column(schema_db.Integer, nullable=False)
    status = schema_db.Column(schema_db.Text, nullable=False, server_default="registration")
    champion_id = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"))
    created_by = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    created_at = schema_db.Column(schema_db.Integer, nullable=False)
    completed_at = schema_db.Column(schema_db.Integer)


class TournamentParticipant(schema_db.Model):
    __tablename__ = "tournament_participants"

    tournament_id = schema_db.Column(schema_db.Text, schema_db.ForeignKey("tournaments.id", ondelete="CASCADE"), primary_key=True)
    user_id = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), primary_key=True)
    seed = schema_db.Column(schema_db.Integer)
    losses = schema_db.Column(schema_db.Integer, nullable=False, server_default="0")
    eliminated = schema_db.Column(schema_db.Integer, nullable=False, server_default="0")
    joined_at = schema_db.Column(schema_db.Integer, nullable=False)

    __table_args__ = (Index("idx_tournament_participants_tournament", "tournament_id", "eliminated", "losses"),)


class TournamentMatch(schema_db.Model):
    __tablename__ = "tournament_matches"

    id = schema_db.Column(schema_db.Text, primary_key=True, nullable=True)
    tournament_id = schema_db.Column(schema_db.Text, schema_db.ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    sequence = schema_db.Column(schema_db.Integer, nullable=False)
    stage = schema_db.Column(schema_db.Text, nullable=False)
    round_number = schema_db.Column(schema_db.Integer, nullable=False)
    position = schema_db.Column(schema_db.Integer, nullable=False)
    player_one = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"), nullable=False)
    player_two = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"))
    winner = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"))
    loser = schema_db.Column(schema_db.Text, schema_db.ForeignKey("users.id"))
    status = schema_db.Column(schema_db.Text, nullable=False, server_default="pending")
    result_match_id = schema_db.Column(schema_db.Text, schema_db.ForeignKey("matches.id"))
    created_at = schema_db.Column(schema_db.Integer, nullable=False)
    completed_at = schema_db.Column(schema_db.Integer)

    __table_args__ = (Index("idx_tournament_matches_tournament_sequence", "tournament_id", "sequence", "position"),)
