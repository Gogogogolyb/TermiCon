import sqlite3
import datetime
import random
import os
import uuid
import logging
import magic
from flask import Flask, request, jsonify, g, send_from_directory, abort
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB
ALLOWED_MIME_TYPES = {
    'image/png', 'image/jpeg', 'image/gif', 'image/bmp', 'image/webp',
    'audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/mp4', 'audio/flac'
}
EXTENSION_TO_MIME = {
    'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'gif': 'image/gif', 'bmp': 'image/bmp', 'webp': 'image/webp',
    'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg',
    'm4a': 'audio/mp4', 'flac': 'audio/flac'
}

CORS(app)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

socketio = SocketIO(app, cors_allowed_origins="*")

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

DATABASE = 'terminal.db'

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        # Пользователи
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                login TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                registered TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                avatar_id INTEGER,
                color_pref TEXT DEFAULT 'зеленый',
                FOREIGN KEY(avatar_id) REFERENCES images(id)
            )
        ''')
        # Личные сообщения
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                delivered BOOLEAN DEFAULT 0,
                image_id INTEGER,
                audio_id INTEGER,
                FOREIGN KEY(from_id) REFERENCES users(id),
                FOREIGN KEY(to_id) REFERENCES users(id),
                FOREIGN KEY(image_id) REFERENCES images(id),
                FOREIGN KEY(audio_id) REFERENCES audio(id)
            )
        ''')
        # Каналы
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                owner_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT 1,
                FOREIGN KEY(owner_id) REFERENCES users(id)
            )
        ''')
        # Подписчики каналов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channel_subscribers (
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (channel_id, user_id),
                FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        # Сообщения каналов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channel_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                image_id INTEGER,
                audio_id INTEGER,
                FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(image_id) REFERENCES images(id),
                FOREIGN KEY(audio_id) REFERENCES audio(id)
            )
        ''')
        # Статус прочтения каналов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channel_read_status (
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                last_read_message_id INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, channel_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE
            )
        ''')
        # Изображения
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                uploader_id INTEGER NOT NULL,
                uploaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(uploader_id) REFERENCES users(id)
            )
        ''')
        # Аудио
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                uploader_id INTEGER NOT NULL,
                uploaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                duration INTEGER,
                FOREIGN KEY(uploader_id) REFERENCES users(id)
            )
        ''')
        # Индексы
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_to_delivered ON messages(to_id, delivered)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channels_name_active ON channels(name, active)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channel_messages_channel ON channel_messages(channel_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_uploader ON images(uploader_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_audio_uploader ON audio(uploader_id)')

        cursor.execute("INSERT OR IGNORE INTO users (id, login, password_hash) VALUES (0, 'system', '')")
        db.commit()
        logger.info("Database initialized")

init_db()

# ---------- Декораторы ----------
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = request.get_json().get('user_id') if request.is_json else request.args.get('user_id')
        if not user_id:
            return jsonify({'error': 'Не указан user_id'}), 401
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        if not user:
            return jsonify({'error': 'Пользователь не найден'}), 401
        g.user = user
        return f(*args, **kwargs)
    return decorated

def requires_channel(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        data = request.get_json()
        channel_name = data.get('channel_name') if data else None
        if not channel_name:
            return jsonify({'error': 'Не указан канал'}), 400
        db = get_db()
        channel = db.execute('SELECT * FROM channels WHERE name = ? AND active = 1', (channel_name,)).fetchone()
        if not channel:
            return jsonify({'error': 'Канал не найден'}), 404
        g.channel = channel
        return f(*args, **kwargs)
    return decorated

def owns_media(media_type):
    def decorator(f):
        @wraps(f)
        def decorated(media_id, *args, **kwargs):
            user_id = request.args.get('user_id')
            if not user_id:
                # Пытаемся получить из JSON
                if request.is_json:
                    user_id = request.get_json().get('user_id')
            if not user_id:
                return jsonify({'error': 'Не указан user_id'}), 401
            db = get_db()
            user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            if not user:
                return jsonify({'error': 'Пользователь не найден'}), 401
            g.user = user

            # Проверка прав доступа к медиа
            if media_type == 'image':
                media = db.execute('SELECT * FROM images WHERE id = ?', (media_id,)).fetchone()
            else:
                media = db.execute('SELECT * FROM audio WHERE id = ?', (media_id,)).fetchone()
            if not media:
                abort(404)

            # Если пользователь является владельцем файла — разрешаем
            if media['uploader_id'] == user_id:
                g.media = media
                return f(media_id, *args, **kwargs)

            # Проверяем, участвует ли пользователь в диалогах/каналах, где использовался этот файл
            if media_type == 'image':
                # Проверка личных сообщений
                msg = db.execute('''
                    SELECT 1 FROM messages
                    WHERE image_id = ? AND (from_id = ? OR to_id = ?)
                ''', (media_id, user_id, user_id)).fetchone()
                if not msg:
                    # Проверка сообщений каналов
                    msg = db.execute('''
                        SELECT 1 FROM channel_messages cm
                        JOIN channel_subscribers cs ON cm.channel_id = cs.channel_id
                        WHERE cm.image_id = ? AND cs.user_id = ?
                    ''', (media_id, user_id)).fetchone()
            else:  # audio
                msg = db.execute('''
                    SELECT 1 FROM messages
                    WHERE audio_id = ? AND (from_id = ? OR to_id = ?)
                ''', (media_id, user_id, user_id)).fetchone()
                if not msg:
                    msg = db.execute('''
                        SELECT 1 FROM channel_messages cm
                        JOIN channel_subscribers cs ON cm.channel_id = cs.channel_id
                        WHERE cm.audio_id = ? AND cs.user_id = ?
                    ''', (media_id, user_id)).fetchone()

            if not msg:
                abort(403)

            g.media = media
            return f(media_id, *args, **kwargs)
        return decorated
    return decorator

# ---------- Вспомогательные функции (без изменений, но добавлены новые) ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in EXTENSION_TO_MIME

def validate_mime(file):
    mime = magic.from_buffer(file.read(2048), mime=True)
    file.seek(0)
    return mime in ALLOWED_MIME_TYPES

def get_user_by_login(login):
    db = get_db()
    cursor = db.execute('SELECT * FROM users WHERE login = ?', (login,))
    return cursor.fetchone()

def get_user_by_id(user_id):
    db = get_db()
    cursor = db.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    return cursor.fetchone()

def generate_unique_id():
    db = get_db()
    while True:
        new_id = random.randint(10000000, 99999999)
        cursor = db.execute('SELECT id FROM users WHERE id = ?', (new_id,))
        if not cursor.fetchone():
            return new_id

def create_user(login, password):
    password_hash = generate_password_hash(password)
    db = get_db()
    user_id = generate_unique_id()
    db.execute('INSERT INTO users (id, login, password_hash) VALUES (?, ?, ?)',
               (user_id, login, password_hash))
    db.commit()
    logger.info(f"New user registered: {login} (ID: {user_id})")
    return user_id

def update_last_seen(user_id):
    db = get_db()
    db.execute('UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE id = ?', (user_id,))
    db.commit()

def get_unread_summary(user_id):
    db = get_db()
    cursor = db.execute('''
        SELECT u.id as from_id, u.login as from_login, COUNT(*) as count
        FROM messages m
        JOIN users u ON m.from_id = u.id
        WHERE m.to_id = ? AND m.delivered = 0
        GROUP BY m.from_id
        ORDER BY MAX(m.timestamp) DESC
    ''', (user_id,))
    return [dict(row) for row in cursor.fetchall()]

def get_undelivered_messages(user_id, from_id=None):
    db = get_db()
    if from_id is None:
        cursor = db.execute('''
            SELECT m.id, m.from_id, m.content, m.timestamp, u.login as from_login, m.image_id, m.audio_id
            FROM messages m
            JOIN users u ON m.from_id = u.id
            WHERE m.to_id = ? AND m.delivered = 0
            ORDER BY m.timestamp
        ''', (user_id,))
    else:
        cursor = db.execute('''
            SELECT m.id, m.from_id, m.content, m.timestamp, u.login as from_login, m.image_id, m.audio_id
            FROM messages m
            JOIN users u ON m.from_id = u.id
            WHERE m.to_id = ? AND m.from_id = ? AND m.delivered = 0
            ORDER BY m.timestamp
        ''', (user_id, from_id))
    messages = cursor.fetchall()
    if messages:
        ids = [m['id'] for m in messages]
        placeholders = ','.join('?' * len(ids))
        db.execute(f'UPDATE messages SET delivered = 1 WHERE id IN ({placeholders})', ids)
        db.commit()
    result = []
    for m in messages:
        msg = {
            'from_id': m['from_id'],
            'from_login': m['from_login'],
            'content': m['content'],
            'timestamp': m['timestamp'],
            'image_id': m['image_id'],
            'audio_id': m['audio_id']
        }
        result.append(msg)
    return result

def save_message(from_id, to_id, content, image_id=None, audio_id=None):
    if len(content) > 1000:
        raise ValueError("Сообщение слишком длинное (макс. 1000 символов)")
    # Проверка принадлежности медиа
    db = get_db()
    if image_id:
        img = db.execute('SELECT id FROM images WHERE id = ? AND uploader_id = ?', (image_id, from_id)).fetchone()
        if not img:
            raise ValueError("Изображение не найдено или не принадлежит вам")
    if audio_id:
        aud = db.execute('SELECT id FROM audio WHERE id = ? AND uploader_id = ?', (audio_id, from_id)).fetchone()
        if not aud:
            raise ValueError("Аудио не найдено или не принадлежит вам")

    db.execute('''
        INSERT INTO messages (from_id, to_id, content, delivered, image_id, audio_id) VALUES (?, ?, ?, 0, ?, ?)
    ''', (from_id, to_id, content, image_id, audio_id))
    db.commit()
    socketio.emit(f'user_{to_id}_new_message', {'from': from_id}, room=f'user_{to_id}')

def get_channel_by_name(name):
    db = get_db()
    cursor = db.execute('SELECT * FROM channels WHERE name = ? AND active = 1', (name,))
    return cursor.fetchone()

def get_channel_by_id(channel_id):
    db = get_db()
    cursor = db.execute('SELECT * FROM channels WHERE id = ? AND active = 1', (channel_id,))
    return cursor.fetchone()

def is_subscriber(channel_id, user_id):
    db = get_db()
    cursor = db.execute('SELECT 1 FROM channel_subscribers WHERE channel_id = ? AND user_id = ?', (channel_id, user_id))
    return cursor.fetchone() is not None

def subscribe_user(channel_id, user_id):
    db = get_db()
    db.execute('INSERT OR IGNORE INTO channel_subscribers (channel_id, user_id) VALUES (?, ?)', (channel_id, user_id))
    db.commit()

def unsubscribe_user(channel_id, user_id):
    db = get_db()
    db.execute('DELETE FROM channel_subscribers WHERE channel_id = ? AND user_id = ?', (channel_id, user_id))
    db.commit()

def add_channel_message(channel_id, user_id, content, image_id=None, audio_id=None):
    if len(content) > 1000:
        raise ValueError("Сообщение слишком длинное (макс. 1000 символов)")
    db = get_db()
    # Проверка принадлежности медиа
    if image_id:
        img = db.execute('SELECT id FROM images WHERE id = ? AND uploader_id = ?', (image_id, user_id)).fetchone()
        if not img:
            raise ValueError("Изображение не найдено или не принадлежит вам")
    if audio_id:
        aud = db.execute('SELECT id FROM audio WHERE id = ? AND uploader_id = ?', (audio_id, user_id)).fetchone()
        if not aud:
            raise ValueError("Аудио не найдено или не принадлежит вам")

    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO channel_messages (channel_id, user_id, content, image_id, audio_id) VALUES (?, ?, ?, ?, ?)
    ''', (channel_id, user_id, content, image_id, audio_id))
    db.commit()
    msg_id = cursor.lastrowid
    cursor = db.execute('SELECT user_id FROM channel_subscribers WHERE channel_id = ?', (channel_id,))
    subscribers = [row['user_id'] for row in cursor.fetchall()]
    for sub_id in subscribers:
        if sub_id != user_id:  # не отправляем уведомление автору
            socketio.emit(f'user_{sub_id}_new_channel_message', {'channel': channel_id}, room=f'user_{sub_id}')
    return msg_id

def get_channel_unread_count(user_id, channel_id):
    db = get_db()
    cursor = db.execute('''
        SELECT COUNT(*) as cnt
        FROM channel_messages m
        LEFT JOIN channel_read_status rs ON rs.channel_id = m.channel_id AND rs.user_id = ?
        WHERE m.channel_id = ? AND m.id > IFNULL(rs.last_read_message_id, 0)
    ''', (user_id, channel_id))
    row = cursor.fetchone()
    return row['cnt'] if row else 0

def mark_channel_messages_read(user_id, channel_id, up_to_message_id=None):
    db = get_db()
    if up_to_message_id is None:
        cursor = db.execute('SELECT MAX(id) as max_id FROM channel_messages WHERE channel_id = ?', (channel_id,))
        row = cursor.fetchone()
        up_to_message_id = row['max_id'] if row and row['max_id'] else 0
    db.execute('''
        INSERT INTO channel_read_status (user_id, channel_id, last_read_message_id)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, channel_id) DO UPDATE SET last_read_message_id = excluded.last_read_message_id
    ''', (user_id, channel_id, up_to_message_id))
    db.commit()

def get_channel_messages(user_id, channel_id, limit=50, offset=0):
    db = get_db()
    cursor = db.execute('SELECT last_read_message_id FROM channel_read_status WHERE user_id = ? AND channel_id = ?', (user_id, channel_id))
    row = cursor.fetchone()
    last_read = row['last_read_message_id'] if row else 0
    cursor = db.execute('''
        SELECT m.id, m.user_id, u.login as user_login, m.content, m.timestamp, m.image_id, m.audio_id
        FROM channel_messages m
        JOIN users u ON m.user_id = u.id
        WHERE m.channel_id = ? AND m.id > ?
        ORDER BY m.timestamp
        LIMIT ? OFFSET ?
    ''', (channel_id, last_read, limit, offset))
    messages = cursor.fetchall()
    if messages:
        max_id = max(m['id'] for m in messages)
        mark_channel_messages_read(user_id, channel_id, max_id)
    result = []
    for m in messages:
        result.append({
            'id': m['id'],
            'user_id': m['user_id'],
            'user_login': m['user_login'],
            'content': m['content'],
            'timestamp': m['timestamp'],
            'image_id': m['image_id'],
            'audio_id': m['audio_id']
        })
    return result

def delete_channel(channel_id, owner_id):
    db = get_db()
    channel = get_channel_by_id(channel_id)
    if not channel:
        return False, "Канал не найден"
    if channel['owner_id'] != owner_id:
        return False, "Только владелец может удалить канал"
    cursor = db.execute('SELECT user_id FROM channel_subscribers WHERE channel_id = ?', (channel_id,))
    subscribers = [row['user_id'] for row in cursor.fetchall()]
    db.execute('DELETE FROM channels WHERE id = ?', (channel_id,))
    db.commit()
    for sub_id in subscribers:
        save_message(0, sub_id, f"Канал '{channel['name']}' был удален владельцем.")
        socketio.emit(f'user_{sub_id}_new_message', {'from': 0}, room=f'user_{sub_id}')
    return True, "Канал удалён"

def get_channels_unread_summary(user_id):
    db = get_db()
    cursor = db.execute('''
        SELECT c.id as channel_id, c.name as channel_name, u.login as owner_login,
               (SELECT COUNT(*) FROM channel_messages cm
                LEFT JOIN channel_read_status crs ON crs.channel_id = cm.channel_id AND crs.user_id = ?
                WHERE cm.channel_id = c.id AND cm.id > IFNULL(crs.last_read_message_id, 0)) as unread_count
        FROM channels c
        JOIN users u ON c.owner_id = u.id
        JOIN channel_subscribers cs ON cs.channel_id = c.id
        WHERE cs.user_id = ? AND c.active = 1
        ORDER BY unread_count DESC
    ''', (user_id, user_id))
    return [dict(row) for row in cursor.fetchall()]

def get_user_channels(user_id):
    db = get_db()
    cursor = db.execute('''
        SELECT c.id, c.name, c.owner_id, u.login as owner_login,
               (SELECT COUNT(*) FROM channel_messages cm
                LEFT JOIN channel_read_status crs ON crs.channel_id = c.id AND crs.user_id = ?
                WHERE cm.channel_id = c.id AND cm.id > IFNULL(crs.last_read_message_id, 0)) as unread_count
        FROM channels c
        JOIN users u ON c.owner_id = u.id
        JOIN channel_subscribers cs ON cs.channel_id = c.id
        WHERE cs.user_id = ? AND c.active = 1
        ORDER BY c.name
    ''', (user_id, user_id))
    return [dict(row) for row in cursor.fetchall()]

def is_online(user_id, minutes=2):
    user = get_user_by_id(user_id)
    if not user:
        return False
    last_seen = datetime.datetime.fromisoformat(user['last_seen'])
    delta = datetime.datetime.now() - last_seen
    return delta.total_seconds() < minutes * 60

def get_online_users(minutes=2):
    db = get_db()
    cutoff = datetime.datetime.now() - datetime.timedelta(minutes=minutes)
    cursor = db.execute('''
        SELECT id, login, last_seen FROM users
        WHERE last_seen >= ? AND id != 0
        ORDER BY last_seen DESC
    ''', (cutoff.isoformat(),))
    return [dict(row) for row in cursor.fetchall()]

# ---------- Эндпоинты ----------
@app.route('/register', methods=['POST'])
@limiter.limit("3 per minute")
def register():
    data = request.get_json()
    if not data or 'login' not in data or 'password' not in data:
        return jsonify({'error': 'Необходимо указать логин и пароль'}), 400
    login = data['login'].strip()
    password = data['password']
    if not login or not password:
        return jsonify({'error': 'Логин и пароль не могут быть пустыми'}), 400
    if len(login) > 50 or len(password) > 128:
        return jsonify({'error': 'Логин или пароль слишком длинные'}), 400
    if get_user_by_login(login):
        return jsonify({'error': 'Пользователь с таким логином уже существует'}), 400
    try:
        user_id = create_user(login, password)
        return jsonify({'result': [f'Регистрация успешна. Ваш ID: {user_id}'], 'error': None}), 201
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    data = request.get_json()
    if not data or 'login' not in data or 'password' not in data:
        return jsonify({'error': 'Необходимо указать логин и пароль'}), 400
    login = data['login'].strip()
    password = data['password']
    try:
        user = get_user_by_login(login)
        if not user or not check_password_hash(user['password_hash'], password):
            return jsonify({'error': 'Неверный логин или пароль'}), 401
        update_last_seen(user['id'])
        logger.info(f"User logged in: {login} (ID: {user['id']})")
        return jsonify({
            'result': [{
                'id': user['id'],
                'login': user['login'],
                'registered': user['registered'],
                'avatar_id': user['avatar_id'],
                'color_pref': user['color_pref']
            }],
            'error': None
        })
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/check_session', methods=['POST'])
def check_session():
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'error': 'Не указан user_id'}), 400
    try:
        user = get_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'Пользователь не найден'}), 404
        return jsonify({
            'result': {
                'id': user['id'],
                'login': user['login'],
                'registered': user['registered'],
                'avatar_id': user['avatar_id'],
                'color_pref': user['color_pref']
            },
            'error': None
        })
    except Exception as e:
        logger.error(f"Check session error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/unread_summary', methods=['GET'])
@requires_auth
def unread_summary():
    user_id = g.user['id']
    try:
        personal = get_unread_summary(user_id)
        channels = get_channels_unread_summary(user_id)
        return jsonify({'result': {'personal': personal, 'channels': channels}, 'error': None})
    except Exception as e:
        logger.error(f"Unread summary error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/read_messages', methods=['POST'])
@requires_auth
def read_messages():
    user_id = g.user['id']
    data = request.get_json()
    from_id = data.get('from_id')
    try:
        update_last_seen(user_id)
        messages = get_undelivered_messages(user_id, from_id)
        return jsonify({'result': messages, 'error': None})
    except Exception as e:
        logger.error(f"Read messages error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/channel/create', methods=['POST'])
@limiter.limit("10 per minute")
@requires_auth
def channel_create():
    user_id = g.user['id']
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Не указано название канала'}), 400
    if len(name) > 100:
        return jsonify({'error': 'Название канала слишком длинное'}), 400
    if get_channel_by_name(name):
        return jsonify({'error': 'Канал с таким названием уже существует'}), 400
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute('INSERT INTO channels (name, owner_id) VALUES (?, ?)', (name, user_id))
        channel_id = cursor.lastrowid
        subscribe_user(channel_id, user_id)
        db.commit()
        logger.info(f"Channel created: {name} by user {user_id}")
        return jsonify({'result': [f'Канал "{name}" создан. Вы автоматически подписаны.'], 'error': None})
    except Exception as e:
        logger.error(f"Channel create error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/channel/subscribe', methods=['POST'])
@requires_auth
@requires_channel
def channel_subscribe():
    user_id = g.user['id']
    channel = g.channel
    if is_subscriber(channel['id'], user_id):
        return jsonify({'result': ['Вы уже подписаны на этот канал.'], 'error': None})
    try:
        subscribe_user(channel['id'], user_id)
        return jsonify({'result': [f'Вы подписались на канал "{channel["name"]}".'], 'error': None})
    except Exception as e:
        logger.error(f"Channel subscribe error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/channel/unsubscribe', methods=['POST'])
@requires_auth
@requires_channel
def channel_unsubscribe():
    user_id = g.user['id']
    channel = g.channel
    if not is_subscriber(channel['id'], user_id):
        return jsonify({'result': ['Вы не подписаны на этот канал.'], 'error': None})
    try:
        unsubscribe_user(channel['id'], user_id)
        return jsonify({'result': [f'Вы отписались от канала "{channel["name"]}".'], 'error': None})
    except Exception as e:
        logger.error(f"Channel unsubscribe error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/channel/send', methods=['POST'])
@limiter.limit("10 per minute")
@requires_auth
@requires_channel
def channel_send():
    user_id = g.user['id']
    channel = g.channel
    data = request.get_json()
    content = data.get('content', '').strip()
    image_id = data.get('image_id')
    audio_id = data.get('audio_id')
    if not content and not image_id and not audio_id:
        return jsonify({'error': 'Не указано сообщение, изображение или аудио'}), 400
    if content and len(content) > 1000:
        return jsonify({'error': 'Сообщение слишком длинное (макс. 1000 символов)'}), 400
    if channel['owner_id'] != user_id:
        return jsonify({'error': 'Только владелец канала может отправлять сообщения'}), 403
    try:
        update_last_seen(user_id)
        add_channel_message(channel['id'], user_id, content or '', image_id, audio_id)
        return jsonify({'result': [f'Сообщение отправлено в канал "{channel["name"]}".'], 'error': None})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Channel send error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/channel/read', methods=['POST'])
@requires_auth
@requires_channel
def channel_read():
    user_id = g.user['id']
    channel = g.channel
    data = request.get_json()
    limit = data.get('limit', 50)
    offset = data.get('offset', 0)
    if not is_subscriber(channel['id'], user_id):
        return jsonify({'error': 'Вы не подписаны на этот канал'}), 403
    try:
        update_last_seen(user_id)
        messages = get_channel_messages(user_id, channel['id'], limit, offset)
        result_lines = [f'Новые сообщения из канала "{channel["name"]}":']
        if messages:
            for msg in messages:
                line = f'[{msg["timestamp"]}] {msg["user_login"]}: {msg["content"]}'
                if msg['image_id']:
                    line += f' [Изображение ID: {msg["image_id"]}]'
                if msg['audio_id']:
                    line += f' [Аудио ID: {msg["audio_id"]}]'
                result_lines.append(line)
        else:
            result_lines.append('Нет новых сообщений.')
        return jsonify({'result': result_lines, 'error': None})
    except Exception as e:
        logger.error(f"Channel read error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/channel/delete', methods=['POST'])
@requires_auth
@requires_channel
def channel_delete():
    user_id = g.user['id']
    channel = g.channel
    try:
        ok, msg = delete_channel(channel['id'], user_id)
        if not ok:
            return jsonify({'error': msg}), 403
        return jsonify({'result': [msg], 'error': None})
    except Exception as e:
        logger.error(f"Channel delete error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/command', methods=['POST'])
@limiter.limit("30 per minute")
@requires_auth
def handle_command():
    user_id = g.user['id']
    data = request.get_json()
    command = data.get('command', '').strip().lower()
    args = data.get('args', {})
    result_lines = []
    try:
        if command == 'написать':
            to_id = args.get('to_id')
            message = args.get('message', '').strip()
            image_id = args.get('image_id')
            audio_id = args.get('audio_id')
            if not to_id or (not message and not image_id and not audio_id):
                result_lines.append('Ошибка: укажите ID получателя и сообщение, изображение или аудио.')
            else:
                if int(to_id) == user_id:
                    result_lines.append('Ошибка: нельзя отправить сообщение самому себе.')
                else:
                    recipient = get_user_by_id(to_id)
                    if not recipient:
                        result_lines.append(f'Ошибка: пользователь с ID {to_id} не найден')
                    else:
                        try:
                            save_message(user_id, to_id, message or '', image_id, audio_id)
                            result_lines.append(f'Сообщение для {recipient["login"]} (ID {to_id}) отправлено')
                            update_last_seen(user_id)
                        except ValueError as e:
                            result_lines.append(f'Ошибка: {e}')
        elif command == 'профиль':
            profile_id = args.get('profile_id')
            if not profile_id:
                result_lines.append('Ошибка: укажите ID пользователя.')
            else:
                profile_user = get_user_by_id(profile_id)
                if not profile_user:
                    result_lines.append(f'Пользователь с ID {profile_id} не найден')
                else:
                    online = is_online(profile_id)
                    status = 'в сети' if online else 'оффлайн'
                    result_lines.append(f'Логин: {profile_user["login"]}')
                    result_lines.append(f'ID: {profile_user["id"]}')
                    result_lines.append(f'Дата регистрации: {profile_user["registered"]}')
                    if profile_user['avatar_id']:
                        result_lines.append(f'Аватар ID: {profile_user["avatar_id"]}')
                    result_lines.append(f'Цвет текста: {profile_user["color_pref"]}')
                    result_lines.append(f'Статус: {status}')
        elif command == 'аватар':
            image_id = args.get('image_id')
            if not image_id:
                result_lines.append('Ошибка: укажите ID изображения.')
            else:
                db = get_db()
                cursor = db.execute('SELECT id FROM images WHERE id = ? AND uploader_id = ?', (image_id, user_id))
                if not cursor.fetchone():
                    result_lines.append('Ошибка: изображение не найдено или вы не являетесь его владельцем.')
                else:
                    db.execute('UPDATE users SET avatar_id = ? WHERE id = ?', (image_id, user_id))
                    db.commit()
                    result_lines.append(f'Аватар установлен. ID изображения: {image_id}')
                    update_last_seen(user_id)
        elif command == 'пинг':
            result_lines.append('понг')
        else:
            result_lines.append(f'Неизвестная команда: {command}')
    except Exception as e:
        logger.error(f"Command error: {e}")
        result_lines.append('Внутренняя ошибка сервера')
    return jsonify({'result': result_lines, 'error': None})

@app.route('/online_users', methods=['GET'])
def online_users():
    minutes = request.args.get('minutes', default=2, type=int)
    try:
        users = get_online_users(minutes)
        return jsonify({'result': users, 'error': None})
    except Exception as e:
        logger.error(f"Online users error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/my_channels', methods=['GET'])
@requires_auth
def my_channels():
    user_id = g.user['id']
    try:
        channels = get_user_channels(user_id)
        return jsonify({'result': channels, 'error': None})
    except Exception as e:
        logger.error(f"My channels error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/set_color', methods=['POST'])
@requires_auth
def set_color():
    user_id = g.user['id']
    data = request.get_json()
    color = data.get('color')
    if not color:
        return jsonify({'error': 'Не указан цвет'}), 400
    valid_colors = ['зеленый', 'красный', 'синий', 'желтый', 'оранжевый', 'розовый', 'голубой']
    if color not in valid_colors:
        return jsonify({'error': f'Недопустимый цвет. Доступны: {", ".join(valid_colors)}'}), 400
    try:
        db = get_db()
        db.execute('UPDATE users SET color_pref = ? WHERE id = ?', (color, user_id))
        db.commit()
        update_last_seen(user_id)
        return jsonify({'result': f'Цвет текста изменён на {color}', 'error': None})
    except Exception as e:
        logger.error(f"Set color error: {e}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/upload_image', methods=['POST'])
@requires_auth
def upload_image():
    user_id = g.user['id']
    if 'image' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'Недопустимый тип файла по расширению'}), 400
    if not validate_mime(file):
        return jsonify({'error': 'Недопустимый MIME-тип файла'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    stored_filename = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], stored_filename))

    db = get_db()
    cursor = db.cursor()
    mime_type = EXTENSION_TO_MIME.get(ext, 'application/octet-stream')
    if mime_type.startswith('audio/'):
        cursor.execute('''
            INSERT INTO audio (original_filename, stored_filename, uploader_id)
            VALUES (?, ?, ?)
        ''', (file.filename, stored_filename, user_id))
        db.commit()
        media_id = cursor.lastrowid
        media_type = 'audio'
    else:
        cursor.execute('''
            INSERT INTO images (original_filename, stored_filename, uploader_id)
            VALUES (?, ?, ?)
        ''', (file.filename, stored_filename, user_id))
        db.commit()
        media_id = cursor.lastrowid
        media_type = 'image'

    logger.info(f"File uploaded: {file.filename} (ID: {media_id}, type: {media_type}) by user {user_id}")
    return jsonify({'result': {'media_id': media_id, 'type': media_type}, 'error': None})

@app.route('/image/<int:image_id>')
@owns_media('image')
def get_image(image_id):
    return send_from_directory(app.config['UPLOAD_FOLDER'], g.media['stored_filename'])

@app.route('/audio/<int:audio_id>')
@owns_media('audio')
def get_audio(audio_id):
    return send_from_directory(app.config['UPLOAD_FOLDER'], g.media['stored_filename'])

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# ---------- WebSocket ----------
@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected')

@socketio.on('subscribe')
def handle_subscribe(data):
    user_id = data.get('user_id')
    if user_id:
        room = f'user_{user_id}'
        join_room(room)
        logger.info(f'User {user_id} subscribed to {room}')

@socketio.on('unsubscribe')
def handle_unsubscribe(data):
    user_id = data.get('user_id')
    if user_id:
        room = f'user_{user_id}'
        leave_room(room)
        logger.info(f'User {user_id} unsubscribed from {room}')

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
