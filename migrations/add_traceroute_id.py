def migrate(db):
    cursor = db.cursor()
    try:
        # Add traceroute_id column
        cursor.execute("""
            ALTER TABLE traceroute 
            ADD COLUMN traceroute_id BIGINT AUTO_INCREMENT PRIMARY KEY FIRST
        """)
        db.commit()
    except Exception as e:
        if "Duplicate column name" not in str(e):
            raise
    finally:
        cursor.close()