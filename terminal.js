// terminal.js
(function() {
    const API_BASE = '';

    const outputArea = document.getElementById('outputArea');
    const promptSpan = document.getElementById('prompt');
    const commandInput = document.getElementById('commandInput');
    const fileInput = document.getElementById('fileUpload');
    const autocompleteMenu = document.getElementById('autocompleteMenu');
    const windowsContainer = document.getElementById('windowsContainer');

    // Состояние
    let history = []; // храним строки как объекты {text, imageId, audioId}
    let commandHistory = [];
    let historyIndex = 0;
    let authenticated = false;
    let currentUserId = null;
    let currentLogin = null;
    let mode = 'normal';
    let tempLogin = '';
    let tempPassword = '';
    let autoReadEnabled = false;
    let pendingImageId = null;
    let pendingAudioId = null;
    let currentColor = 'зеленый';
    let lastUnreadHash = null;

    let currentCompletions = [];
    let selectedCompletionIndex = -1;

    let socket = null;

    const colorMap = {
        'зеленый': '#00ff00',
        'красный': '#ff0000',
        'синий': '#0000ff',
        'желтый': '#ffff00',
        'оранжевый': '#ff8800',
        'розовый': '#ff00ff',
        'голубой': '#00ffff'
    };

    const VALID_COLORS = Object.keys(colorMap);

    const commandCategories = {
        'Основные': ['написать', 'профиль', 'пинг', 'мойид', 'непрочитанные', 'прочитать', 'прочитать всё', 'помощь', 'выход', 'онлайн', 'каналы', 'цвет', 'автопрочтение'],
        'Каналы': ['канал создать', 'канал подписаться', 'канал отписаться', 'канал написать', 'канал прочитать', 'канал удалить'],
        'Медиа': ['загрузить картинку', 'загрузить музыку', 'прочитать картинку', 'прослушать музыку', 'открепить', 'аватар']
    };

    const initialLines = [
        'Добро пожаловать в терминал.',
        'Доступные команды: вход, регистрация, загрузить картинку, загрузить музыку, прочитать картинку, прослушать музыку, онлайн',
        ''
    ];
    history.push(...initialLines.map(text => ({ text })));

    // --- Функции ---
    function openMediaWindow(type, id) {
        const url = type === 'image' ? `/image/${id}?user_id=${currentUserId}` : `/audio/${id}?user_id=${currentUserId}`;
        const title = type === 'image' ? `Изображение ID: ${id}` : `Аудио ID: ${id}`;

        const win = document.createElement('div');
        win.className = 'retro-window';
        win.style.left = '50px';
        win.style.top = '50px';
        win.style.width = type === 'image' ? '400px' : '300px';
        win.style.height = type === 'image' ? '300px' : '150px';

        const titleBar = document.createElement('div');
        titleBar.className = 'title-bar';
        titleBar.innerHTML = `<span class="title">${title}</span><span class="close-btn">×</span>`;

        const content = document.createElement('div');
        content.className = 'window-content';
        if (type === 'image') {
            const img = document.createElement('img');
            img.src = url;
            img.alt = title;
            img.onerror = () => { content.innerHTML = '<p style="color:red">Ошибка загрузки изображения</p>'; };
            content.appendChild(img);
        } else {
            const retroDiv = document.createElement('div');
            retroDiv.className = 'retro-audio';
            const audio = document.createElement('audio');
            audio.controls = true;
            audio.src = url;
            audio.onerror = () => { content.innerHTML = '<p style="color:red">Ошибка загрузки аудио</p>'; };
            retroDiv.appendChild(audio);
            content.appendChild(retroDiv);
        }

        win.appendChild(titleBar);
        win.appendChild(content);
        windowsContainer.appendChild(win);

        const closeBtn = titleBar.querySelector('.close-btn');
        closeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            win.remove();
        });

        // Drag
        let isDragging = false;
        let offsetX, offsetY;

        titleBar.addEventListener('mousedown', (e) => {
            if (e.target === closeBtn) return;
            isDragging = true;
            const rect = win.getBoundingClientRect();
            offsetX = e.clientX - rect.left;
            offsetY = e.clientY - rect.top;
            win.style.cursor = 'move';
            e.preventDefault();
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            const containerRect = windowsContainer.getBoundingClientRect();
            let newLeft = e.clientX - offsetX - containerRect.left;
            let newTop = e.clientY - offsetY - containerRect.top;
            newLeft = Math.max(0, Math.min(containerRect.width - win.offsetWidth, newLeft));
            newTop = Math.max(0, Math.min(containerRect.height - win.offsetHeight, newTop));
            win.style.left = newLeft + 'px';
            win.style.top = newTop + 'px';
        });

        document.addEventListener('mouseup', () => {
            if (isDragging) {
                isDragging = false;
                win.style.cursor = 'default';
            }
        });
    }

    function escapeHTML(str) {
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    function linkify(text) {
        const urlPattern = /(https?:\/\/[^\s]+)/g;
        return text.replace(urlPattern, '<a href="$1" target="_blank">$1</a>');
    }

    function setTextColor(colorName) {
        currentColor = colorName;
        const cssColor = colorMap[colorName] || '#00ff00';
        outputArea.style.color = cssColor;
        commandInput.style.color = cssColor;
    }

    function updatePrompt() {
        let promptStr = '';
        if (!authenticated && mode === 'normal') promptStr = '>';
        else if (authenticated && mode === 'normal') promptStr = `${currentLogin}@pc:~$`;
        else if (mode === 'awaiting_login') promptStr = 'Логин:';
        else if (mode === 'awaiting_password') promptStr = 'Пароль:';
        else if (mode === 'awaiting_register_login') promptStr = 'Придумайте логин:';
        else if (mode === 'awaiting_register_password') promptStr = 'Придумайте пароль:';
        else if (mode === 'awaiting_channel_delete_confirmation') promptStr = 'Подтверждение:';
        promptSpan.textContent = promptStr;
    }

    function render() {
        // Оптимизированный рендеринг: не перерисовываем всё, а добавляем только новые строки
        // Но для простоты оставим полную перерисовку, т.к. история не очень большая
        let html = '';
        for (let lineObj of history) {
            let escaped = escapeHTML(lineObj.text);
            let linked = linkify(escaped);
            if (lineObj.imageId) {
                linked += ` <span class="media-link" onclick="openMediaWindow('image', ${lineObj.imageId})">[Изображение ID: ${lineObj.imageId}]</span>`;
            }
            if (lineObj.audioId) {
                linked += ` <div class="retro-audio"><audio controls src="/audio/${lineObj.audioId}?user_id=${currentUserId}"></audio><span class="audio-label">ID: ${lineObj.audioId}</span></div>`;
            }
            html += `<div class="output-line">${linked}</div>`;
        }
        outputArea.innerHTML = html;
        outputArea.scrollTop = outputArea.scrollHeight;
        updatePrompt();
    }

    function appendHistory(text, imageId = null, audioId = null) {
        history.push({ text, imageId, audioId });
        render();
    }

    function resetMode() {
        mode = 'normal';
        tempLogin = '';
        tempPassword = '';
        commandInput.value = '';
        render();
    }

    // --- Сессия ---
    function saveSession() {
        if (authenticated && currentUserId) {
            const session = {
                userId: currentUserId,
                login: currentLogin,
                color: currentColor,
                timestamp: Date.now()
            };
            localStorage.setItem('termicon_session', JSON.stringify(session));
        }
    }

    function loadSession() {
        const saved = localStorage.getItem('termicon_session');
        if (saved) {
            try {
                const session = JSON.parse(saved);
                if (Date.now() - session.timestamp < 7 * 24 * 60 * 60 * 1000) {
                    return session;
                }
            } catch (e) {
                console.error('Failed to parse session', e);
            }
        }
        return null;
    }

    function clearSession() {
        localStorage.removeItem('termicon_session');
    }

    function saveCommandHistory() {
        localStorage.setItem('termicon_history', JSON.stringify(commandHistory));
    }

    function loadCommandHistory() {
        const saved = localStorage.getItem('termicon_history');
        if (saved) {
            try {
                commandHistory = JSON.parse(saved);
                historyIndex = commandHistory.length;
            } catch (e) {
                console.error('Failed to parse history', e);
            }
        }
    }
    loadCommandHistory();

    // --- WebSocket ---
    function connectWebSocket() {
        if (socket && socket.connected) return;
        socket = io(API_BASE || window.location.origin, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionAttempts: 5
        });
        socket.on('connect', () => {
            console.log('WebSocket connected');
            if (currentUserId) {
                socket.emit('subscribe', { user_id: currentUserId });
            }
        });
        socket.on('disconnect', () => {
            console.log('WebSocket disconnected, attempting reconnect...');
        });
        socket.on('connect_error', (err) => {
            console.error('WebSocket connection error:', err);
        });
        socket.on(`user_${currentUserId}_new_message`, (data) => {
            appendHistory(`📩 Новое личное сообщение от пользователя ID ${data.from}`);
            if (autoReadEnabled) {
                autoReadAllPersonal();
            } else {
                fetchUnreadSummary(true);
            }
        });
        socket.on(`user_${currentUserId}_new_channel_message`, (data) => {
            appendHistory(`📢 Новое сообщение в канале (ID канала ${data.channel})`);
            if (autoReadEnabled) {
                autoReadAllChannels();
            } else {
                fetchUnreadSummary(true);
            }
        });
    }

    function disconnectWebSocket() {
        if (socket) {
            if (currentUserId) {
                socket.emit('unsubscribe', { user_id: currentUserId });
            }
            socket.disconnect();
            socket = null;
        }
    }

    // --- API запросы ---
    async function apiRequest(method, endpoint, body = null) {
        const options = {
            method,
            headers: { 'Content-Type': 'application/json' }
        };
        if (body) {
            options.body = JSON.stringify(body);
        }
        try {
            const resp = await fetch(`${API_BASE}${endpoint}`, options);
            const data = await resp.json();
            if (!resp.ok || data.error) {
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            return data;
        } catch (err) {
            appendHistory(`Ошибка: ${err.message}`);
            return null;
        }
    }

    async function fetchUnreadSummary(silent = false) {
        if (!authenticated || !currentUserId) return null;
        const data = await apiRequest('GET', `/unread_summary?user_id=${currentUserId}`);
        if (!data) return null;
        const personal = data.result.personal || [];
        const channels = data.result.channels || [];
        const personalHash = JSON.stringify(personal);
        const channelsHash = JSON.stringify(channels);
        const currentHash = personalHash + '|' + channelsHash;
        if (currentHash !== lastUnreadHash) {
            lastUnreadHash = currentHash;
            if (!silent) {
                if (personal.length) {
                    appendHistory('Непрочитанные личные сообщения:');
                    personal.forEach(item => {
                        appendHistory(`  От ${item.from_login} (ID ${item.from_id}): ${item.count} сообщ.`);
                    });
                }
                if (channels.length) {
                    appendHistory('Непрочитанные сообщения в каналах:');
                    channels.forEach(ch => {
                        appendHistory(`  Канал "${ch.channel_name}" (владелец ${ch.owner_login}): ${ch.unread_count} нов.`);
                    });
                }
                if (!personal.length && !channels.length && lastUnreadHash !== null) {
                    appendHistory('Все сообщения прочитаны.');
                }
            }
        }
        return { personal, channels };
    }

    async function autoReadAllPersonal() {
        if (!authenticated || !currentUserId) return;
        const data = await apiRequest('POST', '/read_messages', { user_id: currentUserId });
        if (data && data.result.length) {
            appendHistory('📨 Автоматически прочитанные личные сообщения:');
            data.result.forEach(msg => {
                let line = `[${msg.timestamp}] ${msg.from_login} (${msg.from_id}): ${msg.content}`;
                appendHistory(line, msg.image_id, msg.audio_id);
            });
        }
    }

    async function autoReadAllChannels() {
        const summary = await fetchUnreadSummary(true);
        if (!summary) return;
        for (const ch of summary.channels) {
            const data = await apiRequest('POST', '/channel/read', { user_id: currentUserId, channel_name: ch.channel_name });
            if (data && data.result.length > 1) {
                appendHistory(`📢 Канал "${ch.channel_name}":`);
                for (let i = 1; i < data.result.length; i++) {
                    appendHistory(data.result[i]);
                }
            }
        }
    }

    async function readMessages(fromId = null) {
        const data = await apiRequest('POST', '/read_messages', { user_id: currentUserId, from_id: fromId });
        if (data) {
            const messages = data.result || [];
            if (messages.length) {
                if (fromId) {
                    appendHistory(`Сообщения от пользователя ID ${fromId}:`);
                } else {
                    appendHistory('Все непрочитанные личные сообщения:');
                }
                messages.forEach(msg => {
                    let line = `[${msg.timestamp}] ${msg.from_login} (${msg.from_id}): ${msg.content}`;
                    appendHistory(line, msg.image_id, msg.audio_id);
                });
            } else {
                appendHistory('Нет личных сообщений для отображения.');
            }
            await fetchUnreadSummary(true);
        }
    }

    async function channelRead(name) {
        const data = await apiRequest('POST', '/channel/read', { user_id: currentUserId, channel_name: name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
            await fetchUnreadSummary(true);
        }
    }

    async function channelCreate(name) {
        const data = await apiRequest('POST', '/channel/create', { user_id: currentUserId, name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
        }
    }

    async function channelSubscribe(name) {
        const data = await apiRequest('POST', '/channel/subscribe', { user_id: currentUserId, channel_name: name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
        }
    }

    async function channelUnsubscribe(name) {
        const data = await apiRequest('POST', '/channel/unsubscribe', { user_id: currentUserId, channel_name: name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
        }
    }

    async function channelSend(name, message, imageId = null, audioId = null) {
        const data = await apiRequest('POST', '/channel/send', {
            user_id: currentUserId,
            channel_name: name,
            content: message,
            image_id: imageId,
            audio_id: audioId
        });
        if (data) {
            data.result.forEach(line => appendHistory(line));
        }
    }

    async function channelDelete(name) {
        const data = await apiRequest('POST', '/channel/delete', { user_id: currentUserId, channel_name: name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
            await fetchUnreadSummary(true);
        }
    }

    async function performLogin(login, password) {
        const data = await apiRequest('POST', '/login', { login, password });
        if (data) {
            const user = data.result[0];
            authenticated = true;
            currentUserId = user.id;
            currentLogin = user.login;
            autoReadEnabled = false;
            pendingImageId = null;
            pendingAudioId = null;
            if (user.color_pref) {
                setTextColor(user.color_pref);
            }
            appendHistory(`Добро пожаловать, ${user.login}!`);
            await fetchUnreadSummary(false);
            connectWebSocket();
            saveSession();
        }
        resetMode();
    }

    async function performRegister(login, password) {
        const data = await apiRequest('POST', '/register', { login, password });
        if (data) {
            data.result.forEach(line => appendHistory(line));
        }
        resetMode();
    }

    async function checkSession() {
        const session = loadSession();
        if (!session) return;
        const data = await apiRequest('POST', '/check_session', { user_id: session.userId });
        if (data) {
            const user = data.result;
            authenticated = true;
            currentUserId = user.id;
            currentLogin = user.login;
            autoReadEnabled = false;
            pendingImageId = null;
            pendingAudioId = null;
            if (user.color_pref) {
                setTextColor(user.color_pref);
            } else if (session.color) {
                setTextColor(session.color);
            }
            appendHistory(`Добро пожаловать, ${user.login}! (сессия восстановлена)`);
            await fetchUnreadSummary(false);
            connectWebSocket();
        } else {
            clearSession();
        }
    }

    function uploadFile(file) {
        const formData = new FormData();
        formData.append('user_id', currentUserId);
        formData.append('image', file);

        fetch(`${API_BASE}/upload_image`, {
            method: 'POST',
            body: formData
        })
        .then(resp => resp.json())
        .then(data => {
            if (data.error) {
                appendHistory(`Ошибка загрузки: ${data.error}`);
            } else {
                const mediaId = data.result.media_id;
                const type = data.result.type;
                if (type === 'image') {
                    pendingImageId = mediaId;
                    appendHistory(`✅ Изображение загружено. ID: ${mediaId}. Оно будет прикреплено к следующему отправленному сообщению.`);
                } else {
                    pendingAudioId = mediaId;
                    appendHistory(`🎵 Аудио загружено. ID: ${mediaId}. Оно будет прикреплено к следующему отправленному сообщению.`);
                }
            }
        })
        .catch(err => {
            appendHistory(`Ошибка соединения при загрузке: ${err.message}`);
        });
    }

    async function fetchOnlineUsers() {
        const data = await apiRequest('GET', '/online_users');
        if (data) {
            const users = data.result;
            if (users.length === 0) {
                appendHistory('Нет пользователей в сети.');
            } else {
                appendHistory('Пользователи в сети:');
                users.forEach(u => {
                    appendHistory(`  ${u.login} (ID: ${u.id}) — последняя активность: ${u.last_seen}`);
                });
            }
        }
    }

    async function fetchMyChannels() {
        const data = await apiRequest('GET', `/my_channels?user_id=${currentUserId}`);
        if (data) {
            const channels = data.result;
            if (channels.length === 0) {
                appendHistory('Вы не подписаны ни на один канал.');
            } else {
                appendHistory('Ваши каналы:');
                channels.forEach(ch => {
                    appendHistory(`  ${ch.name} (владелец: ${ch.owner_login}) — непрочитано: ${ch.unread_count}`);
                });
            }
        }
    }

    async function setUserColor(color) {
        const data = await apiRequest('POST', '/set_color', { user_id: currentUserId, color });
        if (data) {
            appendHistory(data.result);
            setTextColor(color);
            saveSession();
        }
    }

    // --- Команды ---
    const commands = {
        'написать': (args) => {
            if (args.length < 2) {
                appendHistory('Использование: написать [ID] [сообщение]');
                return;
            }
            const toId = args[0];
            if (isNaN(toId)) {
                appendHistory('Ошибка: ID должен быть числом');
                return;
            }
            const message = args.slice(1).join(' ');
            apiCommand('написать', { to_id: toId, message, image_id: pendingImageId, audio_id: pendingAudioId });
            pendingImageId = null;
            pendingAudioId = null;
        },
        'профиль': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: профиль [ID]');
                return;
            }
            const profileId = args[0];
            if (isNaN(profileId)) {
                appendHistory('Ошибка: ID должен быть числом');
                return;
            }
            apiCommand('профиль', { profile_id: profileId });
        },
        'пинг': () => appendHistory('понг'),
        'мойид': () => appendHistory(`Ваш ID: ${currentUserId}`),
        'непрочитанные': () => fetchUnreadSummary(false),
        'прочитать': (args) => {
            if (args.length === 0) {
                appendHistory('Использование: прочитать [всё | ID]');
                return;
            }
            if (args[0] === 'всё') {
                readMessages();
            } else {
                const fromId = args[0];
                if (isNaN(fromId)) {
                    appendHistory('Ошибка: ID должен быть числом');
                    return;
                }
                readMessages(fromId);
            }
        },
        'прочитать всё': () => readMessages(),
        'помощь': () => showHelp(),
        'выход': () => {
            authenticated = false;
            currentUserId = null;
            currentLogin = null;
            lastUnreadHash = null;
            autoReadEnabled = false;
            pendingImageId = null;
            pendingAudioId = null;
            disconnectWebSocket();
            setTextColor('зеленый');
            clearSession();
            appendHistory('Вы вышли из аккаунта.');
            appendHistory('Доступные команды: вход, регистрация, загрузить картинку, загрузить музыку, прочитать картинку, прослушать музыку, онлайн');
        },
        'онлайн': fetchOnlineUsers,
        'каналы': fetchMyChannels,
        'цвет': (args) => {
            if (args.length < 1) {
                appendHistory(`Использование: цвет [название]. Доступны: ${VALID_COLORS.join(', ')}`);
                return;
            }
            const color = args[0];
            if (!VALID_COLORS.includes(color)) {
                appendHistory(`Недопустимый цвет. Доступны: ${VALID_COLORS.join(', ')}`);
                return;
            }
            setUserColor(color);
        },
        'автопрочтение': () => {
            autoReadEnabled = !autoReadEnabled;
            appendHistory(`Автоматическое чтение ${autoReadEnabled ? 'включено' : 'отключено'}.`);
        },
        'канал создать': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: канал создать [название]');
                return;
            }
            const name = args.join(' ');
            channelCreate(name);
        },
        'канал подписаться': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: канал подписаться [название]');
                return;
            }
            const name = args.join(' ');
            channelSubscribe(name);
        },
        'канал отписаться': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: канал отписаться [название]');
                return;
            }
            const name = args.join(' ');
            channelUnsubscribe(name);
        },
        'канал написать': (args) => {
            if (args.length < 2) {
                appendHistory('Использование: канал написать [название] [сообщение]');
                return;
            }
            const name = args[0];
            const message = args.slice(1).join(' ');
            channelSend(name, message, pendingImageId, pendingAudioId);
            pendingImageId = null;
            pendingAudioId = null;
        },
        'канал прочитать': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: канал прочитать [название]');
                return;
            }
            const name = args.join(' ');
            channelRead(name);
        },
        'канал удалить': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: канал удалить [название]');
                return;
            }
            const name = args.join(' ');
            channelDelete(name);
        },
        'загрузить картинку': () => {
            fileInput.accept = 'image/png, image/jpeg, image/gif, image/bmp, image/webp';
            fileInput.click();
        },
        'загрузить музыку': () => {
            fileInput.accept = 'audio/mp3, audio/wav, audio/ogg, audio/m4a, audio/flac';
            fileInput.click();
        },
        'прочитать картинку': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: прочитать картинку [ID]');
                return;
            }
            const id = args[0];
            if (isNaN(id)) {
                appendHistory('Ошибка: ID должен быть числом');
                return;
            }
            openMediaWindow('image', id);
        },
        'прослушать музыку': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: прослушать музыку [ID]');
                return;
            }
            const id = args[0];
            if (isNaN(id)) {
                appendHistory('Ошибка: ID должен быть числом');
                return;
            }
            openMediaWindow('audio', id);
        },
        'открепить': () => {
            pendingImageId = null;
            pendingAudioId = null;
            appendHistory('Прикреплённые медиа сброшены.');
        },
        'аватар': (args) => {
            if (args.length < 1) {
                appendHistory('Использование: аватар [ID изображения]');
                return;
            }
            const imageId = args[0];
            if (isNaN(imageId)) {
                appendHistory('Ошибка: ID должен быть числом');
                return;
            }
            apiCommand('аватар', { image_id: imageId });
        }
    };

    function showHelp() {
        appendHistory('Доступные команды (после входа):');
        for (let [cat, cmds] of Object.entries(commandCategories)) {
            appendHistory(`  ${cat}: ${cmds.join(', ')}`);
        }
    }

    async function apiCommand(command, args) {
        const data = await apiRequest('POST', '/command', { user_id: currentUserId, command, args });
        if (data && data.result) {
            data.result.forEach(line => appendHistory(line));
        }
    }

    // --- Автодополнение ---
    function getAllCommands() {
        if (!authenticated) {
            return ['вход', 'регистрация', 'загрузить картинку', 'загрузить музыку', 'прочитать картинку', 'прослушать музыку', 'онлайн'];
        } else {
            return Object.keys(commands);
        }
    }

    function showAutocomplete(completions) {
        if (!completions || completions.length === 0) {
            hideAutocomplete();
            return;
        }
        let categorized = {};
        for (let cmd of completions) {
            let found = false;
            for (let [cat, cmds] of Object.entries(commandCategories)) {
                if (cmds.includes(cmd) || (cat === 'Основные' && !cmds.includes(cmd) && !cmd.startsWith('канал ') && !cmd.startsWith('загрузить') && !cmd.startsWith('прочитать') && cmd !== 'открепить' && cmd !== 'аватар')) {
                    if (!categorized[cat]) categorized[cat] = [];
                    categorized[cat].push(cmd);
                    found = true;
                    break;
                }
            }
            if (!found) {
                if (!categorized['Прочее']) categorized['Прочее'] = [];
                categorized['Прочее'].push(cmd);
            }
        }
        currentCompletions = completions;
        selectedCompletionIndex = 0;
        renderAutocomplete(categorized);
        autocompleteMenu.classList.add('visible');
    }

    function hideAutocomplete() {
        autocompleteMenu.classList.remove('visible');
        currentCompletions = [];
        selectedCompletionIndex = -1;
    }

    function renderAutocomplete(categorized) {
        let html = '';
        let flatIndex = 0;
        for (let [category, items] of Object.entries(categorized)) {
            html += `<div class="autocomplete-category">${category}</div>`;
            items.sort().forEach(item => {
                const selectedClass = flatIndex === selectedCompletionIndex ? 'selected' : '';
                html += `<div class="autocomplete-item ${selectedClass}" data-index="${flatIndex}">${item}</div>`;
                flatIndex++;
            });
        }
        autocompleteMenu.innerHTML = html;
        document.querySelectorAll('.autocomplete-item').forEach(el => {
            el.addEventListener('click', (e) => {
                const index = parseInt(e.target.dataset.index);
                if (!isNaN(index) && currentCompletions[index]) {
                    completeWith(currentCompletions[index]);
                }
            });
        });
    }

    function completeWith(completion) {
        if (!completion) return;
        commandInput.value = completion;
        commandInput.setSelectionRange(completion.length, completion.length);
        hideAutocomplete();
    }

    function getCompletionList(prefix) {
        const all = getAllCommands();
        return all.filter(cmd => cmd.startsWith(prefix));
    }

    function handleTab() {
        if (mode !== 'normal') {
            hideAutocomplete();
            return;
        }
        const input = commandInput;
        const cursorPos = input.selectionStart;
        const beforeCursor = input.value.slice(0, cursorPos);
        const lastSpaceBefore = beforeCursor.lastIndexOf(' ');
        const startOfWord = lastSpaceBefore === -1 ? 0 : lastSpaceBefore + 1;
        const wordPrefix = beforeCursor.slice(startOfWord);
        const completions = getCompletionList(wordPrefix);
        if (completions.length === 0) {
            hideAutocomplete();
            return;
        }
        showAutocomplete(completions);
    }

    // --- Обработка ввода ---
    function executeCommand(cmd) {
        const trimmed = cmd.trim();
        if (trimmed === '' && mode === 'normal') return;

        if (autocompleteMenu.classList.contains('visible') && selectedCompletionIndex !== -1) {
            const selected = currentCompletions[selectedCompletionIndex];
            if (selected) {
                completeWith(selected);
            }
            return;
        }

        hideAutocomplete();

        if (mode !== 'normal') {
            handleInputInMode(trimmed);
            return;
        }

        commandHistory.push(trimmed);
        historyIndex = commandHistory.length;
        saveCommandHistory();

        const promptChar = authenticated ? `${currentLogin}@pc:~$` : '>';
        appendHistory(`${promptChar} ${trimmed}`);

        if (!authenticated) {
            handleUnauthenticatedCommand(trimmed);
            commandInput.value = '';
            render();
            return;
        }

        const parts = trimmed.toLowerCase().split(' ');
        const command = parts[0];
        const args = parts.slice(1);

        if (commands[command]) {
            commands[command](args);
        } else {
            // Поиск команды с пробелом (например "канал создать")
            const twoWord = parts.slice(0,2).join(' ');
            if (commands[twoWord]) {
                commands[twoWord](parts.slice(2));
            } else {
                appendHistory(`Неизвестная команда: ${command}. Введите "помощь" для списка команд.`);
            }
        }

        commandInput.value = '';
        render();
    }

    function handleUnauthenticatedCommand(trimmed) {
        const parts = trimmed.toLowerCase().split(' ');
        const command = parts[0];
        if (command === 'вход' || command === 'login') {
            mode = 'awaiting_login';
            appendHistory('Введите логин:');
        } else if (command === 'регистрация' || command === 'register') {
            mode = 'awaiting_register_login';
            appendHistory('Придумайте логин:');
        } else if (command === 'загрузить' && parts[1] === 'картинку') {
            fileInput.accept = 'image/png, image/jpeg, image/gif, image/bmp, image/webp';
            fileInput.click();
        } else if (command === 'загрузить' && parts[1] === 'музыку') {
            fileInput.accept = 'audio/mp3, audio/wav, audio/ogg, audio/m4a, audio/flac';
            fileInput.click();
        } else if (command === 'прочитать' && parts[1] === 'картинку' && parts[2]) {
            openMediaWindow('image', parts[2]);
        } else if (command === 'прослушать' && parts[1] === 'музыку' && parts[2]) {
            openMediaWindow('audio', parts[2]);
        } else if (command === 'онлайн') {
            fetchOnlineUsers();
        } else {
            appendHistory('Неизвестная команда. Доступные: вход, регистрация, загрузить картинку, загрузить музыку, прочитать картинку [id], прослушать музыку [id], онлайн');
        }
    }

    function handleInputInMode(input) {
        if (mode === 'awaiting_login') {
            tempLogin = input;
            mode = 'awaiting_password';
            appendHistory('Введите пароль:');
        } else if (mode === 'awaiting_password') {
            tempPassword = input;
            performLogin(tempLogin, tempPassword);
        } else if (mode === 'awaiting_register_login') {
            tempLogin = input;
            mode = 'awaiting_register_password';
            appendHistory('Придумайте пароль:');
        } else if (mode === 'awaiting_register_password') {
            tempPassword = input;
            performRegister(tempLogin, tempPassword);
        }
    }

    // --- Инициализация и обработчики событий ---
    setTimeout(checkSession, 100);

    commandInput.addEventListener('input', () => {
        if (mode !== 'normal') return;
        const val = commandInput.value.trim().toLowerCase();
        if (val.length < 2) {
            hideAutocomplete();
            return;
        }
        const all = getAllCommands();
        const completions = all.filter(cmd => cmd.includes(val)).slice(0, 20);
        if (completions.length > 0) {
            showAutocomplete(completions);
        } else {
            hideAutocomplete();
        }
    });

    commandInput.addEventListener('keydown', (e) => {
        const key = e.key;
        const input = commandInput;

        if (autocompleteMenu.classList.contains('visible')) {
            if (key === 'ArrowUp') {
                e.preventDefault();
                if (selectedCompletionIndex > 0) {
                    selectedCompletionIndex--;
                } else {
                    selectedCompletionIndex = currentCompletions.length - 1;
                }
                let categorized = {};
                for (let cmd of currentCompletions) {
                    let found = false;
                    for (let [cat, cmds] of Object.entries(commandCategories)) {
                        if (cmds.includes(cmd) || (cat === 'Основные' && !cmds.includes(cmd) && !cmd.startsWith('канал ') && !cmd.startsWith('загрузить') && !cmd.startsWith('прочитать') && cmd !== 'открепить' && cmd !== 'аватар')) {
                            if (!categorized[cat]) categorized[cat] = [];
                            categorized[cat].push(cmd);
                            found = true;
                            break;
                        }
                    }
                    if (!found) {
                        if (!categorized['Прочее']) categorized['Прочее'] = [];
                        categorized['Прочее'].push(cmd);
                    }
                }
                renderAutocomplete(categorized);
                return;
            }
            if (key === 'ArrowDown') {
                e.preventDefault();
                if (selectedCompletionIndex < currentCompletions.length - 1) {
                    selectedCompletionIndex++;
                } else {
                    selectedCompletionIndex = 0;
                }
                let categorized = {};
                for (let cmd of currentCompletions) {
                    let found = false;
                    for (let [cat, cmds] of Object.entries(commandCategories)) {
                        if (cmds.includes(cmd) || (cat === 'Основные' && !cmds.includes(cmd) && !cmd.startsWith('канал ') && !cmd.startsWith('загрузить') && !cmd.startsWith('прочитать') && cmd !== 'открепить' && cmd !== 'аватар')) {
                            if (!categorized[cat]) categorized[cat] = [];
                            categorized[cat].push(cmd);
                            found = true;
                            break;
                        }
                    }
                    if (!found) {
                        if (!categorized['Прочее']) categorized['Прочее'] = [];
                        categorized['Прочее'].push(cmd);
                    }
                }
                renderAutocomplete(categorized);
                return;
            }
            if (key === 'Enter' || key === 'Tab') {
                e.preventDefault();
                if (selectedCompletionIndex !== -1) {
                    completeWith(currentCompletions[selectedCompletionIndex]);
                } else {
                    hideAutocomplete();
                }
                return;
            }
            if (key === 'Escape') {
                e.preventDefault();
                hideAutocomplete();
                return;
            }
        }

        if (e.ctrlKey && key === 'v') {
            e.preventDefault();
            navigator.clipboard.readText().then(text => {
                const start = input.selectionStart;
                const end = input.selectionEnd;
                const newValue = input.value.substring(0, start) + text + input.value.substring(end);
                input.value = newValue;
                input.setSelectionRange(start + text.length, start + text.length);
            }).catch(err => {
                appendHistory('Не удалось вставить текст: ' + err);
            });
            return;
        }

        if (key === 'Enter') {
            e.preventDefault();
            executeCommand(input.value);
            return;
        }

        if (key === 'Tab') {
            e.preventDefault();
            handleTab();
            return;
        }

        if (key === 'ArrowUp') {
            e.preventDefault();
            if (historyIndex > 0) {
                historyIndex--;
                input.value = commandHistory[historyIndex] || '';
                setTimeout(() => input.setSelectionRange(input.value.length, input.value.length), 0);
            }
            return;
        }
        if (key === 'ArrowDown') {
            e.preventDefault();
            if (historyIndex < commandHistory.length - 1) {
                historyIndex++;
                input.value = commandHistory[historyIndex] || '';
                setTimeout(() => input.setSelectionRange(input.value.length, input.value.length), 0);
            } else {
                historyIndex = commandHistory.length;
                input.value = '';
            }
            return;
        }
    });

    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            if (!authenticated) {
                appendHistory('Ошибка: необходимо войти в систему для загрузки файлов.');
                return;
            }
            uploadFile(file);
        }
        fileInput.value = '';
    });

    document.getElementById('terminalContainer').addEventListener('click', () => commandInput.focus());

    commandInput.addEventListener('blur', () => {
        setTimeout(() => hideAutocomplete(), 200);
    });

    window.openMediaWindow = openMediaWindow;

    render();
})();