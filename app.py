# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
from datetime import timezone
import os
import database_manager as db
import scheduling_algorithms as sa

app = Flask(__name__)
# App config
app.secret_key = os.environ.get('SECRET_KEY', 'dev_secret_for_local_testing')

# Database setup
db.initialize_db()

def get_current_user_id():
    return session.get('user_id')

# Auth routes

@app.route('/signup', methods=('GET', 'POST'))
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        # signup form uses name="confirm" in template
        confirm = request.form.get('confirm', '')
        error = None
        if not username:
            error = 'Username required.'
        elif not password:
            error = 'Password required.'
        elif password != confirm:
            error = 'Passwords do not match.'
        else:
            conn = db.get_db_connection()
            try:
                # Check for existing username ignoring case
                existing = conn.execute('SELECT id FROM users WHERE LOWER(username) = LOWER(?)', (username,)).fetchone()
                if existing:
                    error = 'Username already exists.'
                else:
                    hashed = generate_password_hash(password)
                    conn.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, hashed))
                    conn.commit()
                    flash('Account created! Please log in.', 'success')
                    return redirect(url_for('login'))
            except Exception:
                # Unique constraint or other DB error
                error = 'Username already exists or invalid input.'
            finally:
                conn.close()
        if error:
            return render_template('signup.html', error=error)
    return render_template('signup.html')

@app.route('/login', methods=('GET', 'POST'))
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = db.get_db_connection()
        # Perform case-insensitive username lookup
        user = conn.execute('SELECT * FROM users WHERE LOWER(username) = LOWER(?)', (username,)).fetchone()
        conn.close()
        error = None
        if not user:
            # No account with that username (case-insensitive)
            error = 'No account found — please sign up or check your username/password.'
        elif not check_password_hash(user['password_hash'], password):
            # Username exists but password mismatch
            error = 'Incorrect username or password.'
        else:
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('main_schedule_view'))
        return render_template('login.html', error=error)
    return render_template('login.html')

@app.route('/logout', methods=('POST',))
def logout():
    session.clear()
    flash('Logged out', 'success')
    return redirect(url_for('login'))

# Main views

@app.route('/schedule')
def main_schedule_view():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    # Build schedule data for calendar + task list
    blocks = db.fetch_all_schedule_blocks(user_id)
    
    # Group blocks
    blocks_by_task = {}  # task_id -> list of blocks
    fixed_blocks = []  # blocks with no task_id (fixed activities)
    blocks_by_date = {}  # for calendar view
    
    for b in blocks:
    # Skip blocks that reference completed tasks
        task_id = b.get('task_id')
        if task_id:
            task = db.get_task_by_id(task_id)
            if task and task.get('status') == 'COMPLETED':
                # Don't show schedule blocks for completed tasks
                continue
        
    # Calendar view
        date_key = b['start_time'].strftime('%Y-%m-%d')
        if date_key not in blocks_by_date:
            blocks_by_date[date_key] = []
        blocks_by_date[date_key].append({
            'name': b['activity_name'],
            'start': b['start_time'].strftime('%H:%M'),
            'end': b['end_time'].strftime('%H:%M'),
            'fixed': bool(b['is_fixed'])
        })
        
    # Task list grouping
        if task_id:
            if task_id not in blocks_by_task:
                blocks_by_task[task_id] = []
            blocks_by_task[task_id].append(b)
        else:
            # Fixed activity (no task)
            fixed_blocks.append(b)
    
    # Build display blocks
    display_blocks = []
    
    # Tasks (group time slots)
    for task_id, task_blocks in blocks_by_task.items():
        # Combine all time slots for this task
        time_slots = []
        for tb in task_blocks:
            time_slots.append(f"{tb['start_time'].strftime('%Y-%m-%d %H:%M')} → {tb['end_time'].strftime('%H:%M')}")

    # Task info (use due date, not slot date)
        task = db.get_task_by_id(task_id)
        is_recurring = task.get('is_recurring', 0) if task else 0
        task_due_date = task.get('due_date') if task else None
        
        # Use the first block's data but add all time slots
        first_block = task_blocks[0]
        block_data = {
            'id': first_block['id'],
            'activity_name': first_block['activity_name'],
            'start_time': first_block['start_time'].strftime('%Y-%m-%d %H:%M'),
            'end_time': first_block['end_time'].strftime('%Y-%m-%d %H:%M'),
            'is_fixed': bool(first_block['is_fixed']),
            'task_id': first_block.get('task_id'),
            'is_recurring': bool(is_recurring),  # Add recurring flag
            'due_date': task_due_date,
            'time_slots': time_slots  # All time slots for this task
        }
        display_blocks.append(block_data)
    
    # Fixed activities (group recurring)
    recurring_map = {}  # key: (name, pattern) -> {'fb': first_block, 'occurrences': set()}
    one_time_activities = []

    for fb in fixed_blocks:
        recurrence_pattern = fb.get('recurrence_pattern', 'once')

        if recurrence_pattern in ['weekly', 'biweekly']:
            name = fb['activity_name']
            key = (name, recurrence_pattern)
            day_name = fb['start_time'].strftime('%A')
            time_slot = f"{fb['start_time'].strftime('%H:%M')} → {fb['end_time'].strftime('%H:%M')}"
            occ = f"{day_name} {time_slot}"
            if key not in recurring_map:
                recurring_map[key] = {'fb': fb, 'occurrences': []}
            if occ not in recurring_map[key]['occurrences']:
                recurring_map[key]['occurrences'].append(occ)
        else:
            one_time_activities.append(fb)

    # Recurring fixed activities
    for (name, pattern), info in recurring_map.items():
        fb = info['fb']
        occ_list = info['occurrences']
        block_data = {
            'id': fb['id'],
            'activity_name': fb['activity_name'],
            'start_time': None,  # Don't show specific date
            'end_time': None,
            'day_of_week': None,
            'time_range': None,
            'is_fixed': True,
            'task_id': None,
            'is_recurring': True,
            'recurrence_pattern': pattern,
            'time_slots': occ_list  # list like ['Mon 09:00 → 10:00', 'Wed 09:00 → 10:00']
        }
        display_blocks.append(block_data)
    
    # One-time fixed activities
    for fb in one_time_activities:
        block_data = {
            'id': fb['id'],
            'activity_name': fb['activity_name'],
            'start_time': fb['start_time'].strftime('%Y-%m-%d %H:%M'),
            'end_time': fb['end_time'].strftime('%Y-%m-%d %H:%M'),
            'day_of_week': None,
            'time_range': None,
            'is_fixed': True,
            'task_id': None,
            'is_recurring': False,
            'recurrence_pattern': 'once',
            'time_slots': [f"{fb['start_time'].strftime('%Y-%m-%d %H:%M')} → {fb['end_time'].strftime('%H:%M')}"]
        }
        display_blocks.append(block_data)
    
    import json
    blocks_json = json.dumps(blocks_by_date)
    return render_template('schedule.html', blocks=display_blocks, blocks_json=blocks_json)


@app.route('/')
def landing_view():
    # Landing page
    user_id = get_current_user_id()
    if user_id:
        return redirect(url_for('main_schedule_view'))
    return render_template('landing.html')

@app.route('/add_activity', methods=['GET', 'POST'])
def add_activity():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form.get('activity_name', 'Fixed Activity')
        notes = request.form.get('notes', '')
        recurrence_pattern = request.form.get('recurrence_pattern', 'once')
        is_recurring = 1 if recurrence_pattern in ['weekly', 'biweekly'] else 0

        # Helper to check conflicts robustly
        def check_conflict(u_id, s_dt, e_dt):
            try:
                return sa.check_for_conflict(u_id, s_dt, e_dt)
            except TypeError:
                try:
                    return sa.check_for_conflict(s_dt, e_dt)
                except Exception:
                    return True
            except Exception:
                return True

        if recurrence_pattern == 'once':
            # one-time activity: date + times
            one_date = request.form.get('one_time_date')
            start_time_only = request.form.get('start_time_only')
            end_time_only = request.form.get('end_time_only')
            if not one_date or not start_time_only or not end_time_only:
                flash('Please provide date and start/end times for the activity', 'error')
                return redirect(url_for('add_activity'))
            try:
                new_start = datetime.strptime(f"{one_date}T{start_time_only}", '%Y-%m-%dT%H:%M')
                new_end = datetime.strptime(f"{one_date}T{end_time_only}", '%Y-%m-%dT%H:%M')
            except Exception:
                flash('Invalid date/time format', 'error')
                return redirect(url_for('add_activity'))
            if new_end <= new_start:
                flash('End must be after start', 'error')
                return redirect(url_for('add_activity'))

            if not check_conflict(user_id, new_start, new_end):
                flash('Conflict with existing schedule', 'error')
                return redirect(url_for('add_activity'))

            db.insert_schedule_block(user_id, None, name, new_start.isoformat(), new_end.isoformat(), 
                                   is_fixed=1, is_recurring=0, recurrence_pattern='once', notes=notes)
            flash('Fixed activity added', 'success')
            return redirect(url_for('main_schedule_view'))

        else:
            # weekly or biweekly: recurrence period, selected weekdays, time of day
            recurrence_start_str = request.form.get('recurrence_start_date')
            recurrence_end_str = request.form.get('recurrence_end_date')
            selected_days = request.form.getlist('days')  # weekday numbers '0'..'6'
            start_time_only = request.form.get('start_time_only')
            end_time_only = request.form.get('end_time_only')

            if not recurrence_start_str or not recurrence_end_str or not start_time_only or not end_time_only:
                flash('Please provide recurrence period and start/end times', 'error')
                return redirect(url_for('add_activity'))

            try:
                recurrence_start = datetime.strptime(recurrence_start_str, '%Y-%m-%d').date()
                recurrence_end = datetime.strptime(recurrence_end_str, '%Y-%m-%d').date()
            except Exception:
                flash('Invalid recurrence dates', 'error')
                return redirect(url_for('add_activity'))

            if recurrence_end < recurrence_start:
                flash('End date must be after start date', 'error')
                return redirect(url_for('add_activity'))

            try:
                t_start = datetime.strptime(start_time_only, '%H:%M').time()
                t_end = datetime.strptime(end_time_only, '%H:%M').time()
            except Exception:
                flash('Invalid start/end time format', 'error')
                return redirect(url_for('add_activity'))

            from datetime import timedelta
            count = 0
            current_date = recurrence_start
            while current_date <= recurrence_end:
                # weekday number 0=Mon..6=Sun
                if (not selected_days) or (str(current_date.weekday()) in selected_days):
                    block_start = datetime.combine(current_date, t_start)
                    block_end = datetime.combine(current_date, t_end)
                    if block_end <= block_start:
                        # skip invalid
                        current_date = current_date + timedelta(days=1)
                        continue
                    # For biweekly, include only every other week relative to recurrence_start
                    if recurrence_pattern == 'biweekly':
                        weeks_diff = ((current_date - recurrence_start).days // 7)
                        if weeks_diff % 2 != 0:
                            current_date = current_date + timedelta(days=1)
                            continue

                    # conflict check per-block
                    if check_conflict(user_id, block_start, block_end):
                        db.insert_schedule_block(user_id, None, name, block_start.isoformat(), block_end.isoformat(), 
                                               is_fixed=1, is_recurring=1, recurrence_pattern=recurrence_pattern, notes=notes)
                        count += 1
                current_date = current_date + timedelta(days=1)

            flash(f'{recurrence_pattern.capitalize()} activity added ({count} occurrences)', 'success')
            return redirect(url_for('main_schedule_view'))
        
    return render_template('add_activity.html')

@app.route('/schedule_block/<int:block_id>/edit', methods=['GET', 'POST'])
def edit_schedule_block(block_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    
    block = db.get_schedule_block_by_id(block_id, user_id)
    if not block or block['user_id'] != user_id:
        flash('Activity not found.', 'error')
        return redirect(url_for('main_schedule_view'))
    
    if request.method == 'POST':
        name = request.form.get('activity_name')
        start_time_str = request.form.get('start_time')
        end_time_str = request.form.get('end_time')
        notes = request.form.get('notes', None)
        recurrence_pattern = request.form.get('recurrence_pattern', 'once')
        selected_days = request.form.getlist('days')
        recurrence_start_str = request.form.get('recurrence_start_date')
        recurrence_end_str = request.form.get('recurrence_end_date')
        
        try:
            new_start = datetime.fromisoformat(start_time_str)
            new_end = datetime.fromisoformat(end_time_str)
        except Exception:
            flash('Invalid date/time format', 'error')
            return redirect(url_for('edit_schedule_block', block_id=block_id))
        
        if new_start >= new_end:
            flash('Start time must be before end time', 'error')
            return redirect(url_for('edit_schedule_block', block_id=block_id))
        
        # For recurring activities, edits are always applied to the whole series
        # (single-occurrence edits are intentionally not supported to keep UX simple).
        if block['is_recurring']:
            # which occurrence did the user select on the form (optional; used only as an anchor when shifting times)
            occurrence_id = request.form.get('occurrence_id')
            update_choice = 'all'
            if update_choice == 'all':
                # If user is editing recurrence itself (weekly/biweekly/once), regenerate occurrences.
                # We treat "all" as editing the recurring series definition.
                if recurrence_pattern in ['weekly', 'biweekly']:
                    if not selected_days:
                        flash('Please select at least one day', 'error')
                        # preserve posted values
                        block.update({
                            'activity_name': name,
                            'notes': notes,
                            'recurrence_pattern': recurrence_pattern,
                            'is_recurring': True,
                            'recurrence_start_date': recurrence_start_str,
                            'recurrence_end_date': recurrence_end_str,
                            'selected_days': [int(d) for d in selected_days if str(d).isdigit()],
                            'start_time': new_start.isoformat(),
                            'end_time': new_end.isoformat(),
                        })
                        return render_template('edit_activity.html', block=block)

                    try:
                        r_start = datetime.strptime(recurrence_start_str, '%Y-%m-%d').date() if recurrence_start_str else None
                        r_end = datetime.strptime(recurrence_end_str, '%Y-%m-%d').date() if recurrence_end_str else None
                    except Exception:
                        flash('Invalid recurrence dates', 'error')
                        block.update({
                            'activity_name': name,
                            'notes': notes,
                            'recurrence_pattern': recurrence_pattern,
                            'is_recurring': True,
                            'recurrence_start_date': recurrence_start_str,
                            'recurrence_end_date': recurrence_end_str,
                            'selected_days': [int(d) for d in selected_days if str(d).isdigit()],
                            'start_time': new_start.isoformat(),
                            'end_time': new_end.isoformat(),
                        })
                        return render_template('edit_activity.html', block=block)

                    if not r_start or not r_end or r_end < r_start:
                        flash('End date must be after start date', 'error')
                        block.update({
                            'activity_name': name,
                            'notes': notes,
                            'recurrence_pattern': recurrence_pattern,
                            'is_recurring': True,
                            'recurrence_start_date': recurrence_start_str,
                            'recurrence_end_date': recurrence_end_str,
                            'selected_days': [int(d) for d in selected_days if str(d).isdigit()],
                            'start_time': new_start.isoformat(),
                            'end_time': new_end.isoformat(),
                        })
                        return render_template('edit_activity.html', block=block)

                    # Delete old series occurrences (use the original activity name/pattern for lookup)
                    old_name = block.get('activity_name')
                    old_pattern = block.get('recurrence_pattern', 'weekly')
                    old_blocks = db.get_schedule_blocks_by_activity(user_id, old_name, old_pattern)
                    for ob in old_blocks:
                        db.delete_schedule_block(ob['id'], user_id)

                    # Insert new occurrences using the submitted period/days and the submitted time-of-day.
                    t_start = new_start.time()
                    t_end = new_end.time()
                    count = 0
                    current_date = r_start
                    while current_date <= r_end:
                        if str(current_date.weekday()) in selected_days:
                            block_start = datetime.combine(current_date, t_start)
                            block_end = datetime.combine(current_date, t_end)
                            if block_end <= block_start:
                                current_date = current_date + timedelta(days=1)
                                continue
                            if recurrence_pattern == 'biweekly':
                                weeks_diff = ((current_date - r_start).days // 7)
                                if weeks_diff % 2 != 0:
                                    current_date = current_date + timedelta(days=1)
                                    continue

                            # Best-effort conflict check (mirrors add_activity)
                            ok = True
                            try:
                                ok = sa.check_for_conflict(user_id, block_start, block_end)
                            except TypeError:
                                try:
                                    ok = sa.check_for_conflict(block_start, block_end)
                                except Exception:
                                    ok = True
                            except Exception:
                                ok = True

                            if ok:
                                db.insert_schedule_block(
                                    user_id,
                                    None,
                                    name,
                                    block_start.isoformat(),
                                    block_end.isoformat(),
                                    is_fixed=1,
                                    is_recurring=1,
                                    recurrence_pattern=recurrence_pattern,
                                    notes=notes or ''
                                )
                                count += 1
                        current_date = current_date + timedelta(days=1)

                    flash(f'Updated {count} occurrences of "{name}"', 'success')
                    return redirect(url_for('main_schedule_view'))

                # If switching series back to one-time, update only the selected occurrence and drop recurrence.
                if recurrence_pattern == 'once':
                    # delete all old occurrences, keep only the selected as a one-time activity
                    old_name = block.get('activity_name')
                    old_pattern = block.get('recurrence_pattern', 'weekly')
                    old_blocks = db.get_schedule_blocks_by_activity(user_id, old_name, old_pattern)
                    for ob in old_blocks:
                        db.delete_schedule_block(ob['id'], user_id)

                    db.insert_schedule_block(
                        user_id,
                        None,
                        name,
                        new_start.isoformat(),
                        new_end.isoformat(),
                        is_fixed=1,
                        is_recurring=0,
                        recurrence_pattern='once',
                        notes=notes or ''
                    )
                    flash(f'Updated activity "{name}"', 'success')
                    return redirect(url_for('main_schedule_view'))

                # Update all occurrences of this recurring activity
                all_blocks = db.get_schedule_blocks_by_activity(user_id, block['activity_name'], block['recurrence_pattern'])

                duration = new_end - new_start

                # Determine anchor old start: use the selected occurrence if provided, otherwise fall back to the block we opened
                anchor_id = None
                try:
                    anchor_id = int(occurrence_id) if occurrence_id else block_id
                except Exception:
                    anchor_id = block_id

                anchor_block = db.get_schedule_block_by_id(anchor_id, user_id)
                if anchor_block and anchor_block.get('start_time'):
                    anchor_old_start = datetime.fromisoformat(anchor_block['start_time'])
                else:
                    anchor_old_start = datetime.fromisoformat(block['start_time'])

                for b in all_blocks:
                    # fetch each block and shift relative to the anchor occurrence
                    old_block = db.get_schedule_block_by_id(b['id'], user_id)
                    block_old_start = datetime.fromisoformat(old_block['start_time'])
                    time_diff = new_start - anchor_old_start
                    new_block_start = block_old_start + time_diff
                    new_block_end = new_block_start + duration

                    db.update_schedule_block(b['id'], user_id, activity_name=name,
                                            start_time=new_block_start.isoformat(),
                                            end_time=new_block_end.isoformat(),
                                            notes=notes)

                flash(f'Updated all {len(all_blocks)} occurrences of "{name}"', 'success')
            else:
                # update_choice is forced to 'all' for recurring activities
                pass
        else:
            # Non-recurring activity - just update it
            # If user turned a one-time activity into weekly/biweekly, generate occurrences and delete the original.
            if recurrence_pattern in ['weekly', 'biweekly']:
                if not selected_days:
                    flash('Please select at least one day', 'error')
                    block.update({
                        'activity_name': name,
                        'notes': notes,
                        'recurrence_pattern': recurrence_pattern,
                        'is_recurring': True,
                        'recurrence_start_date': recurrence_start_str,
                        'recurrence_end_date': recurrence_end_str,
                        'selected_days': [int(d) for d in selected_days if str(d).isdigit()],
                        'start_time': new_start.isoformat(),
                        'end_time': new_end.isoformat(),
                    })
                    return render_template('edit_activity.html', block=block)

                try:
                    r_start = datetime.strptime(recurrence_start_str, '%Y-%m-%d').date() if recurrence_start_str else None
                    r_end = datetime.strptime(recurrence_end_str, '%Y-%m-%d').date() if recurrence_end_str else None
                except Exception:
                    flash('Invalid recurrence dates', 'error')
                    block.update({
                        'activity_name': name,
                        'notes': notes,
                        'recurrence_pattern': recurrence_pattern,
                        'is_recurring': True,
                        'recurrence_start_date': recurrence_start_str,
                        'recurrence_end_date': recurrence_end_str,
                        'selected_days': [int(d) for d in selected_days if str(d).isdigit()],
                        'start_time': new_start.isoformat(),
                        'end_time': new_end.isoformat(),
                    })
                    return render_template('edit_activity.html', block=block)

                if not r_start or not r_end or r_end < r_start:
                    flash('End date must be after start date', 'error')
                    block.update({
                        'activity_name': name,
                        'notes': notes,
                        'recurrence_pattern': recurrence_pattern,
                        'is_recurring': True,
                        'recurrence_start_date': recurrence_start_str,
                        'recurrence_end_date': recurrence_end_str,
                        'selected_days': [int(d) for d in selected_days if str(d).isdigit()],
                        'start_time': new_start.isoformat(),
                        'end_time': new_end.isoformat(),
                    })
                    return render_template('edit_activity.html', block=block)

                # Delete the original one-time block
                db.delete_schedule_block(block_id, user_id)

                t_start = new_start.time()
                t_end = new_end.time()
                count = 0
                current_date = r_start
                while current_date <= r_end:
                    if str(current_date.weekday()) in selected_days:
                        block_start = datetime.combine(current_date, t_start)
                        block_end = datetime.combine(current_date, t_end)
                        if block_end <= block_start:
                            current_date = current_date + timedelta(days=1)
                            continue
                        if recurrence_pattern == 'biweekly':
                            weeks_diff = ((current_date - r_start).days // 7)
                            if weeks_diff % 2 != 0:
                                current_date = current_date + timedelta(days=1)
                                continue

                        ok = True
                        try:
                            ok = sa.check_for_conflict(user_id, block_start, block_end)
                        except TypeError:
                            try:
                                ok = sa.check_for_conflict(block_start, block_end)
                            except Exception:
                                ok = True
                        except Exception:
                            ok = True

                        if ok:
                            db.insert_schedule_block(
                                user_id,
                                None,
                                name,
                                block_start.isoformat(),
                                block_end.isoformat(),
                                is_fixed=1,
                                is_recurring=1,
                                recurrence_pattern=recurrence_pattern,
                                notes=notes or ''
                            )
                            count += 1
                    current_date = current_date + timedelta(days=1)

                flash(f'Updated {count} occurrences of "{name}"', 'success')
            else:
                db.update_schedule_block(block_id, user_id, activity_name=name, 
                                        start_time=new_start.isoformat(), 
                                        end_time=new_end.isoformat(),
                                        notes=notes)
                flash(f'Updated activity "{name}"', 'success')
        
        return redirect(url_for('main_schedule_view'))
    
    # GET request: show edit form with pre-filled values
    # If this is a recurring activity, gather all occurrences so we can
    # pre-fill recurrence start/end dates and which weekdays are used.
    if block.get('is_recurring'):
        try:
            occurrences = db.get_schedule_blocks_by_activity(user_id, block['activity_name'], block.get('recurrence_pattern', 'weekly'))
            if occurrences:
                # compute recurrence period (min/max dates) and selected weekdays
                    dates = [ (o['start_time'].date() if hasattr(o['start_time'], 'date') else datetime.fromisoformat(o['start_time']).date()) for o in occurrences ]
                    min_date = min(dates).isoformat()
                    max_date = max(dates).isoformat()

                    # collect unique weekdays (0=Mon..6=Sun) from occurrences
                    weekdays = set()
                    occ_entries = []
                    for o in occurrences:
                        st = o['start_time'] if hasattr(o['start_time'], 'weekday') else datetime.fromisoformat(o['start_time'])
                        et = o['end_time'] if hasattr(o['end_time'], 'weekday') else datetime.fromisoformat(o['end_time'])
                        weekdays.add(st.weekday())
                        occ_entries.append({
                            'id': o['id'],
                            'date': st.date().isoformat(),
                            'start': st.strftime('%H:%M'),
                            'end': et.strftime('%H:%M')
                        })

                    block['selected_days'] = sorted(list(weekdays))
                    block['recurrence_start_date'] = min_date
                    block['recurrence_end_date'] = max_date
                    # include occurrences so the template can render a selector
                    block['occurrences'] = occ_entries
        except Exception:
            # best-effort — fallback to whatever is on the single block
            pass

    return render_template('edit_activity.html', block=block)

@app.route('/add_task', methods=['GET', 'POST'])
def add_task():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    if request.method == 'POST':
        task_name = request.form.get('task_name')
        due_date = request.form.get('due_date')
        required_time = request.form.get('required_time')
        priority_weight = request.form.get('priority_weight', 1)
        subject_id = request.form.get('subject_id') or None
        is_recurring = 1 if request.form.get('is_recurring') else 0
        
        # normalize subject_id to int or None
        if subject_id in ('', None):
            subject_id = None
        else:
            try:
                subject_id = int(subject_id)
            except Exception:
                subject_id = None
        try:
            due = datetime.strptime(due_date, '%Y-%m-%d')
            req = float(required_time)
            pri = int(priority_weight)
        except Exception:
            flash('Invalid input types', 'error')
            subs = db.fetch_subjects(user_id)
            return render_template('add_task.html', subjects=subs, form=request.form)

        if req <= 0:
            flash('Required time cannot be zero', 'error')
            subs = db.fetch_subjects(user_id)
            return render_template('add_task.html', subjects=subs, form=request.form)
        db.insert_task(user_id, subject_id, task_name, due.date().isoformat(), req, pri, is_recurring=is_recurring)
        flash('Task added — schedule will be generated', 'success')
        return redirect(url_for('generate_schedule'))
    subs = db.fetch_subjects(user_id)
    return render_template('add_task.html', subjects=subs)

@app.route('/task/<int:task_id>/edit', methods=['GET', 'POST'])
def edit_task(task_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    
    task = db.get_task_by_id(task_id)
    if not task or task['user_id'] != user_id:
        flash('Task not found.', 'error')
        return redirect(url_for('main_schedule_view'))
    
    if request.method == 'POST':
        task_name = request.form.get('task_name')
        due_date = request.form.get('due_date')
        required_time = request.form.get('required_time')
        priority_weight = request.form.get('priority_weight', task['priority_weight'])
        subject_id = request.form.get('subject_id') or None
        
        # normalize subject_id to int or None
        if subject_id in ('', None):
            subject_id = None
        else:
            try:
                subject_id = int(subject_id)
            except Exception:
                subject_id = None
        
        try:
            due = datetime.strptime(due_date, '%Y-%m-%d')
            req = float(required_time)
            pri = int(priority_weight)
        except Exception:
            flash('Invalid input types', 'error')
            subs = db.fetch_subjects(user_id)
            return render_template('edit_task.html', task=task, subjects=subs, form=request.form)

        if req <= 0:
            flash('Required time cannot be zero', 'error')
            subs = db.fetch_subjects(user_id)
            return render_template('edit_task.html', task=task, subjects=subs, form=request.form)
        
        db.update_task(task_id, user_id, name=task_name, due_date=due.date().isoformat(), 
                      required_time=req, priority_weight=pri, subject_id=subject_id)
        flash('Task updated — schedule will be regenerated', 'success')
        return redirect(url_for('generate_schedule'))
    
    # GET request: show edit form with pre-filled values
    subs = db.fetch_subjects(user_id)
    return render_template('edit_task.html', task=task, subjects=subs)

@app.route('/generate_schedule', methods=['GET','POST'])
def generate_schedule():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    # Clear old flexible (non-fixed) schedule blocks before regenerating
    # This prevents duplication when Generate Schedule is clicked multiple times
    db.delete_flexible_schedule_blocks(user_id)
    
    # get tasks and prioritize
    tasks = db.fetch_tasks(user_id, status="PENDING")
    prioritized = sa.task_prioritization(tasks)
    # allocate time slots; allocate_time_slots expects (user_id, prioritized_list)
    try:
        sa.allocate_time_slots(user_id, prioritized)
    except TypeError:
        try:
            sa.allocate_time_slots(prioritized)
        except Exception:
            pass

    # Detect any pending tasks that still have no scheduled blocks.
    # We don't surface an "unscheduled" UI; instead we warn immediately after generation.
    try:
        conn = db.get_db_connection()
        unscheduled = conn.execute(
            """
            SELECT t.id, t.name, t.due_date
            FROM tasks t
            LEFT JOIN schedule_blocks sb
              ON sb.task_id = t.id AND sb.user_id = t.user_id
            WHERE t.user_id = ? AND t.status = 'PENDING'
            GROUP BY t.id
            HAVING COUNT(sb.id) = 0
            ORDER BY t.due_date ASC
            """,
            (user_id,)
        ).fetchall()
        conn.close()
    except Exception:
        unscheduled = []

    if unscheduled:
        names = [row[1] for row in unscheduled][:3]
        extra = len(unscheduled) - len(names)
        details = ", ".join([f'"{n}"' for n in names]) + (f" (+{extra} more)" if extra > 0 else "")
        flash(
            "Some tasks could not be scheduled (no free time available). "
            f"Not scheduled: {details}. "
            "Try reducing required hours, moving fixed activities, or adding more availability, then generate again.",
            'error'
        )
    else:
        flash('Schedule generated.', 'success')
    return redirect(url_for('main_schedule_view'))

@app.route('/complete_task/<int:task_id>', methods=['POST'])
def complete_task(task_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))

    # helper handles recurring tasks (it resets them instead of fully completing)
    completed_task = db.handle_task_completion(task_id)
    
    if completed_task is None:
        flash('Task not found.', 'error')
        return redirect(url_for('main_schedule_view'))
    
    # recurring tasks get reset, normal tasks get marked completed
    if completed_task.get('is_recurring'):
        flash(f"Task reset for next week (due {completed_task['due_date']}). Schedule will be regenerated.", 'success')
    # re-run scheduling so the calendar updates
        db.delete_flexible_schedule_blocks(user_id)
        tasks = db.fetch_tasks(user_id, status="PENDING")
        prioritized = sa.task_prioritization(tasks)
        try:
            sa.allocate_time_slots(user_id, prioritized)
        except TypeError:
            try:
                sa.allocate_time_slots(prioritized)
            except Exception:
                pass
    else:
        try:
            sa.dynamic_rescheduling(task_id)
        except TypeError:
            try:
                sa.dynamic_rescheduling(task_id, user_id=user_id)
            except Exception:
                pass
        flash('Task marked completed. Schedule adjusted.', 'success')
    
    return redirect(url_for('main_schedule_view'))

@app.route('/subjects')
def subjects_view():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    subjects = db.fetch_subjects(user_id)
    return render_template('subjects.html', subjects=subjects)

@app.route('/add_subject', methods=('GET','POST'))
def add_subject():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        teacher = request.form.get('teacher','').strip()
        color_tag = request.form.get('color_tag','').strip()
        short_note = request.form.get('short_note','').strip()
        key_topics = request.form.get('key_topics','').strip()
        legacy_notes = request.form.get('notes','').strip()
        if not key_topics and legacy_notes:
            key_topics = legacy_notes
        if not name:
            flash('Subject name required', 'error')
            return redirect(url_for('add_subject'))
        db.insert_subject(user_id, name, teacher, color_tag, notes='', short_note=short_note, key_topics=key_topics)
        flash('Subject added', 'success')
        return redirect(url_for('subjects_view'))
    return render_template('add_subject.html')

@app.route('/subject/<int:subject_id>', methods=('GET','POST'))
def subject_detail(subject_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    subj = db.get_subject_by_id(subject_id, user_id)
    if not subj:
        flash('Subject not found', 'error')
        return redirect(url_for('subjects_view'))
    # Subject fields are edited via /subject/<id>/edit; this view displays details + class notes.
    notes = db.fetch_subject_notes(user_id, subject_id)
    return render_template('subject_detail.html', subject=subj, notes=notes)


@app.route('/subject/<int:subject_id>/edit', methods=('GET','POST'))
def edit_subject(subject_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))

    subj = db.get_subject_by_id(subject_id, user_id)
    if not subj:
        flash('Subject not found', 'error')
        return redirect(url_for('subjects_view'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        teacher = request.form.get('teacher', '').strip()
        color_tag = request.form.get('color_tag', '').strip()
        short_note = request.form.get('short_note', '').strip()
        key_topics = request.form.get('key_topics', '').strip()
        legacy_notes = request.form.get('notes', '').strip()
        if not key_topics and legacy_notes:
            key_topics = legacy_notes

        if not name:
            flash('Subject name required', 'error')
            return redirect(url_for('edit_subject', subject_id=subject_id))

        db.update_subject(
            subject_id,
            user_id,
            name=name,
            teacher=teacher,
            color_tag=color_tag,
            short_note=short_note,
            key_topics=key_topics,
        )
        flash('Subject updated', 'success')
        return redirect(url_for('subject_detail', subject_id=subject_id))

    return render_template('edit_subject.html', subject=subj)

@app.route('/subject/<int:subject_id>/delete', methods=('POST',))
def delete_subject(subject_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    db.delete_subject(subject_id, user_id)
    flash('Subject and its tasks deleted', 'success')
    return redirect(url_for('subjects_view'))


@app.route('/subject/<int:subject_id>/notes/new', methods=('GET','POST'))
def add_subject_note(subject_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))

    subj = db.get_subject_by_id(subject_id, user_id)
    if not subj:
        flash('Subject not found', 'error')
        return redirect(url_for('subjects_view'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        if not title:
            flash('Note title required', 'error')
            return render_template('add_subject_note.html', subject=subj, form={'title': title, 'content': content})

        db.insert_subject_note(user_id, subject_id, title, content)
        flash('Note added', 'success')
        return redirect(url_for('subject_detail', subject_id=subject_id))

    return render_template('add_subject_note.html', subject=subj)


@app.route('/subject_note/<int:note_id>/edit', methods=('GET','POST'))
def edit_subject_note(note_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))

    note = db.get_subject_note_by_id(note_id, user_id)
    if not note:
        flash('Note not found', 'error')
        return redirect(url_for('subjects_view'))

    subj = db.get_subject_by_id(note['subject_id'], user_id)
    if not subj:
        flash('Subject not found', 'error')
        return redirect(url_for('subjects_view'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        if not title:
            flash('Note title required', 'error')
            note['title'] = title
            note['content'] = content
            return render_template('edit_subject_note.html', subject=subj, note=note)

        db.update_subject_note(note_id, user_id, title=title, content=content)
        flash('Note updated', 'success')
        return redirect(url_for('subject_detail', subject_id=subj['id']))

    return render_template('edit_subject_note.html', subject=subj, note=note)


@app.route('/subject_note/<int:note_id>/delete', methods=('POST',))
def delete_subject_note(note_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))

    note = db.get_subject_note_by_id(note_id, user_id)
    if not note:
        flash('Note not found', 'error')
        return redirect(url_for('subjects_view'))

    db.delete_subject_note(note_id, user_id)
    flash('Note deleted', 'success')
    return redirect(url_for('subject_detail', subject_id=note['subject_id']))


@app.route('/schedule_block/<int:block_id>/delete', methods=('POST',))
def delete_schedule_block_view(block_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    db.delete_schedule_block(block_id, user_id)
    flash('Schedule block deleted', 'success')
    return redirect(url_for('main_schedule_view'))


@app.route('/schedule_block/<int:block_id>/delete_all_recurring', methods=('POST',))
def delete_all_recurring_blocks_view(block_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    
    # Get the block to find its activity name and pattern
    block = db.get_schedule_block_by_id(block_id, user_id)
    if not block:
        flash('Block not found', 'error')
        return redirect(url_for('main_schedule_view'))
    
    # Delete all blocks with same activity name and recurrence pattern
    activity_name = block['activity_name']
    recurrence_pattern = block.get('recurrence_pattern', 'once')
    
    if recurrence_pattern in ['weekly', 'biweekly']:
        deleted_count = db.delete_all_recurring_activities(user_id, activity_name, recurrence_pattern)
        flash(f'Deleted all {deleted_count} occurrences of {activity_name}', 'success')
    else:
        db.delete_schedule_block(block_id, user_id)
        flash('Schedule block deleted', 'success')
    
    return redirect(url_for('main_schedule_view'))


@app.route('/task/<int:task_id>/delete', methods=('POST',))
def delete_task_view(task_id):
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    db.delete_task(task_id, user_id)
    flash('Task deleted', 'success')
    return redirect(url_for('main_schedule_view'))


@app.route('/reset_data', methods=('POST',))
def reset_data_view():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    # Perform data reset for this user
    db.reset_user_data(user_id)
    flash('All your subjects, tasks and schedule blocks have been deleted.', 'success')
    return redirect(url_for('settings_view'))

@app.route('/progress')
def weekly_progress_view():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    today = datetime.now()
    
    # Get metrics and all tasks (both PENDING and COMPLETED)
    metrics = sa.calculate_weekly_status(today, user_id)
    all_pending = db.fetch_tasks(user_id, status="PENDING")
    all_completed = db.fetch_tasks(user_id, status="COMPLETED")
    upcoming_tasks = sorted(all_pending, key=lambda t: t['due_date'])[:5]
    
    # Get week boundaries for report period
    start_of_week = today - timedelta(days=today.weekday())  # Monday
    end_of_week = start_of_week + timedelta(days=6)  # Sunday
    
    # Categorize tasks for weekly report (filter by this week’s date range)
    completed_tasks = []
    pending_tasks = []
    missed_tasks = []
    
    # We'll attribute completed tasks to the week they were completed (completion_date).
    # For pending tasks, users expect to see them even if they're due in the future.
    # We'll still classify overdue pending tasks as "missed".
    for task in all_pending:
        try:
            due = datetime.strptime(task['due_date'], '%Y-%m-%d').date()
        except Exception:
            due = None

        task_payload = {
            'id': task['id'],
            'name': task.get('name') or task.get('task_name'),
            'due_date': task.get('due_date'),
            'subject': task.get('subject_id', 'N/A'),
            'status': task.get('status', 'PENDING')
        }

        if due and due < today.date():
            missed_tasks.append(task_payload)
        else:
            # Only count tasks as "pending this week" if they're due within the current week.
            # Tasks due far in the future can exist but won't appear on the calendar until scheduled.
            if due and due <= end_of_week.date():
                pending_tasks.append(task_payload)

    # Completed tasks: attribute to the week of completion_date if present; otherwise fall back to due_date
    for task in all_completed:
        comp_date_str = task.get('completion_date')
        try:
            comp_date = datetime.strptime(comp_date_str, '%Y-%m-%d').date() if comp_date_str else None
        except Exception:
            comp_date = None

        # If completed within this week, count as completed for this week
        if comp_date and start_of_week.date() <= comp_date <= end_of_week.date():
            completed_tasks.append({
                'id': task['id'],
                'name': task.get('name') or task.get('task_name'),
                'due_date': task.get('due_date'),
                'subject': task.get('subject_id', 'N/A'),
                'status': 'COMPLETED'
            })
        else:
            # If not completed this week, but its due date falls in this week and it's completed later,
            # we should not count it in this week's completed list. Completed tasks are only those
            # whose completion_date falls in the week.
            pass
    
    # Get subjects and calculate stats
    subjects = db.fetch_subjects(user_id)
    subject_map = {s['id']: s['name'] for s in subjects}
    
    # Enrich tasks with subject names
    for task_list in [completed_tasks, pending_tasks, missed_tasks]:
        for task in task_list:
            task['subject'] = subject_map.get(task['subject'], 'General')
    
    subject_breakdown = []
    for subject in subjects:
        # Count tasks for this subject
        subject_tasks = [t for t in all_pending if t.get('subject_id') == subject['id']]
        tasks_remaining = len(subject_tasks)
        
        # Sum required time for remaining tasks
        hours_remaining = sum(t.get('required_time', 0) for t in subject_tasks)
        
        subject_breakdown.append({
            'id': subject['id'],
            'name': subject['name'],
            'teacher': subject.get('teacher', ''),
            'color_tag': subject.get('color_tag', '#bde0fe'),
            'tasks_remaining': tasks_remaining,
            'hours_remaining': round(hours_remaining, 1)
        })
    
    # Calculate overall progress metrics (current week only)
    # Calculate completion percentage using completed / (completed + pending + missed)
    total_pending = len(pending_tasks)
    total_missed = len(missed_tasks)
    total_completed = len(completed_tasks)
    denom = total_completed + total_pending + total_missed
    progress_percentage = 0
    if denom > 0:
        progress_percentage = round((total_completed / denom) * 100)
    
    # Calculate weekly statistics
    completed_count = len(completed_tasks)
    pending_count = len(pending_tasks)
    missed_count = len(missed_tasks)
    
    completion_rate = 0
    weekly_total = completed_count + pending_count + missed_count
    if weekly_total > 0:
        completion_rate = round((completed_count / weekly_total) * 100)
    
    # Prepare report period info
    report_period = {
        'start_date': start_of_week.strftime('%b %d, %Y'),
        'end_date': end_of_week.strftime('%b %d, %Y')
    }
    
    weekly_summary = {
        'completed_count': completed_count,
        'pending_count': pending_count,
        'missed_count': missed_count,
        'completion_rate': completion_rate
    }
    
    # Format upcoming tasks for display
    formatted_upcoming = []
    for task in upcoming_tasks:
        formatted_upcoming.append({
            'id': task['id'],
            'name': task.get('name') or task.get('task_name'),
            'due_date': task['due_date'],
            'status': task.get('status', 'PENDING'),
            'required_time': task.get('required_time', 0)
        })
    
    return render_template('progress.html', 
                         progress_percentage=progress_percentage,
                         upcoming_tasks=formatted_upcoming,
                         subject_breakdown=subject_breakdown,
                         total_tasks=len(pending_tasks),
                         completed_tasks=total_completed,
                         completed_tasks_list=completed_tasks,
                         pending_tasks=pending_tasks,
                         missed_tasks=missed_tasks,
                         weekly_summary=weekly_summary,
                         report_period=report_period)

@app.route('/weekly_report')
def weekly_report_view():
    """
    Weekly Report - Success Criterion #8
    Generates a summary report for LAST WEEK showing completed, pending, and missed tasks
    """
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    today = datetime.now()
    
    # Get LAST week's boundaries (previous Mon-Sun)
    # If today is Monday (0), we want last week's Monday which is 7 days ago
    # Otherwise, we want the Monday of last week
    days_since_monday = today.weekday()  # 0=Mon, 6=Sun
    last_week_monday = today - timedelta(days=days_since_monday + 7)  # Last week's Monday
    last_week_sunday = last_week_monday + timedelta(days=6)  # Last week's Sunday
    
    start_of_week = last_week_monday
    end_of_week = last_week_sunday
    
    # Fetch all tasks
    all_pending = db.fetch_tasks(user_id, status="PENDING")
    all_completed = db.fetch_tasks(user_id, status="COMPLETED")
    
    # Categorize tasks for LAST WEEK as a stable snapshot:
    # - Completed tasks: those with a completion_date that falls inside last week
    # - Pending / Missed: considered only for tasks whose due_date was inside last week
    completed_tasks = []
    pending_tasks = []
    missed_tasks = []

    # Build a combined list to iterate all tasks, but we'll decide buckets using due_date and completion_date
    for task in all_pending + all_completed:
        # parse due date safely
        try:
            task_due_date = datetime.strptime(task['due_date'], '%Y-%m-%d').date()
        except Exception:
            # skip tasks with invalid due dates
            continue

        # Only consider tasks whose due_date falls inside LAST WEEK for pending/missed buckets
        if not (start_of_week.date() <= task_due_date <= end_of_week.date()):
            # However, a task that was completed last week should be counted in completed even if its due_date
            # is outside the week — so check completion_date below.
            pass

        # Check completion_date to see if this task was completed during last week
        comp_date_str = task.get('completion_date')
        try:
            comp_date = datetime.strptime(comp_date_str, '%Y-%m-%d').date() if comp_date_str else None
        except Exception:
            comp_date = None

        if comp_date and start_of_week.date() <= comp_date <= end_of_week.date():
            # Completed during last week — include in completed regardless of due_date
            completed_tasks.append({
                'id': task['id'],
                'name': task.get('name') or task.get('task_name'),
                'due_date': task.get('due_date'),
                'subject': task.get('subject_id', 'N/A'),
                'status': 'COMPLETED'
            })
            continue

        # If due date was in last week, classify as pending or missed depending on current status
        if start_of_week.date() <= task_due_date <= end_of_week.date():
            if task.get('status') == 'COMPLETED':
                # No completion_date but marked completed — include it conservatively
                completed_tasks.append({
                    'id': task['id'],
                    'name': task.get('name') or task.get('task_name'),
                    'due_date': task.get('due_date'),
                    'subject': task.get('subject_id', 'N/A'),
                    'status': 'COMPLETED'
                })
            else:
                # Not completed by now — if due in last week and still not completed, it was missed
                if task_due_date < today.date():
                    missed_tasks.append({
                        'id': task['id'],
                        'name': task.get('name') or task.get('task_name'),
                        'due_date': task.get('due_date'),
                        'subject': task.get('subject_id', 'N/A'),
                        'status': task.get('status', 'PENDING')
                    })
                else:
                    pending_tasks.append({
                        'id': task['id'],
                        'name': task.get('name') or task.get('task_name'),
                        'due_date': task.get('due_date'),
                        'subject': task.get('subject_id', 'N/A'),
                        'status': task.get('status', 'PENDING')
                    })
    
    # Get subjects and enrich task data
    subjects = db.fetch_subjects(user_id)
    subject_map = {s['id']: s['name'] for s in subjects}
    
    for task_list in [completed_tasks, pending_tasks, missed_tasks]:
        for task in task_list:
            task['subject'] = subject_map.get(task['subject'], 'General')
    
    # Calculate weekly statistics
    completed_count = len(completed_tasks)
    pending_count = len(pending_tasks)
    missed_count = len(missed_tasks)
    
    completion_rate = 0
    weekly_total = completed_count + pending_count + missed_count
    if weekly_total > 0:
        completion_rate = round((completed_count / weekly_total) * 100)
    
    # Prepare report period info
    report_period = {
        'start_date': start_of_week.strftime('%b %d, %Y'),
        'end_date': end_of_week.strftime('%b %d, %Y')
    }
    
    weekly_summary = {
        'completed_count': completed_count,
        'pending_count': pending_count,
        'missed_count': missed_count,
        'completion_rate': completion_rate
    }
    
    return render_template('weekly_report.html',
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks,
        missed_tasks=missed_tasks,
        weekly_summary=weekly_summary,
        report_period=report_period
    )

# Weekly Report
@app.route('/settings')
def settings_view():
    user_id = get_current_user_id()
    if not user_id:
        return redirect(url_for('login'))
    conn = db.get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    
    # Format user info for template
    user_info = {
        'user_id': user['id'] if user else 'N/A',
        'username': user['username'] if user else 'User'
    }
    
    return render_template('settings.html', user_info=user_info)

# Template helper
@app.context_processor
def inject_now():
    # Use timezone-aware UTC to avoid deprecation warnings and keep times consistent.
    return {'now': datetime.now(timezone.utc)}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    # Set FLASK_DEBUG=1 only when testing locally.
    debug_mode = os.environ.get('FLASK_DEBUG', '0').strip() == '1'
    app.run(debug=debug_mode, host='0.0.0.0', port=port)