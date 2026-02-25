import sqlite3
import datetime
import os
from flask import Flask, request, jsonify, g, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

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
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                registered TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Таблица личных сообщений
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
        # Таблица каналов
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
        # Таблица подписчиков каналов
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
        # Таблица сообщений каналов
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
        # Таблица статуса прочтения сообщений каналов
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
        # Системный пользователь для уведомлений (id=0)
        cursor.execute("INSERT OR IGNORE INTO users (id, login, password_hash) VALUES (0, 'system', '')")
        db.commit()

def get_user_by_login(login):
    db = get_db()
    cursor = db.execute('SELECT * FROM users WHERE login = ?', (login,))
    return cursor.fetchone()

def get_user_by_id(user_id):
    db = get_db()
    cursor = db.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    return cursor.fetchone()

def create_user(login, password):
    password_hash = generate_password_hash(password)
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO users (login, password_hash) VALUES (?, ?)
    ''', (login, password_hash))
    db.commit()
    return cursor.lastrowid

def update_last_seen(user_id):
    db = get_db()
    db.execute('UPDATE users SET last_seen = CURRENT_TIMESTAMP WHERE id = ?', (user_id,))
    db.commit()

# --- Личные сообщения ---
def get_unread_summary(user_id):
    """Возвращает список отправителей и количество непрочитанных личных сообщений"""
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
    """Возвращает непрочитанные личные сообщения и помечает их доставленными"""
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
    db = get_db()
    db.execute('''
        INSERT INTO messages (from_id, to_id, content, delivered) VALUES (?, ?, ?, 0)
    ''', (from_id, to_id, content))
    db.commit()

# --- Каналы ---
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
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        INSERT INTO channel_messages (channel_id, user_id, content) VALUES (?, ?, ?)
    ''', (channel_id, user_id, content))
    db.commit()
    return cursor.lastrowid

def get_channel_unread_count(user_id, channel_id):
    """Возвращает количество непрочитанных сообщений в канале для пользователя"""
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
    """Отмечает сообщения канала как прочитанные до указанного ID (или все)"""
    db = get_db()
    if up_to_message_id is None:
        # Получаем максимальный ID сообщения в канале
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
    """Возвращает все непрочитанные сообщения канала и помечает их прочитанными"""
    db = get_db()
    # Получаем последний прочитанный ID
    cursor = db.execute('SELECT last_read_message_id FROM channel_read_status WHERE user_id = ? AND channel_id = ?', (user_id, channel_id))
    row = cursor.fetchone()
    last_read = row['last_read_message_id'] if row else 0

    # Получаем новые сообщения
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
    """Удаляет канал (помечает неактивным) и уведомляет подписчиков"""
    db = get_db()
    channel = get_channel_by_id(channel_id)
    if not channel:
        return False, "Канал не найден"
    if channel['owner_id'] != owner_id:
        return False, "Только владелец может удалить канал"

    # Получаем всех подписчиков (кроме владельца)
    cursor = db.execute('SELECT user_id FROM channel_subscribers WHERE channel_id = ? AND user_id != ?', (channel_id, owner_id))
    subscribers = [row['user_id'] for row in cursor.fetchall()]

    # Помечаем канал как неактивный
    db.execute('UPDATE channels SET active = 0 WHERE id = ?', (channel_id,))

    # Отправляем системное сообщение каждому подписчику (как личное)
    for sub_id in subscribers:
        save_message(0, sub_id, f"Канал '{channel['name']}' был удален владельцем.")

    db.commit()
    return True, "Канал удалён"

def get_channels_unread_summary(user_id):
    """Возвращает список каналов с непрочитанными сообщениями для пользователя"""
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

# --- API эндпоинты ---

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    if not data or 'login' not in data or 'password' not in data:
        return jsonify({'error': 'Необходимо указать логин и пароль'}), 400
    login = data['login'].strip()
    password = data['password']
    if not login or not password:
        return jsonify({'error': 'Логин и пароль не могут быть пустыми'}), 400
    if get_user_by_login(login):
        return jsonify({'error': 'Пользователь с таким логином уже существует'}), 400
    user_id = create_user(login, password)
    return jsonify({'id': user_id, 'login': login, 'message': 'Регистрация прошла успешно'}), 201

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
        'id': user['id'],
        'login': user['login'],
        'registered': user['registered'],
        'message': 'Вход выполнен успешно'
    })

@app.route('/unread_summary', methods=['GET'])
def unread_summary():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'Не указан user_id'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    update_last_seen(user_id)
    personal = get_unread_summary(user_id)
    channels = get_channels_unread_summary(user_id)
    return jsonify({'summary': personal, 'channels': channels})

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
    return jsonify({'messages': messages})

# --- Эндпоинты для каналов ---

@app.route('/channel/create', methods=['POST'])
def channel_create():
    data = request.get_json()
    user_id = data.get('user_id')
    name = data.get('name', '').strip()
    if not user_id or not name:
        return jsonify({'error': 'Не указан user_id или название канала'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    if get_channel_by_name(name):
        return jsonify({'error': 'Канал с таким названием уже существует'}), 400
    db = get_db()
    cursor = db.cursor()
    cursor.execute('INSERT INTO channels (name, owner_id) VALUES (?, ?)', (name, user_id))
    channel_id = cursor.lastrowid
    # Владелец автоматически подписывается
    subscribe_user(channel_id, user_id)
    db.commit()
    return jsonify({'result': [f'Канал "{name}" создан. Вы автоматически подписаны.']})

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
        return jsonify({'result': ['Вы уже подписаны на этот канал.']})
    subscribe_user(channel['id'], user_id)
    return jsonify({'result': [f'Вы подписались на канал "{channel_name}".']})

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
        return jsonify({'result': ['Вы не подписаны на этот канал.']})
    unsubscribe_user(channel['id'], user_id)
    return jsonify({'result': [f'Вы отписались от канала "{channel_name}".']})

@app.route('/channel/send', methods=['POST'])
def channel_send():
    data = request.get_json()
    user_id = data.get('user_id')
    channel_name = data.get('channel_name', '').strip()
    content = data.get('content', '').strip()
    if not user_id or not channel_name or not content:
        return jsonify({'error': 'Не указан user_id, название канала или сообщение'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    channel = get_channel_by_name(channel_name)
    if not channel:
        return jsonify({'error': 'Канал не найден'}), 404
    if channel['owner_id'] != user_id:
        return jsonify({'error': 'Только владелец канала может отправлять сообщения'}), 403
    add_channel_message(channel['id'], user_id, content)
    return jsonify({'result': [f'Сообщение отправлено в канал "{channel_name}".']})

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
    messages = get_channel_messages(user_id, channel['id'])
    result_lines = [f'Новые сообщения из канала "{channel_name}":']
    if messages:
        for msg in messages:
            result_lines.append(f'[{msg["timestamp"]}] {msg["user_login"]}: {msg["content"]}')
    else:
        result_lines.append('Нет новых сообщений.')
    return jsonify({'result': result_lines})

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
    return jsonify({'result': [msg]})

@app.route('/command', methods=['POST'])
def handle_command():
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'error': 'Не указан ID пользователя'}), 400
    user = get_user_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    update_last_seen(user_id)
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
                    save_message(user_id, to_id, message)
                    result_lines.append(f'Сообщение для {recipient["login"]} (ID {to_id}) отправлено')
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

    return jsonify({'result': result_lines})

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)