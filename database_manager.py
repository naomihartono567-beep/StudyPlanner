# database_manager.py
import os
import sqlite3
from datetime import datetime

DATABASE_NAME = 'study_planner.db'

# Database path
_DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), DATABASE_NAME)
DATABASE_PATH = os.environ.get('STUDY_PLANNER_DB_PATH', _DEFAULT_DB_PATH)

# Connection
def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

# Schema setup
def initialize_db():
    conn = get_db_connection()
    c = conn.cursor()

    # users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );
    """)

    # subjects
    c.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            teacher TEXT,
            color_tag TEXT,
            notes TEXT DEFAULT '',
            short_note TEXT DEFAULT '',
            key_topics TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)

    # tasks
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_id INTEGER,
            name TEXT NOT NULL,
            due_date TEXT NOT NULL,
            required_time REAL NOT NULL,
            priority_weight INTEGER NOT NULL,
            status TEXT DEFAULT 'PENDING',
            key_topics TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(subject_id) REFERENCES subjects(id)
        );
    """)

    # schedule blocks
    c.execute("""
        CREATE TABLE IF NOT EXISTS schedule_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER,
            activity_name TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            is_fixed INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        );
    """)

    # subject notes (multiple notes per subject)
    c.execute("""
        CREATE TABLE IF NOT EXISTS subject_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(subject_id) REFERENCES subjects(id)
        );
    """)

    conn.commit()
    conn.close()

def migrate_db():
    """Best-effort migrations (adds missing columns/tables)."""
    conn = get_db_connection()
    c = conn.cursor()
    
    try:
        # Add notes column if missing
        c.execute("ALTER TABLE subjects ADD COLUMN notes TEXT DEFAULT '';")
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists
        pass

    try:
        # Add short_note column to subjects table if it doesn't exist
        c.execute("ALTER TABLE subjects ADD COLUMN short_note TEXT DEFAULT '';")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        # Add key_topics column to subjects table if it doesn't exist
        c.execute("ALTER TABLE subjects ADD COLUMN key_topics TEXT DEFAULT '';")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    try:
        # Add is_recurring to tasks if missing
        c.execute("ALTER TABLE tasks ADD COLUMN is_recurring INTEGER DEFAULT 0;")
        conn.commit()
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
    try:
        # Add is_recurring to schedule_blocks if missing
        c.execute("ALTER TABLE schedule_blocks ADD COLUMN is_recurring INTEGER DEFAULT 0;")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    try:
        c.execute("ALTER TABLE schedule_blocks ADD COLUMN recurrence_pattern TEXT DEFAULT 'once';")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE schedule_blocks ADD COLUMN notes TEXT DEFAULT '';")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE tasks ADD COLUMN completion_date TEXT DEFAULT NULL;")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS subject_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subject_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(subject_id) REFERENCES subjects(id)
            );
        """)
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    conn.close()

# Run migrations once at import time (fail-safe)
try:
    migrate_db()
except Exception:
    pass

# Subjects
def fetch_subjects(user_id):
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, teacher, color_tag, notes, short_note, key_topics FROM subjects WHERE user_id = ? ORDER BY name COLLATE NOCASE",
            (user_id,)
        ).fetchall()
    except sqlite3.OperationalError:
        # Handle case where notes column doesn't exist in older database
        rows = conn.execute(
            "SELECT id, name, teacher, color_tag FROM subjects WHERE user_id = ? ORDER BY name COLLATE NOCASE",
            (user_id,)
        ).fetchall()
    conn.close()
    results = []
    for r in rows:
        row_dict = dict(r)
        if 'notes' not in row_dict:
            row_dict['notes'] = ''
        if 'short_note' not in row_dict:
            row_dict['short_note'] = ''
        if 'key_topics' not in row_dict:
            row_dict['key_topics'] = ''
        results.append(row_dict)
    return results

def insert_subject(user_id, name, teacher="", color_tag="", notes="", short_note="", key_topics=""):
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO subjects (user_id, name, teacher, color_tag, notes, short_note, key_topics) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, name, teacher, color_tag, notes, short_note, key_topics)
        )
    except sqlite3.OperationalError:
        # Handle older databases: try progressively simpler inserts.
        try:
            conn.execute(
                "INSERT INTO subjects (user_id, name, teacher, color_tag, notes) VALUES (?, ?, ?, ?, ?)",
                (user_id, name, teacher, color_tag, notes)
            )
        except sqlite3.OperationalError:
            conn.execute(
                "INSERT INTO subjects (user_id, name, teacher, color_tag) VALUES (?, ?, ?, ?)",
                (user_id, name, teacher, color_tag)
            )
    conn.commit()
    conn.close()

def get_subject_by_id(subject_id, user_id):
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, user_id, name, teacher, color_tag, notes, short_note, key_topics FROM subjects WHERE id = ? AND user_id = ?",
            (subject_id, user_id)
        ).fetchone()
    except sqlite3.OperationalError:
        # Handle case where notes column doesn't exist in older database
        row = conn.execute(
            "SELECT id, user_id, name, teacher, color_tag FROM subjects WHERE id = ? AND user_id = ?",
            (subject_id, user_id)
        ).fetchone()
    conn.close()
    if row:
        row_dict = dict(row)
        if 'notes' not in row_dict:
            row_dict['notes'] = ''
        if 'short_note' not in row_dict:
            row_dict['short_note'] = ''
        if 'key_topics' not in row_dict:
            row_dict['key_topics'] = ''
        return row_dict
    return None

def update_subject(subject_id, user_id, name=None, teacher=None, color_tag=None, notes=None, short_note=None, key_topics=None):
    conn = get_db_connection()
    
    updates = []
    params = []
    
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if teacher is not None:
        updates.append("teacher = ?")
        params.append(teacher)
    if color_tag is not None:
        updates.append("color_tag = ?")
        params.append(color_tag)
    
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)

    if short_note is not None:
        updates.append("short_note = ?")
        params.append(short_note)

    if key_topics is not None:
        updates.append("key_topics = ?")
        params.append(key_topics)

    if updates:
        params.extend([subject_id, user_id])
        sql = f"UPDATE subjects SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
        
        try:
            conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            err = str(e)
            if any(col in err for col in ["notes", "short_note", "key_topics"]):
                updates = []
                params = []
                if name is not None:
                    updates.append("name = ?")
                    params.append(name)
                if teacher is not None:
                    updates.append("teacher = ?")
                    params.append(teacher)
                if color_tag is not None:
                    updates.append("color_tag = ?")
                    params.append(color_tag)
                if notes is not None and "notes" not in err:
                    updates.append("notes = ?")
                    params.append(notes)

                if updates:
                    params.extend([subject_id, user_id])
                    sql = f"UPDATE subjects SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
                    conn.execute(sql, params)
            else:
                raise
        
        conn.commit()
    
    conn.close()

def delete_subject(subject_id, user_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM tasks WHERE subject_id = ? AND user_id = ?", (subject_id, user_id))
    conn.execute("DELETE FROM subjects WHERE id = ? AND user_id = ?", (subject_id, user_id))
    conn.commit()
    conn.close()

def delete_task(task_id, user_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM schedule_blocks WHERE task_id = ? AND user_id = ?", (task_id, user_id))
    conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
    conn.commit()
    conn.close()

def delete_schedule_block(block_id, user_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM schedule_blocks WHERE id = ? AND user_id = ?", (block_id, user_id))
    conn.commit()
    conn.close()


# Subject note helpers

def fetch_subject_notes(user_id, subject_id):
    conn = get_db_connection()
    rows = conn.execute(
        """SELECT id, user_id, subject_id, title, content, created_at, updated_at
           FROM subject_notes
           WHERE user_id = ? AND subject_id = ?
           ORDER BY updated_at DESC, created_at DESC, id DESC""",
        (user_id, subject_id)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_subject_note_by_id(note_id, user_id):
    conn = get_db_connection()
    row = conn.execute(
        """SELECT id, user_id, subject_id, title, content, created_at, updated_at
           FROM subject_notes
           WHERE id = ? AND user_id = ?""",
        (note_id, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def insert_subject_note(user_id, subject_id, title, content=""):
    conn = get_db_connection()
    conn.execute(
        """INSERT INTO subject_notes (user_id, subject_id, title, content, created_at, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (user_id, subject_id, title, content)
    )
    conn.commit()
    conn.close()


def update_subject_note(note_id, user_id, title=None, content=None):
    updates = []
    params = []
    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if content is not None:
        updates.append("content = ?")
        params.append(content)

    if not updates:
        return

    updates.append("updated_at = datetime('now')")
    conn = get_db_connection()
    params.extend([note_id, user_id])
    sql = f"UPDATE subject_notes SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def delete_subject_note(note_id, user_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM subject_notes WHERE id = ? AND user_id = ?", (note_id, user_id))
    conn.commit()
    conn.close()

def get_schedule_block_by_id(block_id, user_id):
    """Get one schedule block."""
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM schedule_blocks WHERE id = ? AND user_id = ?",
        (block_id, user_id)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def delete_all_recurring_activities(user_id, activity_name, recurrence_pattern):
    conn = get_db_connection()
    cursor = conn.execute(
        "DELETE FROM schedule_blocks WHERE user_id = ? AND activity_name = ? AND recurrence_pattern = ?",
        (user_id, activity_name, recurrence_pattern)
    )
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count

def delete_flexible_schedule_blocks(user_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM schedule_blocks WHERE user_id = ? AND is_fixed = 0", (user_id,))
    conn.commit()
    conn.close()

def reset_user_data(user_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM schedule_blocks WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM subjects WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# Task helpers

def insert_task(user_id, subject_id, name, due_date, required_time, priority_weight, is_recurring=0):
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO tasks (user_id, subject_id, name, due_date, required_time, priority_weight, is_recurring) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, subject_id, name, due_date, required_time, priority_weight, is_recurring)
        )
    except sqlite3.OperationalError:
        conn.execute(
            "INSERT INTO tasks (user_id, subject_id, name, due_date, required_time, priority_weight) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, subject_id, name, due_date, required_time, priority_weight)
        )
    conn.commit()
    conn.close()

def fetch_tasks(user_id, status="PENDING"):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE user_id = ? AND status = ? ORDER BY due_date ASC",
        (user_id, status)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_status(task_id, new_status):
    conn = get_db_connection()
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, task_id))
    conn.commit()
    conn.close()

def handle_task_completion(task_id):
    """Mark task completed; recurring tasks are moved forward."""
    from datetime import timedelta
    
    conn = get_db_connection()
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    
    if not task:
        conn.close()
        return None
    
    task_dict = dict(task)
    
    is_recurring = task_dict.get('is_recurring', 0)
    
    if is_recurring:
        try:
            current_due = datetime.strptime(task_dict['due_date'], '%Y-%m-%d')
            new_due = current_due + timedelta(days=7)
            new_due_str = new_due.strftime('%Y-%m-%d')
            
            conn.execute(
                "UPDATE tasks SET due_date = ?, status = 'PENDING' WHERE id = ?",
                (new_due_str, task_id)
            )
            conn.commit()
            task_dict['due_date'] = new_due_str
            task_dict['status'] = 'PENDING'
        except Exception as e:
            print(f"Error resetting recurring task {task_id}: {e}")
            conn.close()
            return None
    else:
        from datetime import date
        completion_str = date.today().isoformat()
        try:
            conn.execute("UPDATE tasks SET status = 'COMPLETED', completion_date = ? WHERE id = ?", (completion_str, task_id))
        except sqlite3.OperationalError:
            conn.execute("UPDATE tasks SET status = 'COMPLETED' WHERE id = ?", (task_id,))
        conn.commit()
        task_dict['status'] = 'COMPLETED'
        task_dict['completion_date'] = completion_str
    
    conn.close()
    return task_dict

def get_task_by_id(task_id):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def fetch_recent_tasks(user_id, limit=5):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_pending_tasks_ordered(user_id):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE user_id = ? AND status = 'PENDING' ORDER BY due_date ASC, priority_weight DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_progress(subject_id, freed_time):
    """Placeholder for subject-based progress."""
    return

# Schedule block helpers

def insert_schedule_block(user_id, task_id, activity_name, start_time, end_time, is_fixed=0, is_recurring=0, recurrence_pattern='once', notes=''):
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO schedule_blocks (user_id, task_id, activity_name, start_time, end_time, is_fixed, is_recurring, recurrence_pattern, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, task_id, activity_name, start_time, end_time, is_fixed, is_recurring, recurrence_pattern, notes))
    conn.commit()
    conn.close()

def fetch_all_schedule_blocks(user_id):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM schedule_blocks WHERE user_id = ? ORDER BY start_time ASC",
        (user_id,)
    ).fetchall()
    conn.close()

    blocks = []
    for r in rows:
        block = dict(r)
        try:
            block['start_time'] = datetime.fromisoformat(block['start_time'])
            block['end_time'] = datetime.fromisoformat(block['end_time'])
        except:
            pass
        blocks.append(block)
    return blocks

def get_fixed_activities(user_id):
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM schedule_blocks WHERE user_id = ? AND is_fixed = 1 ORDER BY start_time",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_schedule_blocks_by_activity(user_id, activity_name, recurrence_pattern):
    """Get schedule blocks for an activity + recurrence pattern."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM schedule_blocks WHERE user_id = ? AND activity_name = ? AND recurrence_pattern = ? ORDER BY start_time",
        (user_id, activity_name, recurrence_pattern)
    ).fetchall()
    conn.close()

    results = []
    for r in rows:
        row = dict(r)
        try:
            row['start_time'] = datetime.fromisoformat(row['start_time'])
            row['end_time'] = datetime.fromisoformat(row['end_time'])
        except Exception:
            # keep original string if parsing fails
            pass
        results.append(row)
    return results

def update_task(task_id, user_id, name=None, due_date=None, required_time=None, priority_weight=None, subject_id=None):
    """Update a task (only fields that aren’t None)."""
    conn = get_db_connection()
    updates = []
    params = []
    
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if due_date is not None:
        updates.append("due_date = ?")
        params.append(due_date)
    if required_time is not None:
        updates.append("required_time = ?")
        params.append(required_time)
    if priority_weight is not None:
        updates.append("priority_weight = ?")
        params.append(priority_weight)
    updates.append("subject_id = ?")
    params.append(subject_id)
    
    if not updates:
        conn.close()
        return
    
    params.append(task_id)
    params.append(user_id)
    
    query = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
    conn.execute(query, params)
    conn.commit()
    conn.close()

def update_schedule_block(block_id, user_id, activity_name=None, start_time=None, end_time=None, notes=None):
    """Update a schedule block (only fields that aren’t None)."""
    conn = get_db_connection()
    updates = []
    params = []

    if activity_name is not None:
        updates.append("activity_name = ?")
        params.append(activity_name)
    if start_time is not None:
        updates.append("start_time = ?")
        params.append(start_time)
    if end_time is not None:
        updates.append("end_time = ?")
        params.append(end_time)
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)

    if not updates:
        conn.close()
        return

    params.append(block_id)
    params.append(user_id)

    query = f"UPDATE schedule_blocks SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
    conn.execute(query, params)
    conn.commit()
    conn.close()