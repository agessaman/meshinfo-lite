import logging


def migrate(db):
    """
    Add ts_created indexes used by retention cleanup and recent-first queries.

    Without these, pruning meshlog and telemetry (and paging chat by time)
    required full table scans.
    """
    indexes = [
        ("idx_meshlog_ts_created", "meshlog"),
        ("idx_telemetry_ts_created", "telemetry"),
        ("idx_text_ts_created", "text"),
    ]
    cursor = None
    try:
        cursor = db.cursor()
        for name, table in indexes:
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {name} ON {table} (ts_created)"
            )
            logging.info(f"Ensured index {name} on {table}(ts_created)")
        db.commit()
    except Exception as e:
        logging.error(f"Error during retention index migration: {e}")
        db.rollback()
        raise
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
