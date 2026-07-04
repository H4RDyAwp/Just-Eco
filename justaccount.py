import os
import time
import hashlib
from datetime import datetime
from functools import wraps

import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-change-in-production')

# ---------- ХЕШИРОВАНИЕ ПАРОЛЕЙ ----------
def hash_password(password):
    salt = os.urandom(16).hex()
    return salt + ':' + hashlib.sha256((salt + password).encode()).hexdigest()

def verify_password(password, hashed):
    salt, h = hashed.split(':')
    return h == hashlib.sha256((salt + password).encode()).hexdigest()

# ---------- ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ ----------
def get_db_connection():
    database_url = os.getenv('DATABASE_URL')
    if database_url:
        conn = psycopg2.connect(database_url, sslmode='require')
    else:
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
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# ---------- ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ ----------
def init_db():
    # Создание таблиц (безопасно, каждая команда в отдельной транзакции)
    conn = get_db_connection()
    cur = get_cursor(conn)
    try:
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
        cur.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT,
                content TEXT NOT NULL,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                likes_count INTEGER DEFAULT 0,
                dislikes_count INTEGER DEFAULT 0
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS reactions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                reaction_type INTEGER NOT NULL CHECK (reaction_type IN (1, -1)),
                UNIQUE(user_id, post_id)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS shop_items (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                category TEXT NOT NULL CHECK (category IN ('post_decoration', 'profile_frame')),
                icon TEXT,
                css_class TEXT,
                emoji TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS user_inventory (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                item_id INTEGER NOT NULL REFERENCES shop_items(id) ON DELETE CASCADE,
                equipped BOOLEAN DEFAULT FALSE,
                purchased_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, item_id)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                receiver_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                is_read BOOLEAN DEFAULT FALSE,
                reply_to_id INTEGER REFERENCES messages(id) ON DELETE SET NULL
            )
        ''')
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка создания таблиц: {e}")
    finally:
        cur.close()
        conn.close()

    # Добавляем колонки, если их нет (для обновления существующей БД)
    for alter_sql in [
        "ALTER TABLE posts ADD COLUMN title TEXT",
        "ALTER TABLE posts ADD COLUMN image_url TEXT"
    ]:
        try:
            conn = get_db_connection()
            cur = get_cursor(conn)
            cur.execute(alter_sql)
            conn.commit()
            cur.close()
            conn.close()
        except psycopg2.errors.DuplicateColumn:
            pass
        except Exception as e:
            print(f"Ошибка добавления колонки: {e}")
            if conn:
                conn.rollback()
            if cur:
                cur.close()
            if conn:
                conn.close()

    # Добавляем товары, если их нет
    conn = get_db_connection()
    cur = get_cursor(conn)
    try:
        items = [
            ('Золотой текст', 'Золотистый цвет текста в постах', 5.00, 'post_decoration', '🌟', 'gold-text', None),
            ('Неоновый пост', 'Переливающаяся обводка и лёгкий фон', 7.00, 'post_decoration', '💡', 'neon-post', None),
            ('Рамка "Космос"', 'Космическая рамка для профиля', 10.00, 'profile_frame', '🌌', 'cosmic-frame', None),
            ('Рамка "Классика"', 'Элегантная золотая рамка', 6.00, 'profile_frame', '✨', 'classic-frame', None),
            ('Bold', 'Жирный шрифт для постов', 6.70, 'post_decoration', '💪', 'bold-text', None),
            ('Matrix Background', 'Анимированный фон в стиле Матрицы', 12.00, 'post_decoration', '💻', 'matrix-bg', None),
            ('Green Text', 'Неоновый зелёный текст', 5.50, 'post_decoration', '🌿', 'green-text', None),
        ]
        for name, desc, price, category, icon, css_class, emoji in items:
            cur.execute("SELECT id FROM shop_items WHERE css_class = %s", (css_class,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO shop_items (name, description, price, category, icon, css_class, emoji) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (name, desc, price, category, icon, css_class, emoji)
                )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Ошибка добавления товаров: {e}")
    finally:
        cur.close()
        conn.close()

    # Создаём администратора
    conn = get_db_connection()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT * FROM users WHERE username = 'admin'")
        if not cur.fetchone():
            hashed = hash_password('admin123')
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name, description, balance, is_admin, tilt_multiplier) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                ('admin', hashed, 'Administrator', 'Главный администратор', 999.99, 1, 0.30)
            )
            conn.commit()
            print("Администратор создан: admin / admin123")
    except Exception as e:
        conn.rollback()
        print(f"Ошибка создания администратора: {e}")
    finally:
        cur.close()
        conn.close()

init_db()

# ---------- ФУНКЦИИ ПОЛЬЗОВАТЕЛЕЙ ----------
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

# ---------- ПОСТЫ ----------
def create_post(user_id, title, content, image_url=None):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute(
        "INSERT INTO posts (user_id, title, content, image_url) VALUES (%s, %s, %s, %s)",
        (user_id, title, content, image_url)
    )
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

# ---------- НАГРАДЫ (КВЕСТЫ) ----------
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

# ---------- СТАТИСТИКА ----------
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

# ---------- ПОИСК ----------
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

# ---------- МАГАЗИН И ИНВЕНТАРЬ ----------
def get_shop_items(category=None):
    conn = get_db_connection()
    cur = get_cursor(conn)
    if category:
        cur.execute("SELECT * FROM shop_items WHERE category = %s ORDER BY price", (category,))
    else:
        cur.execute("SELECT * FROM shop_items ORDER BY category, price")
    items = cur.fetchall()
    cur.close()
    conn.close()
    return items

def get_user_inventory(user_id, category=None):
    conn = get_db_connection()
    cur = get_cursor(conn)
    if category:
        cur.execute('''
            SELECT shop_items.*, user_inventory.equipped
            FROM user_inventory
            JOIN shop_items ON user_inventory.item_id = shop_items.id
            WHERE user_inventory.user_id = %s AND shop_items.category = %s
        ''', (user_id, category))
    else:
        cur.execute('''
            SELECT shop_items.*, user_inventory.equipped
            FROM user_inventory
            JOIN shop_items ON user_inventory.item_id = shop_items.id
            WHERE user_inventory.user_id = %s
        ''', (user_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return items

def get_equipped_items(user_id, category=None):
    conn = get_db_connection()
    cur = get_cursor(conn)
    if category:
        cur.execute('''
            SELECT shop_items.*
            FROM user_inventory
            JOIN shop_items ON user_inventory.item_id = shop_items.id
            WHERE user_inventory.user_id = %s AND user_inventory.equipped = TRUE AND shop_items.category = %s
        ''', (user_id, category))
    else:
        cur.execute('''
            SELECT shop_items.*
            FROM user_inventory
            JOIN shop_items ON user_inventory.item_id = shop_items.id
            WHERE user_inventory.user_id = %s AND user_inventory.equipped = TRUE
        ''', (user_id,))
    items = cur.fetchall()
    cur.close()
    conn.close()
    return items

def get_item_by_id(item_id):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM shop_items WHERE id = %s", (item_id,))
    item = cur.fetchone()
    cur.close()
    conn.close()
    return item

def purchase_item(user_id, item_id):
    item = get_item_by_id(item_id)
    if not item:
        return False, "Товар не найден."
    user = get_user_by_id(user_id)
    if user['balance'] < item['price']:
        return False, "Недостаточно средств."
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT * FROM user_inventory WHERE user_id = %s AND item_id = %s", (user_id, item_id))
    existing = cur.fetchone()
    if existing:
        cur.close()
        conn.close()
        return False, "Товар уже приобретён."
    cur.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (item['price'], user_id))
    cur.execute("INSERT INTO user_inventory (user_id, item_id) VALUES (%s, %s)", (user_id, item_id))
    conn.commit()
    cur.close()
    conn.close()
    return True, "Украшение куплено!"

def toggle_equip_item(user_id, item_id):
    """Включает/выключает украшение. Для постов можно включить несколько, для рамок – только одно."""
    conn = get_db_connection()
    cur = get_cursor(conn)
    item = get_item_by_id(item_id)
    if not item:
        cur.close()
        conn.close()
        return False, "Товар не найден."
    cur.execute("SELECT equipped FROM user_inventory WHERE user_id = %s AND item_id = %s", (user_id, item_id))
    inv = cur.fetchone()
    if not inv:
        cur.close()
        conn.close()
        return False, "У вас нет этого украшения."
    if item['category'] == 'profile_frame':
        if not inv['equipped']:  # включаем новую рамку
            cur.execute("UPDATE user_inventory SET equipped = FALSE WHERE user_id = %s AND item_id IN (SELECT id FROM shop_items WHERE category = 'profile_frame')", (user_id,))
    new_equipped = not inv['equipped']
    cur.execute("UPDATE user_inventory SET equipped = %s WHERE user_id = %s AND item_id = %s", (new_equipped, user_id, item_id))
    conn.commit()
    cur.close()
    conn.close()
    return True, "Украшение " + ("применено" if new_equipped else "отключено")

# ---------- ЛИЧНЫЕ СООБЩЕНИЯ ----------
def get_conversations(user_id):
    """Возвращает список диалогов пользователя с последними сообщениями."""
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT DISTINCT ON (other_user_id) 
            CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END AS other_user_id,
            u.display_name, u.username,
            m.content AS last_message,
            m.created_at AS last_message_time,
            m.is_read,
            m.sender_id AS last_sender_id
        FROM messages m
        JOIN users u ON (CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END) = u.id
        WHERE sender_id = %s OR receiver_id = %s
        ORDER BY other_user_id, m.created_at DESC
    ''', (user_id, user_id, user_id, user_id))
    conversations = cur.fetchall()
    cur.close()
    conn.close()
    return conversations

def get_messages(user_id, other_user_id, limit=50):
    """Возвращает последние сообщения между двумя пользователями."""
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute('''
        SELECT m.*, u.display_name, u.username
        FROM messages m
        JOIN users u ON m.sender_id = u.id
        WHERE (sender_id = %s AND receiver_id = %s)
           OR (sender_id = %s AND receiver_id = %s)
        ORDER BY m.created_at DESC
        LIMIT %s
    ''', (user_id, other_user_id, other_user_id, user_id, limit))
    messages = cur.fetchall()
    cur.close()
    conn.close()
    return messages[::-1]  # переворачиваем в хронологическом порядке

def send_message(sender_id, receiver_id, content):
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute(
        "INSERT INTO messages (sender_id, receiver_id, content) VALUES (%s, %s, %s)",
        (sender_id, receiver_id, content)
    )
    conn.commit()
    cur.close()
    conn.close()

def mark_as_read(message_ids, user_id):
    """Отмечает сообщения как прочитанные для текущего пользователя."""
    if not message_ids:
        return
    conn = get_db_connection()
    cur = get_cursor(conn)
    cur.execute(
        "UPDATE messages SET is_read = TRUE WHERE id = ANY(%s) AND receiver_id = %s",
        (list(message_ids), user_id)
    )
    conn.commit()
    cur.close()
    conn.close()

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

    post_styles = get_equipped_items(user['id'], 'post_decoration')
    profile_frame = get_equipped_items(user['id'], 'profile_frame')
    frame = profile_frame[0] if profile_frame else None

    return render_template('dashboard.html',
                           user=user,
                           total_users=total_users,
                           my_posts=my_posts,
                           stats=stats,
                           post_styles=post_styles,
                           frame=frame)

@app.route('/feed')
def feed():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    posts = get_posts(limit=50, user_id=session['user_id'])
    user_ids = set(post['user_id'] for post in posts)
    user_styles = {}
    if user_ids:
        conn = get_db_connection()
        cur = get_cursor(conn)
        cur.execute('''
            SELECT ui.user_id, si.css_class
            FROM user_inventory ui
            JOIN shop_items si ON ui.item_id = si.id
            WHERE ui.user_id = ANY(%s) AND ui.equipped = TRUE AND si.category = 'post_decoration'
        ''', (list(user_ids),))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        for row in rows:
            user_styles.setdefault(row['user_id'], []).append(row['css_class'])
    for post in posts:
        post['styles'] = user_styles.get(post['user_id'], [])

    return render_template('feed.html', posts=posts)

@app.route('/create_post', methods=['GET', 'POST'])
def create_post_route():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        image_url = request.form.get('image_url', '').strip()

        if not content:
            flash('Текст поста не может быть пустым.', 'error')
            return render_template('create_post.html')
        if len(title) > 100:
            flash('Заголовок не может быть длиннее 100 символов.', 'error')
            return render_template('create_post.html')
        if len(content) > 1000:
            flash('Пост слишком длинный (максимум 1000 символов).', 'error')
            return render_template('create_post.html')

        create_post(session['user_id'], title, content, image_url if image_url else None)

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
    post_styles = get_equipped_items(user_id, 'post_decoration')
    profile_frame = get_equipped_items(user_id, 'profile_frame')
    frame = profile_frame[0] if profile_frame else None
    return render_template('user_profile.html',
                           profile_user=profile_user,
                           posts=posts,
                           stats=stats,
                           post_styles=post_styles,
                           frame=frame)

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

# ---------- ЛИЧНЫЕ СООБЩЕНИЯ (маршруты) ----------
@app.route('/inbox')
def inbox():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    user_id = session['user_id']
    conversations = get_conversations(user_id)
    return render_template('inbox.html', conversations=conversations)

@app.route('/messages/<int:user_id>')
def chat(user_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    current_user_id = session['user_id']
    other_user = get_user_by_id(user_id)
    if not other_user:
        flash('Пользователь не найден.', 'error')
        return redirect(url_for('inbox'))

    messages = get_messages(current_user_id, user_id)
    # Отмечаем входящие сообщения как прочитанные
    unread_ids = [msg['id'] for msg in messages if msg['receiver_id'] == current_user_id and not msg['is_read']]
    if unread_ids:
        mark_as_read(unread_ids, current_user_id)

    # Получаем украшения для каждого сообщения
    user_ids = set(msg['sender_id'] for msg in messages)
    user_styles = {}
    if user_ids:
        conn = get_db_connection()
        cur = get_cursor(conn)
        cur.execute('''
            SELECT ui.user_id, si.css_class
            FROM user_inventory ui
            JOIN shop_items si ON ui.item_id = si.id
            WHERE ui.user_id = ANY(%s) AND ui.equipped = TRUE AND si.category = 'post_decoration'
        ''', (list(user_ids),))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        for row in rows:
            user_styles.setdefault(row['user_id'], []).append(row['css_class'])
    for msg in messages:
        msg['styles'] = user_styles.get(msg['sender_id'], [])

    return render_template('chat.html', other_user=other_user, messages=messages)

@app.route('/send_message/<int:receiver_id>', methods=['POST'])
def send_message_route(receiver_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    content = request.form.get('content', '').strip()
    if not content:
        flash('Сообщение не может быть пустым.', 'error')
        return redirect(url_for('chat', user_id=receiver_id))
    send_message(session['user_id'], receiver_id, content)
    return redirect(url_for('chat', user_id=receiver_id))

# ---------- МАГАЗИН ----------
@app.route('/shop')
def shop():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    items = get_shop_items()
    inventory = get_user_inventory(user['id'])
    owned_ids = [item['id'] for item in inventory]
    return render_template('shop.html', user=user, items=items, owned_ids=owned_ids)

@app.route('/buy/<int:item_id>', methods=['POST'])
def buy_item(item_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    success, message = purchase_item(session['user_id'], item_id)
    flash(message, 'success' if success else 'error')
    return redirect(url_for('shop'))

@app.route('/inventory')
def inventory():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    user = get_user_by_id(session['user_id'])
    inventory_items = get_user_inventory(user['id'])
    return render_template('inventory.html', user=user, inventory=inventory_items)

@app.route('/equip/<int:item_id>', methods=['POST'])
def equip_item(item_id):
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    success, message = toggle_equip_item(session['user_id'], item_id)
    flash(message, 'success' if success else 'error')
    return redirect(url_for('inventory'))

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
    user_ids = set(post['user_id'] for post in posts)
    user_styles = {}
    if user_ids:
        conn = get_db_connection()
        cur = get_cursor(conn)
        cur.execute('''
            SELECT ui.user_id, si.css_class
            FROM user_inventory ui
            JOIN shop_items si ON ui.item_id = si.id
            WHERE ui.user_id = ANY(%s) AND ui.equipped = TRUE AND si.category = 'post_decoration'
        ''', (list(user_ids),))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        for row in rows:
            user_styles.setdefault(row['user_id'], []).append(row['css_class'])
    for post in posts:
        post['styles'] = user_styles.get(post['user_id'], [])
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
    app.run(host="0.0.0.0",debug=False, port=10000)