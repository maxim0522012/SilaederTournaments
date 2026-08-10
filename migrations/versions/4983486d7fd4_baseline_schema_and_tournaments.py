"""baseline schema and tournaments

Revision ID: 4983486d7fd4
Revises: 
Create Date: 2026-08-10 17:13:46.530604

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4983486d7fd4'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'users' not in tables:
        op.create_table(
            'users',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('first_name', sa.Text(), nullable=False),
            sa.Column('last_name', sa.Text(), nullable=False),
            sa.Column('class_name', sa.Text(), server_default='', nullable=False),
            sa.Column('login', sa.Text(), nullable=False),
            sa.Column('password_hash', sa.Text(), nullable=False),
            sa.Column('role', sa.Text(), server_default='user', nullable=False),
            sa.Column('status', sa.Text(), server_default='pending', nullable=False),
            sa.Column('is_player', sa.Integer(), server_default='1', nullable=False),
            sa.Column('created_at', sa.Integer(), nullable=False),
            sa.Column('email', sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        tables.add('users')
    elif 'email' not in {column['name'] for column in inspector.get_columns('users')}:
        op.add_column('users', sa.Column('email', sa.Text(), nullable=True))

    if 'matches' not in tables:
        op.create_table(
            'matches',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('player_one', sa.Text(), nullable=False),
            sa.Column('player_two', sa.Text(), nullable=False),
            sa.Column('score_one', sa.Integer(), nullable=False),
            sa.Column('score_two', sa.Integer(), nullable=False),
            sa.Column('confirmed_by', sa.Text(), nullable=True),
            sa.Column('created_at', sa.Integer(), nullable=False),
            sa.Column('active', sa.Integer(), server_default='1', nullable=False),
            sa.Column('dispute_user', sa.Text(), nullable=True),
            sa.Column('dispute_reason', sa.Text(), nullable=True),
            sa.Column('tournament_match_id', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['player_one'], ['users.id']),
            sa.ForeignKeyConstraint(['player_two'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        tables.add('matches')
    elif 'tournament_match_id' not in {column['name'] for column in sa.inspect(bind).get_columns('matches')}:
        op.add_column('matches', sa.Column('tournament_match_id', sa.Text(), nullable=True))

    if 'requests' not in tables:
        op.create_table(
            'requests',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('token', sa.Text(), nullable=False),
            sa.Column('requester', sa.Text(), nullable=False),
            sa.Column('opponent', sa.Text(), nullable=False),
            sa.Column('score_requester', sa.Integer(), nullable=False),
            sa.Column('score_opponent', sa.Integer(), nullable=False),
            sa.Column('status', sa.Text(), server_default='pending', nullable=False),
            sa.Column('notified', sa.Integer(), server_default='0', nullable=False),
            sa.Column('created_at', sa.Integer(), nullable=False),
            sa.Column('resolved_at', sa.Integer(), nullable=True),
            sa.Column('tournament_match_id', sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(['opponent'], ['users.id']),
            sa.ForeignKeyConstraint(['requester'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        tables.add('requests')
    elif 'tournament_match_id' not in {column['name'] for column in sa.inspect(bind).get_columns('requests')}:
        op.add_column('requests', sa.Column('tournament_match_id', sa.Text(), nullable=True))

    if 'oidc_identities' not in tables:
        op.create_table(
            'oidc_identities',
            sa.Column('issuer', sa.Text(), nullable=False),
            sa.Column('subject', sa.Text(), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('created_at', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('issuer', 'subject'),
        )
        tables.add('oidc_identities')

    if 'account_link_tokens' not in tables:
        op.create_table(
            'account_link_tokens',
            sa.Column('token_hash', sa.Text(), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('issuer', sa.Text(), nullable=False),
            sa.Column('subject', sa.Text(), nullable=False),
            sa.Column('claims_json', sa.Text(), nullable=False),
            sa.Column('expires_at', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('token_hash'),
        )
        tables.add('account_link_tokens')

    if 'oidc_sessions' not in tables:
        op.create_table(
            'oidc_sessions',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('id_token', sa.Text(), nullable=True),
            sa.Column('created_at', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        tables.add('oidc_sessions')

    if 'tournaments' not in tables:
        op.create_table(
            'tournaments',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('name', sa.Text(), nullable=False),
            sa.Column('description', sa.Text(), server_default='', nullable=False),
            sa.Column('registration_deadline', sa.Integer(), nullable=False),
            sa.Column('start_at', sa.Integer(), nullable=False),
            sa.Column('max_players', sa.Integer(), nullable=False),
            sa.Column('status', sa.Text(), server_default='registration', nullable=False),
            sa.Column('champion_id', sa.Text(), nullable=True),
            sa.Column('created_by', sa.Text(), nullable=False),
            sa.Column('created_at', sa.Integer(), nullable=False),
            sa.Column('completed_at', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['champion_id'], ['users.id']),
            sa.ForeignKeyConstraint(['created_by'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        tables.add('tournaments')

    if 'tournament_participants' not in tables:
        op.create_table(
            'tournament_participants',
            sa.Column('tournament_id', sa.Text(), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('seed', sa.Integer(), nullable=True),
            sa.Column('losses', sa.Integer(), server_default='0', nullable=False),
            sa.Column('eliminated', sa.Integer(), server_default='0', nullable=False),
            sa.Column('joined_at', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['tournament_id'], ['tournaments.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('tournament_id', 'user_id'),
        )
        tables.add('tournament_participants')

    if 'tournament_matches' not in tables:
        op.create_table(
            'tournament_matches',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('tournament_id', sa.Text(), nullable=False),
            sa.Column('sequence', sa.Integer(), nullable=False),
            sa.Column('stage', sa.Text(), nullable=False),
            sa.Column('round_number', sa.Integer(), nullable=False),
            sa.Column('position', sa.Integer(), nullable=False),
            sa.Column('player_one', sa.Text(), nullable=False),
            sa.Column('player_two', sa.Text(), nullable=True),
            sa.Column('winner', sa.Text(), nullable=True),
            sa.Column('loser', sa.Text(), nullable=True),
            sa.Column('status', sa.Text(), server_default='pending', nullable=False),
            sa.Column('result_match_id', sa.Text(), nullable=True),
            sa.Column('created_at', sa.Integer(), nullable=False),
            sa.Column('completed_at', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['loser'], ['users.id']),
            sa.ForeignKeyConstraint(['player_one'], ['users.id']),
            sa.ForeignKeyConstraint(['player_two'], ['users.id']),
            sa.ForeignKeyConstraint(['result_match_id'], ['matches.id']),
            sa.ForeignKeyConstraint(['tournament_id'], ['tournaments.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['winner'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )

    index_names = {
        row[0] for row in bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name IS NOT NULL"))
    }
    if 'idx_matches_created' not in index_names:
        op.create_index('idx_matches_created', 'matches', ['created_at'], unique=False)
    if 'idx_users_login' not in index_names:
        op.create_index('idx_users_login', 'users', ['login'], unique=True)
    if 'idx_requests_token' not in index_names:
        op.create_index('idx_requests_token', 'requests', ['token'], unique=True)
    if 'idx_requests_opponent_status' not in index_names:
        op.create_index('idx_requests_opponent_status', 'requests', ['opponent', 'status'], unique=False)
    if 'idx_tournament_participants_tournament' not in index_names:
        op.create_index(
            'idx_tournament_participants_tournament', 'tournament_participants',
            ['tournament_id', 'eliminated', 'losses'], unique=False,
        )
    if 'idx_tournament_matches_tournament_sequence' not in index_names:
        op.create_index(
            'idx_tournament_matches_tournament_sequence', 'tournament_matches',
            ['tournament_id', 'sequence', 'position'], unique=False,
        )
    if 'idx_users_email' not in index_names:
        op.execute("CREATE UNIQUE INDEX idx_users_email ON users(lower(email)) WHERE email IS NOT NULL AND email != ''")
    if 'idx_requests_tournament_pending' not in index_names:
        op.execute(
            "CREATE UNIQUE INDEX idx_requests_tournament_pending ON requests(tournament_match_id) "
            "WHERE tournament_match_id IS NOT NULL AND status='pending'"
        )
    op.execute("PRAGMA optimize")


def downgrade():
    # This baseline can be applied to databases that existed before Alembic.
    # A destructive downgrade would erase school accounts and match history,
    # so rollback is intentionally performed by restoring a database backup.
    pass
