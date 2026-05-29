import sqlite3
import os
import datetime
from typing import Dict, Any, List, Optional

class DBManager:
    def __init__(self, db_path: str = "metadata/sqlite/registry.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.initialize_db()

    def initialize_db(self):
        """Creates tables if they do not exist."""
        cursor = self.conn.cursor()
        
        # downloads table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                glacier TEXT NOT NULL,
                date TEXT NOT NULL,
                tile_id TEXT,
                status TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                filepath TEXT,
                checksum TEXT,
                last_error TEXT,
                geojson_path TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Schema migration: check if geojson_path exists, if not add it
        try:
            cursor.execute("ALTER TABLE downloads ADD COLUMN geojson_path TEXT")
        except sqlite3.OperationalError:
            # Column already exists
            pass

        # Indexing for faster checkpoint lookups
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloads_glacier_date ON downloads(glacier, date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status)")

        self.conn.commit()

    def add_task(self, source: str, glacier: str, date_str: str, tile_id: str = None, geojson_path: str = None) -> int:
        """Adds a new task to the database if it doesn't already exist."""
        cursor = self.conn.cursor()
        
        # Check if already exists
        cursor.execute(
            "SELECT id, status FROM downloads WHERE glacier = ? AND source = ? AND date = ?", 
            (glacier, source, date_str)
        )
        row = cursor.fetchone()
        
        if row:
            # If exists and status is COMPLETED or CLIPPED, preserve it.
            # Otherwise reset to PENDING if checkpoint restart is handled.
            # Also update geojson_path if it was missing previously
            if geojson_path:
                cursor.execute(
                    "UPDATE downloads SET geojson_path = ? WHERE id = ?",
                    (geojson_path, row['id'])
                )
                self.conn.commit()
            return row['id']
            
        cursor.execute("""
            INSERT INTO downloads (source, glacier, date, tile_id, status, geojson_path)
            VALUES (?, ?, ?, ?, 'PENDING', ?)
        """, (source, glacier, date_str, tile_id, geojson_path))
        self.conn.commit()
        return cursor.lastrowid

    def update_task_status(self, task_id: int, status: str, filepath: str = None, 
                           checksum: str = None, last_error: str = None, increment_retry: bool = False):
        """Updates the status and metadata of a task."""
        cursor = self.conn.cursor()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if increment_retry:
            cursor.execute("""
                UPDATE downloads 
                SET status = ?, filepath = COALESCE(?, filepath), checksum = COALESCE(?, checksum), 
                    last_error = ?, retry_count = retry_count + 1, updated_at = ?
                WHERE id = ?
            """, (status, filepath, checksum, last_error, now, task_id))
        else:
            cursor.execute("""
                UPDATE downloads 
                SET status = ?, filepath = COALESCE(?, filepath), checksum = COALESCE(?, checksum), 
                    last_error = ?, updated_at = ?
                WHERE id = ?
            """, (status, filepath, checksum, last_error, now, task_id))
            
        self.conn.commit()

    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Retrieves all tasks that are PENDING or FAILED (with retries remaining)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM downloads WHERE status IN ('PENDING', 'FAILED') ORDER BY date ASC")
        return [dict(row) for row in cursor.fetchall()]

    def reset_running_tasks(self):
        """Resets RUNNING tasks back to PENDING on startup (recovery from crash)."""
        cursor = self.conn.cursor()
        cursor.execute("UPDATE downloads SET status = 'PENDING' WHERE status = 'RUNNING'")
        self.conn.commit()

    def get_task_statistics(self) -> Dict[str, int]:
        """Gathers counts of tasks grouped by status."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT status, COUNT(*) as count FROM downloads GROUP BY status")
        rows = cursor.fetchall()
        
        stats = {
            "PENDING": 0,
            "RUNNING": 0,
            "DOWNLOADED": 0,
            "CLIPPED": 0,
            "FAILED": 0,
            "SKIPPED": 0
        }
        
        for row in rows:
            status = row['status']
            if status in stats:
                stats[status] = row['count']
                
        return stats

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """Gets all tasks in DB."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM downloads ORDER BY updated_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_tasks(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Gets recent tasks for display."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM downloads ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def clear_all(self):
        """Clear database tasks."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM downloads")
        self.conn.commit()

    def close(self):
        """Closes connection."""
        self.conn.close()
