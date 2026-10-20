"""Regression for finalizing a run while its snapshot insert is uncommitted."""

import psycopg
import pytest
from test_storage import SCHEMA, _running, _snapshot
from test_storage import prepared_db as prepared_db


def test_snapshot_insert_blocks_finalization_until_commit(prepared_db):
    db, ids = prepared_db
    run_id = _running(db, ids)
    with psycopg.connect(db.info.dsn, autocommit=True) as finalizer:
        finalizer.execute("SET lock_timeout='250ms'")
        with db.transaction():
            _snapshot(db, ids, run_id)
            with pytest.raises(psycopg.errors.LockNotAvailable):
                finalizer.execute(
                    f"UPDATE {SCHEMA}.sync_runs SET status='succeeded',"
                    "finished_at=clock_timestamp() WHERE id=%s",
                    (run_id,),
                )
        finalizer.execute(
            f"UPDATE {SCHEMA}.sync_runs SET status='succeeded',finished_at=clock_timestamp() WHERE id=%s",
            (run_id,),
        )
        assert finalizer.execute(
            f"SELECT status FROM {SCHEMA}.sync_runs WHERE id=%s", (run_id,)
        ).fetchone() == ("succeeded",)


def test_snapshot_waits_for_uncommitted_finalization_then_rejects_terminal_run(prepared_db):
    db, ids = prepared_db
    run_id = _running(db, ids)
    with psycopg.connect(db.info.dsn, autocommit=True) as writer:
        writer.execute("SET lock_timeout='250ms'")
        with db.transaction():
            db.execute(
                f"UPDATE {SCHEMA}.sync_runs SET status='succeeded',finished_at=clock_timestamp() WHERE id=%s",
                (run_id,),
            )
            with pytest.raises(psycopg.errors.LockNotAvailable):
                _snapshot(writer, ids, run_id)
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            _snapshot(writer, ids, run_id)
