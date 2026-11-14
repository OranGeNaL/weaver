"""identity: local accounts, refresh tokens, OIDC links, login attempts and the admin bootstrap

Revision ID: e5a2b8d1c7f4
Revises: d4e8b1c7a2f5
Create Date: 2026-12-02 12:00:00.000000

Stage 4 (`navidrome-migration-4-identity`): the Hub owns accounts. `users.nd_username`/`nd_password_enc` stay
nullable for the transition (dropped in stage 7); the `sessions` table is replaced by `refresh_tokens`. No working
instances exist, so the `username` backfill only serves development databases.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a2b8d1c7f4"
down_revision: str | None = "d4e8b1c7a2f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=128), nullable=True))
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    # development databases may hold pre-stage-4 rows: their ND name becomes the local name
    op.execute("UPDATE users SET username = nd_username WHERE username IS NULL AND nd_username IS NOT NULL")
    op.execute("UPDATE users SET username = 'user' || id WHERE username IS NULL")
    op.alter_column("users", "username", nullable=False)
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.alter_column("users", "nd_username", existing_type=sa.String(length=128), nullable=True)
    op.alter_column("users", "nd_password_enc", existing_type=sa.Text(), nullable=True)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("family_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replacement_enc", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_refresh_tokens_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_index("ix_refresh_tokens_family", "refresh_tokens", ["family_id"], unique=False)

    op.create_table(
        "oauth_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_oauth_links_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_oauth_links_issuer_subject"),
    )
    op.create_index("ix_oauth_links_user", "oauth_links", ["user_id"], unique=False)
    # the Google links of pre-stage-4 users become ordinary OIDC links
    op.execute(
        "INSERT INTO oauth_links (user_id, provider, issuer, subject, created_at) "
        "SELECT id, 'google', 'https://accounts.google.com', google_sub, now() "
        "FROM users WHERE google_sub IS NOT NULL"
    )

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempts_name_time", "login_attempts", ["username", "attempted_at"], unique=False)

    op.create_table(
        "bootstrap_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.drop_table("sessions")


def downgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)

    op.drop_table("bootstrap_state")
    op.drop_index("ix_login_attempts_name_time", table_name="login_attempts")
    op.drop_table("login_attempts")
    op.drop_index("ix_oauth_links_user", table_name="oauth_links")
    op.drop_table("oauth_links")
    op.drop_index("ix_refresh_tokens_family", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    # `nd_username`/`nd_password_enc` stay nullable on the way down: stage-4 accounts have no Navidrome
    # credentials, and forcing NOT NULL would make the rollback fail (or delete users) on any non-empty database.
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "username")
