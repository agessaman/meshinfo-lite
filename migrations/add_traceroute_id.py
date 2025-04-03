def migrate(db):
    cursor = db.cursor()
    try:
        # First check if traceroute_id exists
        cursor.execute("""
            SELECT COUNT(*) 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'traceroute' 
            AND COLUMN_NAME = 'traceroute_id'
        """)
        
        if cursor.fetchone()[0] == 0:
            # Add the primary key and metadata columns
            cursor.execute("""
                ALTER TABLE traceroute 
                ADD COLUMN traceroute_id BIGINT AUTO_INCREMENT PRIMARY KEY FIRST,
                ADD COLUMN request_id BIGINT NULL AFTER traceroute_id,
                ADD COLUMN channel INT UNSIGNED NULL AFTER to_id,
                ADD COLUMN hop_limit INT UNSIGNED NULL AFTER channel,
                ADD COLUMN success TINYINT(1) NULL AFTER hop_limit,
                ADD COLUMN time TIMESTAMP NULL
            """)
            db.commit()

        # Check and update SNR columns
        cursor.execute("""
            SELECT COUNT(*) 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'traceroute' 
            AND COLUMN_NAME = 'snr_towards'
        """)
        
        if cursor.fetchone()[0] == 0:
            # First, rename existing snr column to snr_towards if it exists
            cursor.execute("""
                ALTER TABLE traceroute 
                CHANGE COLUMN snr snr_towards TEXT NULL,
                ADD COLUMN snr_back TEXT NULL AFTER snr_towards
            """)
            db.commit()

        # Check and add route_back column
        cursor.execute("""
            SELECT COUNT(*) 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'traceroute' 
            AND COLUMN_NAME = 'route_back'
        """)
        
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                ALTER TABLE traceroute
                ADD COLUMN route_back VARCHAR(255) NULL AFTER route
            """)
            db.commit()

        print("Migration completed successfully")
        
    except Exception as e:
        print(f"Migration error: {str(e)}")
        raise
    finally:
        cursor.close()