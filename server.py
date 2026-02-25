import sqlite3
import datetime
import random
import os
import uuid
from flask import Flask, request, jsonify, g, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Измените в продакшене!
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB макс. размер файла
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}

CORS(app)

# Создаём папку для загрузок, если её нет
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Настройка SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

# Настройка лимитера (хранилище в памяти)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

DATABASE = 'terminal.db'

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
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                FOREIGN KEY(from_id) REFERENCES users(id),
                FOREIGN KEY(to_id) REFERENCES users(id),
                FOREIGN KEY(image_id) REFERENCES images(id)
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
                FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(image_id) REFERENCES images(id)
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
        # Индексы для ускорения
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_to_delivered ON messages(to_id, delivered)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channels_name_active ON channels(name, active)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channel_messages_channel ON channel_messages(channel_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_images_uploader ON images(uploader_id)')

        # Системный пользователь (для уведомлений)
        cursor.execute("INSERT OR IGNORE INTO users (id, login, password_hash) VALUES (0, 'system', '')")
        db.commit()

init_db()

# ---------- Вспомогательные функции ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
            SELECT m.id, m.from_id, m.content, m.timestamp, u.login as from_login, m.image_id
            FROM messages m
            JOIN users u ON m.from_id = u.id
            WHERE m.to_id = ? AND m.delivered = 0
            ORDER BY m.timestamp
        ''', (user_id,))
    else:
        cursor = db.execute('''
            SELECT m.id, m.from_id, m.content, m.timestamp, u.login as from_login, m.image_id
            FROM messages m
            JOIN users u ON m.from_id = u.id
            WHERE m.to_id = ? AND m.from_id = ? AND m.delivered = 0
            ORDER BY m.timestamp
        ''', (user_id, from_id))
    messages = cursor.fetchall()
    if messages:
        ids = [m['id'] for m in messages]
        db.execute('UPDATE messages SET delivered = 1 WHERE id IN ({})'.format(','.join('?' * len(ids))), ids)
        db.commit()
    result = []
    for m in messages:
        msg = {
            'from_id': m['from_id'],
            'from_login': m['from_login'],
            'content': m['content'],
            'timestamp': m['timestamp'],
            'image_id': m['image_id']
        }
        result.append(msg)
    return result

def save_message(from_id, to_id, content, image_id=None):
    if len(content) > 1000:
        raise ValueError("Сообщение слишком длинное (макс. 1000 символов)")
    db = get_db()
    db.execute('''
        INSERT INTO messages (from_id, to_id, content, delivered, image_id) VALUES (?, ?, ?, 0, ?)
    ''', (from_id, to_id, content, image_id))
    db.commit()
    # Уведомление через WebSocket
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

def add_channel_message(channel_id, user_id, content, image_id=None):
    if len(content) > 1000:
        raise ValueError("Сообщение слишком длинное (макс. 1000 символов)")
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO channel_messages (channel_id, user_id, content, image_id) VALUES (?, ?, ?, ?)
    ''', (channel_id, user_id, content, image_id))
    db.commit()
    msg_id = cursor.lastrowid
    # Уведомить всех подписчиков канала
    cursor = db.execute('SELECT user_id FROM channel_subscribers WHERE channel_id = ?', (channel_id,))
    subscribers = [row['user_id'] for row in cursor.fetchall()]
    for sub_id in subscribers:
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

def get_channel_messages(user_id, channel_id):
    db = get_db()
    cursor = db.execute('SELECT last_read_message_id FROM channel_read_status WHERE user_id = ? AND channel_id = ?', (user_id, channel_id))
    row = cursor.fetchone()
    last_read = row['last_read_message_id'] if row else 0
    cursor = db.execute('''
        SELECT m.id, m.user_id, u.login as user_login, m.content, m.timestamp, m.image_id
        FROM channel_messages m
        JOIN users u ON m.user_id = u.id
        WHERE m.channel_id = ? AND m.id > ?
        ORDER BY m.timestamp
    ''', (channel_id, last_read))
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
            'image_id': m['image_id']
        })
    return result

def delete_channel(channel_id, owner_id):
    db = get_db()
    channel = get_channel_by_id(channel_id)
    if not channel:
        return False, "Канал не найден"
    if channel['owner_id'] != owner_id:
        return False, "Только владелец может удалить канал"
    # Получаем подписчиков до удаления
    cursor = db.execute('SELECT user_id FROM channel_subscribers WHERE channel_id = ?', (channel_id,))
    subscribers = [row['user_id'] for row in cursor.fetchall()]
    # Удаляем канал (каскадно удалятся подписчики и сообщения)
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

# ---------- Эндпоинты API ----------
@app.route('/register', methods=['POST'])
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
    user_id = create_user(login, password)
    return jsonify({'result': [f'Регистрация успешна. Ваш ID: {user_id}'], 'error': None}), 201

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or 'login' not in data or 'password' not in data:
        return jsonify({'error': 'Необходимо указать логин и пароль'}), 400
    login = data['login'].strip()
    password = data['password']
    user = get_user_by_login(login)
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Неверный логин или пароль'}), 401
    update_last_seen(user['id'])
    return jsonify({
        'result': [{
            'id': user['id'],
            'login': user['login'],
            'registered': user['registered']
        }],
        'error': None
    })

@app.route('/unread_summary', methods=['GET'])
def unread_summary():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Не указан user_id'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    personal = get_unread_summary(user_id)
    channels = get_channels_unread_summary(user_id)
    return jsonify({'result': {'personal': personal, 'channels': channels}, 'error': None})

@app.route('/read_messages', methods=['POST'])
def read_messages():
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'error': 'Не указан user_id'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    from_id = data.get('from_id')
    update_last_seen(user_id)  # действие пользователя
    messages = get_undelivered_messages(user_id, from_id)
    return jsonify({'result': messages, 'error': None})

@app.route('/channel/create', methods=['POST'])
@limiter.limit("10 per minute")
def channel_create():
    data = request.get_json()
    user_id = data.get('user_id')
    name = data.get('name', '').strip()
    if not user_id or not name:
        return jsonify({'error': 'Не указан user_id или название канала'}), 400
    if len(name) > 100:
        return jsonify({'error': 'Название канала слишком длинное'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    if get_channel_by_name(name):
        return jsonify({'error': 'Канал с таким названием уже существует'}), 400
    db = get_db()
    cursor = db.cursor()
    cursor.execute('INSERT INTO channels (name, owner_id) VALUES (?, ?)', (name, user_id))
    channel_id = cursor.lastrowid
    subscribe_user(channel_id, user_id)
    db.commit()
    return jsonify({'result': [f'Канал "{name}" создан. Вы автоматически подписаны.'], 'error': None})

@app.route('/channel/subscribe', methods=['POST'])
def channel_subscribe():
    data = request.get_json()
    user_id = data.get('user_id')
    channel_name = data.get('channel_name', '').strip()
    if not user_id or not channel_name:
        return jsonify({'error': 'Не указан user_id или название канала'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    channel = get_channel_by_name(channel_name)
    if not channel:
        return jsonify({'error': 'Канал не найден'}), 404
    if is_subscriber(channel['id'], user_id):
        return jsonify({'result': ['Вы уже подписаны на этот канал.'], 'error': None})
    subscribe_user(channel['id'], user_id)
    return jsonify({'result': [f'Вы подписались на канал "{channel_name}".'], 'error': None})

@app.route('/channel/unsubscribe', methods=['POST'])
def channel_unsubscribe():
    data = request.get_json()
    user_id = data.get('user_id')
    channel_name = data.get('channel_name', '').strip()
    if not user_id or not channel_name:
        return jsonify({'error': 'Не указан user_id или название канала'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    channel = get_channel_by_name(channel_name)
    if not channel:
        return jsonify({'error': 'Канал не найден'}), 404
    if not is_subscriber(channel['id'], user_id):
        return jsonify({'result': ['Вы не подписаны на этот канал.'], 'error': None})
    unsubscribe_user(channel['id'], user_id)
    return jsonify({'result': [f'Вы отписались от канала "{channel_name}".'], 'error': None})

@app.route('/channel/send', methods=['POST'])
@limiter.limit("10 per minute")
def channel_send():
    data = request.get_json()
    user_id = data.get('user_id')
    channel_name = data.get('channel_name', '').strip()
    content = data.get('content', '').strip()
    image_id = data.get('image_id')  # может быть None
    if not user_id or not channel_name or (not content and not image_id):
        return jsonify({'error': 'Не указан user_id, название канала или сообщение/изображение'}), 400
    if content and len(content) > 1000:
        return jsonify({'error': 'Сообщение слишком длинное (макс. 1000 символов)'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    channel = get_channel_by_name(channel_name)
    if not channel:
        return jsonify({'error': 'Канал не найден'}), 404
    if channel['owner_id'] != user_id:
        return jsonify({'error': 'Только владелец канала может отправлять сообщения'}), 403
    update_last_seen(user_id)
    add_channel_message(channel['id'], user_id, content or '', image_id)
    return jsonify({'result': [f'Сообщение отправлено в канал "{channel_name}".'], 'error': None})

@app.route('/channel/read', methods=['POST'])
def channel_read():
    data = request.get_json()
    user_id = data.get('user_id')
    channel_name = data.get('channel_name', '').strip()
    if not user_id or not channel_name:
        return jsonify({'error': 'Не указан user_id или название канала'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    channel = get_channel_by_name(channel_name)
    if not channel:
        return jsonify({'error': 'Канал не найден'}), 404
    if not is_subscriber(channel['id'], user_id):
        return jsonify({'error': 'Вы не подписаны на этот канал'}), 403
    update_last_seen(user_id)
    messages = get_channel_messages(user_id, channel['id'])
    result_lines = [f'Новые сообщения из канала "{channel_name}":']
    if messages:
        for msg in messages:
            line = f'[{msg["timestamp"]}] {msg["user_login"]}: {msg["content"]}'
            if msg['image_id']:
                line += f' [Изображение ID: {msg["image_id"]}]'
            result_lines.append(line)
    else:
        result_lines.append('Нет новых сообщений.')
    return jsonify({'result': result_lines, 'error': None})

@app.route('/channel/delete', methods=['POST'])
def channel_delete():
    data = request.get_json()
    user_id = data.get('user_id')
    channel_name = data.get('channel_name', '').strip()
    if not user_id or not channel_name:
        return jsonify({'error': 'Не указан user_id или название канала'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    channel = get_channel_by_name(channel_name)
    if not channel:
        return jsonify({'error': 'Канал не найден'}), 404
    ok, msg = delete_channel(channel['id'], user_id)
    if not ok:
        return jsonify({'error': msg}), 403
    return jsonify({'result': [msg], 'error': None})

@app.route('/command', methods=['POST'])
@limiter.limit("30 per minute")
def handle_command():
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'error': 'Не указан ID пользователя'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    command = data.get('command', '').strip().lower()
    args = data.get('args', {})
    result_lines = []
    if command == 'написать':
        to_id = args.get('to_id')
        message = args.get('message', '').strip()
        image_id = args.get('image_id')
        if not to_id or (not message and not image_id):
            result_lines.append('Ошибка: укажите ID получателя и сообщение или изображение.')
        else:
            if int(to_id) == user_id:
                result_lines.append('Ошибка: нельзя отправить сообщение самому себе.')
            else:
                recipient = get_user_by_id(to_id)
                if not recipient:
                    result_lines.append(f'Ошибка: пользователь с ID {to_id} не найден')
                else:
                    try:
                        save_message(user_id, to_id, message or '', image_id)
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
                result_lines.append(f'Статус: {status}')
    elif command == 'пинг':
        result_lines.append('понг')
    else:
        result_lines.append(f'Неизвестная команда: {command}')
    return jsonify({'result': result_lines, 'error': None})

@app.route('/online_users', methods=['GET'])
def online_users():
    minutes = request.args.get('minutes', default=2, type=int)
    users = get_online_users(minutes)
    return jsonify({'result': users, 'error': None})

@app.route('/upload_image', methods=['POST'])
def upload_image():
    user_id = request.form.get('user_id')
    if not user_id:
        return jsonify({'error': 'Не указан user_id'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    if 'image' not in request.files:
        return jsonify({'error': 'Файл не загружен'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'Недопустимый тип файла. Разрешены: png, jpg, jpeg, gif, bmp, webp'}), 400
    # Генерируем уникальное имя
    ext = file.filename.rsplit('.', 1)[1].lower()
    stored_filename = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], stored_filename))
    # Сохраняем в БД
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO images (original_filename, stored_filename, uploader_id)
        VALUES (?, ?, ?)
    ''', (file.filename, stored_filename, user_id))
    db.commit()
    image_id = cursor.lastrowid
    return jsonify({'result': {'image_id': image_id}, 'error': None})

@app.route('/image/<int:image_id>')
def get_image(image_id):
    db = get_db()
    cursor = db.execute('SELECT stored_filename FROM images WHERE id = ?', (image_id,))
    row = cursor.fetchone()
    if not row:
        return 'Изображение не найдено', 404
    return send_from_directory(app.config['UPLOAD_FOLDER'], row['stored_filename'])

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# ---------- WebSocket события ----------
@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

@socketio.on('subscribe')
def handle_subscribe(data):
    user_id = data.get('user_id')
    if user_id:
        room = f'user_{user_id}'
        join_room(room)
        print(f'User {user_id} subscribed to {room}')

@socketio.on('unsubscribe')
def handle_unsubscribe(data):
    user_id = data.get('user_id')
    if user_id:
        room = f'user_{user_id}'
        leave_room(room)
        print(f'User {user_id} unsubscribed from {room}')

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
