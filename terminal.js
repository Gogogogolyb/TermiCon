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
    let history = [];
    let commandHistory = [];
    let historyIndex = 0;
    let authenticated = false;
    let currentUserId = null;
    let currentLogin = null;
    let token = null;               // JWT токен
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
        'зеленый': '#00ff80',
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
        const url = type === 'image' ? `/image/${id}` : `/audio/${id}`;
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

        // Перетаскивание мышью
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

        // Перетаскивание касанием
        titleBar.addEventListener('touchstart', (e) => {
            if (e.target === closeBtn) return;
            e.preventDefault();
            const touch = e.touches[0];
            isDragging = true;
            const rect = win.getBoundingClientRect();
            offsetX = touch.clientX - rect.left;
            offsetY = touch.clientY - rect.top;
            win.style.cursor = 'move';
        }, { passive: false });

        document.addEventListener('touchmove', (e) => {
            if (!isDragging) return;
            e.preventDefault();
            const touch = e.touches[0];
            const containerRect = windowsContainer.getBoundingClientRect();
            let newLeft = touch.clientX - offsetX - containerRect.left;
            let newTop = touch.clientY - offsetY - containerRect.top;
            newLeft = Math.max(0, Math.min(containerRect.width - win.offsetWidth, newLeft));
            newTop = Math.max(0, Math.min(containerRect.height - win.offsetHeight, newTop));
            win.style.left = newLeft + 'px';
            win.style.top = newTop + 'px';
        }, { passive: false });

        document.addEventListener('touchend', () => {
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
        const cssColor = colorMap[colorName] || '#00ff80';
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
        promptSpan.textContent = promptStr;
    }

    function render() {
        let html = '';
        for (let lineObj of history) {
            let escaped = escapeHTML(lineObj.text);
            let linked = linkify(escaped);
            if (lineObj.imageId) {
                linked += ` <span class="media-link" onclick="openMediaWindow('image', ${lineObj.imageId})">[Изображение ID: ${lineObj.imageId}]</span>`;
            }
            if (lineObj.audioId) {
                linked += ` <div class="retro-audio"><audio controls src="/audio/${lineObj.audioId}"></audio><span class="audio-label">ID: ${lineObj.audioId}</span></div>`;
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

    // --- Сессия (храним только токен) ---
    function saveSession(token, userId, login, color) {
        const session = {
            token: token,
            userId: userId,
            login: login,
            color: color,
            timestamp: Date.now()
        };
        localStorage.setItem('termicon_session', JSON.stringify(session));
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

    // --- WebSocket с аутентификацией ---
    function connectWebSocket() {
        if (socket && socket.connected) return;
        socket = io(API_BASE || window.location.origin, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionAttempts: 5
        });
        socket.on('connect', () => {
            console.log('WebSocket connected, authenticating...');
            socket.emit('authenticate', { token: token });
        });
        socket.on('authenticated', (data) => {
            if (data.status === 'ok') {
                console.log('WebSocket authenticated');
            } else {
                console.error('WebSocket authentication failed');
            }
        });
        socket.on('disconnect', () => {
            console.log('WebSocket disconnected');
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
            socket.disconnect();
            socket = null;
        }
    }

    // --- API запросы с токеном ---
    async function apiRequest(method, endpoint, body = null) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': token ? `Bearer ${token}` : ''
            }
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
        const data = await apiRequest('GET', '/unread_summary');
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
        const data = await apiRequest('POST', '/read_messages', {});
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
            const data = await apiRequest('POST', '/channel/read', { channel_name: ch.channel_name });
            if (data && data.result.length > 1) {
                appendHistory(`📢 Канал "${ch.channel_name}":`);
                for (let i = 1; i < data.result.length; i++) {
                    appendHistory(data.result[i]);
                }
            }
        }
    }

    async function readMessages(fromId = null) {
        const data = await apiRequest('POST', '/read_messages', { from_id: fromId });
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
        const data = await apiRequest('POST', '/channel/read', { channel_name: name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
            await fetchUnreadSummary(true);
        }
    }

    async function channelCreate(name) {
        const data = await apiRequest('POST', '/channel/create', { name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
        }
    }

    async function channelSubscribe(name) {
        const data = await apiRequest('POST', '/channel/subscribe', { channel_name: name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
        }
    }

    async function channelUnsubscribe(name) {
        const data = await apiRequest('POST', '/channel/unsubscribe', { channel_name: name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
        }
    }

    async function channelSend(name, message, imageId = null, audioId = null) {
        const data = await apiRequest('POST', '/channel/send', {
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
        const data = await apiRequest('POST', '/channel/delete', { channel_name: name });
        if (data) {
            data.result.forEach(line => appendHistory(line));
            await fetchUnreadSummary(true);
        }
    }

    async function performLogin(login, password) {
        const data = await apiRequest('POST', '/login', { login, password });
        if (data) {
            token = data.result.token;
            const user = data.result.user;
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
            await fetchUn            await fetchUnreadSummary(false);
readSummary(false);
            connect            connectWebSocketWebSocket();
           ();
            saveSession saveSession(token, user.id(token, user.id, user, user.login, currentColor.login, currentColor);
       );
        }
        }
        resetMode resetMode();
   ();
    }

    }

    async function async function performRegister performRegister(login(login, password, password) {
) {
        const        const data = await api data = await apiRequest('POST', '/registerRequest('POST', '/register', { login,', { login, password });
        if password });
        if (data (data) {
            appendHistory(`) {
            appendHistory(`РегиРегистрация успешстрация успешна.на. Ва Ваш ID: ${ш ID: ${datadata.result.user_id.result.user_id}`);
            // Не}`);
            // Не в входимходим автоматически, предлага автоматически, предлагаем войем войти
ти
        }
        reset        }
        resetMode();
Mode();
    }

    }

    async function checkSession()    async function checkSession() {
        const session = loadSession();
 {
        const session = loadSession();
        if        if (!session) return (!session) return;
       ;
        token = token = session.token session.token;
       ;
        // Пров // Проверим токен, заперим токен, запросивросив данные пользователя (можно через данные пользователя (можно через /un /unread_sumread_summary илиmary или спец. спец. энд эндпоинпоинт)
        const data =т)
        const data = await api await apiRequest('GET', '/unread_summary');
        ifRequest('GET', '/unread_summary');
        if (data (data) {
) {
            //            // Т Токенокен вали валидный
           дный
            authenticated = authenticated = true;
 true;
            current            currentUserId =UserId = session.user session.userId;
Id;
            current            currentLogin = session.loginLogin =;
            setTextColor(session session.login;
            setTextColor.color ||(session.color || 'зеленый');
 'зеленый');
            append            appendHistory(`History(`Добро пожДобро пожаловать, ${аловать, ${sessionsession.login}! (се.login}!ссия восстановлена (сессия восстановлена)`);
)`);
            await            await fetchUn fetchUnreadSummary(false);
readSummary(false);
            connect            connectWebSocketWebSocket();
        } else();
        } else {
            {
            clearSession clearSession();
           ();
            token = token = null;
 null;
        }
        }
    }

    }

    function    function uploadFile uploadFile(file) {
       (file) {
        const form const formData =Data = new FormData();
 new Form        formData.append('imageData();
        formData.append', file);

       ('image', file);

        fetch(`${API_B fetch(`${API_BASE}/upload_image`, {
ASE}/upload_image`, {
            method            method: ': 'POST',
POST',
            headers: {
            headers: {
                '                'Authorization':Authorization': `Bearer `Bearer ${token}`
 ${token}`
            },
            },
            body            body: form: formData
Data
        })
        })
        .        .then(resp =>then(res resp.jsonp =>())
        .then(data => resp.json())
        .then {
            if (data.error(data => {
            if (data.error) {
) {
                appendHistory(`                appendHistory(`ОшибОшибка зака загрузкигрузки: ${data.error: ${data.error}`);
           }`);
            } else } else {
                {
                const media const mediaId = data.resultId = data.result.media.media_id;
_id;
                const                const type = data.result type =.type;
 data.result.type;
                if                if (type (type === ' === 'image')image') {
                    {
                    pendingImage pendingImageId = mediaId;
                    appendHistoryId = mediaId;
                    appendHistory(`✅ Изображ(`✅ Изображение заение загружгружено. ID:ено. ID: ${media ${mediaId}.Id}. Оно Оно будет прикреплено будет прикреплено к следую к следующему отправщему отправленному сообщленному сообщению.ению.`);
               `);
                } else {
                    } else {
                    pendingAudio pendingAudioId =Id = mediaId;
                    mediaId;
                    appendHistory appendHistory(`🎵 Аудио(`🎵 Аудио загружено. ID загружено: ${mediaId. ID: ${mediaId}. Оно будет}. О прикрепно будет прикреплено клено к следующему следующему отправлен отправленному сообщениюному сообщению.`);
.`);
                }
            }
        })
        .                }
            }
        })
        .catch(errcatch(err => {
 => {
            appendHistory(`            appendHistory(`ОшибОшибка соединка соединения при загрузения при загрузке:ке: ${err ${err.message}`);
.message}`);
        });
        });
    }

    }

    async    async function fetchOnlineUsers function fetchOnlineUsers() {
() {
        const        const data = await api data = await apiRequest('Request('GET',GET', '/online '/online_users');
_users');
        if        if (data) {
 (data) {
            const            const users = data.result;
            if (users.length === 0) {
                appendHistory('Нет пользова users = data.result;
            if (users.length === 0) {
                appendHistory('Нет пользователей втелей в сети.');
 сети.');
            }            } else {
 else {
                append                appendHistory('History('Пользователи вПользова сети:тели в сети:');
               ');
                users.forEach users.forEach(u => {
                   (u => {
                    appendHistory appendHistory(`  ${u(`  ${u.login}.login} (ID (ID: ${: ${u.id}) —u.id}) — последняя последняя активность активность: ${: ${u.lastu.last_seen_seen}`);
               }`);
                });
            });
            }
        }
        }
    }
    }

    }

    async function async function fetchMyChannels fetchMyChannels() {
() {
        const        const data = data = await apiRequest(' await apiRequest('GETGET',', '/my '/my_channels');
_channels');
        if        if (data (data) {
) {
            const            const channels = channels = data.result data.result;
           ;
            if (channels if (channels.length ===.length === 0 0) {
) {
                appendHistory('                appendHistory('Вы неВы не подписаны подписаны ни на ни на один ка один канал.');
нал.');
            }            } else {
 else {
                appendHistory('                appendВашиHistory('Ваши кана каналы:лы:');
               ');
                channels.forEach channels.forEach(ch => {
                   (ch => {
                    appendHistory appendHistory(` (`  ${ch ${ch.name}.name} (владеле (владелец: ${ch.ц: ${ch.ownerowner_login})_login}) — непрочитано: — непрочитано: ${ch ${ch.unread.unread_count}`);
_count}`);
                });
                });
            }
            }
        }
    }

        }
    async    }

    async function set function setUserColorUserColor(color)(color) {
        {
        const data = await const data = await apiRequest apiRequest('POST('POST', '/', '/set_colorset_color', {', { color });
 color });
        if        if (data (data) {
) {
            append            appendHistory(data.result);
History(data            set.result);
            setTextColorTextColor(color);
            //(color);
            // Об Обновляемновляем сесси сессию
ю
            const            const session = session = loadSession();
            loadSession if (();
            if (session)session) {
                {
                session.color session.color = color = color;
               ;
                saveSession(session.token, session.userId, saveSession(session.token, session.userId, session.login, color session.login, color);
           );
            }
        }
        }
    }

    // --- }
    }

    // --- Коман Команды ---
   ды --- const commands
    const commands = {
 = {
        '        'напнаписать':исать': (args (args) =>) => {
            {
            if ( if (args.length < args.length < 2)2) {
                {
                appendHistory appendHistory('Использова('Использование:ние: написать написать [ID [ID] [сооб] [сообщение]щение]');
                return;
');
                return;
            }
            }
            const toId            const toId = args[0];
            if ( = args[0];
            if (isNaNisNaN(toId(toId)) {
                append)) {
                appendHistory('History('Ошибка:Ошибка: ID должен ID должен быть чис быть числом');
лом');
                return;
            }
                           return;
            }
            const message const message = args.slice( = args.slice(1).1).join(' ');
           join(' ');
            apiCommand('на apiCommand('написать', { to_idписать', { to_id: toId,: toId, message, message, image_id image_id: pending: pendingImageId, audioImageId, audio_id:_id: pendingAudio pendingAudioId });
            pendingId });
            pendingImageIdImageId = null = null;
           ;
            pendingAudio pendingAudioId =Id = null;
 null;
        },
        '        },
        'профипрофиль': (argsль': (args) => {
            if () => {
            if (args.length < args.length < 1) {
               1) {
                appendHistory('И appendHistory('Использование:спользование: профиль [ID]');
 профиль [ID]');
                return                return;
            }
           ;
            }
            const profile const profileId =Id = args[0];
 args[0];
            if (is            if (isNaN(profileId)) {
NaN(profileId)) {
                append                appendHistory('History('ОшибОшибка:ка: ID должен ID должен быть числом');
 быть числом');
                return                return;
            }
           ;
            apiCommand('про }
            apiCommand('профильфиль', {', { profile_id: profile profile_id: profileId });
Id });
        },
        '        },
        'пингпинг': ()': () => appendHistory('понг'),
        'мой => appendHistory('понг'),
        'мойид':ид': () => appendHistory(`Ваш ID () => appendHistory(`Ваш ID: ${: ${currentUserId}`),
currentUserId}`),
        '        'непронепрочитанныечитанные': ()': () => fetch => fetchUnreadUnreadSummary(falseSummary(false),
       ),
        'про 'прочитать': (argsчитать':) => (args) => {
            if (args.length ===  {
            if (args.length === 0) {
                appendHistory0) {
                appendHistory('И('Использоваспользование: прочитатьние: прочитать [в [всёсё | ID]');
 | ID]');
                return                return;
           ;
            }
            if ( }
            if (args[0]args[0] === 'вс === 'всё')ё') {
                {
                readMessages();
            readMessages();
            } else } else {
                const from {
               Id = args const fromId = args[0];
                if[0];
                if (is (isNaN(fromId))NaN(fromId)) {
                    {
                    appendHistory appendHistory('О('Ошибкашибка: ID: ID должен быть числом должен быть числом');
                   ');
                    return;
                }
 return;
                read                }
                readMessages(fromMessages(fromId);
Id);
            }
        },
            }
        },
        '        'прочитатьпрочитать всё': всё': () => () => readMessages readMessages(),
       (),
        'помощ 'помощь':ь': () => showHelp () => showHelp(),
       (),
        'вых 'выход':од': () => {
            () => {
            authenticated = authenticated = false;
            currentUserId = false;
            currentUserId = null;
 null;
            currentLogin =            currentLogin = null;
 null;
            token            token = null;
            = null;
            lastUn lastUnreadHash = null;
           readHash = null autoRead;
           Enabled = autoReadEnabled = false;
            pending false;
            pendingImageIdImageId = null = null;
            pendingAudio;
            pendingAudioId =Id = null;
 null;
            disconnect            disconnectWebSocket();
            setTextWebSocket();
           Color(' setTextColor('зелензеленый');
ый');
            clearSession();
            clearSession();
            append            appendHistory('Вы выHistory('Вы вышли изшли из аккаунта аккаунта.');
           .');
            appendHistory('Д appendHistoryоступные коман('Доступды:ные команды: вход, вход, регистра регистрация, загруция, загрузить картинкузить кар, затинкугрузить музыку, загрузить музыку, прочитать кар, прочитать картинкутинку, про, прослушатьслушать музыку, онлайн музыку, онлайн');
       ');
        },
        'онлайн': fetchOnline },
        'онлайн': fetchOnlineUsers,
        'Users,
        'каналы': fetchMyканалы': fetchMyChannelsChannels,
       ,
        'цвет': ( 'цвет': (args)args) => {
            if => {
            if (args (args.length.length < 1) {
 < 1) {
                append                appendHistory(`History(`ИспользованиеИспользование: цвет: цвет [на [название]. Доступны: ${звание]. Доступны: ${VALID_COLORSVALID_COLORS.join(',.join(', ')}`);
                ')}`);
                return;
 return;
            }
            }
            const color = args            const color = args[0];
            if[0];
 (!VALID_COL            if (!VALID_COLORS.includesORS.includes(color))(color)) {
                {
                appendHistory appendHistory(`Н(`Недопуедопустимстимый цвет. Доступныый цвет. Доступны: ${VALID_COLORS: ${VALID_CO.join(',LORS.join(', ')} ')}`);
               `);
                return return;
            }
            setUserColor(color);
        },
        ';
            }
            setUserColor(color);
        },
        'автавтопрочтопрочтение': () =>ение': () => {
            {
            autoRead autoReadEnabled =Enabled = !auto !autoReadEnabledReadEnabled;
           ;
            appendHistory appendHistory(`Ав(`Автоматическое чттоматическое чтение ${autoReadEnabled ?ение ${autoReadEnabled ? 'включено 'включено' :' : 'отключено 'отключено'}.`);
       '}.`);
        },
        'ка },
        'канал создатьнал создать': (': (args)args) => {
            if => {
            if (args (args.length.length < 1) {
 < 1) {
                append                appendHistory('History('ИспользованиеИсп: каользование: канал создатьнал создать [на [название]');
звание]');
                return;
                           return;
            }
            }
            const name const name = args.join(' = args.join(' ');
            ');
            channelCreate(name);
 channelCreate(name);
        },
        },
        '        'каналканал подп подписатьсяисаться': (args)': (args) => {
 => {
            if            if (args.length (args < 1.length < 1) {
) {
                append                appendHistory('History('ИспИспользование: канал подользование: канал подписаписатьсяться [наз [название]вание]');
               ');
                return;
            }
 return;
            }
            const            const name = name = args.join(' ');
 args.join(' ');
            channel            channelSubscribe(name);
        },
       Subscribe(name);
        },
        'канал от 'канал отписаться': (argsписаться':) => (args) => {
            if (args.length {
            if (args.length <  < 1)1) {
                {
                appendHistory appendHistory('И('Использование:спользова каналние: канал отп отписатьсяисаться [на [названиезвание]');
]');
                return                return;
           ;
            }
            const name = args }
            const name = args.join('.join(' ');
            ');
            channelUn channelUnsubscribe(name);
       subscribe(name);
        },
        },
        'канал нап 'канал написать':исать': (args (args) =>) => {
            if ( {
            if (args.lengthargs.length < 2) < 2) {
                {
                appendHistory appendHistory('Использова('Использование:ние: канал канал написать написать [ [наназваниезвание]] [сообщение] [сообщение]');
               ');
                return;
 return;
            }
            }
            const name =            const name = args args[0];
[0];
            const            const message = args.slice message = args.slice(1(1).join).join(' ');
(' ');
            channelSend(name, message            channelSend(name, message, pending, pendingImageIdImageId, pending, pendingAudioIdAudioId);
            pendingImage);
            pendingImageId =Id = null;
 null;
            pending            pendingAudioIdAudioId = null;
        = null;
        },
        'ка },
        'канал прочитать': (args) =>нал прочитать': (args) => {
            if ( {
            if (args.lengthargs.length <  < 1) {
               1) {
                appendHistory appendHistory('Использование:('Использование: канал канал прочитать [на прочитатьзвание [название]');
]');
                return                return;
           ;
            }
            }
            const name const name = args = args.join(' ');
           .join(' ');
            channelRead channelRead(name);
        },
        '(name);
        },
        'каналканал удалить удалить': (args) => {
': (args) => {
            if (args            if (args.length.length < 1 < 1) {
) {
                appendHistory('                appendHistory('ИспИспользованиеользование: ка: канал уданал удалитьлить [название]');
                return;
 [название]');
                return;
            }
            }
            const            const name = name = args.join args.join(' ');
(' ');
            channel            channelDelete(name);
       Delete(name);
        },
        },
        'за 'загрузитьгрузить картин картинку':ку': () => () => {
            fileInput {
           .accept fileInput.accept = ' = 'image/pimage/png,ng, image/j image/jpeg,peg, image/gif, image/gif, image/b image/bmp,mp, image/web image/webp';
p';
            fileInput.click            fileInput.click();
       ();
        },
        'за },
        'загрузитьгрузить музыку': () => {
 музыку': ()            file => {
            fileInput.acInput.accept = 'audio/mpcept = 'audio3,/mp3, audio/wav, audio/wav, audio/ audio/ogg,ogg, audio/m4a audio/m4a, audio, audio/flac/flac';
           ';
            fileInput.click();
        },
 fileInput.click();
        },
        '        'прочитать картинпрочитать картинку':ку': (args) => (args) => {
            if (args.length < 1) {
                appendHistory('Использова {
            if (args.length < 1) {
                appendHistory('Использование: прочитать картинние: прочитать картинкуку [ID] [ID]');
                return;
');
                return;
            }
            }
            const            const id = args id = args[0];
            if[0];
            if (is (isNaN(idNaN(id)) {
)) {
                appendHistory('                appendHistory('ОшибОшибка:ка: ID должен ID должен быть чис быть числом');
лом');
                return                return;
            }
           ;
            openMedia }
            openMediaWindow('Window('image',image', id);
 id);
        },
        '        },
        'прослушатьпрослушать музыку музыку': (': (args)args) => {
 => {
            if (args            if (args.length.length < 1 < 1) {
) {
                append                appendHistory('History('ИспИспользованиеользование: про: прослушатьслушать музыку музыку [ID [ID]');
]');
                return                return;
           ;
            }
            const id }
            const id = args = args[0[0];
           ];
            if (isNaN if (isNaN(id))(id)) {
                appendHistory {
                appendHistory('О('Ошибкашибка: ID: ID должен быть должен быть числом числом');
               ');
                return;
            }
            open return;
            }
            openMediaWindow('audioMediaWindow', id('audio', id);
       );
        },
        'от },
        'открепить': ()крепить => {
': () => {
            pending            pendingImageId = null;
           ImageId = null;
            pendingAudioId = null;
            append pendingAudioId = null;
            appendHistory('ПрикрепHistory('Прикреплённыелённые медиа медиа сброш сброшены.');
ены.');
        },
        },
        'ава        'аватар':тар': (args (args) => {
           ) => {
            if (args.length < 1) if (args.length < 1) {
                appendHistory(' {
                appendHistory('ИспользоваИспользование:ние: ава аватар [ID изображтар [ID изображения]ения]');
                return;
');
                return;
            }
            }
            const            const imageId imageId = args = args[0[0];
           ];
            if (isNaN if (isNaN(imageId(imageId)) {
                append)) {
                appendHistory('History('Ошибка: ID долженОшиб быть числом');
ка: ID должен быть числом');
                return;
                           return;
            }
            }
            apiCommand apiCommand('аватар('аватар', {', { image_id: image image_id: imageId });
Id });
        }
        }
    };

    function    };

    function showHelp showHelp() {
() {
        appendHistory('        appendHistory('ДоступныеДоступные команды (после входа):');
 команды (после входа):');
        for (let        for (let [cat, cmds] of [cat, cmds] of Object. Object.entries(commandCategories))entries(commandCategories)) {
            {
            appendHistory appendHistory(`  ${cat(`  ${cat}: ${}: ${cmdscmds.join(',.join(', ')}`);
        ')}`);
        }
    }
    }

    async function }

    async function apiCommand(command, apiCommand args)(command, args) {
        {
        await api await apiRequest('POST', '/command', {Request('POST', '/command', { command, args });
 command,    }

 args });
    }

    //    // --- Автод --- Авополтодополнение ---нение ---
    function getAll
    function getAllCommands()Commands() {
        if (! {
       authenticated if (!) {
authenticated) {
            return            return ['вход', ['в 'регистрация', 'ход', 'регистрациязагру', 'зить карзагрузить картинкутинку', 'загрузить музыку', 'прочитать кар', 'загрузить музыку', 'протинку', 'читать картинкупрос', 'прослушатьлушать музыку музыку', 'онлайн', 'онлайн'];
       '];
        } else } else {
            return Object {
            return Object.keys(.keys(commands);
        }
    }

commands);
        }
    }

    function    function showAut showAutocomplete(completocomplete(completions) { /*ions) { /* ... ... без изменений ... */ }
 без изменений ... */ }
    function    function hideAutocomplete() { /* ... без hideAutocomplete() { /* ... без изменений ... */ }
    изменений ... */ }
    function renderAutocomplete function renderAutocomplete(categorized) {(categorized) { /* ... /* ... без изменений ... без измен */ }
    functionений ... */ }
    function completeWith completeWith(com(completion)pletion) { /* ... без { /* изменений ... без изменений ... */ ... */ }
    function get }
    function getCompletionListCompletionList(prefix(prefix) { /* ...) { /* ... без изменений ... без изменений ... */ }
    function */ }
    function handleTab() { /* ... без измен handleTab() { /* ... без изменений ... */ }

    // --- Оений ... */ }

    // --- Обработка ввода ---бработка ввода ---
    function execute
   Command(cmd) function executeCommand(cmd) {
 {
        const trimmed = cmd.trim        const trimmed = cmd.trim();
        if (trimmed === ''();
        if (trimmed === '' && mode && mode === 'normal') === ' return;

normal') return;

        if        if (autocompleteMenu (autocompleteMenu.classList.contains('visible.classList.contains('visible') && selectedCompletionIndex !== -1') && selectedCompletionIndex !==) {
            const selected = currentCom -1) {
            const selected = currentCompletions[selectedpletions[selectedCompletionIndexCompletionIndex];
            if (];
            if (selected)selected) {
                {
                completeWith(selected);
            completeWith(selected);
            }
            return;
        }

        hide }
            return;
        }

Autocomplete        hideAutocomplete();

       ();

        if ( if (mode !== 'normalmode !== 'normal') {
            handleInputIn') {
            handleMode(InputInMode(trimmedtrimmed);
            return;
        }

);
            return;
        }

        commandHistory.push(trim        commandHistory.push(trimmed);
med);
        history        historyIndex = commandHistoryIndex = commandHistory.length;
.length;
        save        saveCommandHistoryCommandHistory();

        const promptChar = authenticated ? `${currentLogin}@pc:~$` :();

        const promptChar = authenticated ? `${currentLogin}@pc:~$` : '>';
 '>';
        append        appendHistory(`${promptHistory(`${Char}promptChar} ${trim ${trimmed}`med}`);

       );

        if (! if (!authenticated) {
            handleauthenticated) {
            handleUnauthenticatedUnauthenticatedCommand(trimmed);
            commandInput.value =Command(trimmed);
            commandInput.value = '';
            render();
 '';
            render();
            return            return;
       ;
        }

        const parts = trimmed.toLowerCase(). }

        const parts = trimmed.toLowerCase().split(' ');
       split(' ');
        const command = parts const command = parts[0[0];
       ];
        const args = parts const args = parts.slice(.slice(1);

        if1);

        if (commands (commands[command]) {
[command]) {
            commands[command            commands[command](args);
       ](args);
        } else } else {
            const twoWord = parts.slice(0,2 {
            const twoWord = parts.slice(0,2).join(' ');
).join(' ');
            if (commands            if[two (commands[twoWord])Word]) {
                commands {
               [twoWord](parts.slice( commands[twoWord](parts2));
.slice(            }2));
            } else {
 else {
                append                appendHistory(`History(`НеизНеизвестнаявестная команда: ${ команда: ${command}.command}. Введите "помощ Введите "помощь"ь" для списка команд для списка команд.`);
.`);
            }
            }
        }

        commandInput.value        }

        commandInput.value = '';
        render();
    = '';
        render();
    }

    }

    function handle function handleUnauthenticatedUnauthenticatedCommand(Command(trimmedtrimmed) { /* ...) { /* ... без измен без изменений ... */ }
ений ... */ }
    function    function handleInput handleInputInModeInMode(input) { /* ... без(input) { /* ... без изменений изменений ... */ }

    ... */ }

    // --- Инициализация и обработ // --- Инициализация и обработчикичики ---
    setTimeout ---
(checkSession    setTimeout(checkSession, 100);

    command, 100);

Input.addEventListener    commandInput.addEventListener('input('input', ()', () => {
        if => {
        if (mode (mode !== 'normal') !== ' return;
normal') return;
        const val = commandInput.value.trim        const val =().toLowerCase();
 commandInput.value.trim().toLowerCase();
        if        if (val.length < 2 (val.length < 2) {
            hide) {
            hideAutocomplete();
           Autocomplete();
            return;
        }
 return;
        }
        const all =        const all = getAllCommands getAllCommands();
        const complet();
       ions = const completions = all.filter all.filter(cmd(cmd => cmd.includes(val => cmd.includes(val)).slice(0, )).slice(0, 20);
        if20);
        if (completions.length > (completions.length > 0 0) {
) {
            show            showAutocompleteAutocomplete(completions(completions);
       );
        } else {
            hideAut } else {
            hideAutocomplete();
ocomplete();
        }
    });

    command        }
    });

    commandInput.addEventListenerInput.addEventListener('key('keydown', (edown', (e) => {
       ) => {
        const key const key = e = e.key;
        const input = commandInput.key;
        const input = commandInput;

        if (autocomplete;

        if (autocompleteMenu.classListMenu.classList.contains('visible')).contains('visible')) {
            {
            // ... обработка // ... обработка навига навигации ...
ции ...
            return            return;
        }

        if (;
        }

        if (e.e.ctrlKey && keyctrlKey && key === ' === 'v')v') {
            e.preventDefault {
            e.preventDefault();
            navigator.clipboard.read();
            navigator.clipboard.readText().then(textText().then(text => {
                const => {
                const start = start = input.se input.selectionStart;
                const endlectionStart;
                const end = input.selectionEnd;
                const = input.selectionEnd;
                const newValue newValue = input = input.value.substring(0, start.value.substring(0, start) +) + text + input.value text + input.value.substring(end);
               .substring(end);
                input.value = new input.value = newValue;
Value;
                input                input.setSelectionRange(start.setSelectionRange(start + text + text.length, start +.length, text.length);
            start + text.length);
            }).catch(err => }).catch {
               (err => {
                appendHistory appendHistory('Не('Не удалось вставить удалось текст: ' + err);
 вставить текст: ' + err);
            });
            });
            return            return;
        }

        if (key ===;
        }

        if ( 'Enter') {
key === 'Enter') {
            e.preventDefault();
            e.preventDefault();
            executeCommand(input            executeCommand(input.value);
.value);
            return;
        }

                   return;
        if ( }

        if (key ===key === 'Tab 'Tab') {
            e') {
.preventDefault();
            e.preventDefault();
            handle            handleTab();
Tab();
            return;
                   return;
        }

        }

        if (key === if ( 'Arrowkey === 'ArrowUp')Up') {
            e.preventDefault {
            e.preventDefault();
           ();
            if (historyIndex >  if (historyIndex0) {
                > 0) {
                historyIndex--;
                input.value = commandHistory historyIndex--;
                input.value = commandHistory[historyIndex] || '';
               [historyIndex] || '';
                setTimeout(() setTimeout(() => input.setSelectionRange(input => input.setSelectionRange(input.value.length.value.length, input, input.value.length), .value.length), 0);
            }
0);
            }
            return            return;
        }
       ;
        }
        if (key === ' if (key ===ArrowDown') 'Arrow {
           Down') {
            e.preventDefault();
            e.preventDefault();
            if (historyIndex if (historyIndex < commandHistory.length < commandHistory.length - 1) {
                historyIndex - 1) {
                historyIndex++;
                input.value = commandHistory++;
                input.value = commandHistory[historyIndex[historyIndex] || '';
               ] || setTimeout(() => input '';
                setTimeout(() => input.setSelectionRange.setSelectionRange(input.value.length(input.value.length, input, input.value.length), .value.length), 0);
            }0);
            } else {
                history else {
                historyIndex = commandHistoryIndex = commandHistory.length;
                input.length;
                input.value =.value = '';
            '';
            }
            return;
 }
            return;
        }
    });

    file        }
    });

Input.addEventListener('change', (e) => {
    fileInput.addEventListener('change', (e) => {
        const file = e.target        const file =.files e.target.files[0[0];
        if (];
        if (file)file) {
            {
            if (!authenticated if (!authenticated) {
) {
                append                appendHistory('ОшибHistory('Ошибка:ка: необходимо войти в систему для загруз необходимо войти в систему для загрузки файлов.');
ки файлов.');
                return;
                           return;
            }
            }
            uploadFile(file);
 uploadFile(file);
        }
        file        }
        fileInput.value = '';
    });

    documentInput.value = '';
    });

    document.getElementById('.getElementById('terminalContainer').addterminalContainer').addEventListener('click', () =>EventListener(' commandInputclick', () => commandInput.focus());

   .focus commandInput.addEventListener('());

    commandInput.addEventListener('blur', ()blur', () => {
 => {
        setTimeout(() =>        setTimeout(() => hideAut hideAutocomplete(),ocomplete(), 200);
    200 });

   );
    });

    window.open window.openMediaWindow = openMediaWindowMediaWindow = openMediaWindow;

   ;

    render();
})();
 render();
