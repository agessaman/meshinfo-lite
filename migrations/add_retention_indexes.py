import logging


def migrate(db):
    """
    Add time indexes used by retention cleanup and recent-window queries.

    Without these, pruning meshlog and telemetry, paging chat by time, and
    every rx_time window query on message_reception (zero-hop links, relay
    inference, message maps) required full table scans.
    """
    indexes = [
        ("idx_meshlog_ts_created", "meshlog", "ts_created"),
        ("idx_telemetry_ts_created", "telemetry", "ts_created"),
        ("idx_text_ts_created", "text", "ts_created"),
        ("idx_message_reception_rx_time", "message_reception", "rx_time"),
    ]
    cursor = None
    try:
        cursor = db.cursor()
        for name, table, column in indexes:
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"
            )
            logging.info(f"Ensured index {name} on {table}({column})")
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
