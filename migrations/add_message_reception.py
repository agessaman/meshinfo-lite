import logging
import configparser

def migrate(db):
    """
    Migrate database to add message_id and message_reception tracking
    """
    try:
        cursor = db.cursor()
        
        # Ensure we're in the correct database
        cursor.execute("SELECT DATABASE()")
        current_db = cursor.fetchone()[0]
        if not current_db:
            raise Exception("No database selected")
        
        # Check if message_id column exists in text table
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.COLUMNS 
            WHERE TABLE_NAME = 'text'
            AND COLUMN_NAME = 'message_id'
        """)
        has_message_id = cursor.fetchone()[0] > 0

        if not has_message_id:
            logging.info("Adding message_id column to text table...")
            cursor.execute("""
                ALTER TABLE text
                ADD COLUMN message_id BIGINT,
                ADD INDEX idx_text_message_id (message_id)
            """)
            db.commit()
            logging.info("Added message_id column successfully")

        # Check if message_reception table exists
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.TABLES 
            WHERE TABLE_NAME = 'message_reception'
        """)
        has_reception_table = cursor.fetchone()[0] > 0

        if not has_reception_table:
            logging.info("Creating message_reception table...")
            cursor.execute("""
                CREATE TABLE message_reception (
                    message_id BIGINT NOT NULL,
                    from_id BIGINT NOT NULL,
                    received_by_id BIGINT NOT NULL,
                    rx_snr FLOAT,
                    rx_rssi INT,
                    rx_time BIGINT,
                    ts_created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (message_id, received_by_id),
                    INDEX idx_message_reception_from (from_id),
                    INDEX idx_message_reception_ts (ts_created)
                )
            """)
            db.commit()
            logging.info("Created message_reception table successfully")

    except Exception as e:
        logging.error(f"Error performing migration: {e}")
        raise
    finally:
        cursor.close()
