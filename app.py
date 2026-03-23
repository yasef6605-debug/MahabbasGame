import eventlet
eventlet.monkey_patch()

import logging
import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template_string, request, session
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
import json
# تم إزالة webview للسيرفر الخارجي
from threading import Thread

# إعداد السجلات
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MohibisGame")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'mohibis_secret_key_2024'
# تحسين أداء السوكيت
socketio = SocketIO(app, cors_allowed_origins="*", manage_session=True, async_mode='eventlet')

DB_PATH = 'mohibis.db'

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # جدول اللاعبين
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        display_name TEXT,
        profile_image TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMP
    )
    ''')
    # جدول الإحصائيات
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS statistics (
        player_id INTEGER PRIMARY KEY,
        total_games INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        best_time FLOAT,
        total_attempts INTEGER DEFAULT 0,
        total_score INTEGER DEFAULT 100,
        wins_by_mode TEXT, -- JSON string
        best_scores_by_difficulty TEXT, -- JSON string
        last_bonus_date TEXT, -- YYYY-MM-DD
        bonus_streak INTEGER DEFAULT 0,
        FOREIGN KEY (player_id) REFERENCES players (id)
    )
    ''')
    # فحص إذا كان العمود موجوداً بالفعل (للترقية)
    try:
        cursor.execute('ALTER TABLE statistics ADD COLUMN last_bonus_date TEXT')
    except:
        pass
    try:
        cursor.execute('ALTER TABLE statistics ADD COLUMN bonus_streak INTEGER DEFAULT 0')
    except:
        pass
    conn.execute('UPDATE statistics SET total_score = 100 WHERE total_score < 100 OR total_score IS NULL')
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

# --- قوالب HTML ---
html_template = """
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>لعبة المحيبس الاحترافية</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <style>
        :root {
            --gold: #d4af37;
            --bright-gold: #f9d71c;
            --dark-blue: #0f192d;
            --v-glow: rgba(212, 175, 55, 0.5);
        }
        
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        
        body {
            margin: 0;
            padding: 0;
            background: var(--dark-blue);
            color: white;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            overflow: hidden;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }

        /* شاشات الدخول والاشتراك */
        .auth-screen {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: linear-gradient(135deg, #0f192d 0%, #1e2a4a 100%);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            z-index: 2000;
        }

        .auth-box {
            background: rgba(0, 0, 0, 0.4);
            padding: 30px;
            border-radius: 20px;
            border: 2px solid var(--gold);
            width: 90%;
            max-width: 400px;
            text-align: center;
            box-shadow: 0 0 30px var(--v-glow);
        }

        .auth-box h1 { color: var(--bright-gold); margin-bottom: 25px; }
        .auth-box input {
            width: 100%;
            padding: 12px;
            margin-bottom: 15px;
            border-radius: 10px;
            border: 1px solid var(--gold);
            background: rgba(255, 255, 255, 0.1);
            color: white;
            font-size: 16px;
        }

        /* واجهة المستخدم الرئيسية */
        /* القائمة الجانبية للمتصلين */
        #players-sidebar {
            position: fixed;
            top: 0;
            right: -300px;
            width: 280px;
            height: 100%;
            background: rgba(15, 25, 45, 0.95);
            border-left: 2px solid var(--gold);
            z-index: 1500;
            transition: 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            flex-direction: column;
            box-shadow: -10px 0 30px rgba(0,0,0,0.5);
        }

        #players-sidebar.active { right: 0; }

        .sidebar-header {
            padding: 20px;
            background: rgba(0,0,0,0.3);
            border-bottom: 1px solid var(--gold);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        #players-list {
            flex: 1;
            overflow-y: auto;
            padding: 15px;
        }

        .online-player-card {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(212, 175, 55, 0.2);
            border-radius: 12px;
            padding: 10px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: 0.3s;
        }

        .online-player-card:hover { border-color: var(--gold); background: rgba(212, 175, 55, 0.1); }

        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-left: 5px;
        }
        .status-online { background: #44cc44; box-shadow: 0 0 5px #44cc44; }
        .status-busy { background: #ff4444; }

        #sidebar-overlay {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.5);
            display: none;
            z-index: 1400;
        }

        #header {
            padding: 10px 20px;
            background: rgba(0, 0, 0, 0.5);
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--gold);
        }

        .user-info { display: flex; align-items: center; gap: 10px; cursor: pointer; }
        .user-info img { width: 40px; height: 40px; border-radius: 50%; border: 2px solid var(--gold); }

        #mode-screen {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 20px;
            padding: 20px;
        }

        .btn-nav {
            background: linear-gradient(to bottom, var(--gold), #b8860b);
            color: black;
            border: none;
            padding: 15px 30px;
            border-radius: 15px;
            font-size: 20px;
            font-weight: bold;
            cursor: pointer;
            width: 100%;
            max-width: 300px;
            transition: 0.3s;
            box-shadow: 0 5px 15px rgba(0,0,0,0.3);
        }

        .btn-nav:active { transform: scale(0.95); }

        /* منطقة اللعب */
        #game-container, #online-game {
            display: none;
            flex: 1;
            padding: 10px;
            text-align: center;
            overflow-y: auto;
            max-height: 100vh;
            flex-direction: column;
            align-items: center;
            position: relative;
            z-index: 100;
        }

        .hands-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 10px;
            max-width: 500px;
            margin: 15px auto;
            width: 100%;
        }

        @media (max-width: 500px) {
            .hands-grid {
                grid-template-columns: repeat(4, 1fr) !important;
                gap: 5px !important;
                padding: 5px !important;
                max-width: 100% !important;
            }
            .hand-box { 
                border-radius: 8px !important;
                border-width: 1px !important;
            }
            .hand-number {
                font-size: 12px !important;
                top: 1px !important;
                right: 3px !important;
                opacity: 1 !important;
                background: rgba(0,0,0,0.5);
                border-radius: 3px;
                padding: 0 2px;
            }
            .btn-nav { font-size: 14px; padding: 10px 15px; }
        }

        .hand-box {
            aspect-ratio: 1;
            background: rgba(255, 255, 255, 0.05);
            border: 2px solid rgba(212, 175, 55, 0.3);
            border-radius: 15px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: 0.3s;
            position: relative;
            overflow: hidden;
        }

        .hand-number {
            position: absolute;
            top: 5px;
            right: 8px;
            font-size: 14px;
            color: var(--bright-gold);
            font-weight: bold;
            z-index: 10;
            text-shadow: 1px 1px 2px black;
        }

        .hand-box img { 
            width: 85%; 
            height: 85%; 
            object-fit: contain;
            pointer-events: none;
        }
        .hand-box.victory { background: rgba(0, 255, 0, 0.2); border-color: #00ff00; }
        .hand-box.fail { background: rgba(255, 0, 0, 0.2); border-color: #ff0000; }

        /* الأونلاين */
        .online-header {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            padding: 10px;
            background: rgba(0,0,0,0.4);
            border-bottom: 1px solid var(--v-glow);
            gap: 10px;
        }

        .player-card {
            background: rgba(0, 0, 0, 0.3);
            padding: 10px;
            border-radius: 12px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid var(--v-glow);
            font-size: 14px;
        }

        .player-card img { width: 35px; height: 35px; border-radius: 50%; }

        .mini-card {
            width: fit-content;
            margin: 5px auto;
            padding: 4px 12px !important;
            font-size: 13px !important;
            border-radius: 8px !important;
            gap: 6px;
            background: rgba(0, 0, 0, 0.5) !important;
            border: 1px solid rgba(212, 175, 55, 0.4) !important;
            display: flex;
            align-items: center;
            box-shadow: 0 0 10px rgba(0,0,0,0.5);
        }
        .mini-card img {
            width: 25px !important;
            height: 25px !important;
        }

        .quick-chat-container {
            position: relative;
            display: inline-block;
        }

        .quick-chat-dropdown {
            display: none;
            position: absolute;
            bottom: 100%;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(15, 25, 45, 0.95);
            border: 2px solid var(--gold);
            border-radius: 15px;
            padding: 10px;
            width: 200px;
            max-height: 250px;
            overflow-y: auto;
            z-index: 2000;
            box-shadow: 0 0 20px rgba(0,0,0,0.5);
        }

        .quick-chat-dropdown.active { display: block; }

        .quick-msg-btn {
            display: block;
            width: 100%;
            padding: 8px;
            margin-bottom: 5px;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(212, 175, 55, 0.3);
            color: white;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            text-align: right;
        }

        .quick-msg-btn:hover { background: rgba(212, 175, 55, 0.2); }

        .emoji-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 5px;
            margin-bottom: 10px;
            border-bottom: 1px solid rgba(212, 175, 55, 0.3);
            padding-bottom: 10px;
        }

        .emoji-btn {
            font-size: 20px;
            background: none;
            border: none;
            cursor: pointer;
            padding: 5px;
            border-radius: 5px;
        }

        .emoji-btn:hover { background: rgba(255,255,255,0.1); }

        /* نافذة الملف الشخصي المنسدلة تحت صورة اللاعب مباشرة */
        #profile-modal {
            display: none;
            position: absolute;
            top: 60px; /* تحت الهيدر مباشرة */
            left: 10px; /* تغيير من اليمين إلى اليسار ليناسب موقع الملف الشخصي */
            right: auto;
            width: 80px; /* العرض الجديد كما طلبت */
            z-index: 9999;
            pointer-events: none;
        }

        #profile-modal.active {
            display: block;
            pointer-events: auto;
        }

        .profile-content {
            background: linear-gradient(to bottom, #1e2a4a, #0f192d);
            width: 80px; /* العرض الجديد كما طلبت */
            padding: 5px;
            border-radius: 10px;
            border: 1px solid var(--gold);
            box-shadow: 0 5px 25px rgba(0,0,0,0.8);
            transform: translateY(-20px);
            opacity: 0;
            transition: all 0.3s ease-out;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 5px;
        }

        #profile-modal.active .profile-content {
            transform: translateY(0);
            opacity: 1;
        }

        #chat-box {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 300px;
            height: 400px;
            background: rgba(0,0,0,0.8);
            border: 2px solid var(--gold);
            border-radius: 15px;
            display: none;
            flex-direction: column;
            z-index: 1000;
        }

        /* الرسوم المتحركة */
        #canvas-fx {
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            pointer-events: none;
            z-index: 5000;
        }
    </style>
</head>
<body>
    <canvas id="canvas-fx"></canvas>

    <!-- شاشة تسجيل الدخول -->
    <div id="login-screen" class="auth-screen">
        <div class="auth-box">
            <h1>تسجيل الدخول</h1>
            <input type="text" id="login-username" placeholder="اسم المستخدم">
            <input type="password" id="login-password" placeholder="كلمة المرور">
            <button class="btn-nav" onclick="login()">دخول</button>
            <p onclick="showRegister()" style="margin-top:15px; cursor:pointer; color:var(--gold);">ليس لديك حساب؟ سجل الآن</p>
        </div>
    </div>

    <!-- شاشة الاشتراك -->
    <div id="register-screen" class="auth-screen" style="display:none;">
        <div class="auth-box">
            <h1>حساب جديد</h1>
            <input type="text" id="reg-username" placeholder="اسم المستخدم (بالانجليزي)">
            <input type="text" id="reg-displayname" placeholder="اسم العرض (اللقب)">
            <input type="password" id="reg-password" placeholder="كلمة المرور">
            <button class="btn-nav" onclick="register()">إنشاء حساب</button>
            <p onclick="showLogin()" style="margin-top:15px; cursor:pointer; color:var(--gold);">لديك حساب بالفعل؟ سجل دخول</p>
        </div>
    </div>

    <div id="sidebar-overlay" onclick="toggleSidebar(false)"></div>
    <div id="players-sidebar">
        <div class="sidebar-header">
            <h3 style="color:var(--gold); margin:0;">اللاعبين المتصلين</h3>
            <button onclick="toggleSidebar(false)" style="background:none; border:none; color:white; font-size:24px; cursor:pointer;">&times;</button>
        </div>
        <div id="players-list">
            <!-- قائمة اللاعبين سيتم تعبئتها ديناميكياً -->
        </div>
    </div>

    <div id="main-ui" style="display:none; height:100%; display:flex; flex-direction:column; position:relative;">
        <div id="header">
            <div style="display:flex; gap:10px;">
                <button onclick="toggleSidebar(true)" style="background:none; border:none; color:var(--gold); cursor:pointer; font-size:20px;">👥</button>
                <button onclick="logout()" style="background:none; border:none; color:#ff4444; cursor:pointer; font-size:14px;">خروج</button>
            </div>
            <div style="color:var(--bright-gold); font-weight:bold; font-size:14px;">لعبة المحيبس 💍</div>
            <div class="user-info" onclick="showProfile()">
                <div style="display:flex; flex-direction:column; align-items:flex-end;">
                    <span id="current-displayname" style="font-size:12px;">...</span>
                    <span id="current-balance" style="color:var(--gold); font-size:10px;">0 🏆</span>
                </div>
                <div id="header-profile-img-container" style="position:relative;">
                    <img id="header-profile-img" src="https://www.gravatar.com/avatar/000?d=mp" style="width:30px; height:30px;">
                    <div id="header-player-level" style="position:absolute; bottom:-3px; right:-3px; background:var(--gold); color:black; border-radius:50%; width:14px; height:14px; font-size:8px; display:flex; align-items:center; justify-content:center; font-weight:bold;">1</div>
                </div>
            </div>
        </div>

        <!-- نافذة الملف الشخصي والإحصائيات المدمجة المنسدلة -->
        <div id="profile-modal" onclick="if(event.target === this) closeProfile()">
            <div class="profile-content">
                <!-- الرصيد -->
                <div style="background:rgba(255,215,0,0.1); padding:4px; border-radius:8px; border:1px solid var(--gold); text-align:center; width:100%;">
                    <p style="margin:0; color:#aaa; font-size:7px;">رصيد</p>
                    <p style="font-size:10px; color:var(--bright-gold); font-weight:bold; margin:1px 0; overflow:hidden; text-overflow:ellipsis;"><span id="stat-balance-large">0</span></p>
                    <div id="stat-title-display" style="font-size:6px; white-space:nowrap; overflow:hidden;"></div>
                </div>

                <!-- الإحصائيات -->
                <div style="display:flex; flex-direction:column; gap:4px; width:100%;">
                    <div style="background:rgba(0,0,0,0.3); padding:4px; border-radius:5px; border:1px solid rgba(255,255,255,0.05); text-align:center;">
                        <p style="margin:0; color:#aaa; font-size:6px;">مستوى</p>
                        <span id="stat-level" style="color:var(--bright-gold); font-size:10px; font-weight:bold;">1</span>
                    </div>
                    <div style="background:rgba(0,0,0,0.3); padding:4px; border-radius:5px; border:1px solid rgba(255,255,255,0.05); text-align:center;">
                        <p style="margin:0; color:#aaa; font-size:6px;">فوز</p>
                        <span id="stat-rate" style="color:#44cc44; font-size:10px; font-weight:bold;">0%</span>
                    </div>
                    <div style="background:rgba(0,0,0,0.3); padding:4px; border-radius:5px; border:1px solid rgba(255,255,255,0.05); text-align:center;">
                        <p style="margin:0; color:#aaa; font-size:6px;">لعب</p>
                        <span id="stat-total" style="color:white; font-size:10px; font-weight:bold;">0</span>
                    </div>
                </div>

                <!-- التعديل -->
                <div id="profile-edit-section" style="width:100%; border-top:1px solid rgba(255,255,255,0.1); padding-top:6px;">
                    <div style="margin-bottom:6px; display:flex; flex-direction:column; gap:3px;">
                        <input type="text" id="edit-display-name" placeholder="لقب" style="width:100%; padding:3px; border-radius:3px; border:1px solid #444; background:rgba(0,0,0,0.2); color:white; font-size:8px; text-align:center;">
                        <button class="btn-nav" onclick="updateProfileName()" style="width:100%; padding:3px; font-size:8px; margin:0;">حفظ</button>
                    </div>
                    <input type="file" id="profile-upload" onchange="uploadProfileImage(event)" accept="image/*" style="display:none;">
                    <button class="btn-nav" onclick="document.getElementById('profile-upload').click()" style="background:rgba(255,255,255,0.05); color:#aaa; font-size:7px; padding:3px; margin:0; width:100%;">📷</button>
                </div>
                
                <button class="btn-nav" onclick="closeProfile()" style="margin-top:5px; background:rgba(255,255,255,0.1); color:white; font-size:9px; padding:4px; width:100%;">🔼</button>
            </div>
        </div>

        <div id="mode-screen">
            <div id="daily-bonus-container" style="width:100%; max-width:300px; margin-bottom:10px;">
                <button id="daily-bonus-btn" class="btn-nav" onclick="claimDailyBonus()" style="background:linear-gradient(to bottom, #ff9800, #f57c00); font-size:16px; padding:10px; width:100%;">🎁 احصل على مكافأتك اليومية (100 🏆)</button>
            </div>
            <button class="btn-nav" onclick="startGame('guessing')">وضع التخمين</button>
            <button class="btn-nav" onclick="startGame('challenge')">وضع التحدي</button>
            <button class="btn-nav" onclick="toggleSidebar(true)" style="background:linear-gradient(to bottom, #1e2a4a, #0f192d); color:var(--gold); border:1px solid var(--gold);">لعب أونلاين</button>
            <button class="btn-nav" onclick="showLeaderboard()" style="background:linear-gradient(to bottom, #d4af37, #b8860b); font-size:18px;">🏆 قائمة المتصدرين</button>
        </div>

        <!-- وضع اللعب الفردي -->
        <div id="game-container">
            <h2 id="status">يا الله توكلنا.. دور المحبس!</h2>
            <div id="timer-display" style="font-size:24px; color:var(--gold); margin-bottom:10px; display:none;">الوقت: 03:00</div>
            <div class="hands-grid">
                {% for i in range(1, 9) %}
                <div class="hand-box" id="box{{i}}" onclick="checkHand(event, {{i}}, '{{ 'left' if i % 2 != 0 else 'right' }}')">
                    <span class="hand-number">{{i}}</span>
                    <img id="img{{i}}" src="/static/{{ 'left' if i % 2 != 0 else 'right' }}_closed.png">
                </div>
                {% endfor %}
            </div>
            <div id="game-controls" style="display:flex; gap:10px; justify-content:center; flex-wrap:wrap;">
                <button id="hint-btn" class="btn-nav" onclick="useHint()" style="background:linear-gradient(to bottom, #9c27b0, #7b1fa2); color:white; font-size:14px; padding:10px 20px; display:none;">💡 تلميح (30 🏆)</button>
                <button id="play-again-btn" class="btn-nav" onclick="resetCurrentGame()" style="display:none; background:linear-gradient(to bottom, #44cc44, #228b22); color:white;">لعب مرة أخرى</button>
                <button class="btn-nav" onclick="backToMenu()">رجوع للقائمة</button>
            </div>
        </div>

        <div id="online-game">
            <div class="online-header">
                <div id="player1-card" class="player-card mini-card" style="margin:0;">
                    <img id="player1-img" src="https://www.gravatar.com/avatar/000?d=mp">
                    <span id="player1-name">المضيف</span>
                </div>
                
                <div style="font-size:28px; color:var(--bright-gold); font-weight:bold; margin:0 15px; display:flex; align-items:center; gap:10px; background:rgba(0,0,0,0.3); padding:5px 15px; border-radius:10px; border:1px solid var(--gold);">
                    <span id="player1-stats">0</span>
                    <span>-</span>
                    <span id="player2-stats">0</span>
                </div>

                <div id="player2-card" class="player-card mini-card" style="margin:0;">
                    <span id="player2-name">الضيف</span>
                    <img id="player2-img" src="https://www.gravatar.com/avatar/000?d=mp">
                </div>
            </div>
            
            <h2 id="online-message" style="color:var(--bright-gold); font-size:18px; margin:10px 0;">انتظر بدء اللعبة...</h2>
            
            <div class="hands-grid" id="online-hands-grid">
                {% for i in range(1, 9) %}
                <div class="hand-box" id="online-box{{i}}" onclick="checkOnlineHand({{i}}, '{{ 'left' if i % 2 != 0 else 'right' }}')">
                    <span class="hand-number">{{i}}</span>
                    <img id="online-img{{i}}" src="/static/{{ 'left' if i % 2 != 0 else 'right' }}_closed.png">
                </div>
                {% endfor %}
            </div>

            <div id="chat-messages" style="width:100%; max-width:400px; height:60px; overflow-y:auto; background:rgba(0,0,0,0.3); border-radius:10px; padding:8px; margin:10px auto; text-align:right; font-size:12px;"></div>
            
            <div id="online-quick-msgs" style="display:flex; gap:5px; justify-content:center; width:100%;">
                <div class="quick-chat-container">
                    <button class="btn-nav" onclick="toggleQuickChat()" style="font-size:12px; padding:8px 12px; width:auto; border-radius:10px;">💬 دردشة سريعة</button>
                    <div id="quick-chat-dropdown" class="quick-chat-dropdown">
                        <div class="emoji-grid">
                            <button class="emoji-btn" onclick="sendQuickMessage('😂')">😂</button>
                            <button class="emoji-btn" onclick="sendQuickMessage('🤣')">🤣</button>
                            <button class="emoji-btn" onclick="sendQuickMessage('😜')">😜</button>
                            <button class="emoji-btn" onclick="sendQuickMessage('🤫')">🤫</button>
                            <button class="emoji-btn" onclick="sendQuickMessage('🤔')">🤔</button>
                            <button class="emoji-btn" onclick="sendQuickMessage('👀')">👀</button>
                            <button class="emoji-btn" onclick="sendQuickMessage('🔥')">🔥</button>
                            <button class="emoji-btn" onclick="sendQuickMessage('👏')">👏</button>
                        </div>
                        <button class="quick-msg-btn" onclick="sendQuickMessage('وين المحبس؟')">وين المحبس؟ 💍</button>
                        <button class="quick-msg-btn" onclick="sendQuickMessage('عاش ايدك يا بطل')">عاش ايدك يا بطل 👏</button>
                        <button class="quick-msg-btn" onclick="sendQuickMessage('قربت حيل!')">قربت حيل! 🔥</button>
                        <button class="quick-msg-btn" onclick="sendQuickMessage('صياد والله!')">صياد والله! 🎯</button>
                        <button class="quick-msg-btn" onclick="sendQuickMessage('حاول مرة ثانية')">حاول مرة ثانية 😜</button>
                        <button class="quick-msg-btn" onclick="sendQuickMessage('المحبس ضاع!')">المحبس ضاع! 😂</button>
                        <button class="quick-msg-btn" onclick="sendQuickMessage('بالحظ هاي!')">بالحظ هاي! 🤔</button>
                        <button class="quick-msg-btn" onclick="sendQuickMessage('يا الله توكلنا')">يا الله توكلنا 🤲</button>
                    </div>
                </div>
                <button class="btn-nav" onclick="leaveOnlineGame()" style="font-size:12px; padding:8px 12px; width:auto; background:red; border-radius:10px;">انسحاب</button>
            </div>
        </div>
    </div>

    <!-- نافذة المتصدرين -->
    <div id="leaderboard-modal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.9); z-index:3500; align-items:center; justify-content:center; padding:15px;">
        <div style="background:#1e2a4a; padding:20px; border-radius:20px; border:2px solid var(--gold); max-width:400px; width:95%; max-height:85vh; display:flex; flex-direction:column; box-shadow: 0 0 30px rgba(212, 175, 55, 0.3);">
            <h2 style="color:var(--bright-gold); margin:0 0 15px 0; text-align:center; font-size:22px;">🏆 المتصدرين</h2>
            <div id="leaderboard-list" style="flex:1; overflow-y:auto; padding-right:5px;">
                <!-- سيتم تعبئة القائمة هنا -->
            </div>
            <button class="btn-nav" onclick="closeLeaderboard()" style="margin-top:15px; padding:10px; font-size:16px;">إغلاق</button>
        </div>
    </div>

    <!-- نافذة الدعوة -->
    <div id="invitation-popup" style="display:none; position:fixed; top:20px; left:50%; transform:translateX(-50%); background:var(--dark-blue); border:2px solid var(--gold); padding:20px; border-radius:15px; z-index:4000; box-shadow:0 0 20px var(--gold); min-width:250px;">
        <h3 id="invitation-from" style="margin:0 0 10px 0;">وصلتك دعوة تحدي!</h3>
        <p id="invitation-bet-display" style="color:var(--bright-gold); font-weight:bold; font-size:18px; margin-bottom:15px;"></p>
        <div style="display:flex; gap:10px; justify-content:center;">
            <button class="btn-nav" onclick="acceptInvitation()" style="padding:10px 20px; font-size:16px;">قبول ✅</button>
            <button class="btn-nav" onclick="declineInvitation()" style="padding:10px 20px; font-size:16px; background:red;">رفض ❌</button>
        </div>
    </div>

    <!-- نافذة تحديد الرهان -->
    <div id="bet-modal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); z-index:5000; justify-content:center; align-items:center;">
        <div style="background:var(--dark-blue); border:2px solid var(--gold); padding:30px; border-radius:20px; text-align:center; max-width:400px; width:90%;">
            <h2 style="color:var(--gold); margin-top:0;">تحديد مبلغ الرهان</h2>
            <p style="color:white; margin-bottom:20px;">أدخل عدد النقاط التي تود المراهنة عليها (الحد الأدنى 20)</p>
            <input type="number" id="bet-input" value="20" min="20" style="width:100%; padding:15px; border-radius:10px; border:1px solid var(--gold); background:rgba(0,0,0,0.3); color:white; font-size:20px; text-align:center; margin-bottom:20px;">
            <div style="display:flex; gap:10px;">
                <button class="btn-nav" onclick="confirmInvitationWithBet()" style="flex:1; padding:15px; font-size:18px;">إرسال التحدي 🎮</button>
                <button class="btn-nav" onclick="closeBetModal()" style="flex:1; padding:15px; font-size:18px; background:red;">إلغاء ❌</button>
            </div>
        </div>
    </div>

    <!-- الأصوات -->
    <audio id="left0" src="/static/1.mp3" preload="auto"></audio> <!-- صوت المحاولة الخاطئة -->
    <audio id="left1" src="/static/2.mp3" preload="auto"></audio> <!-- صوت الخسارة -->
    <audio id="left2" src="/static/3.mp3" preload="auto"></audio> <!-- صوت الفوز -->

    <script>
        const socket = io({
            reconnectionAttempts: 5,
            timeout: 10000
        });
        
        socket.on('connect_error', (error) => {
            console.error('Socket Connection Error:', error);
        });
        
        socket.on('connect', () => {
            console.log('Connected to server with SID:', socket.id);
        });
        
        socket.on('disconnect', (reason) => {
            console.log('Disconnected from server:', reason);
        });
        
        // --- متغيرات الحالة العامة ---
        let isGameOver = false;
        let selectedMode = 'free';
        let timeLeft = 180;
        let timerInterval;
        let ringPosition = 0;
        let attemptsLeft = 5;
        let maxAttempts = 5;
        
        // معلومات اللاعب
        window.playerId = null;
        window.username = '';
        window.displayName = '';
        window.profileImage = '';

        // نظام الإحصائيات
        let stats = {
            totalGames: 0, wins: 0, losses: 0, bestTime: null, totalAttempts: 0, totalScore: 0,
            winsByMode: { guessing: 0, challenge: 0, online: 0 },
            bestScoresByDifficulty: { easy: 0, medium: 0, hard: 0 }
        };

        let onlinePlayers = []; // لتخزين قائمة اللاعبين المتصلين عالمياً

        // إعدادات الصعوبة
        const difficultySettings = {
            easy: { ringPositions: 6, attempts: 7, timeLimit: 240, scoreMultiplier: 1.0 },
            medium: { ringPositions: 7, attempts: 5, timeLimit: 180, scoreMultiplier: 1.5 },
            hard: { ringPositions: 8, attempts: 3, timeLimit: 120, scoreMultiplier: 2.0 }
        };
        let selectedDifficulty = 'medium';

        // إعدادات المستخدم
        const userSettings = {
            soundEnabled: true,
            volume: 1.0,
            animationsEnabled: true,
            theme: 'gold'
        };

        // --- وظائف الهوية (Auth) ---
        function showRegister() {
            document.getElementById('login-screen').style.display = 'none';
            document.getElementById('register-screen').style.display = 'flex';
        }

        function showLogin() {
            document.getElementById('register-screen').style.display = 'none';
            document.getElementById('login-screen').style.display = 'flex';
        }

        function login() {
            const user = document.getElementById('login-username').value.trim();
            const pass = document.getElementById('login-password').value.trim();
            if (!user || !pass) { alert('يرجى إدخال البيانات'); return; }
            socket.emit('login', { username: user, password: pass });
        }

        function register() {
            const user = document.getElementById('reg-username').value.trim();
            const display = document.getElementById('reg-displayname').value.trim();
            const pass = document.getElementById('reg-password').value.trim();
            if (!user || !pass) { alert('يرجى إكمال البيانات'); return; }
            socket.emit('register', { username: user, display_name: display, password: pass });
        }

        function logout() {
            socket.emit('logout');
            window.playerId = null;
            window.username = null;
            location.reload();
        }

        function updateUserData(data) {
            try {
                window.playerId = data.player_id;
                window.username = data.username;
                window.displayName = data.display_name || data.username;
                window.profileImage = data.profile_image || 'https://www.gravatar.com/avatar/000?d=mp';
                
                const nameEl = document.getElementById('current-displayname');
                const imgEl = document.getElementById('header-profile-img');
                const balanceEl = document.getElementById('current-balance');
                const titleEl = document.getElementById('current-title');
                const levelEl = document.getElementById('header-player-level');
                
                if (nameEl) nameEl.innerText = window.displayName;
                if (imgEl) imgEl.src = window.profileImage;
                
                if (data.stats) {
                    const s = data.stats;
                    stats = {
                        totalGames: s.total_games || 0,
                        wins: s.wins || 0,
                        losses: s.losses || 0,
                        bestTime: s.best_time || null,
                        totalAttempts: s.total_attempts || 0,
                        totalScore: Number(s.total_score || 0),
                        winsByMode: typeof s.wins_by_mode === 'string' ? JSON.parse(s.wins_by_mode) : s.wins_by_mode || { guessing: 0, challenge: 0, online: 0 },
                        bestScoresByDifficulty: typeof s.best_scores_by_difficulty === 'string' ? JSON.parse(s.best_scores_by_difficulty) : s.best_scores_by_difficulty || { easy: 0, medium: 0, hard: 0 },
                        lastBonusDate: s.last_bonus_date,
                        bonusStreak: Number(s.bonus_streak || 0)
                    };
                    if (balanceEl) balanceEl.innerText = `رصيد: ${stats.totalScore.toLocaleString()} 🏆`;
                    
                    // تحديث اللقب والمستوى
                    const rank = getRankData(stats.totalScore);
                    if (titleEl) {
                        titleEl.innerText = rank.title;
                        titleEl.style.color = rank.color;
                        titleEl.style.borderColor = rank.color;
                    }
                    if (levelEl) levelEl.innerText = rank.level;

                    // فحص وتحديث المكافأة اليومية
                    const today = new Date().toISOString().split('T')[0];
                    const dailyBtn = document.getElementById('daily-bonus-btn');
                    if (stats.lastBonusDate === today) {
                        document.getElementById('daily-bonus-container').style.display = 'none';
                    } else {
                        document.getElementById('daily-bonus-container').style.display = 'block';
                        // حساب قيمة المكافأة المتوقعة للعرض
                        const nextStreak = (stats.bonusStreak % 7) + 1;
                        const nextAmount = 10 + (nextStreak - 1) * 5;
                        if (dailyBtn) dailyBtn.innerHTML = `🎁 مكافأة اليوم ${nextStreak} (${nextAmount} 🏆)`;
                    }
                }
                updateStatsDisplay();
            } catch (err) {
                console.error('Error updating user data:', err);
            }
        }

        function getRankData(score) {
            if (score < 500) return {level: 1, title: "مبتدئ", color: "#aaa"};
            if (score < 1500) return {level: 2, title: "هاوي", color: "#44cc44"};
            if (score < 3500) return {level: 3, title: "محترف", color: "#2196F3"};
            if (score < 7000) return {level: 4, title: "صياد", color: "#9c27b0"};
            if (score < 15000) return {level: 5, title: "خبير محيبس", color: "#ff9800"};
            return {level: 6, title: "ملك المحيبس 👑", color: "#f9d71c"};
        }

        // --- وظائف الملف الشخصي ---
        function showProfile() {
            socket.emit('get_profile');
        }

        function updateProfileName() {
            const newName = document.getElementById('edit-display-name').value.trim();
            if (!newName) return;
            socket.emit('update_profile', { display_name: newName });
        }

        function uploadProfileImage(event) {
            const file = event.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = function(e) { 
                socket.emit('update_profile', { profile_image: e.target.result }); 
            };
            reader.readAsDataURL(file);
        }

        function displayProfileModal(player, statsData) {
            updateUserData({
                player_id: player.id,
                username: player.username,
                display_name: player.display_name,
                profile_image: player.profile_image,
                stats: statsData
            });
            updateStatsDisplay();
            const modal = document.getElementById('profile-modal');
            modal.style.display = 'flex';
            setTimeout(() => modal.classList.add('active'), 10);
        }

        function closeProfile() {
            const modal = document.getElementById('profile-modal');
            modal.classList.remove('active');
            setTimeout(() => modal.style.display = 'none', 500);
        }

        // --- وظائف الإحصائيات ---
        function loadStats() {
            if (window.playerId) {
                socket.emit('get_profile');
            } else {
                const saved = localStorage.getItem('mohibisStats');
                if (saved) {
                    try { stats = JSON.parse(saved); updateStatsDisplay(); } catch (e) {}
                }
            }
        }

        function saveStats() {
            // سنلغي الحفظ المباشر من العميل للسيرفر لحماية الرصيد
            // سيتم تحديث النقاط عبر أحداث خاصة في السيرفر فقط
            if (!window.playerId) {
                localStorage.setItem('mohibisStats', JSON.stringify(stats));
            }
        }

        function updateStatsDisplay() {
            try {
                if (!document.getElementById('stat-level')) return;
                
                // التأكد من أن القيم أرقام
                const score = stats && stats.totalScore !== undefined ? parseInt(stats.totalScore) : 0;
                const wins = stats && stats.wins !== undefined ? parseInt(stats.wins) : 0;
                const games = stats && stats.totalGames !== undefined ? parseInt(stats.totalGames) : 0;
                const losses = stats && stats.losses !== undefined ? parseInt(stats.losses) : 0;
                
                const rank = getRankData(score);
                
                const levelEl = document.getElementById('stat-level');
                const totalEl = document.getElementById('stat-total');
                const winsEl = document.getElementById('stat-wins');
                const scoreEl = document.getElementById('stat-score');
                const balanceLargeEl = document.getElementById('stat-balance-large');
                const rateEl = document.getElementById('stat-rate');
                
                if (levelEl) {
                    levelEl.innerText = rank.level;
                    levelEl.style.color = rank.color;
                }
                // إضافة اللقب في نافذة الإحصائيات
                let titleStatEl = document.getElementById('stat-title-display');
                if (!titleStatEl) {
                    titleStatEl = document.createElement('p');
                    titleStatEl.id = 'stat-title-display';
                    levelEl.parentElement.appendChild(titleStatEl);
                }
                titleStatEl.innerHTML = `اللقب: <span style="color:${rank.color};">${rank.title}</span>`;

                if (totalEl) totalEl.innerText = games;
                if (totalEl) totalEl.innerText = games;
                if (winsEl) winsEl.innerText = wins;
                if (scoreEl) scoreEl.innerText = Number(score).toLocaleString();
                if (balanceLargeEl) balanceLargeEl.innerText = Number(score).toLocaleString();
                
                const rate = games > 0 ? Math.round((Number(wins) / Number(games)) * 100) : 0;
                if (rateEl) rateEl.innerText = rate + '%';
                
                // تحديث الرصيد في الواجهة الرئيسية فوراً
                const mainBalanceEl = document.getElementById('current-balance');
                if (mainBalanceEl) {
                    mainBalanceEl.innerText = `رصيد: ${score.toLocaleString()} 🏆`;
                }

                console.log('Stats Updated UI:', { score, level, wins, games, rate });
            } catch (err) {
                console.error('Error updating stats display:', err);
            }
        }

        function updateStats(win, mode, timeUsed = null, attemptsUsed = 0) {
            stats.totalGames++;
            let pointsEarned = 0;
            if (win) {
                stats.wins++;
                stats.winsByMode[mode]++;
                
                if (mode === 'guessing') {
                    pointsEarned = 50;
                } else {
                    pointsEarned = 25 - (attemptsUsed * 5);
                }

                if (pointsEarned < 0) pointsEarned = 0;
                if (pointsEarned > 50) pointsEarned = 50; 
                
                // إرسال طلب للسيرفر لإضافة النقاط بدلاً من تحديثها محلياً فقط
                if (window.playerId) {
                    socket.emit('add_points_server', {points: pointsEarned, mode: mode, win: true});
                } else {
                    stats.totalScore = Number(stats.totalScore) + pointsEarned;
                }
            } else {
                stats.losses++;
                if (mode === 'guessing') {
                    pointsEarned = -10;
                    if (window.playerId) {
                        socket.emit('add_points_server', {points: pointsEarned, mode: mode, win: false});
                    } else {
                        stats.totalScore = Math.max(0, Number(stats.totalScore) + pointsEarned);
                    }
                    showPointsNotification(pointsEarned);
                }
            }
            stats.totalAttempts += attemptsUsed;
            saveStats();
            updateStatsDisplay();
            if (win && pointsEarned > 0) showPointsNotification(pointsEarned);
        }

        function showStats() {
            showProfile();
        }

        function closeStats() {
            closeProfile();
        }

        // --- وظائف الميزات الجديدة ---
        function showLeaderboard() {
            socket.emit('get_leaderboard');
            document.getElementById('leaderboard-modal').style.display = 'flex';
        }

        function closeLeaderboard() {
            document.getElementById('leaderboard-modal').style.display = 'none';
        }

        function claimDailyBonus() {
            socket.emit('claim_daily_bonus');
        }

        function useHint() {
            if (isGameOver) return;
            socket.emit('use_hint');
        }

        // --- وظائف اللعبة ---
        function startGame(mode) {
            selectedMode = mode;
            document.getElementById('mode-screen').style.display = 'none';
            document.getElementById('game-container').style.display = 'block';
            resetCurrentGame();
        }

        function resetCurrentGame() {
            isGameOver = false;
            clearInterval(timerInterval);
            document.getElementById('play-again-btn').style.display = 'none';
            const settings = difficultySettings[selectedDifficulty];
            ringPosition = Math.floor(Math.random() * settings.ringPositions) + 1;
            
            // في نمط التحدي نستخدم محاولات الصعوبة، وفي نمط التخمين محاولة واحدة، وفي الأنماط الأخرى نستخدم 5 محاولات كأساس للنقاط
            attemptsLeft = (selectedMode === 'challenge') ? settings.attempts : (selectedMode === 'guessing' ? 1 : 5);
            maxAttempts = attemptsLeft;
            
            timeLeft = settings.timeLimit;
            
            document.getElementById('status').innerText = "يا الله توكلنا.. دور المحبس! 💍";
            for(let i=1; i<=8; i++) {
                let side = (i % 2 !== 0) ? 'left' : 'right';
                const box = document.getElementById('box'+i);
                const img = document.getElementById('img'+i);
                if (box) box.classList.remove('victory', 'fail');
                if (img) img.src = `/static/${side}_closed.png`;
            }
            updateAttemptsDisplay();
            document.getElementById('timer-display').style.display = 'none';
            
            // إظهار زر التلميح فقط في وضع التخمين
            const hintBtn = document.getElementById('hint-btn');
            if (hintBtn) {
                hintBtn.style.display = (selectedMode === 'guessing') ? 'block' : 'none';
                hintBtn.disabled = false;
                hintBtn.style.opacity = '1';
            }
        }

        function checkHand(e, id, side) {
            if (isGameOver) return;
            const box = document.getElementById('box' + id);
            const img = document.getElementById('img' + id);
            if (box && (box.classList.contains('fail') || box.classList.contains('victory'))) return;

            if (id === ringPosition) {
                isGameOver = true;
                document.getElementById('play-again-btn').style.display = 'block';
                box.classList.add('victory');
                img.src = (side === 'right') ? '/static/right_winner.png' : '/static/left_winner.png';
                document.getElementById('status').innerText = "👑 المحبس بأيدك تستاهل رصيدك!";
                createFaces(window.innerWidth/2, window.innerHeight/2, 'win');
                playAudio('left2');
                updateStats(true, selectedMode, null, (maxAttempts - attemptsLeft));
            } else {
                if (box.classList.contains('fail')) return;
                box.classList.add('fail');
                img.src = '/static/' + side + '_open.png';
                
                // تنقيص المحاولات في جميع الأنماط لحساب النقاط
                attemptsLeft--;
                updateAttemptsDisplay();

                if (selectedMode === 'challenge' || selectedMode === 'guessing') {
                    if (attemptsLeft <= 0) {
                        isGameOver = true;
                        document.getElementById('play-again-btn').style.display = 'block';
                        
                        if (selectedMode === 'guessing') {
                            document.getElementById('status').innerText = "❌ تخمين خاطئ! خسرت 10 نقاط";
                        } else {
                            document.getElementById('status').innerText = "❌ ضاع البات ماضن بعد تلكونه!";
                        }

                        let winBox = document.getElementById('box' + ringPosition);
                        let winImg = document.getElementById('img' + ringPosition);
                        let winSide = (ringPosition % 2 !== 0) ? 'left' : 'right';
                        winBox.classList.add('victory');
                        winImg.src = '/static/' + winSide + '_winner.png';
                        playAudio('left1');
                        updateStats(false, selectedMode, null, maxAttempts);
                    } else {
                        document.getElementById('status').innerText = `❌ حاول مرة ثانية! متبقي ${attemptsLeft}`;
                        playAudio('left0');
                    }
                } else {
                    // في الأنماط الأخرى، لا تنتهي اللعبة بانتهاء المحاولات إلا إذا وصل للصفر وأردنا ذلك، 
                    // لكن هنا سنكتفي بتحديث الرسالة
                    if (attemptsLeft > 0) {
                        document.getElementById('status').innerText = "❌ خطأ! حاول مرة ثانية";
                    } else {
                        document.getElementById('status').innerText = "❌ خطأ! انتهت محاولات النقاط، لكن يمكنك الاستمرار";
                    }
                    playAudio('left0');
                }
            }
        }

        function backToMenu() {
            document.getElementById('game-container').style.display = 'none';
            document.getElementById('mode-screen').style.display = 'flex';
        }

        function updateAttemptsDisplay() {
            if (selectedMode === 'challenge') {
                document.getElementById('status').innerText = `المحاولات: ${attemptsLeft} | يا الله توكلنا.. 💍`;
            }
        }

        // --- وظائف الصوت ---
        function playAudio(id) {
            if (!userSettings.soundEnabled) return;
            const a = document.getElementById(id);
            if (!a) { console.error('Audio element not found:', id); return; }
            if (!a.src) { console.error('Audio source missing for:', id); return; }
            
            a.pause();
            a.currentTime = 0;
            a.volume = userSettings.volume;
            
            const playPromise = a.play();
            if (playPromise !== undefined) {
                playPromise.catch(error => {
                    console.error('Audio play failed:', error);
                });
            }
        }

        // --- وظائف الرسوم (FX) ---
        const canvas = document.getElementById('canvas-fx');
        const ctx = canvas.getContext('2d', { alpha: true });
        let particles = [];
        
        function resizeCanvas() {
            const dpr = window.devicePixelRatio || 1;
            canvas.width = window.innerWidth * dpr;
            canvas.height = window.innerHeight * dpr;
            canvas.style.width = window.innerWidth + 'px';
            canvas.style.height = window.innerHeight + 'px';
            ctx.scale(dpr, dpr);
        }
        window.addEventListener('resize', resizeCanvas);
        resizeCanvas();

        function createFaces(x, y, type) {
            const faces = type === 'lose' ? ['😂', '😆', '🤣', '😜'] : ['🏆', '👑', '🎉', '🥇'];
            for (let i = 0; i < 40; i++) {
                particles.push({
                    x: x + (Math.random() - 0.5) * 300, y: y + (Math.random() - 0.5) * 300,
                    vx: (Math.random() - 0.5) * 12, vy: (Math.random() - 1) * 12 - 6,
                    alpha: 1, text: faces[Math.floor(Math.random() * faces.length)],
                    size: Math.random() * 30 + 24, gravity: 0.15, isText: true
                });
            }
        }

        function animate() {
            ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
            for (let i = particles.length - 1; i >= 0; i--) {
                const p = particles[i];
                p.x += p.vx; p.y += p.vy; p.vy += p.gravity; p.alpha -= 0.015;
                if (p.alpha <= 0) { particles.splice(i, 1); continue; }
                ctx.globalAlpha = p.alpha;
                ctx.font = `bold ${p.size}px Arial`;
                ctx.fillText(p.text, p.x, p.y);
            }
            requestAnimationFrame(animate);
        }
        animate();

        function showPointsNotification(points) {
            const div = document.createElement('div');
            div.style.cssText = `position:fixed; top:50%; left:50%; transform:translate(-50%,-50%); background:rgba(30,42,74,0.9); border:3px solid var(--gold); border-radius:20px; padding:20px 40px; z-index:10000; color:${points > 0 ? 'var(--bright-gold)' : '#ff4444'}; font-size:32px; font-weight:bold; animation:pointsPopup 2s ease-out forwards;`;
            div.innerHTML = `${points > 0 ? '+' : ''}${points} 🏆`;
            document.body.appendChild(div);
            setTimeout(() => div.remove(), 2000);
        }

        // --- وظائف الأونلاين ---
        let currentRoom = null;
        let myRole = null;
        let myTurn = false;
        let gamePhase = 'waiting';

        function toggleSidebar(show) {
            const sidebar = document.getElementById('players-sidebar');
            const overlay = document.getElementById('sidebar-overlay');
            if (show) {
                sidebar.classList.add('active');
                overlay.style.display = 'block';
                socket.emit('get_players');
            } else {
                sidebar.classList.remove('active');
                overlay.style.display = 'none';
            }
        }

        let pendingToSid = null;

        function sendInvitation(toSid) {
            if (!onlinePlayers || onlinePlayers.length === 0) {
                alert('جاري تحديث قائمة اللاعبين.. يرجى المحاولة بعد ثوانٍ');
                socket.emit('get_players');
                return;
            }

            const self = onlinePlayers.find(p => p.sid === socket.id);
            const opponent = onlinePlayers.find(p => p.sid === toSid);

            if (!opponent) {
                alert('هذا اللاعب لم يعد متصلاً!');
                return;
            }

            if (!self) {
                console.warn("Self not found in onlinePlayers, attempting to re-register...");
                socket.emit('get_players');
                // سنسمح بالاستمرار هنا لأن السيرفر سيتحقق من الهوية على أي حال
                // لكننا سنفقد فحص تحدي النفس مؤقتاً
                pendingToSid = toSid;
                document.getElementById('bet-modal').style.display = 'flex';
                return;
            }

            // منع اللاعب من تحدي نفسه عبر التحقق من هوية اللاعب
            if (self.player_id === opponent.player_id) {
                alert("لا يمكنك تحدي نفسك!");
                return;
            }

            pendingToSid = toSid;
            document.getElementById('bet-modal').style.display = 'flex';
        }

        function closeBetModal() {
            document.getElementById('bet-modal').style.display = 'none';
            pendingToSid = null;
        }

        function confirmInvitationWithBet() {
            const betInput = document.getElementById('bet-input');
            const betAmount = parseInt(betInput.value);
            
            console.log('Sending invitation to:', pendingToSid, 'with bet:', betAmount);
            
            if (!pendingToSid) {
                alert("حدث خطأ: لم يتم تحديد اللاعب الخصم!");
                return;
            }
            
            if (isNaN(betAmount) || betAmount < 20) {
                alert("الرهان يجب أن يكون رقماً ولا يقل عن 20 نقطة!");
                return;
            }
            
            if (betAmount > stats.totalScore) {
                alert("رصيدك غير كافٍ لهذا الرهان!");
                return;
            }
            
            socket.emit('send_invitation', {to_sid: pendingToSid, bet: betAmount});
            closeBetModal();
            toggleSidebar(false);
            alert('تم إرسال الدعوة، انتظر الرد...');
        }

        function acceptInvitation() {
            if (pendingInvitation) {
                socket.emit('accept_invitation', {
                    host_sid: pendingInvitation.from_sid,
                    guest_sid: socket.id,
                    bet: pendingInvitation.bet
                });
                document.getElementById('invitation-popup').style.display = 'none';
                pendingInvitation = null;
            }
        }

        function declineInvitation() {
            if (pendingInvitation) {
                socket.emit('decline_invitation', {invitation_id: pendingInvitation.invitation_id});
                document.getElementById('invitation-popup').style.display = 'none';
                pendingInvitation = null;
            }
        }

        function leaveOnlineGame() {
            if (currentRoom) socket.emit('leave_room', {room_id: currentRoom});
            currentRoom = null;
            document.getElementById('online-game').style.display = 'none';
            document.getElementById('mode-screen').style.display = 'flex';
            resetOnlineBoard();
            toggleSidebar(true); // فتح قائمة المتصلين للبحث عن تحدي جديد
        }

        function resetOnlineBoard() {
            for(let i=1; i<=8; i++) {
                let side = (i % 2 !== 0) ? 'left' : 'right';
                const box = document.getElementById('online-box'+i);
                const img = document.getElementById('online-img'+i);
                if (box) box.classList.remove('victory', 'fail');
                if (img) img.src = `/static/${side}_closed.png`;
            }
        }

        function checkOnlineHand(id, side) {
            if (!currentRoom || !myTurn) return;
            
            // منع الضغط على يد مفتوحة مسبقاً
            const box = document.getElementById('online-box' + id);
            if (box && (box.classList.contains('fail') || box.classList.contains('victory'))) return;

            if (gamePhase === 'hiding') {
                socket.emit('check_hand', {room_id: currentRoom, hand_id: id, side: side});
                document.getElementById('online-message').innerText = 'تم الضم! انتظر بحث الخصم...';
                myTurn = false;
            } else if (gamePhase === 'finding') {
                socket.emit('check_hand', {room_id: currentRoom, hand_id: id, side: side});
            }
        }

        function requestRematch() {
            if (currentRoom) {
                socket.emit('request_rematch', {room_id: currentRoom});
                alert('تم إرسال طلب إعادة اللعب، انتظر موافقة الخصم...');
            }
        }

        function toggleQuickChat() {
            const dropdown = document.getElementById('quick-chat-dropdown');
            dropdown.classList.toggle('active');
        }

        function sendQuickMessage(msg) {
            if (currentRoom) {
                socket.emit('send_message', {room_id: currentRoom, message: msg});
                document.getElementById('quick-chat-dropdown').classList.remove('active');
            }
        }

        function addChatMessage(sender, message, isSelf = false) {
            const chatDiv = document.getElementById('chat-messages');
            if (!chatDiv) return;
            const msgDiv = document.createElement('div');
            msgDiv.style.cssText = `padding:8px; border-radius:10px; background:${isSelf ? 'rgba(212,175,55,0.2)' : 'rgba(0,0,0,0.3)'}; border:1px solid ${isSelf ? 'var(--gold)' : 'rgba(255,255,255,0.1)'}; margin-bottom:5px;`;
            msgDiv.innerHTML = `<p style="color:${isSelf ? 'var(--bright-gold)' : '#aaa'}; font-size:12px; margin:0;">${sender}</p><p style="color:#fff; font-size:14px; margin:0;">${message}</p>`;
            chatDiv.appendChild(msgDiv);
            chatDiv.scrollTop = chatDiv.scrollHeight;
        }

        function updateGamePhase(phase, currentTurn) {
            gamePhase = phase;
            myTurn = (myRole === currentTurn);
            const msgEl = document.getElementById('online-message');
            if (phase === 'hiding') {
                msgEl.innerText = myTurn ? '👈 دورك لضم المحبس (اختر يد)' : '⏳ الخصم يقوم بضم المحبس الآن...';
                resetOnlineBoard();
            } else if (phase === 'finding') {
                msgEl.innerText = myTurn ? '🔍 دورك للبحث عن المحبس (5 محاولات)' : '👀 الخصم يبحث عن المحبس...';
            }
            if (document.getElementById('player1-card'))
                document.getElementById('player1-card').style.boxShadow = (currentTurn === 'host') ? '0 0 15px var(--gold)' : 'none';
            if (document.getElementById('player2-card'))
                document.getElementById('player2-card').style.boxShadow = (currentTurn === 'guest') ? '0 0 15px var(--gold)' : 'none';
        }

        // --- SocketIO Listeners ---
        socket.on('register_response', (data) => { alert(data.message); if (data.success) showLogin(); });
        socket.on('login_response', (data) => {
            if (data.success) { 
                updateUserData(data); 
                document.getElementById('login-screen').style.display = 'none'; 
                document.getElementById('register-screen').style.display = 'none'; 
                document.getElementById('mode-screen').style.display = 'flex'; 
                loadStats(); 
            } else alert(data.message);
        });
        socket.on('session_check', (data) => {
            if (data.logged_in) { 
                updateUserData(data); 
                document.getElementById('login-screen').style.display = 'none'; 
                document.getElementById('register-screen').style.display = 'none'; 
                document.getElementById('mode-screen').style.display = 'flex'; 
                loadStats(); 
            }
        });
        socket.on('profile_data', (data) => { if (data && data.player) displayProfileModal(data.player, data.statistics); });
        socket.on('profile_updated', (data) => {
            if (data.success) { 
                updateUserData({ player_id: window.playerId, username: window.username, display_name: data.display_name, profile_image: data.profile_image }); 
                alert('تم التحديث بنجاح'); 
            } else alert(data.message);
        });
        socket.on('players_list_updated', (players) => {
            onlinePlayers = players; // حفظ القائمة عالمياً
            const listDiv = document.getElementById('players-list');
            if (!listDiv) return;
            listDiv.innerHTML = '';
            players.forEach(player => {
                if (player.sid !== socket.id) {
                    const div = document.createElement('div');
                    div.className = 'online-player-card';
                    div.innerHTML = `
                        <div style="display:flex; align-items:center; gap:10px;">
                            <img src="${player.profile_image || 'https://www.gravatar.com/avatar/000?d=mp'}" style="width:35px; height:35px; border-radius:50%;">
                            <div>
                                <div style="color:white; font-size:14px; font-weight:bold;">${player.name}</div>
                                <div style="display:flex; align-items:center;">
                                    <div class="status-dot ${player.status === 'available' ? 'status-online' : 'status-busy'}"></div>
                                    <span style="color:#aaa; font-size:11px;">${player.status === 'available' ? 'متاح' : 'في لعبة'}</span>
                                </div>
                            </div>
                        </div>
                        ${player.status === 'available' ? `<button onclick="sendInvitation('${player.sid}')" style="background:var(--gold); border:none; border-radius:8px; padding:5px 12px; font-size:12px; cursor:pointer; color:black; font-weight:bold;">تحدي</button>` : ''}
                    `;
                    listDiv.appendChild(div);
                }
            });
        });

        let pendingInvitation = null;
        socket.on('invitation_received', (data) => {
            pendingInvitation = data;
            document.getElementById('invitation-from').innerText = `دعوة من: ${data.from_name}`;
            document.getElementById('invitation-bet-display').innerText = `الرهان: ${data.bet} نقطة 🏆`;
            document.getElementById('invitation-popup').style.display = 'block';
        });
        socket.on('invitation_accepted', (data) => {
            currentRoom = data.room_id;
            myRole = (data.host_sid === socket.id) ? 'host' : 'guest';
            
            // وظيفة الانتقال الإجباري
            function forceOnlineUI() {
                // إخفاء كل شيء
                document.getElementById('mode-screen').style.display = 'none';
                document.getElementById('game-container').style.display = 'none';
                document.getElementById('login-screen').style.display = 'none';
                document.getElementById('register-screen').style.display = 'none';
                if (document.getElementById('sidebar-overlay')) document.getElementById('sidebar-overlay').style.display = 'none';
                if (document.getElementById('players-sidebar')) document.getElementById('players-sidebar').classList.remove('active');
                
                // إظهار منطقة اللعب أونلاين
                const onlineGame = document.getElementById('online-game');
                onlineGame.style.display = 'flex';
                onlineGame.style.flexDirection = 'column';
                onlineGame.scrollIntoView();
            }
            
            forceOnlineUI();
            
            // تحديث المعلومات
            document.getElementById('player1-name').innerText = data.host_name;
            document.getElementById('player2-name').innerText = data.guest_name;
            document.getElementById('player1-stats').innerText = data.host_wins || 0;
            document.getElementById('player2-stats').innerText = data.guest_wins || 0;
            
            resetOnlineBoard();
            updateGamePhase(data.phase, data.current_turn);
            addChatMessage('System', '🎮 بدأت اللعبة الآن!');
        });
        socket.on('phase_changed', (data) => { updateGamePhase(data.phase, data.current_turn); if (data.message) addChatMessage('System', data.message); });
        socket.on('hand_checked', (data) => {
            const box = document.getElementById('online-box' + data.hand_id);
            const img = document.getElementById('online-img' + data.hand_id);
            if (data.result === 'win') { box.classList.add('victory'); img.src = (data.side === 'right') ? '/static/right_winner.png' : '/static/left_winner.png'; playAudio('left2'); createFaces(window.innerWidth/2, window.innerHeight/2, 'win'); }
            else if (data.result === 'fail') { box.classList.add('fail'); img.src = '/static/' + data.side + '_open.png'; playAudio('left0'); if (myTurn) document.getElementById('online-message').innerText = `❌ خطأ! متبقي ${data.attempts_left} محاولات`; }
            else if (data.result === 'lose_all') { 
                box.classList.add('fail'); 
                img.src = '/static/' + data.side + '_open.png'; 
                playAudio('left1'); 
                addChatMessage('System', data.message);
            }
            if (data.host_wins !== undefined) {
                document.getElementById('player1-stats').innerText = data.host_wins;
                document.getElementById('player2-stats').innerText = data.guest_wins;
            }
            if (data.game_over_final) {
                setTimeout(() => {
                    alert(data.win_msg);
                    addChatMessage('System', data.win_msg);
                    
                    // الخروج التلقائي فوراً للقائمة الجانبية للبحث عن خصم آخر
                    leaveOnlineGame(); 
                }, 2000);
                return;
            }
            if (data.next_phase) {
                setTimeout(() => { 
                    updateGamePhase(data.next_phase, data.next_turn); 
                    resetOnlineBoard();
                    if (data.game_over) {
                        document.getElementById('online-controls').style.display = 'flex';
                        document.getElementById('online-quick-msgs').style.display = 'none';
                    }
                }, 2000);
            }
        });
        socket.on('chat_message', (data) => { addChatMessage(data.sender, data.message, data.is_self); });
        socket.on('player_left', (data) => { alert(data.message); leaveOnlineGame(); });
        socket.on('rematch_requested', (data) => {
            if (confirm(`اللاعب ${data.from_name} يطلب إعادة لعب البطولة، هل توافق؟`)) {
                socket.emit('accept_rematch', {room_id: currentRoom});
            } else {
                socket.emit('decline_rematch', {room_id: currentRoom});
            }
        });
        socket.on('rematch_started', (data) => {
            currentRoom = data.room_id; // تحديث معرف الغرفة
            myRole = (data.host_sid === socket.id) ? 'host' : 'guest';
            resetOnlineBoard();
            updateGamePhase(data.phase, data.current_turn);
            
            // العودة لشاشة اللعب
            toggleSidebar(false);
            document.getElementById('mode-screen').style.display = 'none';
            document.getElementById('game-container').style.display = 'none';
            document.getElementById('online-game').style.display = 'flex';
            
            document.getElementById('online-controls').style.display = 'none';
            document.getElementById('online-quick-msgs').style.display = 'flex';
            if (data.host_wins !== undefined) {
                document.getElementById('player1-stats').innerText = data.host_wins;
                document.getElementById('player2-stats').innerText = data.guest_wins;
            }
            if (data.bet) {
                addChatMessage('System', `💰 الرهان في هذه البطولة: ${data.bet} نقطة`);
            }
            addChatMessage('System', '🔄 بدأت جولة جديدة!');
        });
        socket.on('error', (data) => { 
            console.error('Server Error:', data.message);
            alert(data.message); 
        });
        socket.on('stats_updated', (data) => {
            if (data.stats) {
                const s = data.stats;
                stats.totalScore = Number(s.total_score);
                stats.lastBonusDate = s.last_bonus_date;
                stats.bonusStreak = Number(s.bonus_streak || 0);
                
                const balanceEl = document.getElementById('current-balance');
                if (balanceEl) balanceEl.innerText = `رصيد: ${stats.totalScore.toLocaleString()} 🏆`;
                
                // تحديث اللقب والمستوى
                const rank = getRankData(stats.totalScore);
                const titleEl = document.getElementById('current-title');
                const levelEl = document.getElementById('header-player-level');
                if (titleEl) {
                    titleEl.innerText = rank.title;
                    titleEl.style.color = rank.color;
                    titleEl.style.borderColor = rank.color;
                }
                if (levelEl) levelEl.innerText = rank.level;

                if (data.bonus_claimed) {
                    showPointsNotification(data.amount);
                    alert(`مبروك! حصلت على مكافأة اليوم ${data.streak}: ${data.amount} 🏆`);
                    document.getElementById('daily-bonus-container').style.display = 'none';
                }
                updateStatsDisplay();
            }
        });

        socket.on('leaderboard_data', (players) => {
            const listEl = document.getElementById('leaderboard-list');
            if (!listEl) return;
            listEl.innerHTML = '';
            players.forEach((player, index) => {
                const isTop3 = index < 3;
                const colors = ['#ffd700', '#c0c0c0', '#cd7f32'];
                const rankColor = isTop3 ? colors[index] : '#444';
                
                const div = document.createElement('div');
                div.style.cssText = `
                    display:flex; align-items:center; gap:8px; 
                    background:rgba(255,255,255,0.03); 
                    padding:8px 12px; border-radius:12px; margin-bottom:6px; 
                    border:1px solid ${isTop3 ? rankColor : 'transparent'};
                    box-shadow: ${isTop3 ? '0 0 10px ' + rankColor + '44' : 'none'};
                `;
                
                div.innerHTML = `
                    <div style="font-size:14px; font-weight:bold; width:20px; color:${isTop3 ? rankColor : '#888'};">${index + 1}</div>
                    <img src="${player.profile_image || 'https://www.gravatar.com/avatar/000?d=mp'}" 
                         style="width:32px; height:32px; border-radius:50%; border:1.5px solid ${player.color || '#gold'};">
                    <div style="flex:1; min-width:0;">
                        <div style="font-weight:bold; font-size:13px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${player.display_name}</div>
                        <div style="font-size:10px; color:${player.color || '#aaa'};">${player.title || 'لاعب'}</div>
                    </div>
                    <div style="text-align:right;">
                        <div style="color:var(--bright-gold); font-weight:bold; font-size:13px;">${Number(player.total_score).toLocaleString()} 🏆</div>
                        <div style="font-size:9px; color:#888;">${player.wins} فوز</div>
                    </div>
                `;
                listEl.appendChild(div);
            });
        });

        socket.on('hint_purchased', (data) => {
            if (data.success) {
                // منطق التلميح: كشف يد خاطئة واحدة
                let wrongHands = [];
                for (let i = 1; i <= 8; i++) {
                    const box = document.getElementById('box' + i);
                    if (i !== ringPosition && !box.classList.contains('fail')) {
                        wrongHands.push(i);
                    }
                }
                
                if (wrongHands.length > 0) {
                    const randomWrong = wrongHands[Math.floor(Math.random() * wrongHands.length)];
                    const side = (randomWrong % 2 !== 0) ? 'left' : 'right';
                    const box = document.getElementById('box' + randomWrong);
                    const img = document.getElementById('img' + randomWrong);
                    
                    box.classList.add('fail');
                    img.src = '/static/' + side + '_open.png';
                    
                    // تعطيل زر التلميح بعد الاستخدام
                    const hintBtn = document.getElementById('hint-btn');
                    hintBtn.disabled = true;
                    hintBtn.style.opacity = '0.5';
                    
                    showPointsNotification(-data.cost);
                }
            }
        });

        // --- تهيئة عند التحميل ---
        document.addEventListener('DOMContentLoaded', () => {
            setTimeout(() => socket.emit('check_session'), 100);
        });

        // دعم اللمس للجوال
        let lastTouchEnd = 0;
        document.addEventListener('touchend', (e) => {
            const now = Date.now();
            if (now - lastTouchEnd <= 300) e.preventDefault();
            lastTouchEnd = now;
        }, false);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(html_template)

# --- دوال مساعدة ---
def get_player_rank_data(score):
    if score < 500: return {"level": 1, "title": "مبتدئ", "color": "#aaa"}
    if score < 1500: return {"level": 2, "title": "هاوي", "color": "#44cc44"}
    if score < 3500: return {"level": 3, "title": "محترف", "color": "#2196F3"}
    if score < 7000: return {"level": 4, "title": "صياد", "color": "#9c27b0"}
    if score < 15000: return {"level": 5, "title": "خبير محيبس", "color": "#ff9800"}
    return {"level": 6, "title": "ملك المحيبس 👑", "color": "#f9d71c"}

# --- أحداث SocketIO ---

players = {} # sid -> player_info
rooms = {} # room_id -> room_info

def register_player_sid(sid):
    if 'player_id' in session:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT display_name, profile_image FROM players WHERE id = ?', (session['player_id'],))
        p = cursor.fetchone()
        conn.close()
        
        if p:
            players[sid] = {
                'sid': sid,
                'player_id': session['player_id'],
                'name': p['display_name'],
                'profile_image': p['profile_image'],
                'status': 'available'
            }
            return True
    return False

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")
    if register_player_sid(request.sid):
        emit('players_list_updated', list(players.values()), broadcast=True)

@socketio.on('register')
def handle_register(data):
    username = data.get('username')
    password = data.get('password')
    display_name = data.get('display_name')
    
    if not username or not password:
        emit('register_response', {'success': False, 'message': 'بيانات ناقصة'})
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        hashed_pw = generate_password_hash(password)
        cursor.execute('INSERT INTO players (username, password, display_name) VALUES (?, ?, ?)',
                       (username, hashed_pw, display_name or username))
        player_id = cursor.lastrowid
        # إنشاء سجل إحصائيات فارغ
        cursor.execute('INSERT INTO statistics (player_id, wins_by_mode, best_scores_by_difficulty) VALUES (?, ?, ?)',
                       (player_id, json.dumps({'guessing': 0, 'challenge': 0, 'online': 0}), 
                        json.dumps({'easy': 0, 'medium': 0, 'hard': 0})))
        conn.commit()
        emit('register_response', {'success': True, 'message': 'تم التسجيل بنجاح! سجل دخولك الآن'})
    except sqlite3.IntegrityError:
        emit('register_response', {'success': False, 'message': 'اسم المستخدم موجود بالفعل'})
    finally:
        conn.close()

@socketio.on('login')
def handle_login(data):
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM players WHERE username = ?', (username,))
    player = cursor.fetchone()
    
    if player and check_password_hash(player['password'], password):
        session['player_id'] = player['id']
        session['username'] = player['username']
        cursor.execute('UPDATE players SET last_login = ? WHERE id = ?', (datetime.now(), player['id']))
        
        # جلب الإحصائيات
        cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (player['id'],))
        stats = cursor.fetchone()
        
        conn.commit()
        conn.close()
        
        # تحديث قائمة اللاعبين المتصلين
        register_player_sid(request.sid)
        emit('players_list_updated', list(players.values()), broadcast=True)
        
        emit('login_response', {
            'success': True,
            'player_id': player['id'],
            'username': player['username'],
            'display_name': player['display_name'],
            'profile_image': player['profile_image'],
            'stats': dict(stats) if stats else None
        })
    else:
        conn.close()
        emit('login_response', {'success': False, 'message': 'خطأ في الاسم أو كلمة المرور'})

@socketio.on('check_session')
def handle_check_session():
    if 'player_id' in session:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM players WHERE id = ?', (session['player_id'],))
        player = cursor.fetchone()
        cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (session['player_id'],))
        stats = cursor.fetchone()
        conn.close()
        
        if player:
            # تحديث قائمة اللاعبين المتصلين
            register_player_sid(request.sid)
            emit('players_list_updated', list(players.values()), broadcast=True)

            emit('session_check', {
                'logged_in': True,
                'player_id': player['id'],
                'username': player['username'],
                'display_name': player['display_name'],
                'profile_image': player['profile_image'],
                'stats': dict(stats) if stats else None
            })
    else:
        emit('session_check', {'logged_in': False})

@socketio.on('logout')
def handle_logout():
    session.clear()
    emit('logout_success')

@socketio.on('update_profile')
def handle_update_profile(data):
    if 'player_id' not in session: return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if 'display_name' in data:
        cursor.execute('UPDATE players SET display_name = ? WHERE id = ?', (data['display_name'], session['player_id']))
    if 'profile_image' in data:
        cursor.execute('UPDATE players SET profile_image = ? WHERE id = ?', (data['profile_image'], session['player_id']))
        
    conn.commit()
    
    # جلب البيانات المحدثة
    cursor.execute('SELECT display_name, profile_image FROM players WHERE id = ?', (session['player_id'],))
    player = cursor.fetchone()
    conn.close()
    
    emit('profile_updated', {
        'success': True,
        'display_name': player['display_name'],
        'profile_image': player['profile_image']
    })

@socketio.on('get_profile')
def handle_get_profile():
    if 'player_id' not in session: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, display_name, profile_image, created_at, last_login FROM players WHERE id = ?', (session['player_id'],))
    player = cursor.fetchone()
    cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (session['player_id'],))
    stats = cursor.fetchone()
    conn.close()
    
    if player:
        emit('profile_data', {
            'player': dict(player),
            'statistics': dict(stats) if stats else None
        })

@socketio.on('add_points_server')
def handle_add_points_server(data):
    if 'player_id' not in session: return
    points = data.get('points', 0)
    mode = data.get('mode', 'free')
    win = data.get('win', False)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # تحديث النقاط في قاعدة البيانات مباشرة
    cursor.execute('''
        UPDATE statistics 
        SET total_score = total_score + ?, 
            total_games = total_games + 1,
            wins = wins + ?,
            losses = losses + ?
        WHERE player_id = ?
    ''', (points, 1 if win else 0, 0 if win else 1, session['player_id']))
    
    # تأمين الرصيد من النزول تحت 100 (اختياري، حسب رغبتك)
    # cursor.execute('UPDATE statistics SET total_score = 100 WHERE player_id = ? AND total_score < 100', (session['player_id'],))
    
    conn.commit()
    
    # جلب البيانات المحدثة وإرسالها للعميل
    cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (session['player_id'],))
    updated_stats = cursor.fetchone()
    conn.close()
    
    emit('stats_updated', {'stats': dict(updated_stats)})
    logger.info(f"Points added on server for {session['player_id']}: {points}")

@socketio.on('update_stats')
def handle_update_stats(data):
    # تم تقييد هذه الوظيفة لمنع تصفير الحساب من جانب العميل
    # سيتم فقط تحديث البيانات غير الحساسة إذا لزم الأمر
    if 'player_id' not in session: return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE statistics SET 
        best_time = ?, wins_by_mode = ?, best_scores_by_difficulty = ?
        WHERE player_id = ?
    ''', (
        data['bestTime'], json.dumps(data['winsByMode']),
        json.dumps(data['bestScoresByDifficulty']), session['player_id']
    ))
    conn.commit()
    conn.close()

@socketio.on('get_leaderboard')
def handle_get_leaderboard():
    conn = get_db_connection()
    cursor = conn.cursor()
    # جلب أفضل 10 لاعبين بناءً على مجموع النقاط
    cursor.execute('''
        SELECT p.display_name, p.profile_image, s.total_score, s.wins
        FROM players p
        JOIN statistics s ON p.id = s.player_id
        ORDER BY s.total_score DESC
        LIMIT 10
    ''')
    top_players = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # إضافة الألقاب لكل لاعب في القائمة
    for player in top_players:
        rank_info = get_player_rank_data(player['total_score'])
        player['title'] = rank_info['title']
        player['color'] = rank_info['color']
        
    emit('leaderboard_data', top_players)

@socketio.on('claim_daily_bonus')
def handle_claim_daily_bonus():
    if 'player_id' not in session: return
    
    from datetime import datetime, timedelta
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT last_bonus_date, bonus_streak, total_score FROM statistics WHERE player_id = ?', (session['player_id'],))
    row = cursor.fetchone()
    
    if row:
        last_date = row['last_bonus_date']
        streak = row['bonus_streak'] or 0
        
        if last_date == today:
            emit('error', {'message': 'لقد حصلت على مكافأتك اليومية بالفعل! عد غداً'})
        else:
            # تحديث المتتالية (Streak)
            if last_date == yesterday:
                streak = (streak % 7) + 1
            else:
                streak = 1
            
            # حساب قيمة المكافأة (اليوم الأول 10، اليوم الثاني 15، وهكذا...)
            bonus_amount = 10 + (streak - 1) * 5
            
            cursor.execute('''
                UPDATE statistics 
                SET total_score = total_score + ?, last_bonus_date = ?, bonus_streak = ? 
                WHERE player_id = ?
            ''', (bonus_amount, today, streak, session['player_id']))
            conn.commit()
            
            # جلب البيانات المحدثة
            cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (session['player_id'],))
            updated_stats = cursor.fetchone()
            
            # إرسال تحديث شامل للإحصائيات
            emit('stats_updated', {
                'stats': dict(updated_stats), 
                'bonus_claimed': True, 
                'amount': bonus_amount,
                'streak': streak
            })
            logger.info(f"Daily bonus claimed by {session['player_id']}: {bonus_amount} (Day {streak})")
    
    conn.close()

@socketio.on('use_hint')
def handle_use_hint():
    if 'player_id' not in session: return
    
    hint_cost = 30
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT total_score FROM statistics WHERE player_id = ?', (session['player_id'],))
    row = cursor.fetchone()
    
    if row and row['total_score'] >= hint_cost:
        cursor.execute('UPDATE statistics SET total_score = total_score - ? WHERE player_id = ?', (hint_cost, session['player_id']))
        conn.commit()
        
        cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (session['player_id'],))
        updated_stats = cursor.fetchone()
        emit('stats_updated', {'stats': dict(updated_stats)})
        emit('hint_purchased', {'success': True, 'cost': hint_cost})
    else:
        emit('error', {'message': 'رصيدك غير كافٍ لشراء تلميح! (التكلفة 30 🏆)'})
    
    conn.close()

# --- منطق الأونلاين ---

@socketio.on('get_players')
def handle_get_players():
    # تحديث معلومات اللاعب الحالي
    register_player_sid(request.sid)
    
    available_players = [p for p in players.values()]
    emit('players_list_updated', available_players, broadcast=True)

@socketio.on('send_invitation')
def handle_invitation(data):
    to_sid = data.get('to_sid')
    bet = data.get('bet', 20)
    
    logger.info(f"Invitation attempt: from={request.sid}, to={to_sid}, bet={bet}")
    
    if request.sid not in players:
        logger.warning(f"Sender {request.sid} not in players list")
        emit('error', {'message': 'خطأ: بياناتك غير موجودة في قائمة المتصلين! يرجى تحديث الصفحة'})
        return

    if to_sid not in players:
        logger.warning(f"Target player {to_sid} not found in players list")
        emit('error', {'message': 'اللاعب المستهدف لم يعد متصلاً!'})
        return
        
    if players[to_sid]['status'] != 'available':
        logger.warning(f"Target player {to_sid} is busy (status: {players[to_sid]['status']})")
        emit('error', {'message': 'اللاعب المستهدف في لعبة أخرى حالياً!'})
        return
        
    logger.info(f"Emitting invitation_received to {to_sid} from {players[request.sid]['name']}")
    emit('invitation_received', {
        'from_sid': request.sid,
        'from_name': players[request.sid]['name'],
        'invitation_id': f"{request.sid}_{to_sid}",
        'bet': bet
    }, room=to_sid)

@socketio.on('accept_invitation')
def handle_accept(data):
    host_sid = data.get('host_sid')
    guest_sid = data.get('guest_sid')
    bet = data.get('bet', 20)
    
    logger.info(f"Accepting invitation: host={host_sid}, guest={guest_sid}, bet={bet}")
    
    if host_sid in players and guest_sid in players:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            host_pid = players[host_sid].get('player_id')
            guest_pid = players[guest_sid].get('player_id')
            
            host_stats = cursor.execute('SELECT total_score FROM statistics WHERE player_id = ?', (host_pid,)).fetchone()
            guest_stats = cursor.execute('SELECT total_score FROM statistics WHERE player_id = ?', (guest_pid,)).fetchone()
            
            if not host_stats or not guest_stats:
                logger.error(f"Stats not found for players: host_pid={host_pid}, guest_pid={guest_pid}")
                emit('error', {'message': 'خطأ في جلب بيانات اللاعبين!'}, room=request.sid)
                return

            if host_stats['total_score'] < bet or guest_stats['total_score'] < bet:
                logger.warning("Insufficient balance for bet")
                emit('error', {'message': 'رصيد أحد اللاعبين غير كافٍ!'}, room=request.sid)
                return

            room_id = f"room_{host_sid}_{guest_sid}"
            join_room(room_id, sid=host_sid)
            join_room(room_id, sid=guest_sid)
            
            players[host_sid]['status'] = 'playing'
            players[guest_sid]['status'] = 'playing'
            
            rooms[room_id] = {
                'host': host_sid,
                'guest': guest_sid,
                'phase': 'hiding',
                'current_turn': 'host',
                'ring_position': 0,
                'attempts': 5,
                'host_wins': 0,
                'guest_wins': 0,
                'checked_hands': [],
                'bet': bet
            }
            
            acceptance_data = {
                'room_id': room_id,
                'host_sid': host_sid,
                'guest_sid': guest_sid,
                'host_name': players[host_sid]['name'],
                'guest_name': players[guest_sid]['name'],
                'host_image': players[host_sid]['profile_image'],
                'guest_image': players[guest_sid]['profile_image'],
                'phase': 'hiding',
                'current_turn': 'host',
                'host_wins': 0,
                'guest_wins': 0,
                'bet': bet
            }
            
            logger.info(f"Emitting invitation_accepted to room {room_id}")
            emit('invitation_accepted', acceptance_data, room=room_id)
            emit('players_list_updated', list(players.values()), broadcast=True)
        except Exception as e:
            logger.exception(f"Error in handle_accept: {str(e)}")
            emit('error', {'message': 'حدث خطأ أثناء بدء اللعبة'}, room=request.sid)
        finally:
            conn.close()
    else:
        logger.warning(f"One of the players disconnected: host={host_sid in players}, guest={guest_sid in players}")
        emit('error', {'message': 'أحد اللاعبين لم يعد متصلاً!'}, room=request.sid)

@socketio.on('check_hand')
def handle_check_hand(data):
    room_id = data.get('room_id')
    hand_id = data.get('hand_id')
    side = data.get('side')
    
    if room_id not in rooms: return
    room = rooms[room_id]
    
    # مرحلة الضم
    if room['phase'] == 'hiding':
        if (room['current_turn'] == 'host' and request.sid == room['host']) or \
           (room['current_turn'] == 'guest' and request.sid == room['guest']):
            room['ring_position'] = hand_id
            room['phase'] = 'finding'
            room['current_turn'] = 'guest' if room['current_turn'] == 'host' else 'host'
            room['attempts'] = 5
            room['checked_hands'] = []
            emit('phase_changed', {'phase': 'finding', 'current_turn': room['current_turn'], 'message': 'تم الضم! دور الخصم للبحث'}, room=room_id)
            
    # مرحلة البحث
    elif room['phase'] == 'finding':
        if (room['current_turn'] == 'host' and request.sid == room['host']) or \
           (room['current_turn'] == 'guest' and request.sid == room['guest']):
            
            # منع تكرار الضغط على نفس اليد
            if hand_id in room['checked_hands']:
                return
            
            room['checked_hands'].append(hand_id)
            
            if hand_id == room['ring_position']:
                # فوز الباحث
                winner_role = room['current_turn']
                if winner_role == 'host':
                    room['host_wins'] += 1
                else:
                    room['guest_wins'] += 1
                
                game_over_final = False
                win_msg = ""
                if room['host_wins'] >= 5:
                    game_over_final = True
                    win_msg = f"🏆 انتهت اللعبة! {players[room['host']]['name']} فاز بالبطولة (5 نقاط)!"
                elif room['guest_wins'] >= 5:
                    game_over_final = True
                    win_msg = f"🏆 انتهت اللعبة! {players[room['guest']]['name']} فاز بالبطولة (5 نقاط)!"

                emit('hand_checked', {
                    'hand_id': hand_id, 'side': side, 'result': 'win',
                    'next_phase': 'hiding' if not game_over_final else None, 
                    'next_turn': room['current_turn'] if not game_over_final else None,
                    'game_over': True,
                    'game_over_final': game_over_final,
                    'win_msg': win_msg,
                    'host_wins': room['host_wins'],
                    'guest_wins': room['guest_wins']
                }, room=room_id)
                
                if game_over_final:
                    room['phase'] = 'finished'
                    
                    # توزيع الأرباح والخصم
                    bet = room.get('bet', 20)
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    try:
                        winner_sid = room['host'] if room['host_wins'] >= 10 else room['guest']
                        loser_sid = room['guest'] if room['host_wins'] >= 10 else room['host']
                        
                        winner_pid = players[winner_sid]['player_id']
                        loser_pid = players[loser_sid]['player_id']
                        
                        # إضافة للفائز
                        cursor.execute('UPDATE statistics SET total_score = total_score + ? WHERE player_id = ?', (bet, winner_pid))
                        # خصم من الخاسر
                        cursor.execute('UPDATE statistics SET total_score = total_score - ? WHERE player_id = ?', (bet, loser_pid))
                        conn.commit()
                        
                        logger.info(f"Payout completed: winner={winner_pid}, loser={loser_pid}, amount={bet}")
                        
                        # إرسال تحديث الرصيد للجميع في الغرفة
                        for sid in [winner_sid, loser_sid]:
                            p_stats = cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (players[sid]['player_id'],)).fetchone()
                            emit('stats_updated', {'stats': dict(p_stats)}, room=sid)
                    except Exception as e:
                        logger.error(f"Error in payout: {str(e)}")
                    finally:
                        conn.close()
                else:
                    room['phase'] = 'hiding'
            else:
                room['attempts'] -= 1
                if room['attempts'] <= 0:
                    # خسارة الباحث
                    hider_sid = room['host'] if room['current_turn'] == 'guest' else room['guest']
                    hider_role = 'host' if hider_sid == room['host'] else 'guest'
                    
                    if hider_role == 'host':
                        room['host_wins'] += 1
                    else:
                        room['guest_wins'] += 1

                    game_over_final = False
                    win_msg = ""
                    if room['host_wins'] >= 5:
                        game_over_final = True
                        win_msg = f"🏆 انتهت اللعبة! {players[room['host']]['name']} فاز بالبطولة (5 نقاط)!"
                    elif room['guest_wins'] >= 5:
                        game_over_final = True
                        win_msg = f"🏆 انتهت اللعبة! {players[room['guest']]['name']} فاز بالبطولة (5 نقاط)!"

                    emit('hand_checked', {
                        'hand_id': hand_id, 'side': side, 'result': 'lose_all',
                        'message': 'نفدت المحاولات! الخافي ربح الجولة',
                        'next_phase': 'hiding' if not game_over_final else None, 
                        'next_turn': hider_role if not game_over_final else None,
                        'game_over': True,
                        'game_over_final': game_over_final,
                        'win_msg': win_msg,
                        'host_wins': room['host_wins'],
                        'guest_wins': room['guest_wins']
                    }, room=room_id)
                    
                    if game_over_final:
                        room['phase'] = 'finished'
                        
                        # توزيع الأرباح والخصم
                        bet = room.get('bet', 20)
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        try:
                            winner_sid = room['host'] if room['host_wins'] >= 10 else room['guest']
                            loser_sid = room['guest'] if room['host_wins'] >= 10 else room['host']
                            
                            winner_pid = players[winner_sid]['player_id']
                            loser_pid = players[loser_sid]['player_id']
                            
                            # إضافة للفائز
                            cursor.execute('UPDATE statistics SET total_score = total_score + ? WHERE player_id = ?', (bet, winner_pid))
                            # خصم من الخاسر
                            cursor.execute('UPDATE statistics SET total_score = total_score - ? WHERE player_id = ?', (bet, loser_pid))
                            conn.commit()
                            
                            logger.info(f"Payout completed: winner={winner_pid}, loser={loser_pid}, amount={bet}")
                            
                            # إرسال تحديث الرصيد للجميع في الغرفة
                            for sid in [winner_sid, loser_sid]:
                                p_stats = cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (players[sid]['player_id'],)).fetchone()
                                emit('stats_updated', {'stats': dict(p_stats)}, room=sid)
                        except Exception as e:
                            logger.error(f"Error in payout: {str(e)}")
                        finally:
                            conn.close()
                    else:
                        room['phase'] = 'hiding'
                        room['current_turn'] = hider_role
                else:
                    emit('hand_checked', {
                        'hand_id': hand_id, 'side': side, 'result': 'fail',
                        'attempts_left': room['attempts']
                    }, room=room_id)

@socketio.on('send_message')
def handle_message(data):
    room_id = data.get('room_id')
    msg = data.get('message')
    if room_id:
        emit('chat_message', {
            'sender': players[request.sid]['name'],
            'message': msg,
            'is_self': False
        }, room=room_id, skip_sid=request.sid)
        emit('chat_message', {
            'sender': players[request.sid]['name'],
            'message': msg,
            'is_self': True
        }, room=request.sid)

@socketio.on('request_rematch')
def handle_request_rematch(data):
    room_id = data.get('room_id')
    # إذا كانت الغرفة موجودة (لم يتم حذفها بعد)
    if room_id in rooms:
        emit('rematch_requested', {
            'from_sid': request.sid,
            'from_name': players[request.sid]['name']
        }, room=room_id, skip_sid=request.sid)
    else:
        # إذا كانت الغرفة حُذفت، نحتاج لمنطق خاص لإعادة إنشائها أو التعامل معها
        # للتبسيط، سنبقي الغرفة حتى يرفض الطرفان
        pass

@socketio.on('leave_room')
def handle_leave(data):
    room_id = data.get('room_id')
    if room_id in rooms:
        room = rooms[room_id]
        # إذا كانت البطولة انتهت، نترك الغرفة مفتوحة قليلاً للسماح بطلب إعادة اللعب
        if room.get('phase') == 'finished':
            # اللاعب يخرج من الغرفة تقنياً لكن السيرفر يحفظ بياناتها
            pass
        else:
            # انسحاب أثناء اللعب: خصم الرهان من المنسحب وإضافته للخصم
            bet = room.get('bet', 20)
            winner_sid = room['guest'] if request.sid == room['host'] else room['host']
            loser_sid = request.sid
            
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                winner_pid = players[winner_sid]['player_id']
                loser_pid = players[loser_sid]['player_id']
                
                # تحديث قاعدة البيانات
                cursor.execute('UPDATE statistics SET total_score = total_score + ? WHERE player_id = ?', (bet, winner_pid))
                cursor.execute('UPDATE statistics SET total_score = total_score - ? WHERE player_id = ?', (bet, loser_pid))
                conn.commit()
                
                logger.info(f"Withdrawal Payout: winner={winner_pid}, loser={loser_pid}, amount={bet}")
                
                # إبلاغ الفائز بالانسحاب وتحديث رصيده
                p_stats = cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (winner_pid,)).fetchone()
                emit('stats_updated', {'stats': dict(p_stats)}, room=winner_sid)
                emit('player_left', {'message': 'الخصم انسحب! لقد ربحت مبلغ الرهان 💰'}, room=winner_sid)
            except Exception as e:
                logger.error(f"Error in withdrawal payout: {str(e)}")
            finally:
                conn.close()

            host_sid = room['host']
            guest_sid = room['guest']
            if host_sid in players: players[host_sid]['status'] = 'available'
            if guest_sid in players: players[guest_sid]['status'] = 'available'
            del rooms[room_id]
            emit('players_list_updated', list(players.values()), broadcast=True)
    leave_room(room_id)

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in players:
        # البحث عن الغرف النشطة التي يشارك فيها هذا اللاعب للتعامل مع الانسحاب المفاجئ
        for room_id, room in list(rooms.items()):
            if (request.sid == room['host'] or request.sid == room['guest']) and room.get('phase') != 'finished':
                # تنفيذ نفس منطق الانسحاب
                bet = room.get('bet', 20)
                winner_sid = room['guest'] if request.sid == room['host'] else room['host']
                
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    winner_pid = players[winner_sid]['player_id']
                    loser_pid = players[request.sid]['player_id']
                    cursor.execute('UPDATE statistics SET total_score = total_score + ? WHERE player_id = ?', (bet, winner_pid))
                    cursor.execute('UPDATE statistics SET total_score = total_score - ? WHERE player_id = ?', (bet, loser_pid))
                    conn.commit()
                    p_stats = cursor.execute('SELECT * FROM statistics WHERE player_id = ?', (winner_pid,)).fetchone()
                    emit('stats_updated', {'stats': dict(p_stats)}, room=winner_sid)
                    emit('player_left', {'message': 'الخصم غادر اللعبة! لقد ربحت مبلغ الرهان 💰'}, room=winner_sid)
                except: pass
                finally: conn.close()
                
                other_sid = room['guest'] if request.sid == room['host'] else room['host']
                if other_sid in players: players[other_sid]['status'] = 'available'
                del rooms[room_id]
                break

        del players[request.sid]
        emit('players_list_updated', list(players.values()), broadcast=True)

@socketio.on('accept_rematch')
def handle_accept_rematch(data):
    room_id = data.get('room_id')
    if room_id in rooms:
        room = rooms[room_id]
        room['phase'] = 'hiding'
        # المضيف يبدأ دائماً في الإعادة
        room['current_turn'] = 'host'
        room['ring_position'] = 0
        room['attempts'] = 5
        room['checked_hands'] = []
        # إعادة تعيين النقاط عند إعادة اللعب الكلي
        room['host_wins'] = 0
        room['guest_wins'] = 0
        emit('rematch_started', {
            'phase': 'hiding',
            'current_turn': 'host',
            'host_wins': 0,
            'guest_wins': 0,
            'room_id': room_id,
            'host_sid': room['host'],
            'guest_sid': room['guest'],
            'bet': room.get('bet', 20)
        }, room=room_id)

@socketio.on('decline_rematch')
def handle_decline_rematch(data):
    room_id = data.get('room_id')
    if room_id in rooms:
        emit('player_left', {'message': 'تم رفض إعادة اللعب. جاري البحث عن خصم جديد...'}, room=room_id)
        # تحديث حالة اللاعبين ليكونوا متاحين مرة أخرى
        host_sid = rooms[room_id]['host']
        guest_sid = rooms[room_id]['guest']
        if host_sid in players: players[host_sid]['status'] = 'available'
        if guest_sid in players: players[guest_sid]['status'] = 'available'
        del rooms[room_id]
        emit('players_list_updated', list(players.values()), broadcast=True)

def get_local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def run_flask():
    # السيرفرات الخارجية مثل Render تحدد المنفذ عبر متغير بيئة
    port = int(os.environ.get("PORT", 8081))
    logger.info(f"Server starting on port {port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == '__main__':
    init_db()
    run_flask()
