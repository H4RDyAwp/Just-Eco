import sqlite3
import hashlib
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'

# ---------- ХЕШИРОВАНИЕ ----------
def hash_password(password):
    salt = os.urandom(16).hex()
    return salt + ':' + hashlib.sha256((salt + password).encode()).hexdigest()

def verify_password(password, hashed):
    salt, h = hashed.split(':')
    return h == hashlib.sha256((salt + password).encode()).hexdigest()

# ---------- БАЗА ДАННЫХ ----------
def get_db():
    db = sqlite3.connect('instance/users.db')
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    # Таблица пользователей
    db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT,
            balance REAL DEFAULT 0.0,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            tilt_multiplier REAL DEFAULT 0.30
        )
    ''')
    # Таблица постов
    db.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            likes_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    # Таблица лайков (уникальная пара user_id + post_id)
    db.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE,
            UNIQUE(user_id, post_id)
        )
    ''')
    db.commit()

    # Добавляем столбец tilt_multiplier, если его нет (для старой БД)
    try:
        db.execute('ALTER TABLE users ADD COLUMN tilt_multiplier REAL DEFAULT 0.30')
        db.commit()
    except sqlite3.OperationalError:
        pass

    # Создаём администратора
    admin = db.execute('SELECT * FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin:
        hashed = hash_password('admin123')
        db.execute(
            'INSERT INTO users (username, password_hash, display_name, description, balance, is_admin, tilt_multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('admin', hashed, 'Administrator', 'Главный администратор', 999.99, 1, 0.30)
        )
        db.commit()
        print("Администратор создан: admin / admin123")
    db.close()

init_db()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def get_user_by_username(username):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    db.close()
    return user

def get_user_by_id(user_id):
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    db.close()
    return user

def get_all_users():
    db = get_db()
    users = db.execute('SELECT * FROM users ORDER BY id').fetchall()
    db.close()
    return users

def get_user_count():
    db = get_db()
    count = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    db.close()
    return count

# ---------- ФУНКЦИИ ДЛЯ ПОСТОВ ----------
def create_post(user_id, content):
    db = get_db()
    db.execute('INSERT INTO posts (user_id, content) VALUES (?, ?)', (user_id, content))
    db.commit()
    db.close()

def get_posts(limit=50, offset=0, user_id=None):
    """Возвращает посты. Если передан user_id, добавляет поле liked (1/0)."""
    db = get_db()
    if user_id is not None:
        posts = db.execute('''
            SELECT posts.*, users.display_name, users.username,
                   CASE WHEN likes.id IS NOT NULL THEN 1 ELSE 0 END AS liked
            FROM posts
            JOIN users ON posts.user_id = users.id
            LEFT JOIN likes ON likes.post_id = posts.id AND likes.user_id = ?
            ORDER BY posts.created_at DESC
            LIMIT ? OFFSET ?
        ''', (user_id, limit, offset)).fetchall()
    else:
        posts = db.execute('''
            SELECT posts.*, users.display_name, users.username
            FROM posts
            JOIN users ON posts.user_id = users.id
            ORDER BY posts.created_at DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset)).fetchall()
    db.close()
    return posts

def get_user_posts(user_id, current_user_id=None):
    """Посты конкретного пользователя с полем liked, если передан current_user_id."""
    db = get_db()
    if current_user_id is not None:
        posts = db.execute('''
            SELECT posts.*, users.display_name, users.username,
                   CASE WHEN likes.id IS NOT NULL THEN 1 ELSE 0 END AS liked
            FROM posts
            JOIN users ON posts.user_id = users.id
            LEFT JOIN likes ON likes.post_id = posts.id AND likes.user_id = ?
            WHERE posts.user_id = ?
            ORDER BY posts.created_at DESC
        ''', (current_user_id, user_id)).fetchall()
    else:
        posts = db.execute('''
            SELECT posts.*, users.display_name, users.username
            FROM posts
            JOIN users ON posts.user_id = users.id
            WHERE posts.user_id = ?
            ORDER BY posts.created_at DESC
        ''', (user_id,)).fetchall()
    db.close()
    return posts

def get_post(post_id):
    db = get_db()
    post = db.execute('''
        SELECT posts.*, users.display_name, users.username 
        FROM posts 
        JOIN users ON posts.user_id = users.id 
        WHERE posts.id = ?
    ''', (post_id,)).fetchone()
    db.close()
    return post

def get_like(user_id, post_id):
    db = get_db()
    like = db.execute('SELECT * FROM likes WHERE user_id = ? AND post_id = ?', (user_id, post_id)).fetchone()
    db.close()
    return like

def add_like(user_id, post_id):
    """Добавляет лайк, если его ещё нет. Возвращает True/False."""
    db = get_db()
    try:
        db.execute('INSERT INTO likes (user_id, post_id) VALUES (?, ?)', (user_id, post_id))
        db.execute('UPDATE posts SET likes_count = likes_count + 1 WHERE id = ?', (post_id,))
        db.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False
    finally:
        db.close()
    return success

def delete_post(post_id, user_id, is_admin):
    db = get_db()
    post = db.execute('SELECT * FROM posts WHERE id = ?', (post_id,)).fetchone()
    if not post:
        db.close()
        return False
    if post['user_id'] == user_id or is_admin:
        db.execute('DELETE FROM posts WHERE id = ?', (post_id,))
        db.commit()
        db.close()
        return True
    db.close()
    return False

# ---------- КОНТЕКСТНЫЙ ПРОЦЕССОР ----------
@app.context_processor
def inject_user():
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        return dict(current_user=user)
    return dict(current_user=None)

# ---------- ДЕКОРАТОР ДЛЯ АДМИНОВ ----------
from functools import wraps
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Пожалуйста, войдите.', 'error')
            return redirect(url_for('login'))
        user = get_user_by_id(session['user_id'])
        if not user or user['is_admin'] != 1:
            flash('Доступ запрещён. Только для администраторов.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# ---------- МАРШРУТЫ ----------
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        display_name = request.form['display_name'].strip()
        description = request.form.get('description', '').strip()

        if not username or not password or not display_name:
            flash('Все поля, кроме описания, обязательны.', 'error')
            return render_template('register.html')

        existing = get_user_by_username(username)
        if existing:
            flash('Пользователь с таким именем уже существует.', 'error')
            return render_template('register.html')

        hashed = hash_password(password)
        db = get_db()
        db.execute(
            'INSERT INTO users (username, password_hash, display_name, description, balance, tilt_multiplier) VALUES (?, ?, ?, ?, ?, ?)',
            (username, hashed, display_name, description, 0.0, 0.30)
        )
        db.commit()
        db.close()

        flash('Регистрация успешна! Теперь войдите.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        user = get_user_by_username(username)
        if not user or not verify_password(password, user['password_hash']):
            flash('Неверное имя пользователя или пароль.', 'error')
            return render_template('login.html')

        session['user_id'] = user['id']
        session['username'] = user['username']
        flash('Добро пожаловать, ' + user['display_name'] + '!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(session['user_id'])
    if not user:
        session.clear()
        flash('Сессия устарела.', 'error')
        return redirect(url_for('login'))

    total_users = get_user_count() if user['is_admin'] == 1 else None
    my_posts = get_user_posts(user['id'], current_user_id=session['user_id'])

    return render_template('dashboard.html', user=user, total_users=total_users, my_posts=my_posts)

@app.route('/feed')
def feed():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    posts = get_posts(limit=50, user_id=session['user_id'])
    return render_template('feed.html', posts=posts)

@app.route('/create_post', methods=['GET', 'POST'])
def create_post_route():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if not content:
            flash('Пост не может быть пустым.', 'error')
            return render_template('create_post.html')
        if len(content) > 1000:
            flash('Пост слишком длинный (максимум 1000 символов).', 'error')
            return render_template('create_post.html')

        create_post(session['user_id'], content)
        flash('Пост опубликован!', 'success')
        return redirect(url_for('feed'))

    return render_template('create_post.html')

@app.route('/like/<int:post_id>')
def like_post_route(post_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    user_id = session['user_id']
    if add_like(user_id, post_id):
        flash('Лайк поставлен!', 'success')
    else:
        flash('Вы уже лайкнули этот пост.', 'info')
    return redirect(request.referrer or url_for('feed'))

@app.route('/delete_post/<int:post_id>', methods=['POST'])
def delete_post_route(post_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(session['user_id'])
    if delete_post(post_id, session['user_id'], user['is_admin'] == 1):
        flash('Пост удалён.', 'success')
    else:
        flash('Не удалось удалить пост.', 'error')

    return redirect(request.referrer or url_for('feed'))

# ---------- НАСТРОЙКИ ----------
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(session['user_id'])
    if not user:
        session.clear()
        flash('Сессия устарела.', 'error')
        return redirect(url_for('login'))

    # Обработка удаления аккаунта
    if request.method == 'POST' and request.form.get('delete_account'):
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if username != user['username']:
            flash('Неверное имя пользователя.', 'error')
            return render_template('settings.html', user=user)

        if not verify_password(password, user['password_hash']):
            flash('Неверный пароль.', 'error')
            return render_template('settings.html', user=user)

        db = get_db()
        db.execute('DELETE FROM users WHERE id = ?', (user['id'],))
        db.commit()
        db.close()

        session.clear()
        flash('Ваш аккаунт был удалён.', 'info')
        return redirect(url_for('login'))

    # Обычное обновление профиля
    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        description = request.form.get('description', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        tilt_multiplier = request.form.get('tilt_multiplier', 0.30)

        if not display_name:
            flash('Отображаемое имя не может быть пустым.', 'error')
            return render_template('settings.html', user=user)

        try:
            tilt_multiplier = float(tilt_multiplier.replace(',', '.'))
            if tilt_multiplier < 0 or tilt_multiplier > 2:
                raise ValueError
        except:
            flash('Некорректное значение 3D-эффекта (от 0 до 2).', 'error')
            return render_template('settings.html', user=user)

        db = get_db()
        db.execute(
            'UPDATE users SET display_name = ?, description = ?, tilt_multiplier = ? WHERE id = ?',
            (display_name, description, tilt_multiplier, user['id'])
        )

        if new_password:
            if len(new_password) < 4:
                flash('Пароль должен быть не менее 4 символов.', 'error')
                return render_template('settings.html', user=user)
            if new_password != confirm_password:
                flash('Пароли не совпадают.', 'error')
                return render_template('settings.html', user=user)
            hashed = hash_password(new_password)
            db.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hashed, user['id']))

        db.commit()
        db.close()

        flash('Настройки обновлены!', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html', user=user)

# ---------- АДМИНКА ----------
@app.route('/admin')
@admin_required
def admin_panel():
    users = get_all_users()
    return render_template('admin.html', users=users)

@app.route('/admin/posts')
@admin_required
def admin_posts():
    posts = get_posts(limit=100, user_id=None)  # админу не нужно поле liked
    return render_template('admin_posts.html', posts=posts)

@app.route('/admin/post/delete/<int:post_id>', methods=['POST'])
@admin_required
def admin_delete_post(post_id):
    db = get_db()
    db.execute('DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()
    db.close()
    flash('Пост удалён администратором.', 'success')
    return redirect(request.referrer or url_for('admin_posts'))

@app.route('/admin/edit/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def admin_edit_user(user_id):
    user = get_user_by_id(user_id)
    if not user:
        flash('Пользователь не найден.', 'error')
        return redirect(url_for('admin_panel'))

    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        description = request.form.get('description', '').strip()
        balance = request.form.get('balance', '').strip()
        is_admin = 1 if request.form.get('is_admin') else 0

        if not display_name:
            flash('Отображаемое имя обязательно.', 'error')
            return render_template('admin_edit.html', user=user)

        try:
            balance = float(balance.replace(',', '.'))
        except:
            flash('Некорректный формат баланса.', 'error')
            return render_template('admin_edit.html', user=user)

        db = get_db()
        db.execute(
            'UPDATE users SET display_name = ?, description = ?, balance = ?, is_admin = ? WHERE id = ?',
            (display_name, description, balance, is_admin, user_id)
        )
        db.commit()
        db.close()
        flash('Данные пользователя обновлены.', 'success')
        return redirect(url_for('admin_panel'))

    return render_template('admin_edit.html', user=user)

@app.route('/admin/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    if user_id == session['user_id']:
        flash('Нельзя удалить самого себя.', 'error')
        return redirect(url_for('admin_panel'))

    db = get_db()
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    db.close()
    flash('Пользователь удалён.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host="0.0.0.0",debug=False, port=10000)