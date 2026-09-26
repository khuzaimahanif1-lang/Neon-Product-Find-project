import sqlite3
from sqlalchemy.engine import make_url
from app.config import Settings

emails = ["signup-check-1789296724256@example.com","signup-main-1789297037644@example.com"]
with sqlite3.connect(make_url(Settings().database_url).database) as connection:
    removed = 0
    for email in emails:
        user = connection.execute('SELECT id, name FROM users WHERE email = ?', (email,)).fetchone()
        if not user or user[1] != 'Signup Check':
            continue
        saved = connection.execute('SELECT COUNT(*) FROM saved_finds WHERE user_id = ?', (user[0],)).fetchone()[0]
        history = connection.execute('SELECT COUNT(*) FROM search_history WHERE user_id = ?', (user[0],)).fetchone()[0]
        if saved or history:
            continue
        connection.execute('DELETE FROM auth_sessions WHERE user_id = ?', (user[0],))
        connection.execute('DELETE FROM users WHERE id = ? AND email = ?', (user[0], email))
        removed += 1
print(f'Removed {removed} empty accounts created by this signup check.')
