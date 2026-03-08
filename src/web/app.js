// StackOwl Web UI Logic

const state = {
    owls: [],
    activeOwl: null,
    chatHistory: [],
    isWaiting: false,
};

// --- Initialization ---

async function init() {
    await fetchOwls();
    setupEventListeners();

    // Default config
    marked.setOptions({
        gfm: true,
        breaks: true
    });
}

// --- API Calls ---

async function fetchOwls() {
    try {
        const res = await fetch('/api/owls');
        const owls = await res.json();
        state.owls = owls;

        if (owls.length > 0) {
            setActiveOwl(owls[0].name);
        }

        renderOwlsList();
        renderParliamentOwls();
    } catch (e) {
        console.error('Failed to fetch owls', e);
    }
}

async function sendMessage(text) {
    if (!state.activeOwl || state.isWaiting || !text.trim()) return;

    state.isWaiting = true;
    updateChatUI();

    // Add user message to UI immediately
    const inputEl = document.getElementById('chat-input');
    inputEl.value = '';

    appendMessage('user', text, 'You');

    // Show loading state
    const historyEl = document.getElementById('chat-history');
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'message assistant loading-msg';
    loadingDiv.innerHTML = `<div class="message-content"><span class="assistant-name">${state.activeOwl.emoji} ${state.activeOwl.name}</span><p>Thinking...</p></div>`;
    historyEl.appendChild(loadingDiv);
    historyEl.scrollTop = historyEl.scrollHeight;

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, owlName: state.activeOwl.name })
        });

        const data = await res.json();

        // Remove loading
        historyEl.removeChild(loadingDiv);

        if (data.error) {
            appendMessage('system', data.error);
        } else {
            appendMessage('assistant', data.content, state.activeOwl.name, state.activeOwl.emoji);
        }
    } catch (e) {
        historyEl.removeChild(loadingDiv);
        appendMessage('system', 'Network error. Could not reach StackOwl.');
    } finally {
        state.isWaiting = false;
        updateChatUI();
    }
}

async function searchPellets(query = '') {
    const grid = document.getElementById('pellets-grid');
    grid.innerHTML = '<div class="loading">Searching...</div>';

    try {
        const res = await fetch(`/api/pellets?q=${encodeURIComponent(query)}`);
        const pellets = await res.json();

        if (pellets.length === 0) {
            grid.innerHTML = '<div class="loading">No pellets found.</div>';
            return;
        }

        grid.innerHTML = '';
        pellets.forEach(p => {
            const card = document.createElement('div');
            card.className = 'pellet-card';

            const tagsHtml = p.tags.map(t => `<span class="tag">${t}</span>`).join('');

            card.innerHTML = `
                <h3>${p.title}</h3>
                <div class="excerpt">${escapeHtml(p.content.substring(0, 150))}...</div>
                <div class="meta">
                    <div class="tags">${tagsHtml}</div>
                    <span>${new Date(p.generatedAt).toLocaleDateString()}</span>
                </div>
            `;

            card.addEventListener('click', () => openPelletModal(p));
            grid.appendChild(card);
        });
    } catch (e) {
        grid.innerHTML = '<div class="loading">Error loading pellets.</div>';
    }
}

async function startParliament(topic, selectedOwlNames) {
    const loadingEl = document.getElementById('parliament-loading');
    const actionsEl = document.querySelector('.modal-actions');

    loadingEl.classList.remove('hidden');
    actionsEl.classList.add('hidden');

    try {
        const res = await fetch('/api/parliament', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic, owlNames: selectedOwlNames })
        });

        const data = await res.json();

        if (data.error) {
            alert('Parliament Error: ' + data.error);
        } else {
            // Close modal, switch to Chat, and post the report
            document.getElementById('parliament-modal').classList.add('hidden');
            switchTab('chat-view');

            appendMessage('system', 'Parliament Session Concluded: ' + topic);
            appendMessage('assistant', document.getElementById('parliament-topic').value, 'User Inquiry');
            appendMessage('assistant', data.report, 'Parliament Ledger', '🏛️');

            document.getElementById('parliament-topic').value = '';
        }
    } catch (e) {
        alert('Failed to convene parliament.');
    } finally {
        loadingEl.classList.add('hidden');
        actionsEl.classList.remove('hidden');
    }
}

// --- UI Rendering ---

function renderOwlsList() {
    const container = document.getElementById('owls-list');
    container.innerHTML = '';

    state.owls.forEach(owl => {
        const el = document.createElement('div');
        el.className = `owl-item ${state.activeOwl?.name === owl.name ? 'active' : ''}`;
        el.innerHTML = `
            <div class="owl-emoji">${owl.emoji}</div>
            <div class="owl-info">
                <h4>${owl.name}</h4>
                <p>${owl.type}</p>
            </div>
        `;
        el.addEventListener('click', () => setActiveOwl(owl.name));
        container.appendChild(el);
    });
}

function renderParliamentOwls() {
    const container = document.getElementById('parliament-owls');
    container.innerHTML = '';

    state.owls.forEach(owl => {
        const el = document.createElement('label');
        el.className = 'parliament-owl-checkbox';
        el.innerHTML = `
            <input type="checkbox" value="${owl.name}" checked>
            <span>${owl.emoji} ${owl.name}</span>
        `;
        container.appendChild(el);
    });
}

function setActiveOwl(name) {
    const owl = state.owls.find(o => o.name === name);
    if (!owl) return;

    state.activeOwl = owl;

    // Update header
    document.getElementById('header-owl-emoji').textContent = owl.emoji;
    document.getElementById('header-owl-name').textContent = owl.name;
    document.getElementById('header-owl-type').textContent = `${owl.type} • Challenge: ${owl.challengeLevel}`;

    // Remount list to show active state
    renderOwlsList();

    // Add system message if switching
    appendMessage('system', `Switched to ${owl.emoji} ${owl.name}.`);
}

function appendMessage(role, content, name = null, emoji = null) {
    const historyEl = document.getElementById('chat-history');
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;

    const parsedContent = role === 'system' ? content : marked.parse(content);

    let headerHtml = '';
    if (role === 'assistant' && name) {
        headerHtml = `<span class="assistant-name">${emoji ? emoji + ' ' : ''}${name}</span>`;
    }

    msgDiv.innerHTML = `<div class="message-content">${headerHtml}<div class="md">${parsedContent}</div></div>`;
    historyEl.appendChild(msgDiv);
    historyEl.scrollTop = historyEl.scrollHeight;
}

function updateChatUI() {
    const btn = document.getElementById('btn-send');
    const input = document.getElementById('chat-input');

    if (state.isWaiting) {
        btn.disabled = true;
        input.disabled = true;
    } else {
        btn.disabled = false;
        input.disabled = false;
        input.focus();
    }
}

// --- Event Listeners and Navigation ---

function setupEventListeners() {
    // Tabs
    document.querySelectorAll('.tab-btn[data-target]').forEach(btn => {
        btn.addEventListener('click', (e) => {
            switchTab(e.currentTarget.dataset.target);
            document.querySelectorAll('.tab-btn[data-target]').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
        });
    });

    // Chat
    const chatInput = document.getElementById('chat-input');
    const btnSend = document.getElementById('btn-send');

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage(chatInput.value);
        }
    });

    btnSend.addEventListener('click', () => {
        sendMessage(chatInput.value);
    });

    // Parliament Modal
    document.getElementById('btn-parliament').addEventListener('click', () => {
        document.getElementById('parliament-modal').classList.remove('hidden');
    });

    document.getElementById('btn-close-parliament').addEventListener('click', () => {
        document.getElementById('parliament-modal').classList.add('hidden');
    });

    document.getElementById('btn-cancel-parliament').addEventListener('click', () => {
        document.getElementById('parliament-modal').classList.add('hidden');
    });

    document.getElementById('btn-start-parliament').addEventListener('click', () => {
        const topic = document.getElementById('parliament-topic').value;
        const checkboxes = document.querySelectorAll('#parliament-owls input:checked');
        const selectedOwls = Array.from(checkboxes).map(cb => cb.value);

        if (!topic.trim()) {
            alert('Please enter a topic.');
            return;
        }
        if (selectedOwls.length < 2) {
            alert('Please select at least 2 owls.');
            return;
        }

        startParliament(topic, selectedOwls);
    });

    // Pellets Search
    let debounceTimer;
    document.getElementById('pellets-search').addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            searchPellets(e.target.value);
        }, 500);
    });

    // Close Pellet Modal
    document.getElementById('btn-close-pellet').addEventListener('click', () => {
        document.getElementById('pellet-modal').classList.add('hidden');
    });
}

function switchTab(targetId) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById(targetId).classList.add('active');

    if (targetId === 'pellets-view') {
        searchPellets();
    }
}

function openPelletModal(pellet) {
    document.getElementById('reader-title').textContent = pellet.title;
    document.getElementById('reader-date').textContent = new Date(pellet.generatedAt).toLocaleString();
    document.getElementById('reader-owls').textContent = pellet.owls.join(', ');

    document.getElementById('reader-tags').innerHTML = pellet.tags.map(t => `<span class="tag">${t}</span>`).join('');
    document.getElementById('reader-body').innerHTML = marked.parse(pellet.content);

    document.getElementById('pellet-modal').classList.remove('hidden');
}

// Utils
function escapeHtml(unsafe) {
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Start
document.addEventListener('DOMContentLoaded', init);
