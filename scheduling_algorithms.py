# scheduling_algorithms.py
from datetime import datetime, timedelta
import database_manager as db

# Helpers

def get_days_until_due(due_date):
    """Return non-negative days remaining until a due date."""
    today = datetime.today()
    if isinstance(due_date, datetime):
        delta = (due_date.date() - today.date()).days
    else:
        delta = 0
    return max(0, delta)

def selection_sort_tasks(task_list):
    """Sort task entries (dicts) by SCORE descending."""
    N = len(task_list)
    for i in range(N - 1):
        max_index = i
        for j in range(i + 1, N):
            if task_list[j]['SCORE'] > task_list[max_index]['SCORE']:
                max_index = j
        if max_index != i:
            task_list[i], task_list[max_index] = task_list[max_index], task_list[i]
    return task_list

def task_prioritization(task_records):
    """Compute a priority score for each task and return sorted tasks."""
    MAX_URGENCY_DAYS = 90
    PRIORITY_FACTOR = 10
    task_priority_list = []
    for task in task_records:
        days_remaining = get_days_until_due(task.get('due_date', datetime.now()))
        urgency_score = MAX_URGENCY_DAYS - days_remaining
        priority_weight = int(task.get('priority_weight', 1))
        total_priority_score = (urgency_score * PRIORITY_FACTOR) + priority_weight
        task_priority_list.append({
            'TASK_DETAIL': task,
            'SCORE': total_priority_score
        })
    return selection_sort_tasks(task_priority_list)

def check_for_conflict(user_id, new_start, new_end):
    """Return True if the time range does not overlap existing schedule blocks."""
    blocks = db.fetch_all_schedule_blocks(user_id)
    for b in blocks:
        start = b['start_time']
        end = b['end_time']
        if new_start < end and new_end > start:
            return False
    return True

def calculate_free_time(user_id, fixed_activities, days_window=7):
    """Find free time slots in the next N days (08:00–22:00), excluding fixed activities."""
    now = datetime.now()
    free_slots = []
    for d in range(1, days_window + 1):
        day = (now + timedelta(days=d)).replace(hour=8, minute=0, second=0, microsecond=0)
        window_start = day
        window_end = day.replace(hour=22)
        available = [{'start_time': window_start, 'end_time': window_end}]
        for fa in fixed_activities:
            try:
                fa_start = datetime.fromisoformat(fa['start_time'])
                fa_end = datetime.fromisoformat(fa['end_time'])
            except Exception:
                continue
            new_available = []
            for a in available:
                a_start = a['start_time']
                a_end = a['end_time']
                if fa_end <= a_start or fa_start >= a_end:
                    new_available.append(a)
                else:
                    if fa_start > a_start:
                        new_available.append({'start_time': a_start, 'end_time': fa_start})
                    if fa_end < a_end:
                        new_available.append({'start_time': fa_end, 'end_time': a_end})
            available = new_available
        for a in available:
            duration_hours = (a['end_time'] - a['start_time']).total_seconds() / 3600.0
            # Ignore very tiny slots
            if duration_hours >= 0.25:
                free_slots.append({'start_time': a['start_time'], 'duration': duration_hours})
    return free_slots

# Scheduling

def allocate_time_slots(user_id, prioritized_task_list):
    """Create flexible schedule blocks for prioritized tasks."""
    fixed_activities = db.get_fixed_activities(user_id)
    available_slots = calculate_free_time(user_id, fixed_activities)
    if not available_slots:
        return
    for entry in prioritized_task_list:
        task = entry['TASK_DETAIL']
        required = float(task.get('required_time', 0))
        task_id = task.get('id')
        task_name = task.get('name', 'Task')
        remaining = required
        for slot in available_slots:
            if remaining <= 0:
                break
            allocation = min(remaining, slot['duration'])
            if allocation <= 0:
                continue
            start_time = slot['start_time']
            end_time = start_time + timedelta(hours=allocation)
            db.insert_schedule_block(user_id, task_id, task_name, start_time.isoformat(), end_time.isoformat(), is_fixed=0)
            remaining -= allocation
            slot['duration'] -= allocation
            slot['start_time'] = end_time
        if remaining > 0:
            db.update_status(task_id, "INSUFFICIENT_TIME_WARNING")

def dynamic_rescheduling(completed_task_id, user_id=None):
    """Mark a task completed and update subject progress."""
    task = db.get_task_by_id(completed_task_id)
    if not task:
        return
    if task.get('status') == "COMPLETED":
        return
    db.update_status(completed_task_id, "COMPLETED")
    freed = float(task.get('required_time', 0))
    db.update_progress(task.get('subject_id'), freed)
    return

def calculate_weekly_status(current_date, user_id):
    """Return weekly counts: completed, pending, missed (based on due date)."""
    DAYS_IN_WEEK = 7
    start_date = current_date - timedelta(days=DAYS_IN_WEEK)
    metrics = {'COMPLETED': 0, 'PENDING': 0, 'MISSED': 0}
    tasks = db.fetch_recent_tasks(user_id)
    for t in tasks:
        due = t.get('due_date', current_date)
        if isinstance(due, str):
            try:
                due = datetime.strptime(due, '%Y-%m-%d').date()
            except Exception:
                due = current_date.date()
        elif isinstance(due, datetime):
            due = due.date()
        else:
            due = current_date.date()
        
        if due < start_date.date():
            continue
        status = t.get('status', 'PENDING')
        if status == "COMPLETED":
            metrics['COMPLETED'] += 1
        else:
            if due < current_date.date():
                metrics['MISSED'] += 1
            else:
                metrics['PENDING'] += 1
    return metrics