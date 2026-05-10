html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gemma Game Studio</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg:#0a0a0f;--surface:#12121a;--border:#1e1e2e;
    --accent:#7c3aed;--accent2:#06b6d4;--good:#10b981;--warn:#f59e0b;--err:#ef4444;
    --text:#e2e8f0;--muted:#64748b;--font:'JetBrains Mono',monospace;
  }
  body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:13px;min-height:100vh}
  h1,h2,h3{font-weight:600;letter-spacing:-.02em}
  button{cursor:pointer;border:none;outline:none;font-family:var(--font);font-size:13px}
  input,textarea,select{font-family:var(--font);font-size:13px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;width:100%}
  input:focus,textarea:focus,select:focus{outline:2px solid var(--accent);border-color:var(--accent)}
  .btn{padding:8px 18px;border-radius:6px;font-weight:600;transition:all .15s}
  .btn-primary{background:var(--accent);color:#fff}
  .btn-primary:hover{background:#6d28d9}
  .btn-secondary{background:var(--surface);color:var(--text);border:1px solid var(--border)}
  .btn-secondary:hover{border-color:var(--accent);color:var(--accent)}
  .btn-ghost{background:transparent;color:var(--muted)}
  .btn-ghost:hover{color:var(--text)}
  .badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:700;letter-spacing:.05em}
  .badge-creative{background:#1e1b4b;color:#a5b4fc}
  .badge-build{background:#052e16;color:#86efac}
  .badge-repair{background:#431407;color:#fdba74}
  .badge-playtest{background:#0c4a6e;color:#7dd3fc}
  .hidden{display:none!important}

  /* ── AUTH ── */
  #page-auth{display:flex;align-items:center;justify-content:center;min-height:100vh;
    background:radial-gradient(ellipse at 50% 0%,#1e1040 0%,var(--bg) 70%)}
  .auth-card{background:var(--surface);border:1px solid var(--border);border-radius:16px;
    padding:48px;width:400px;text-align:center}
  .auth-card h1{font-size:1.8rem;margin-bottom:4px}
  .auth-card .subtitle{color:var(--muted);margin-bottom:32px}
  .auth-card .logo{font-size:3rem;margin-bottom:16px}
  .auth-field{text-align:left;margin-bottom:16px}
  .auth-field label{display:block;color:var(--muted);margin-bottom:6px;font-size:11px;letter-spacing:.1em;text-transform:uppercase}
  .auth-error{color:var(--err);margin-top:12px;font-size:12px}

  /* ── SHELL ── */
  #shell{display:flex;flex-direction:column;min-height:100vh}
  .topbar{background:var(--surface);border-bottom:1px solid var(--border);
    padding:0 24px;height:52px;display:flex;align-items:center;gap:16px;flex-shrink:0}
  .topbar .logo{font-size:1.1rem;font-weight:700;color:var(--accent);letter-spacing:-.03em}
  .topbar .logo span{color:var(--muted)}
  .topbar .active-game{color:var(--accent2);font-size:12px;background:#0e2a33;
    padding:3px 10px;border-radius:99px;border:1px solid #155e75}
  .topbar .spacer{flex:1}
  .topbar-nav{display:flex;gap:4px}
  .topbar-nav button{background:transparent;color:var(--muted);padding:6px 14px;border-radius:6px;border:none}
  .topbar-nav button.active,.topbar-nav button:hover{background:var(--border);color:var(--text)}

  /* ── GAME MANAGER ── */
  #page-games{padding:32px}
  .page-header{display:flex;align-items:center;gap:16px;margin-bottom:32px}
  .page-header h2{font-size:1.5rem}
  .games-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}
  .game-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;
    padding:24px;cursor:pointer;transition:all .15s;position:relative}
  .game-card:hover{border-color:var(--accent);transform:translateY(-2px)}
  .game-card .game-icon{font-size:2.5rem;margin-bottom:12px}
  .game-card h3{font-size:1.1rem;margin-bottom:4px}
  .game-card .game-meta{color:var(--muted);font-size:12px;margin-bottom:12px}
  .game-card .game-stats{display:flex;gap:12px;font-size:12px;color:var(--muted);margin-top:12px}
  .new-game-card{border-style:dashed;display:flex;flex-direction:column;align-items:center;
    justify-content:center;min-height:180px;color:var(--muted)}
  .new-game-card:hover{border-color:var(--accent);color:var(--accent)}
  .new-game-card .plus{font-size:2rem;margin-bottom:8px}

  /* ── WIZARD ── */
  #page-wizard{padding:32px;max-width:720px;margin:0 auto}
  .wizard-steps{display:flex;gap:8px;margin-bottom:40px;align-items:center}
  .wizard-step{display:flex;align-items:center;gap:8px;flex:1}
  .wizard-step-num{width:28px;height:28px;border-radius:50%;background:var(--border);
    color:var(--muted);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
  .wizard-step.active .wizard-step-num{background:var(--accent);color:#fff}
  .wizard-step.done .wizard-step-num{background:var(--good);color:#fff}
  .wizard-step-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
  .wizard-step.active .wizard-step-label{color:var(--text)}
  .wizard-connector{flex:1;height:1px;background:var(--border);margin:0 4px}
  .section{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px;margin-bottom:16px}
  .section h3{margin-bottom:16px;font-size:1rem;color:var(--accent2)}
  .form-row{margin-bottom:16px}
  .form-row label{display:block;color:var(--muted);margin-bottom:6px;font-size:11px;letter-spacing:.08em;text-transform:uppercase}
  .style-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
  .style-option{background:var(--bg);border:2px solid var(--border);border-radius:8px;
    padding:14px;text-align:center;cursor:pointer;transition:all .15s}
  .style-option:hover{border-color:var(--muted)}
  .style-option.selected{border-color:var(--accent);background:#1e1040}
  .style-option .icon{font-size:1.8rem;margin-bottom:6px}
  .style-option .name{font-weight:600;font-size:13px}
  .style-option .desc{color:var(--muted);font-size:11px;margin-top:2px}
  .scale-list{display:flex;flex-direction:column;gap:8px}
  .scale-option{background:var(--bg);border:2px solid var(--border);border-radius:8px;
    padding:12px 16px;cursor:pointer;display:flex;align-items:center;gap:12px;transition:all .15s}
  .scale-option:hover{border-color:var(--muted)}
  .scale-option.selected{border-color:var(--accent);background:#1e1040}
  .scale-option .scale-name{font-weight:700;width:90px;flex-shrink:0}
  .scale-option .scale-detail{color:var(--muted);font-size:12px}
  .tone-pills{display:flex;flex-wrap:wrap;gap:8px}
  .tone-pill{padding:5px 14px;border-radius:99px;border:1px solid var(--border);
    cursor:pointer;font-size:12px;transition:all .15s}
  .tone-pill.selected{background:var(--accent);border-color:var(--accent);color:#fff}
  .multi-pills{display:flex;flex-wrap:wrap;gap:8px}
  .multi-pill{padding:5px 14px;border-radius:99px;border:1px solid var(--border);
    cursor:pointer;font-size:12px;transition:all .15s}
  .multi-pill.selected{background:#155e75;border-color:var(--accent2);color:var(--accent2)}
  .wizard-nav{display:flex;justify-content:space-between;margin-top:24px}

  /* ── STUDIO ── */
  #page-studio{display:flex;flex-direction:column;flex:1;padding:16px;gap:12px}
  .studio-header{display:flex;align-items:center;gap:12px}
  .studio-header h2{font-size:1.1rem}
  .stat-row{display:flex;gap:10px;flex-wrap:wrap}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:8px;
    padding:10px 16px;white-space:nowrap}
  .stat .label{color:var(--muted);font-size:10px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:2px}
  .stat .value{font-size:1.1rem;font-weight:700}
  .studio-main{display:grid;grid-template-columns:1fr 340px 340px;gap:12px;flex:1;min-height:0}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:10px;
    display:flex;flex-direction:column;overflow:hidden}
  .panel-header{padding:10px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;flex-shrink:0}
  .panel-header .title{font-weight:700;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
  .panel-body{flex:1;overflow:auto;padding:10px}
  #log-output{font-size:11px;line-height:1.7;white-space:pre-wrap;word-break:break-all}
  #log-output .log-info{color:#94a3b8}
  #log-output .log-warn{color:var(--warn)}
  #log-output .log-err{color:var(--err)}
  #log-output .log-good{color:var(--good)}
  .chat-messages{display:flex;flex-direction:column;gap:8px;min-height:0}
  .chat-msg{padding:8px 12px;border-radius:8px;font-size:12px;line-height:1.5;max-width:90%;word-break:break-word}
  .chat-msg.human{background:#1e1b4b;align-self:flex-end}
  .chat-msg.agent{background:var(--bg);border:1px solid var(--border);align-self:flex-start}
  .chat-input-row{display:flex;gap:6px;padding:8px;border-top:1px solid var(--border);flex-shrink:0}
  .chat-input-row input{flex:1;padding:7px 10px}
  .shots-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
  .shot-item{border-radius:6px;overflow:hidden;aspect-ratio:16/9;cursor:pointer;position:relative}
  .shot-item img{width:100%;height:100%;object-fit:cover}
  .shot-item .shot-label{position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent,rgba(0,0,0,.8));
    padding:4px 6px;font-size:10px;color:#fff}
  #lightbox{position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:1000;
    display:none;align-items:center;justify-content:center}
  #lightbox img{max-width:90vw;max-height:90vh;border-radius:8px}
  #lightbox.open{display:flex}
  #lightbox-close{position:absolute;top:16px;right:20px;font-size:2rem;color:#fff;cursor:pointer;background:none;border:none}
  .studio-tabs{display:flex;gap:4px;padding:8px;border-bottom:1px solid var(--border)}
  .studio-tab{padding:5px 14px;border-radius:6px;font-size:12px;font-weight:600;
    background:transparent;color:var(--muted);border:none;cursor:pointer}
  .studio-tab.active{background:var(--border);color:var(--text)}
  .tab-pane{display:none;padding:12px;flex:1;overflow:auto}
  .tab-pane.active{display:block}
  textarea.editor{width:100%;height:300px;background:var(--bg);resize:vertical}
</style>
</head>
<body>

<!-- AUTH -->
<div id="page-auth" class="hidden">
  <div class="auth-card">
    <div class="logo">🎮</div>
    <h1>Game Studio</h1>
    <p class="subtitle" id="auth-subtitle">Sign in to continue</p>
    <div class="auth-field">
      <label id="auth-label">Password</label>
      <input type="password" id="auth-password" placeholder="Enter password"
        onkeydown="if(event.key==='Enter')authSubmit()">
    </div>
    <div id="auth-confirm-row" class="auth-field hidden">
      <label>Confirm Password</label>
      <input type="password" id="auth-confirm" placeholder="Confirm password">
    </div>
    <button class="btn btn-primary" style="width:100%" onclick="authSubmit()" id="auth-btn">Sign In</button>
    <div class="auth-error hidden" id="auth-error"></div>
  </div>
</div>

<!-- SHELL -->
<div id="shell" class="hidden">
  <div class="topbar">
    <div class="logo">🎮 <span>Gemma</span> Game Studio</div>
    <div class="active-game hidden" id="active-game-badge"></div>
    <div class="spacer"></div>
    <div class="topbar-nav">
      <button onclick="showPage('games')" id="nav-games">Games</button>
      <button onclick="showPage('studio')" id="nav-studio" class="hidden">Studio</button>
    </div>
    <button class="btn btn-ghost" onclick="logout()" style="margin-left:8px">Sign out</button>
  </div>
  <div style="flex:1;display:flex;flex-direction:column">

    <!-- GAME MANAGER -->
    <div id="page-games" class="hidden">
      <div class="page-header"><h2>Your Games</h2></div>
      <div class="games-grid" id="games-grid"></div>
    </div>

    <!-- WIZARD -->
    <div id="page-wizard" class="hidden">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:32px">
        <button class="btn btn-ghost" onclick="showPage('games')" style="font-size:1.2rem;padding:4px 8px">&#8592;</button>
        <h2 style="font-size:1.4rem">New Game</h2>
      </div>
      <div class="wizard-steps" id="wizard-steps"></div>
      <div id="wizard-body"></div>
      <div class="wizard-nav">
        <button class="btn btn-secondary" id="wiz-back" onclick="wizNav(-1)">Back</button>
        <button class="btn btn-primary" id="wiz-next" onclick="wizNav(1)">Next</button>
      </div>
    </div>

    <!-- STUDIO -->
    <div id="page-studio" class="hidden">
      <div class="studio-header">
        <button class="btn btn-ghost" onclick="showPage('games')" style="font-size:1.1rem;padding:4px 8px">&#8592;</button>
        <h2 id="studio-title">Studio</h2>
        <div class="badge badge-build" id="studio-phase-badge">BUILD</div>
      </div>
      <div class="stat-row">
        <div class="stat"><div class="label">Mode</div><div class="value" id="stat-mode">&#8212;</div></div>
        <div class="stat"><div class="label">Iteration</div><div class="value" id="stat-iter">&#8212;</div></div>
        <div class="stat"><div class="label">Tasks Done</div><div class="value" id="stat-tasks">&#8212;</div></div>
        <div class="stat"><div class="label">Build</div><div class="value" id="stat-build">&#8212;</div></div>
      </div>
      <div class="studio-main">
        <div class="panel">
          <div class="panel-header">
            <span class="title">Logs</span>
            <button class="btn btn-ghost" style="margin-left:auto;font-size:11px" onclick="clearLogs()">Clear</button>
          </div>
          <div class="panel-body" id="log-output"></div>
        </div>
        <div class="panel" style="display:flex;flex-direction:column">
          <div class="panel-header"><span class="title">Chat with Gemma</span></div>
          <div class="panel-body chat-messages" id="chat-messages"></div>
          <div class="chat-input-row">
            <input type="text" id="chat-input" placeholder="Message Gemma&#8230;"
              onkeydown="if(event.key==='Enter')sendChat()">
            <button class="btn btn-primary" onclick="sendChat()">Send</button>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <span class="title">Observation Deck</span>
            <button class="btn btn-ghost" style="margin-left:auto;font-size:11px" onclick="clearScreenshots()">Clear</button>
          </div>
          <div class="panel-body"><div class="shots-grid" id="shots-grid"></div></div>
        </div>
      </div>
      <div class="panel">
        <div class="studio-tabs">
          <button class="studio-tab active" onclick="switchTab('manifesto',this)">Manifesto</button>
          <button class="studio-tab" onclick="switchTab('journal',this)">Journal</button>
          <button class="studio-tab" onclick="switchTab('reminders',this)">Reminders</button>
        </div>
        <div id="tab-manifesto" class="tab-pane active">
          <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
            <button class="btn btn-secondary" onclick="saveManifesto()">Save</button>
          </div>
          <textarea class="editor" id="manifesto-editor"></textarea>
        </div>
        <div id="tab-journal" class="tab-pane">
          <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
            <button class="btn btn-secondary" onclick="saveJournal()">Save</button>
          </div>
          <textarea class="editor" id="journal-editor"></textarea>
        </div>
        <div id="tab-reminders" class="tab-pane">
          <div id="reminders-list" style="display:flex;flex-direction:column;gap:6px;margin-bottom:12px"></div>
          <div style="display:flex;gap:6px">
            <input type="text" id="reminder-input" placeholder="Add reminder&#8230;" style="flex:1">
            <button class="btn btn-primary" onclick="addReminder()">Add</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<div id="lightbox" onclick="closeLightbox()">
  <button id="lightbox-close" onclick="closeLightbox()">&#215;</button>
  <img id="lightbox-img" src="" alt="">
</div>

<script>
const WS_URL = 'ws://' + location.host + '/ws';
let ws = null;
let activeGame = null;
let state = {};

// ---- ROUTER ----
function showPage(name) {
  ['auth','games','wizard','studio'].forEach(function(p) {
    var el = document.getElementById('page-' + p);
    if (el) el.classList.add('hidden');
  });
  var target = document.getElementById('page-' + name);
  if (target) target.classList.remove('hidden');
  document.querySelectorAll('.topbar-nav button').forEach(function(b) { b.classList.remove('active'); });
  var nb = document.getElementById('nav-' + name);
  if (nb) nb.classList.add('active');
}

// ---- AUTH ----
var isSetup = false;

async function bootAuth() {
  var res = await fetch('/auth/check');
  var data = await res.json();
  if (data.authenticated) { bootShell(); return; }
  if (!data.configured) {
    isSetup = true;
    document.getElementById('auth-subtitle').textContent = 'Create your studio password';
    document.getElementById('auth-label').textContent = 'New Password';
    document.getElementById('auth-btn').textContent = 'Create Studio';
    document.getElementById('auth-confirm-row').classList.remove('hidden');
  }
  document.getElementById('shell').classList.add('hidden');
  document.getElementById('page-auth').classList.remove('hidden');
}

async function authSubmit() {
  var pw = document.getElementById('auth-password').value;
  var errEl = document.getElementById('auth-error');
  errEl.classList.add('hidden');
  if (isSetup) {
    var confirm = document.getElementById('auth-confirm').value;
    if (pw !== confirm) { errEl.textContent = 'Passwords do not match'; errEl.classList.remove('hidden'); return; }
    var r = await fetch('/auth/setup', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
    var d = await r.json();
    if (!r.ok) { errEl.textContent = d.error; errEl.classList.remove('hidden'); return; }
  } else {
    var r = await fetch('/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
    var d = await r.json();
    if (!r.ok) { errEl.textContent = d.error || 'Invalid password'; errEl.classList.remove('hidden'); return; }
  }
  document.getElementById('page-auth').classList.add('hidden');
  bootShell();
}

async function logout() {
  await fetch('/auth/logout', {method:'POST'});
  location.reload();
}

// ---- SHELL BOOT ----
async function bootShell() {
  document.getElementById('shell').classList.remove('hidden');
  connectWS();
  await loadGames();
  showPage('games');
}

// ---- WEBSOCKET ----
function connectWS() {
  ws = new WebSocket(WS_URL);
  ws.onmessage = function(e) {
    var data = JSON.parse(e.data);
    if (data.type === 'log') appendLog(data.text);
    else if (data.type === 'agent_chat') appendChat('agent', data.text);
    else if (data.type === 'human_chat') appendChat('human', data.text);
    else if (data.type === 'screenshot') appendShot(data.url, data.name);
    else if (data.type === 'state') { state[data.key] = data.value; updateStats(); }
    else if (data.type === 'game_created' || data.type === 'game_switched') loadGames();
  };
  ws.onclose = function() { setTimeout(connectWS, 3000); };
}

// ---- GAME MANAGER ----
var STYLE_ICONS = {
  'side-scroller':'&#127748;','top-down':'&#128506;','metroidvania':'&#128302;',
  'isometric':'&#9876;','point-and-click':'&#128172;'
};
var PHASE_CLASS = {
  'CREATIVE':'badge-creative','BUILD':'badge-build','REPAIR':'badge-repair','PLAYTEST':'badge-playtest'
};

async function loadGames() {
  var r = await fetch('/api/games');
  var data = await r.json();
  var games = data.games || [];
  var grid = document.getElementById('games-grid');
  grid.innerHTML = '';
  games.forEach(function(g) {
    var card = document.createElement('div');
    card.className = 'game-card';
    card.innerHTML = '<div class="game-icon">' + (STYLE_ICONS[g.style] || '&#127918;') + '</div>' +
      '<h3>' + g.name + '</h3>' +
      '<div class="game-meta">' + g.style + ' &middot; ' + g.scale + ' scale &middot; ' + g.multiplayer + '</div>' +
      '<div><span class="badge ' + (PHASE_CLASS[g.phase] || 'badge-build') + '">' + g.phase + '</span></div>' +
      '<div class="game-stats"><span>&#10003; ' + g.tasks_complete + ' tasks</span></div>';
    card.onclick = function() { openStudio(g); };
    grid.appendChild(card);
  });
  var nc = document.createElement('div');
  nc.className = 'game-card new-game-card';
  nc.innerHTML = '<div class="plus">&#65291;</div><div>New Game</div>';
  nc.onclick = startWizard;
  grid.appendChild(nc);
}

async function openStudio(game) {
  activeGame = game;
  await fetch('/api/games/switch', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({slug: game.slug})
  });
  document.getElementById('studio-title').textContent = game.name;
  document.getElementById('active-game-badge').textContent = game.name;
  document.getElementById('active-game-badge').classList.remove('hidden');
  document.getElementById('nav-studio').classList.remove('hidden');
  await Promise.all([loadChatHistory(), loadScreenshots(), loadManifesto(), loadJournal(), loadReminders(), loadState()]);
  showPage('studio');
}

// ---- WIZARD ----
var STEPS = ['Basics','Style','Scale','Seeds','Mechanics'];
var STYLES = [
  {id:'side-scroller',icon:'&#127748;',name:'Side-scroller',desc:'Cinematic, parallax, platforming'},
  {id:'top-down',icon:'&#128506;',name:'Top-down RPG',desc:'Exploration, overworld, NPC-heavy'},
  {id:'metroidvania',icon:'&#128302;',name:'Metroidvania',desc:'Non-linear, ability gating'},
  {id:'isometric',icon:'&#9876;',name:'Isometric',desc:'2.5D illusion, tactical'},
  {id:'point-and-click',icon:'&#128172;',name:'Point &amp; Click',desc:'Dialogue, inventory, puzzle'},
];
var SCALES = [
  {id:'vignette',name:'Vignette',detail:'1&#8211;2 zones &middot; ~20 tasks &middot; no CREATIVE phase'},
  {id:'indie',name:'Indie',detail:'3&#8211;6 zones &middot; ~60 tasks &middot; brief CREATIVE phase'},
  {id:'classic',name:'Classic',detail:'10&#8211;20 zones &middot; ~150 tasks &middot; full world-building'},
  {id:'epic',name:'Epic',detail:'30&#8211;60 zones &middot; ~350 tasks &middot; deep lore &amp; factions'},
  {id:'vast',name:'Vast',detail:'Infinite &middot; procedural world &middot; very deep CREATIVE phase'},
];
var TONES = ['Dark','Mysterious','Lighthearted','Epic','Surreal','Horror','Comedic','Romantic'];
var COMBAT = ['None','Turn-based','Real-time action','Stealth','Puzzle-based'];
var PROGRESSION = ['Story-only','Leveling','Skill tree','Item-based'];
var MULTIPLAYER = ['Single-player','Co-op (2&#8211;8)','MMO-lite (instanced zones)'];

var wizStep = 0;
var wizData = {style:'side-scroller',scale:'indie',tones:[],combat:'None',progression:'Story-only',multiplayer:'Single-player'};

function startWizard() {
  wizStep = 0;
  wizData = {style:'side-scroller',scale:'indie',tones:[],combat:'None',progression:'Story-only',multiplayer:'Single-player'};
  renderWizard();
  showPage('wizard');
}

function renderWizard() {
  // Steps
  var stepsEl = document.getElementById('wizard-steps');
  stepsEl.innerHTML = STEPS.map(function(s, i) {
    return '<div class="wizard-step ' + (i===wizStep?'active':i<wizStep?'done':'') + '">' +
      (i>0?'<div class="wizard-connector"></div>':'') +
      '<div class="wizard-step-num">' + (i<wizStep?'&#10003;':i+1) + '</div>' +
      '<div class="wizard-step-label">' + s + '</div></div>';
  }).join('');

  var body = document.getElementById('wizard-body');
  if (wizStep === 0) {
    body.innerHTML = '<div class="section"><h3>Game Basics</h3>' +
      '<div class="form-row"><label>Game Name</label>' +
      '<input type="text" id="wiz-name" value="' + (wizData.name||'') + '" placeholder="e.g. Nexus City" oninput="wizData.name=this.value"></div>' +
      '<div class="form-row"><label>One-line premise</label>' +
      '<input type="text" id="wiz-premise" value="' + (wizData.premise||'') + '" placeholder="e.g. A detective in a city that swallowed its underground" oninput="wizData.premise=this.value"></div></div>';
  } else if (wizStep === 1) {
    body.innerHTML = '<div class="section"><h3>Game Style</h3><div class="style-grid">' +
      STYLES.map(function(s) {
        return '<div class="style-option ' + (wizData.style===s.id?'selected':'') + '" onclick="selectStyle(\'' + s.id + '\')">' +
          '<div class="icon">' + s.icon + '</div><div class="name">' + s.name + '</div><div class="desc">' + s.desc + '</div></div>';
      }).join('') + '</div></div>';
  } else if (wizStep === 2) {
    body.innerHTML = '<div class="section"><h3>World Scale</h3><div class="scale-list">' +
      SCALES.map(function(s) {
        return '<div class="scale-option ' + (wizData.scale===s.id?'selected':'') + '" onclick="selectScale(\'' + s.id + '\')">' +
          '<div class="scale-name">' + s.name + '</div><div class="scale-detail">' + s.detail + '</div></div>';
      }).join('') + '</div></div>';
  } else if (wizStep === 3) {
    var tonesHtml = TONES.map(function(t) {
      return '<div class="tone-pill ' + (wizData.tones.includes(t)?'selected':'') + '" onclick="toggleTone(\'' + t + '\')">' + t + '</div>';
    }).join('');
    body.innerHTML = '<div class="section"><h3>Creative Seeds</h3>' +
      '<div class="form-row"><label>Visual Seed</label>' +
      '<textarea rows="2" style="resize:vertical" placeholder="Describe the look and feel of your world&#8230;" oninput="wizData.visualSeed=this.value">' + (wizData.visualSeed||'') + '</textarea></div>' +
      '<div class="form-row"><label>Story Seed</label>' +
      '<textarea rows="2" style="resize:vertical" placeholder="The core narrative premise or mystery&#8230;" oninput="wizData.storySeed=this.value">' + (wizData.storySeed||'') + '</textarea></div>' +
      '<div class="form-row"><label>Tone</label><div class="tone-pills">' + tonesHtml + '</div></div></div>';
  } else if (wizStep === 4) {
    var combatHtml = COMBAT.map(function(c) {
      return '<div class="multi-pill ' + (wizData.combat===c?'selected':'') + '" onclick="wizData.combat=\'' + c + '\';renderWizard()">' + c + '</div>';
    }).join('');
    var progHtml = PROGRESSION.map(function(p) {
      return '<div class="multi-pill ' + (wizData.progression===p?'selected':'') + '" onclick="wizData.progression=\'' + p + '\';renderWizard()">' + p + '</div>';
    }).join('');
    var mpHtml = MULTIPLAYER.map(function(m) {
      return '<div class="multi-pill ' + (wizData.multiplayer===m?'selected':'') + '" onclick="wizData.multiplayer=\'' + m + '\';renderWizard()">' + m + '</div>';
    }).join('');
    body.innerHTML = '<div class="section"><h3>Mechanics</h3>' +
      '<div class="form-row"><label>Combat</label><div class="multi-pills">' + combatHtml + '</div></div>' +
      '<div class="form-row"><label>Progression</label><div class="multi-pills">' + progHtml + '</div></div>' +
      '<div class="form-row"><label>Multiplayer</label><div class="multi-pills">' + mpHtml + '</div></div>' +
      '<div style="margin-top:16px;padding:12px;background:var(--bg);border-radius:8px;font-size:12px;color:var(--muted)">' +
      '&#129302; <strong style="color:var(--accent2)">NPC AI (Local LLM)</strong> &mdash; Always on. NPCs use an in-browser quantized language model (Transformers.js). Ships with the game, works offline.</div></div>';
  }

  document.getElementById('wiz-back').style.visibility = wizStep === 0 ? 'hidden' : 'visible';
  document.getElementById('wiz-next').textContent = wizStep === STEPS.length - 1 ? 'Create Game' : 'Next';
}

function selectStyle(id) { wizData.style = id; renderWizard(); }
function selectScale(id) { wizData.scale = id; renderWizard(); }
function toggleTone(t) {
  var idx = wizData.tones.indexOf(t);
  if (idx >= 0) wizData.tones.splice(idx, 1); else wizData.tones.push(t);
  renderWizard();
}

async function wizNav(dir) {
  if (dir === 1 && wizStep === 0 && !wizData.name) { alert('Please enter a game name.'); return; }
  if (dir === 1 && wizStep === STEPS.length - 1) { await submitWizard(); return; }
  wizStep = Math.max(0, Math.min(STEPS.length - 1, wizStep + dir));
  renderWizard();
}

async function submitWizard() {
  var r = await fetch('/api/games', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(wizData)
  });
  var d = await r.json();
  if (r.ok) { await loadGames(); showPage('games'); }
  else { alert('Error: ' + (d.error || 'Unknown error')); }
}

// ---- STUDIO ----
function appendLog(text) {
  var out = document.getElementById('log-output');
  var line = document.createElement('div');
  var lower = text.toLowerCase();
  var cls = 'log-info';
  if (lower.indexOf('error') >= 0 || lower.indexOf('fail') >= 0) cls = 'log-err';
  else if (lower.indexOf('warn') >= 0) cls = 'log-warn';
  else if (lower.indexOf('clean') >= 0 || lower.indexOf('complet') >= 0 || lower.indexOf('success') >= 0) cls = 'log-good';
  line.className = cls;
  line.textContent = text;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
  while (out.children.length > 500) out.removeChild(out.firstChild);
}
function clearLogs() { document.getElementById('log-output').innerHTML = ''; }

function appendChat(sender, text) {
  var msgs = document.getElementById('chat-messages');
  var d = document.createElement('div');
  d.className = 'chat-msg ' + sender;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
}

async function sendChat() {
  var input = document.getElementById('chat-input');
  var text = input.value.trim();
  if (!text) return;
  input.value = '';
  appendChat('human', text);
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:'chat', text:text}));
}

function appendShot(url, name) {
  var grid = document.getElementById('shots-grid');
  var item = document.createElement('div');
  item.className = 'shot-item';
  item.innerHTML = '<img src="' + url + '" alt="' + (name||'') + '" loading="lazy">' +
    (name ? '<div class="shot-label">' + name + '</div>' : '');
  item.onclick = function() { openLightbox(url); };
  grid.insertBefore(item, grid.firstChild);
  while (grid.children.length > 20) grid.removeChild(grid.lastChild);
}
function openLightbox(url) {
  document.getElementById('lightbox-img').src = url;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() { document.getElementById('lightbox').classList.remove('open'); }

async function clearScreenshots() {
  if (!confirm('Clear all screenshots?')) return;
  await fetch('/api/screenshots', {method:'DELETE'});
  document.getElementById('shots-grid').innerHTML = '';
}

function updateStats() {
  document.getElementById('stat-mode').textContent = state.mode || '—';
  document.getElementById('stat-iter').textContent = state.iteration_count || '—';
  document.getElementById('stat-tasks').textContent = state.tasks_complete || '—';
  document.getElementById('stat-build').textContent = state.last_build_result || '—';
  if (state.mode) {
    var badge = document.getElementById('studio-phase-badge');
    badge.textContent = state.mode;
    badge.className = 'badge ' + (PHASE_CLASS[state.mode] || 'badge-build');
  }
}

async function loadState() {
  var r = await fetch('/api/state');
  var data = await r.json();
  state = data.state || {};
  updateStats();
}

async function loadChatHistory() {
  var r = await fetch('/api/chat/history');
  var data = await r.json();
  var msgs = document.getElementById('chat-messages');
  msgs.innerHTML = '';
  (data.history || []).forEach(function(m) { appendChat(m.sender, m.message); });
}

async function loadScreenshots() {
  var slug = activeGame ? activeGame.slug : '';
  var r = await fetch('/api/screenshots?game=' + encodeURIComponent(slug));
  var data = await r.json();
  document.getElementById('shots-grid').innerHTML = '';
  (data.screenshots || []).forEach(function(s) { appendShot(s.url, s.name); });
}

async function loadManifesto() {
  var r = await fetch('/api/manifesto');
  var data = await r.json();
  document.getElementById('manifesto-editor').value = data.content || '';
}
async function saveManifesto() {
  var content = document.getElementById('manifesto-editor').value;
  await fetch('/api/manifesto', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:content})});
}

async function loadJournal() {
  var r = await fetch('/api/journal');
  var data = await r.json();
  document.getElementById('journal-editor').value = data.content || '';
}
async function saveJournal() {
  var content = document.getElementById('journal-editor').value;
  await fetch('/api/journal', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:content})});
}

async function loadReminders() {
  var r = await fetch('/api/reminders');
  var data = await r.json();
  var list = document.getElementById('reminders-list');
  list.innerHTML = '';
  (data.reminders || []).forEach(function(note) {
    var d = document.createElement('div');
    d.style.cssText = 'padding:8px 12px;background:var(--bg);border-radius:6px;font-size:12px;border:1px solid var(--border)';
    d.textContent = note;
    list.appendChild(d);
  });
}
async function addReminder() {
  var input = document.getElementById('reminder-input');
  var note = input.value.trim();
  if (!note) return;
  input.value = '';
  await fetch('/api/reminders', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({note:note})});
  await loadReminders();
}

function switchTab(name, btn) {
  document.querySelectorAll('.tab-pane').forEach(function(p) { p.classList.remove('active'); });
  document.querySelectorAll('.studio-tab').forEach(function(b) { b.classList.remove('active'); });
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}

// BOOT
bootAuth();
</script>
</body>
</html>"""

with open('/Users/max/Repos/gemma_game_dev/dashboard/index.html', 'w') as f:
    f.write(html)
print('Written:', len(html.splitlines()), 'lines')
