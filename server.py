# server.py (полная версия с экспортом)
import sqlite3
import datetime
import random
import os
import json
from flask import Flask, request, jsonify, g, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Измените в продакшене!
CORS(app)

# Настройка SocketIO
socketio = SocketIO(app, cors_allowed_origins="*")

# Настройка лимитера (хранилище в памяти)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

DATABASE = 'terminal.db'
EXPORT_DIR = 'exports'
os.makedirs(EXPORT_DIR, exist_ok=True)

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
                FOREIGN KEY(from_id) REFERENCES users(id),
                FOREIGN KEY(to_id) REFERENCES users(id)
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
                FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id)
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
        # Индексы для ускорения
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_to_delivered ON messages(to_id, delivered)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(from_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channels_name_active ON channels(name, active)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_channel_messages_channel ON channel_messages(channel_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)')

        # Системный пользователь (для уведомлений)
        cursor.execute("INSERT OR IGNORE INTO users (id, login, password_hash) VALUES (0, 'system', '')")
        db.commit()

init_db()

# ---------- Вспомогательные функции ----------
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
            SELECT m.id, m.from_id, m.content, m.timestamp, u.login as from_login
            FROM messages m
            JOIN users u ON m.from_id = u.id
            WHERE m.to_id = ? AND m.delivered = 0
            ORDER BY m.timestamp
        ''', (user_id,))
    else:
        cursor = db.execute('''
            SELECT m.id, m.from_id, m.content, m.timestamp, u.login as from_login
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
    return [{'from_id': m['from_id'], 'from_login': m['from_login'], 'content': m['content'], 'timestamp': m['timestamp']} for m in messages]

def save_message(from_id, to_id, content):
    if len(content) > 1000:
        raise ValueError("Сообщение слишком длинное (макс. 1000 символов)")
    db = get_db()
    db.execute('''
        INSERT INTO messages (from_id, to_id, content, delivered) VALUES (?, ?, ?, 0)
    ''', (from_id, to_id, content))
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

def add_channel_message(channel_id, user_id, content):
    if len(content) > 1000:
        raise ValueError("Сообщение слишком длинное (макс. 1000 символов)")
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO channel_messages (channel_id, user_id, content) VALUES (?, ?, ?)
    ''', (channel_id, user_id, content))
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
        SELECT m.id, m.user_id, u.login as user_login, m.content, m.timestamp
        FROM channel_messages m
        JOIN users u ON m.user_id = u.id
        WHERE m.channel_id = ? AND m.id > ?
        ORDER BY m.timestamp
    ''', (channel_id, last_read))
    messages = cursor.fetchall()
    if messages:
        max_id = max(m['id'] for m in messages)
        mark_channel_messages_read(user_id, channel_id, max_id)
    return [dict(m) for m in messages]

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
    db.execute('DELETE FROM channels WHERE id = ?', (channel_id,))
    db.commit()
    for sub_id in subscribers:
        if sub_id != owner_id:  # не отправляем владельцу, он уже знает
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
    update_last_seen(user_id)
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
    if not user_id or not channel_name or not content:
        return jsonify({'error': 'Не указан user_id, название канала или сообщение'}), 400
    if len(content) > 1000:
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
    add_channel_message(channel['id'], user_id, content)
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
            result_lines.append(f'[{msg["timestamp"]}] {msg["user_login"]}: {msg["content"]}')
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
        if not to_id or not message:
            result_lines.append('Ошибка: укажите ID получателя и сообщение.')
        else:
            if int(to_id) == user_id:
                result_lines.append('Ошибка: нельзя отправить сообщение самому себе.')
            else:
                recipient = get_user_by_id(to_id)
                if not recipient:
                    result_lines.append(f'Ошибка: пользователь с ID {to_id} не найден')
                else:
                    try:
                        save_message(user_id, to_id, message)
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

@app.route('/export_my_data', methods=['POST'])
def export_my_data():
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'error': 'Не указан user_id'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    db = get_db()
    # Получаем последние 100 сообщений, где пользователь отправитель или получатель
    cursor = db.execute('''
        SELECT m.id, m.from_id, m.to_id, m.content, m.timestamp,
               u_from.login as from_login, u_to.login as to_login
        FROM messages m
        JOIN users u_from ON m.from_id = u_from.id
        JOIN users u_to ON m.to_id = u_to.id
        WHERE m.from_id = ? OR m.to_id = ?
        ORDER BY m.timestamp DESC
        LIMIT 100
    ''', (user_id, user_id))
    messages = [dict(row) for row in cursor.fetchall()]

    user_data = {
        'id': user['id'],
        'login': user['login'],
        'registered': user['registered'],
        'last_seen': user['last_selast_seen']
    }

en']
    }

    export_data    export = {
       _data = {
        'user': user 'user':_data,
        ' user_data,
        'messages':messages': messages
 messages
       }

    filename }

    filename = f"user_{user = f"user_id}_{_{user_id}_{datetime.datetimedatetime.datetime.now().str.now().strftimeftime('%Y('%Y%m%m%d_%H%d_%%MH%M%S')}.%S')}.json"
json"
    file    filepath = os.pathpath = os.path.join(.join(EXPORTEXPORT_DIR,_DIR, filename)
 filename)
    with    with open(filepath, 'w open(filepath, 'w', encoding', encoding='utf-8='utf-8') as') as f:
 f:
        json.dump        json(export.dump(export_data, f, ensure__data, f,ascii ensure_ascii=False,=False, indent= indent=2)

    return2)

    return jsonify jsonify({'result':({'result': [f' [f'ДанДанныеные сохранены сохранены в файл { в файл {filename}filename}'], ''], 'error':error': None})

 None})

@app@app.route('/')
def index.route('/')
def index():
   ():
    return send_from return send_from_directory_directory('.', 'index('.', 'index.html')

# ---------.html')

- Web# ---------- WebSocket событияSocket события ----------
@socket ----------
io.on@socketio.on('connect('connect')
def')
def handle_ handle_connect():
    printconnect():
    print('Client('Client connected')

@socketio.on connected')

@socket('disio.on('disconnect')
def handleconnect')
_disconnectdef handle_disconnect():
   ():
    print('Client disconnected print('')

@sClient disconnected')

ocketio@socketio.on('subscribe')
def handle.on('subscribe')
def_subscribe handle_subscribe(data):
(data):
    user    user_id =_id = data.get('user data.get('user_id')
    if_id')
 user_id    if user_id:
       :
        room = f'user_{ room = f'user_{user_id}user_id}'
'
        join        join_room_room(room)
       (room print(f)
        print(f'User'User {user {user_id} subscribed to_id} subscribed to {room {room}')

}')

@socketio.on@socketio.on('un('unsubscribe')
subscribe')
def handle_unsubscribedef handle(data):
_unsubscribe(data):
    user_id =    user_id = data.get data.get('user('user_id')
_id')
    if    if user_id:
        user_id:
        room = f' room =user_{ f'user_{user_id}'
user_id}'
        leave        leave_room_room(room(room)
        print(f)
       'User {user print(f'User {user_id} unsub_id} unscribed from {roomsubscribed from {room}')

}')

if __if __name__name__ == '__main__':
 == '__main__':
    socket    socketio.run(app,io.run(app, host=' host='0.0.0.0.0.0.0', port=0', port=50005000, debug, debug=False)
=False)
