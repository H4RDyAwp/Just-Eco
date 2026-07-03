import os
import time
import hashlib
from datetime import datetime
from functools import wraps

import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv

load_dotenv()  # загружаем переменные из .env

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-change-in-production')

# ---------- ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ ----------
def get_db_connection():
    """Возвращает соединение с PostgreSQL, используя DATABASE_URL или отдельные переменные."""
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        # Если есть DATABASE_URL – используем её
        conn = psycopg2.connect(database_url, sslmode='require')
    else:
        # Иначе собираем из отдельных переменных (для локальной разработки)
        conn = psycopg2.connect(
            dbname=os.getenv('DB_NAME', 'justid'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', 'postgres'),
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432'),
            sslmode=os.getenv('DB_SSLMODE', 'disable')
        )
    conn.autocommit = False
    return conn

def get_cursor(conn):
    """Возвращает курсор с доступом по имени колонки."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ---------- ИНИЦИАЛИЗАЦИЯ БАЗЫ ----------
def init_db():
    conn = get_db_connection()
    cur = get_cursor(conn)
    try:
        # Таблица users
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                description TEXT,
                balance REAL DEFAULT 0.0,
                is_admin INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                tilt_multiplier REAL DEFAULT 0.30,
                last_reward_time DOUBLE PRECISION
            )
        ''')
        # Таблица posts
        cur.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                likes_count INTEGER DEFAULT 0,
                dislikes_count INTEGER DEFAULT 0
            )
        ''')
        # Таблица reactions (лайки/дизлайки)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS reactions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                reaction_type INTEGER NOT NULL CHECK (reaction_type IN (1, -1)),
                UNIQUE(user_id, post_id)
            )
        ''')
        conn.commit()

        # Создаём администратора, если его нет
        cur.execute("SELECT * FROM users WHERE username = 'admin'")
        admin = cur.fetchone()
        if not admin:
            hashed = hash_password('admin123')
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name, description, balance, is_admin, tilt_multiplier) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ('admin', hashed, 'Administrator', 'Главный администратор', 999.99, 1, 0.30)
            )
            conn.commit()
            print("Администратор создан: admin / admin123")
    except Exception as e:
        conn.rollback()
        print(f"Ошибка инициализации БД: {e}")
    finally:
        cur.close()
        conn.close()

init_db()

# ---------- ХЕШИРОВАНИЕ ПАРОЛЕЙ ----------
def hash_password(password):
    salt = os.urandom(16).hex()
    return salt + ':' + hashlib.sha256((salt + password).encode()).hexdigest()

def verify_password(password, hashed):
    salt, h = hashed.split(':')
    return h == hashlib.sha256((salt + password).encode()).hexdigest()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def get_user_by_username(username):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user

def get_user_by_id(user_id):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()
    cur.close()
    conn.close()
    return user

def get_all_users():
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM users ORDER BY id")
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users

def get_user_count():
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()['count']
    cur.close()
    conn.close()
    return count

# ---------- ФУНКЦИИ ДЛЯ ПОСТОВ ----------
def create_post(user_id, content):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("INSERT INTO posts (user_id, content) VALUES (%s, %s)", (user_id, content))
    conn.commit()
    cur.close()
    conn.close()

def get_posts(limit=50, offset=0, user_id=None):
    conn = get_db_connection()
    cur = get_cursor(conn)
    if user_id is not None:
        cur.execute('''
            SELECT posts.*, users.display_name, users.username,
                   CASE WHEN reactions.reaction_type = 1 THEN 1 ELSE 0 END AS liked,
                   CASE WHEN reactions.reaction_type = -1 THEN 1 ELSE 0 END AS disliked
            FROM posts
            JOIN users ON posts.user_id = users.id
            LEFT JOIN reactions ON reactions.post_id = posts.id AND reactions.user_id = %s
            ORDER BY posts.created_at DESC
            LIMIT %s OFFSET %s
        ''', (user_id, limit, offset))
    else:
        cur.execute('''
            SELECT posts.*, users.display_name, users.username
            FROM posts
            JOIN users ON posts.user_id = users.id
            ORDER BY posts.created_at DESC
            LIMIT %s OFFSET %s
        ''', (limit, offset))
    posts = cur.fetchall()
    cur.close()
    conn.close()
    return posts

def get_user_posts(user_id, current_user_id=None):
    conn = get_db_connection()
    cur = get_cursor(conn)
    if current_user_id is not None:
        cur.execute('''
            SELECT posts.*, users.display_name, users.username,
                   CASE WHEN reactions.reaction_type = 1 THEN 1 ELSE 0 END AS liked,
                   CASE WHEN reactions.reaction_type = -1 THEN 1 ELSE 0 END AS disliked
            FROM posts
            JOIN users ON posts.user_id = users.id
            LEFT JOIN reactions ON reactions.post_id = posts.id AND reactions.user_id = %s
            WHERE posts.user_id = %s
            ORDER BY posts.created_at DESC
        ''', (current_user_id, user_id))
    else:
        cur.execute('''
            SELECT posts.*, users.display_name, users.username
            FROM posts
            JOIN users ON posts.user_id = users.id
            WHERE posts.user_id = %s
            ORDER BY posts.created_at DESC
        ''', (user_id,))
    posts = cur.fetchall()
    cur.close()
    conn.close()
    return posts

def get_post(post_id):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT posts.*, users.display_name, users.username 
        FROM posts 
        JOIN users ON posts.user_id = users.id 
        WHERE posts.id = %s
    ''', (post_id,))
    post = cur.fetchone()
    cur.close()
    conn.close()
    return post

def toggle_reaction(user_id, post_id, reaction_type):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT reaction_type FROM reactions WHERE user_id = %s AND post_id = %s", (user_id, post_id))
    existing = cur.fetchone()

    if existing:
        if existing['reaction_type'] == reaction_type:
            # Удаляем реакцию
            cur.execute("DELETE FROM reactions WHERE user_id = %s AND post_id = %s", (user_id, post_id))
            if reaction_type == 1:
                cur.execute("UPDATE posts SET likes_count = likes_count - 1 WHERE id = %s", (post_id,))
            else:
                cur.execute("UPDATE posts SET dislikes_count = dislikes_count - 1 WHERE id = %s", (post_id,))
            conn.commit()
            cur.close()
            conn.close()
            return {'action': 'removed', 'new_type': None}
        else:
            # Меняем реакцию
            cur.execute("UPDATE reactions SET reaction_type = %s WHERE user_id = %s AND post_id = %s", (reaction_type, user_id, post_id))
            if reaction_type == 1:
                cur.execute("UPDATE posts SET dislikes_count = dislikes_count - 1, likes_count = likes_count + 1 WHERE id = %s", (post_id,))
            else:
                cur.execute("UPDATE posts SET likes_count = likes_count - 1, dislikes_count = dislikes_count + 1 WHERE id = %s", (post_id,))
            conn.commit()
            cur.close()
            conn.close()
            return {'action': 'changed', 'new_type': reaction_type}
    else:
        cur.execute("INSERT INTO reactions (user_id, post_id, reaction_type) VALUES (%s, %s, %s)", (user_id, post_id, reaction_type))
        if reaction_type == 1:
            cur.execute("UPDATE posts SET likes_count = likes_count + 1 WHERE id = %s", (post_id,))
        else:
            cur.execute("UPDATE posts SET dislikes_count = dislikes_count + 1 WHERE id = %s", (post_id,))
        conn.commit()
        cur.close()
        conn.close()
        return {'action': 'added', 'new_type': reaction_type}

def delete_post(post_id, user_id, is_admin):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM posts WHERE id = %s", (post_id,))
    post = cur.fetchone()
    if not post:
        cur.close()
        conn.close()
        return False
    if post['user_id'] == user_id or is_admin:
        cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    cur.close()
    conn.close()
    return False

# ---------- ФУНКЦИИ ДЛЯ НАГРАД (КВЕСТЫ) ----------
def get_last_reward_time(user_id):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT last_reward_time FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row['last_reward_time'] if row else None

def can_claim_reward(user_id):
    last = get_last_reward_time(user_id)
    if last is None:
        return True
    try:
        last = float(last)
    except (TypeError, ValueError):
        return True
    return (time.time() - last) >= 180

def claim_reward(user_id):
    if not can_claim_reward(user_id):
        return False
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("UPDATE users SET balance = balance + 3.67, last_reward_time = %s WHERE id = %s", (time.time(), user_id))
    conn.commit()
    cur.close()
    conn.close()
    return True

# ---------- СТАТИСТИКА ПОЛЬЗОВАТЕЛЯ ----------
def get_user_stats(user_id):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT COUNT(*) FROM posts WHERE user_id = %s", (user_id,))
    posts_count = cur.fetchone()['count']
    cur.execute("SELECT COALESCE(SUM(likes_count), 0) FROM posts WHERE user_id = %s", (user_id,))
    likes_sum = cur.fetchone()['coalesce']
    cur.execute("SELECT COALESCE(SUM(dislikes_count), 0) FROM posts WHERE user_id = %s", (user_id,))
    dislikes_sum = cur.fetchone()['coalesce']
    rating = likes_sum - dislikes_sum
    cur.close()
    conn.close()
    return {
        'posts_count': posts_count,
        'likes_received': likes_sum,
        'dislikes_received': dislikes_sum,
        'rating': rating
    }

# ---------- ПОИСК ПОЛЬЗОВАТЕЛЕЙ ----------
def search_users(query, limit=20):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT id, username, display_name, description, balance 
        FROM users 
        WHERE username ILIKE %s OR display_name ILIKE %s
        ORDER BY display_name
        LIMIT %s
    ''', ('%' + query + '%', '%' + query + '%', limit))
    users = cur.fetchall()
    cur.close()
    conn.close()
    return users

# ---------- КОНТЕКСТНЫЙ ПРОЦЕССОР ----------
@app.context_processor
def inject_user():
    if 'user_id' in session:
        user = get_user_by_id(session['user_id'])
        return dict(current_user=user)
    return dict(current_user=None)

# ---------- ДЕКОРАТОР ДЛЯ АДМИНОВ ----------
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
        conn = get_db_connection()
        cur = get_cursor(conn)
        cur.execute(
            "INSERT INTO users (username, password_hash, display_name, description, balance, tilt_multiplier) VALUES (%s, %s, %s, %s, %s, %s)",
            (username, hashed, display_name, description, 0.0, 0.30)
        )
        conn.commit()
        cur.close()
        conn.close()

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
    stats = get_user_stats(user['id'])

    return render_template('dashboard.html', user=user, total_users=total_users, my_posts=my_posts, stats=stats)

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

        if claim_reward(session['user_id']):
            flash('Пост опубликован! Вы получили $3.67 за квест!', 'success')
        else:
            flash('Пост опубликован, но награда ещё не доступна (кулдаун 3 минуты).', 'info')

        return redirect(url_for('feed'))

    return render_template('create_post.html')

@app.route('/like/<int:post_id>')
def like_post_route(post_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    result = toggle_reaction(session['user_id'], post_id, 1)
    if result['action'] == 'added':
        flash('Лайк поставлен!', 'success')
    elif result['action'] == 'removed':
        flash('Лайк убран.', 'info')
    elif result['action'] == 'changed':
        flash('Вы изменили реакцию на лайк.', 'success')
    return redirect(request.referrer or url_for('feed'))

@app.route('/dislike/<int:post_id>')
def dislike_post_route(post_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    result = toggle_reaction(session['user_id'], post_id, -1)
    if result['action'] == 'added':
        flash('Дизлайк поставлен!', 'success')
    elif result['action'] == 'removed':
        flash('Дизлайк убран.', 'info')
    elif result['action'] == 'changed':
        flash('Вы изменили реакцию на дизлайк.', 'success')
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

@app.route('/quests')
def quests():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    user = get_user_by_id(session['user_id'])
    if not user:
        session.clear()
        flash('Сессия устарела.', 'error')
        return redirect(url_for('login'))

    can_claim = can_claim_reward(user['id'])
    remaining = 0
    if not can_claim:
        last = get_last_reward_time(user['id'])
        if last:
            try:
                last = float(last)
                remaining = max(0, 180 - (time.time() - last))
            except:
                remaining = 0

    return render_template('quests.html', user=user, can_claim=can_claim, remaining=remaining)

@app.route('/users')
def users():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    query = request.args.get('q', '').strip()
    results = []
    if query:
        results = search_users(query)
    return render_template('users.html', query=query, results=results)

@app.route('/user/<int:user_id>')
def user_profile(user_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    profile_user = get_user_by_id(user_id)
    if not profile_user:
        flash('Пользователь не найден.', 'error')
        return redirect(url_for('users'))
    posts = get_user_posts(user_id, current_user_id=session['user_id'])
    stats = get_user_stats(user_id)
    return render_template('user_profile.html', profile_user=profile_user, posts=posts, stats=stats)

@app.route('/send_money/<int:user_id>', methods=['POST'])
def send_money(user_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    sender_id = session['user_id']
    amount_str = request.form.get('amount', '').strip()
    try:
        amount = float(amount_str.replace(',', '.'))
        if amount <= 0:
            raise ValueError
    except:
        flash('Введите корректную сумму.', 'error')
        return redirect(request.referrer or url_for('user_profile', user_id=user_id))

    if sender_id == user_id:
        flash('Нельзя отправить деньги самому себе.', 'error')
        return redirect(request.referrer or url_for('user_profile', user_id=user_id))

    sender = get_user_by_id(sender_id)
    receiver = get_user_by_id(user_id)
    if not sender or not receiver:
        flash('Пользователь не найден.', 'error')
        return redirect(url_for('users'))

    if sender['balance'] < amount:
        flash('Недостаточно средств.', 'error')
        return redirect(request.referrer or url_for('user_profile', user_id=user_id))

    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (amount, sender_id))
    cur.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, user_id))
    conn.commit()
    cur.close()
    conn.close()

    flash(f'Вы отправили ${amount:.2f} пользователю {receiver["display_name"]}.', 'success')
    return redirect(url_for('user_profile', user_id=user_id))

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

    if request.method == 'POST' and request.form.get('delete_account'):
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if username != user['username']:
            flash('Неверное имя пользователя.', 'error')
            return render_template('settings.html', user=user)

        if not verify_password(password, user['password_hash']):
            flash('Неверный пароль.', 'error')
            return render_template('settings.html', user=user)

        conn = get_db_connection()
        cur = get_cursor(conn)
        cur.execute("DELETE FROM users WHERE id = %s", (user['id'],))
        conn.commit()
        cur.close()
        conn.close()

        session.clear()
        flash('Ваш аккаунт был удалён.', 'info')
        return redirect(url_for('login'))

    if request.method == 'POST':
        display_name = request.form.get('display_name', '').strip()
        description = request.form.get('description', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        tilt_multiplier = request.form.get('tilt_multiplier')

        if not display_name:
            flash('Отображаемое имя не может быть пустым.', 'error')
            return render_template('settings.html', user=user)

        if tilt_multiplier is None or tilt_multiplier == '':
            tilt_multiplier = 0.30
        else:
            try:
                tilt_multiplier = float(tilt_multiplier.replace(',', '.'))
                if tilt_multiplier < 0 or tilt_multiplier > 2:
                    raise ValueError
            except ValueError:
                flash('Некорректное значение 3D-эффекта (от 0 до 2).', 'error')
                return render_template('settings.html', user=user)

        conn = get_db_connection()
        cur = get_cursor(conn)
        cur.execute(
            "UPDATE users SET display_name = %s, description = %s, tilt_multiplier = %s WHERE id = %s",
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
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed, user['id']))

        conn.commit()
        cur.close()
        conn.close()

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
    posts = get_posts(limit=100, user_id=None)
    return render_template('admin_posts.html', posts=posts)

@app.route('/admin/post/delete/<int:post_id>', methods=['POST'])
@admin_required
def admin_delete_post(post_id):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
    conn.commit()
    cur.close()
    conn.close()
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

        conn = get_db_connection()
        cur = get_cursor(conn)
        cur.execute(
            "UPDATE users SET display_name = %s, description = %s, balance = %s, is_admin = %s WHERE id = %s",
            (display_name, description, balance, is_admin, user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        flash('Данные пользователя обновлены.', 'success')
        return redirect(url_for('admin_panel'))

    return render_template('admin_edit.html', user=user)

@app.route('/admin/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    if user_id == session['user_id']:
        flash('Нельзя удалить самого себя.', 'error')
        return redirect(url_for('admin_panel'))

    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('Пользователь удалён.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, port=5001)