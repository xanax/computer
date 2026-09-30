"""unify tasks: automation + run history into jobs

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-22

Phase 4 of notes/NOTES-myriad-job-queue.md, and the storage half of
``notes/NOTES-unify-tasks.md``.

Turns the four places a task could live into one:

| was | becomes |
|---|---|
| ``workspace_todos`` | ``jobs`` (already, migration 0009) |
| ``automations`` | ``jobs``, ``trigger='rrule'``, ``executor=<model_id>`` |
| ``automation_runs`` | ``jobs``, ``kind='run'``, ``parent_job=<template>`` |
| chat ``meta.timer_at`` | ``jobs``, ``trigger='at'``, ``parent_chat`` (code, Phase 5) |

Two shape changes make it fit:

1. ``parent_job`` — a recurring task is a *template* that stays ``open`` for
   ever, and each firing is a child ``kind='run'`` row. Without the split one
   row would be both a schedule and a result, so ticking off today's brief
   would destroy the schedule.
2. ``status='paused'`` — how a recurring task is switched off. It keeps its
   ``rrule`` and its history; it just never fires.

Non-destructive, and ids are preserved on both sides: an automation keeps its
id as a job, so existing webhook URLs (``/api/automations/<id>/run?token=…``)
and any bookmarked run links keep resolving. ``automations`` and
``automation_runs`` are left populated and are kept in lockstep for one
release, so a rollback lands on a working table.
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Shape ────────────────────────────────────────────
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("parent_job", sa.Text(), nullable=True))
        batch.create_index("ix_jobs_parent_job", ["parent_job", "created_at"], unique=False)

    # ── 2. automations → recurring task templates ────────────
    # `next_run_at` and `trigger_at` are both epoch **nanoseconds**; nothing is
    # converted. A paused automation becomes a paused template, which is what
    # stops it firing once the job scheduler owns it.
    #
    # `created_at` is a different story: the job queue stamps rows in
    # **milliseconds** (``int(time.time() * 1000)``, the same as `chats`) while
    # `automations` and `automation_runs` stamped theirs in nanoseconds. Both
    # sides land in one column here, so the nanoseconds are divided down: a
    # `jobs` row mixing the two units would sort decades out of place.
    op.execute(
        sa.text(
            """
            INSERT INTO jobs (
                id, user_id, workspace, resource, title, kind, executor, trigger,
                trigger_at, rrule, deadline, payload, status, priority, depends_on,
                attempts, last_error, parent_chat, parent_job, source, origin_chat,
                meta, created_at, updated_at
            )
            SELECT
                a.id, a.user_id, a.workspace, '', a.name, 'task', a.model_id, 'rrule',
                a.next_run_at, a.rrule, NULL, a.prompt,
                CASE WHEN a.is_active THEN 'open' ELSE 'paused' END,
                0, NULL, 0, NULL, NULL, NULL, 'human', NULL,
                json_patch(
                    COALESCE(a.meta, '{}'),
                    json_object('legacy', 'automations', 'is_active', a.is_active)
                ),
                CAST(a.created_at / 1000000 AS INTEGER),
                CAST(a.updated_at / 1000000 AS INTEGER)
            FROM automations a
            WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id = a.id)
            """
        )
    )

    # ── 3. automation_runs → run rows ────────────────────────
    # The old runs were fire-and-forget, so `success` maps to `done`: that is
    # what it meant. Runs created from here on end `needs_review` instead, which
    # is a real change in behaviour, not a re-labelling.
    op.execute(
        sa.text(
            """
            INSERT INTO jobs (
                id, user_id, workspace, resource, title, kind, executor, trigger,
                trigger_at, rrule, deadline, payload, status, priority, depends_on,
                attempts, last_error, parent_chat, parent_job, source, origin_chat,
                meta, created_at, updated_at
            )
            SELECT
                r.id, t.user_id, t.workspace, '', t.title, 'run', t.executor, 'at',
                NULL, NULL, NULL, t.payload,
                CASE WHEN r.status = 'success' THEN 'done' ELSE 'failed' END,
                0, NULL, 1, r.error, NULL, r.automation_id, 'human', NULL,
                json_object(
                    'legacy', 'automation_runs',
                    'automation_id', r.automation_id,
                    'chat_id', r.chat_id
                ),
                CAST(r.created_at / 1000000 AS INTEGER),
                CAST(r.created_at / 1000000 AS INTEGER)
            FROM automation_runs r
            JOIN jobs t ON t.id = r.automation_id
            WHERE NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id = r.id)
            """
        )
    )


def downgrade() -> None:
    # The backfill is a copy: dropping the columns loses only rows created after
    # this migration. `automations` and `automation_runs` are untouched.
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_parent_job")
        batch.drop_column("parent_job")
