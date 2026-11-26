"""宮鬥篇多人破冰遊戲 —— Streamlit 主程式（畫面層）。

啟動：雙擊 start_game.bat。
房主網址：?room=房號&h=房主代碼　玩家網址：?room=房號&p=玩家代碼
"""
import html
import json
import os
import socket
import sqlite3
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import avatars
import db
import game

VERSION = "v0.7.2（時間調整・倒數框與錯誤框修正）"

st.set_page_config(page_title="夢回宮闕", page_icon="🏮", layout="centered")

# 雙向元件：裡面就是原本做好的宮廷畫面（穿越動畫、測驗、冊封、角色卡），不重寫。
_palace = components.declare_component("palace", path=str(Path(__file__).parent / "frontend"))
# 密旨傳遞：宮內人物＋會跑到持旨者頭上的炸彈
_bomb = components.declare_component("bomb", path=str(Path(__file__).parent / "frontend_bomb"))
# 密旨傳遞：鼓掌（浮起的 👏）與追問（由右向左的彈幕）特效，全螢幕透明、不擋操作
_fx = components.declare_component("fx", path=str(Path(__file__).parent / "frontend_fx"))
# 環節轉場動畫（標題＋微動態插圖，約 3 秒後自己淡出）
_cut = components.declare_component("cut", path=str(Path(__file__).parent / "frontend_cut"))
# 謎題結算：收到的徽章（文字先浮現，徽章圖案隨後出現）
_badge = components.declare_component("badge", path=str(Path(__file__).parent / "frontend_badge"))
# 默契大考驗的「題目＋選項」合併框（選項可點，名字前有角色頭貼）
_pick = components.declare_component("pick", path=str(Path(__file__).parent / "frontend_pick"))
# 全頁氛圍：飄花＋背景音樂（裝在外層頁面上，換畫面時不中斷）
_amb = components.declare_component("ambient", path=str(Path(__file__).parent / "frontend_ambient"))
# 倒數框（手機端自己跳秒數）、兵臨城下前兩名排行、衝向城門的兵馬奔騰
_timer = components.declare_component("timer", path=str(Path(__file__).parent / "frontend_timer"))
_top2 = components.declare_component("top2", path=str(Path(__file__).parent / "frontend_top2"))
_stamp = components.declare_component("stampede", path=str(Path(__file__).parent / "frontend_stampede"))

PHASE_LABEL = {"lobby": "大廳等待中", "quiz": "階段 0：心理測驗", "s1": "階段 1：入宮冊封",
               "s2": "階段 2：默契大考驗", "s3": "階段 3：密旨傳遞", "s4": "階段 4：宮廷謎題", "s5": "階段 5：兵臨城下", "s6": "階段 6：論功行賞", "end": "遊戲結束"}


CSS = """<style>
[data-testid="stHeader"], footer, #MainMenu {display:none !important}
.stApp{background:repeating-linear-gradient(90deg,rgba(0,0,0,.07) 0 2px,transparent 2px 52px),
 linear-gradient(#6e1115,#b3261e 45%,#7e171a);color:#fbf0dc;font-family:'Noto Serif TC','PMingLiU',serif}
.block-container{max-width:560px;padding-top:.6rem;padding-bottom:3rem}
[data-testid="stMarkdownContainer"] p, label, [data-testid="stWidgetLabel"] p{color:#fbf0dc}
[data-testid="stTextInput"] input{background:#fff6e6;color:#3a1c1c;border:2px solid #e3b04b;border-radius:10px;font-size:18px}
[data-testid="stButton"] button, [data-testid="stFormSubmitButton"] button, [data-testid="stDownloadButton"] button{
 width:100%;font-weight:900;font-size:18px;color:#5c0f12;background:linear-gradient(#ffe9a8,#e3b04b);
 border:3px solid #fff3c4;border-radius:999px;box-shadow:0 5px 0 #8a5a12;padding:.45rem 1rem}
[data-testid="stButton"] button:disabled{opacity:.45}
[data-testid="stButton"] button:not([data-testid="stBaseButton-primary"]) p,[data-testid="stFormSubmitButton"] button p,
[data-testid="stDownloadButton"] button p{color:#4a2b17 !important}
/* 選項小膠囊：radio 改成可點的膠囊，選中的是金底 */
[data-testid="stRadio"] [role="radiogroup"]{gap:8px;flex-wrap:wrap}
[data-testid="stRadio"] label{background:rgba(0,0,0,.22);border:2px solid #e3b04b;border-radius:999px;padding:6px 16px;
 margin:0;cursor:pointer;transition:transform .1s,background .15s}
[data-testid="stRadio"] label>div:first-child{display:none}
[data-testid="stRadio"] label:has(input:checked){background:linear-gradient(#ffe9a8,#e3b04b);box-shadow:0 3px 0 #8a5a12;
 transform:translateY(-1px)}
[data-testid="stRadio"] label:has(input:checked) p{color:#4a2b17 !important;font-weight:900}
[data-testid="stRadio"] label p{font-size:16px}
.sec{display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 8px;margin:16px 0 6px;padding-left:10px;border-left:5px solid #e3b04b}
.sec .ic{font-size:20px}.sec b{font-size:18px;letter-spacing:.12em;color:#fff3c4}
.sec small{flex-basis:100%;color:#f7c6c9;font-size:13px;letter-spacing:.06em}
[data-testid="stForm"]{background:rgba(0,0,0,.18);border:2px dashed rgba(227,176,75,.6) !important;border-radius:14px;padding:6px 14px 14px}
.bar{height:18px;background:rgba(0,0,0,.28);border-radius:9px;overflow:hidden;border:2px solid #e3b04b;margin:4px 0 10px}
.bar i{display:block;height:100%;background:linear-gradient(#ffe9a8,#e3b04b)}
.bar.def i{background:linear-gradient(#a9cdbd,#2f6f6a)}
.rk{width:100%;border-collapse:collapse;font-size:15px}.rk td{padding:5px 6px;border-bottom:1px solid #e3d2b0}
.rk tr.me td{background:#ffe9a8;font-weight:900}
/* 右側中上方：齒輪（設定）與小房間（放在倒數框下方，避免蓋住倒數與任務提示） */
.st-key-gearbox{position:fixed;top:22vh;right:6px;z-index:995;width:44px !important;gap:8px}
.st-key-gearbox [data-testid="stButton"] button{width:42px;height:42px;min-height:42px;opacity:.93;padding:0;border-radius:50%;font-size:20px;
 box-shadow:0 3px 0 #8a5a12;border-width:2px}
.st-key-ambbox{position:fixed;width:0;height:0;overflow:hidden;opacity:0;pointer-events:none}
/* 暫停：半透明浮窗蓋住目前畫面（畫面本身不跳走） */
.pausefx{position:fixed;inset:0;z-index:990;background:rgba(40,10,10,.55);display:flex;align-items:center;justify-content:center;
 backdrop-filter:blur(1.5px)}
.pausefx>div{background:rgba(251,240,220,.92);color:#5c0f12;border:3px solid #e3b04b;border-radius:16px;padding:18px 26px;text-align:center;
 box-shadow:0 8px 24px rgba(0,0,0,.35)}
.pausefx b{display:block;font-size:22px;letter-spacing:.1em}.pausefx small{display:block;margin-top:6px;font-size:14px;color:#7a3b2a}
/* 設定視窗：左側選單直排，右側內容；手機上也維持左右兩欄 */
div[role="dialog"]{background:#4a0d10 !important;border:3px solid #e3b04b !important}
div[role="dialog"] [data-testid="stHorizontalBlock"]{flex-wrap:nowrap;gap:12px}
div[role="dialog"] [data-testid="stHorizontalBlock"]>div:first-child{min-width:96px;max-width:34%}
.st-key-set_menu [role="radiogroup"]{flex-direction:column;flex-wrap:nowrap}
.st-key-set_menu label{border-radius:10px !important;width:100%}
.end-card{background:linear-gradient(#fff8e8,#f3dfb8);color:#3a1c1c;border:4px solid #e3b04b;border-radius:18px;padding:16px;margin:10px 0;
 box-shadow:0 6px 0 #8a5a12;position:relative}
.end-card h3{margin:4px 0;font-size:24px;letter-spacing:.12em;color:#8a1a1a}.end-card .ttl{display:inline-block;background:#b3261e;color:#fff3c4;
 border-radius:999px;padding:3px 14px;font-weight:900;margin:4px 0}
.end-card .row{margin:8px 0;padding-top:8px;border-top:1px dashed #d8b980;font-size:15px}.end-card .row b{color:#8a1a1a}
.wall{background:rgba(0,0,0,.2);border:2px solid #e3b04b;border-radius:14px;padding:10px 14px;margin:10px 0}
.wall div{padding:6px 0;border-bottom:1px dashed rgba(227,176,75,.5)}
.oath{text-align:center;font-size:28px;font-weight:900;letter-spacing:.3em;color:#fff3c4;text-shadow:0 2px 0 #8a1a1a;margin:16px 0}
.rankcard{display:flex;align-items:center;gap:12px;background:#fbf0dc;color:#3a1c1c;border:3px solid #e3b04b;border-radius:14px;padding:10px 14px;margin:8px 0}
.rankcard .rk{font-size:28px;font-weight:900;color:#b3261e;width:46px;text-align:center}.rankcard.me{box-shadow:0 0 0 4px #ffe9a8}
.rankcard .fh{display:inline-block;background:#b3261e;color:#fff3c4;border-radius:6px;padding:1px 8px;font-size:13px;margin-left:6px}
/* 古風提示框：兩端卷軸、米色宣紙、紅色「諭」字小印 */
.scrollnote{display:flex;align-items:stretch;margin:6px 0 10px;filter:drop-shadow(0 3px 4px rgba(0,0,0,.35));animation:snin .4s ease-out}
@keyframes snin{from{transform:scaleX(.6);opacity:0}to{transform:none;opacity:1}}
.scrollnote .sn-l,.scrollnote .sn-r{width:12px;border-radius:6px;background:linear-gradient(90deg,#6b3e1f,#c48a4a,#6b3e1f);border:1px solid #3a1c0c}
.scrollnote .sn-t{flex:1;background:linear-gradient(#fff8e8,#f3dfb8);border-top:2px solid #b8860b;border-bottom:2px solid #b8860b;padding:8px 12px;
 color:#4a2b17;font-weight:900;font-size:16px;letter-spacing:.06em;line-height:1.5}
.scrollnote .sn-t b{display:inline-block;margin-right:8px;background:#b3261e;color:#fff3c4;border-radius:4px;padding:0 6px;transform:rotate(-6deg)}
/* 兵臨城下：衝向城門時，畫面下方的兵馬奔騰（不擋點擊） */
.st-key-stampbox{position:fixed;left:0;right:0;bottom:0;height:45vh;z-index:900;pointer-events:none}
.st-key-stampbox iframe{width:100vw !important;height:45vh !important}
/* 論功行賞：頒獎動畫 */
.award{position:relative;overflow:hidden;background:radial-gradient(circle at 50% 30%,#fff3c4,#e9c98f 70%);border:3px solid #e3b04b;border-radius:16px;
 padding:10px 6px 0;margin:8px 0;height:190px}
.award .crown{position:absolute;left:50%;top:6px;font-size:34px;transform:translateX(-50%);animation:crown 1.2s ease-out both, cbob 1.4s 1.2s ease-in-out infinite alternate}
@keyframes crown{from{transform:translate(-50%,-80px) rotate(-30deg);opacity:0}to{transform:translateX(-50%);opacity:1}}
@keyframes cbob{to{transform:translate(-50%,-6px)}}
.award .pod{position:absolute;bottom:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;width:31%;border-radius:8px 8px 0 0;
 color:#fff3c4;font-weight:900;animation:rise .9s cubic-bezier(.3,1.4,.5,1) both}
.award .pod b{font-size:22px}.award .pod small{font-size:12px;padding:0 4px;text-align:center;line-height:1.3}
.award .p1{left:34.5%;height:110px;background:linear-gradient(#e0a92c,#a8740d);animation-delay:.6s}
.award .p2{left:2%;height:80px;background:linear-gradient(#bfc4cc,#7f8794);animation-delay:.3s}
.award .p3{right:2%;height:62px;background:linear-gradient(#cd8a4b,#8f5524);animation-delay:0s}
@keyframes rise{from{transform:translateY(100%)}to{transform:none}}
.award i{position:absolute;top:-12px;width:8px;height:12px;border-radius:2px;animation:conf 2.6s linear infinite}
@keyframes conf{to{transform:translateY(220px) rotate(720deg)}}
/* 趣味稱號：紅字＋小插圖 */
.ttl-red{color:#c0101a !important;font-weight:900}
.tline{display:flex;align-items:center;gap:10px;margin:8px 0}.tline svg{flex:none;width:58px;height:58px;animation:tbob 1.2s ease-in-out infinite alternate}
@keyframes tbob{from{transform:translateY(0) rotate(-4deg)}to{transform:translateY(-5px) rotate(4deg)}}
.namecard{position:relative;text-align:center;padding-top:16px}
.namecard .nc-seal{position:absolute;top:-10px;right:12px;width:34px;height:34px;line-height:34px;text-align:center;font-weight:900;
 color:#fff3c4;background:#b3261e;border:2px solid #fff3c4;border-radius:6px;transform:rotate(8deg);box-shadow:0 2px 5px rgba(0,0,0,.4)}
/* 選擇題下方的功能鈕（換題權、免答權、跳過、技能）：橙色，和黃色的選項鈕區分 */
[class*="st-key-swap-"] button,[class*="st-key-free-"] button,[class*="st-key-skip-"] button,[class*="st-key-skill-"] button,[class*="st-key-s5sk-"] button{
 background:linear-gradient(#ffb066,#f07a1a) !important;border-color:#ffe0bd !important;box-shadow:0 5px 0 #8a3d0a !important}
[class*="st-key-swap-"] button p,[class*="st-key-free-"] button p,[class*="st-key-skip-"] button p,[class*="st-key-skill-"] button p,[class*="st-key-s5sk-"] button p{color:#3b1a08 !important}
.st-key-fxbox,.st-key-cutbox{position:fixed;inset:0;pointer-events:none;height:100vh}
.st-key-fxbox{z-index:60}.st-key-cutbox{z-index:80}
.st-key-fxbox iframe,.st-key-cutbox iframe{position:fixed;inset:0;width:100vw;height:100vh !important;border:0;pointer-events:none;background:transparent}
[data-testid="stTabs"] button p{color:#fbf0dc;font-weight:700;font-size:17px}
.gt{text-align:center;margin:.2rem 0 .6rem}
.gt small{letter-spacing:.5em;color:#f7c6c9;font-size:13px}
.gt h1{margin:2px 0;font-size:46px;letter-spacing:.12em;font-weight:900;background:linear-gradient(#fff3c4,#e3b04b 60%,#b9801f);
 -webkit-background-clip:text;background-clip:text;color:transparent !important;filter:drop-shadow(0 3px 0 #5c0f12)}
.gt p{margin:0;font-size:14px;color:#fde9d2}
.gcard{background:#fbf0dc;border:3px solid #e3b04b;border-radius:14px;padding:12px 16px;margin:10px 0;box-shadow:0 8px 20px rgba(0,0,0,.35)}
.gcard, .gcard *{color:#3a1c1c}
.gcard .red{color:#a3182a;font-weight:900}
.chip{display:inline-block;background:#fff6e6;border:2px solid #e3b04b;border-radius:999px;padding:3px 12px;margin:3px;font-weight:700}
.chip.bot{opacity:.75;border-style:dashed}
.chip.dk{color:#4a2b17}
.big{font-size:44px;font-weight:900;letter-spacing:.25em;color:#a3182a}
.badge{display:inline-block;background:#b3261e;color:#fff3c4 !important;border:2px solid #e3b04b;border-radius:6px;padding:2px 12px;font-weight:900;letter-spacing:.2em}
.timer{text-align:center;font-weight:900;font-size:20px;letter-spacing:.15em;color:#fff3c4;background:rgba(0,0,0,.25);
 border:2px solid #e3b04b;border-radius:999px;padding:4px 10px;margin:6px 0}
.timer.soft{font-size:15px;letter-spacing:.12em;color:rgba(255,243,196,.8);background:rgba(0,0,0,.12);
 border:1px solid rgba(227,176,75,.5);opacity:.85;padding:3px 10px}
.trow{display:flex;align-items:center;gap:8px;margin:6px 0}
.trow .timer{flex:1;margin:0}
.act{flex:none;font-weight:900;font-size:16px;letter-spacing:.12em;color:#5c0f12;background:linear-gradient(#ffe27a,#ff9d2e);
 border:2px solid #fff3c4;border-radius:999px;padding:3px 14px;box-shadow:0 0 12px rgba(255,190,70,.75)}
.gcard .pn,.pn{color:#1a7f37;font-weight:900}
.gcard .ask{margin-top:6px;font-size:16px;font-weight:900;line-height:1.6;color:#a3182a;text-align:center}
.gcard .sub{margin-top:4px;font-size:15px;line-height:1.6;color:#7a3b2e;text-align:center}
.qtext{font-size:22px;font-weight:900;line-height:1.6;text-align:center;color:#3a1c1c}
.gcard .green,.green{color:#1a7f37;font-weight:900}
.nw{white-space:nowrap;display:inline-block}
.orow{display:flex;align-items:center;gap:10px;padding:6px 2px}
.orow+.orow{border-top:1px dashed #e8d6b0}
.orow .lt{flex:none;width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;
 font-size:17px;font-weight:900;color:#fff3c4;background:radial-gradient(circle at 35% 30%,#c0392b,#8a1a1a)}
.orow .on{flex:1;min-width:0;font-size:18px;font-weight:900;line-height:1.4}
.orow .on i{display:block;font-style:normal;font-size:14px;font-weight:900;color:#1a7f37;line-height:1.5}
.orow .oc{flex:none;font-size:20px;font-weight:900;color:#a3182a}
.orow.win{background:#fff6e6;border-radius:10px;box-shadow:inset 0 0 0 2px #e3b04b}
.orow.win .lt{background:radial-gradient(circle at 35% 30%,#2fa75a,#1a7f37)}
.clue{user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}
.clue .cn{font-size:15px;font-weight:900;letter-spacing:.18em;color:#8a5a3a;text-align:center}
.clue .cs{font-size:14px;line-height:1.6;color:#7a3b2e;text-align:center;margin-top:2px}
.clue .ct{font-size:19px;font-weight:900;line-height:1.8;color:#3a1c1c;margin-top:10px;
 padding:6px 10px;background:#fff6e6;border-radius:8px;border-left:5px solid #e3b04b}
.gcard .q2{font-size:18px;font-weight:900;line-height:1.7;color:#8a5a3a;margin-top:4px}
.opt-note{font-size:14px;color:#7a3b2e;text-align:center}
.gcard .rowc{display:flex;justify-content:space-between;gap:8px;padding:2px 0}
.tbl{width:100%;border-collapse:collapse;font-size:15px}
.tbl th,.tbl td{border:1px solid #d9b36a;padding:4px 6px;text-align:center}
.mem{padding:6px 0;border-bottom:1px dashed #d9b36a}
.mem:last-child{border-bottom:0}
.mcard{display:flex;gap:12px;align-items:center}
.mcard .av{width:54px;height:54px;border:2px solid #e3b04b}
.mcard .mi{flex:1;min-width:0;line-height:1.6}
.skill{margin:10px 0;border:3px solid #e3b04b;border-radius:14px;overflow:hidden;box-shadow:0 8px 20px rgba(0,0,0,.35);
 background:linear-gradient(135deg,var(--a),var(--b));color:#3a1c1c}
.skill .sh{display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--c);color:#fff3c4}
.skill .sh .av{width:54px;height:54px;border:3px solid #fff3c4;box-shadow:0 2px 6px rgba(0,0,0,.4)}
.skill .st{flex:1;min-width:0}
.skill .st small{display:block;font-size:12px;letter-spacing:.25em;color:#fff3c4}
.skill .st b{display:block;font-size:22px;letter-spacing:.18em;color:#fff3c4}
.skill .seal{flex:none;font-size:13px;font-weight:900;letter-spacing:.1em;padding:3px 8px;border:2px solid #fff3c4;border-radius:6px;
 transform:rotate(6deg);background:#b3261e;color:#fff3c4}
.skill .seal.off{background:#6b6b6b}
.skill .sb{padding:10px 14px 12px;font-size:14px;line-height:1.7;color:#3a1c1c}
.skill .sb i{display:block;margin-bottom:4px;color:#8a3a3a}
.skipbox{margin:14px 0 4px;text-align:center;font-weight:900;letter-spacing:.08em;color:#fff3c4;background:rgba(0,0,0,.28);
 border:2px dashed #ffd36b;border-radius:10px;padding:6px 10px}
.st-key-skip button,[data-testid="stButton"] button[kind="primary"],[data-testid="stBaseButton-primary"]{
 background:linear-gradient(#ff6b4a,#c4261e) !important;color:#fff8e0 !important;border:3px solid #ffe9a8 !important;
 box-shadow:0 5px 0 #6e1115,0 0 18px rgba(255,120,60,.7) !important;font-size:19px !important;animation:skp 1.8s ease-in-out infinite}
.st-key-skip button p,[data-testid="stBaseButton-primary"] p{color:#fff8e0 !important}
.st-key-skip button:disabled,[data-testid="stBaseButton-primary"]:disabled{animation:none}
@keyframes skp{50%{box-shadow:0 5px 0 #6e1115,0 0 30px rgba(255,214,120,.95)}}
</style>"""


def esc(s):
    return html.escape(str(s), quote=True)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def default_base():
    env = os.environ.get("GAME_BASE_URL")
    if env:
        return env.rstrip("/")
    try:
        port = st.get_option("server.port") or 8501
    except Exception:
        port = 8501
    return f"http://{lan_ip()}:{port}"


def qr_box(url):
    """房主大廳的 QR 區：上半「1 讓玩家掃 QR」→ QR code → 下半「2 在瀏覽器輸入網址」＋複製鈕。
    做成一個小元件（內含複製按鈕的程式），外框樣式與頁面上的 .gcard 相同。"""
    qr = qr_img(url) or '<div style="font-size:14px;color:#8a3a3a">（QR code 產生失敗，請改用下方網址）</div>'
    page = """<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;background:transparent}
body{font-family:'Noto Serif TC','PMingLiU',serif;color:#3a1c1c;padding:4px 6px 16px}
.box{background:#fbf0dc;border:3px solid #e3b04b;border-radius:14px;padding:14px 16px 16px;text-align:center;box-shadow:0 8px 20px rgba(0,0,0,.35)}
.st{font-size:18px;font-weight:900;letter-spacing:.08em}
.st i{display:inline-block;width:26px;height:26px;line-height:26px;border-radius:50%;background:#b3261e;color:#fff3c4;font-style:normal;margin-right:8px;font-size:16px}
.qr{margin:12px 0 14px}
.sep{border-top:2px dashed #d9b36a;margin:0 0 14px}
.url{margin:10px 0 12px;padding:8px 10px;background:#fff6e6;border:2px solid #e3b04b;border-radius:10px;font-size:17px;font-weight:900;word-break:break-all;color:#a3182a}
button{font-family:inherit;width:100%;font-weight:900;font-size:18px;color:#5c0f12;background:linear-gradient(#ffe9a8,#e3b04b);
 border:3px solid #fff3c4;border-radius:999px;box-shadow:0 5px 0 #8a5a12;padding:.45rem 1rem;cursor:pointer}
button:active{transform:translateY(3px);box-shadow:0 2px 0 #8a5a12}
</style></head><body><div class="box">
<div class="st"><i>1</i>讓玩家用手機掃描 QR code</div>
<div class="qr">__QR__</div>
<div class="sep"></div>
<div class="st"><i>2</i>或在瀏覽器輸入網址</div>
<div class="url" id="u">__URL__</div>
<button id="b">複製網址</button>
</div><script>
const b=document.getElementById('b'),t=document.getElementById('u').textContent;
function done(){b.textContent='已複製 ✓';setTimeout(()=>b.textContent='複製網址',1800)}
function fb(){const a=document.createElement('textarea');a.value=t;a.style.cssText='position:fixed;opacity:0';document.body.appendChild(a);a.select();
 try{document.execCommand('copy');done()}catch(e){b.textContent='請長按網址複製'}document.body.removeChild(a)}
b.onclick=()=>{if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done,fb)}else fb()};
</script></body></html>"""
    components.html(page.replace("__QR__", qr).replace("__URL__", esc(url)), height=560)


def qr_img(url):
    try:
        import segno
        uri = segno.make(url, error="m").png_data_uri(scale=6, border=2)
        return f'<img src="{uri}" style="width:210px;background:#fff;padding:6px;border-radius:10px">'
    except Exception:
        return ""


@st.cache_resource
def boot():
    db.init()
    db.pause_all_on_boot()
    return True


def title_block(sub=""):
    st.markdown(f'<div class="gt"><small>穿越・後宮・破冰</small><h1>夢回宮闕</h1><p>{esc(sub)}</p></div>',
                unsafe_allow_html=True)


def go_home():
    st.query_params.clear()
    st.rerun()


def _poll(code, token, kind, fp):
    changed = False
    try:
        if token:
            db.touch(token)
        db.tick(code)
        changed = db.fingerprint(code, kind, token if kind == "player" else None) != fp
        cut = st.session_state.get("cut_until")
        if cut and time.time() >= cut and not st.session_state.get("cut_paused"):   # 轉場動畫播完：換成後面的活動
            st.session_state.pop("cut_until", None)
            changed = True
    except Exception:   # noqa: BLE001  任何暫時性錯誤（資料庫忙碌等）都等下一秒再試，不讓畫面跳出錯誤框
        return
    if changed:
        st.rerun()


@st.fragment(run_every=2)
def poller_slow(code, token, kind, fp):
    """每 2 秒檢查一次：更新心跳、推進階段；畫面需要變時整頁重新整理。"""
    _poll(code, token, kind, fp)


@st.fragment(run_every=1)
def poller_fast(code, token, kind, fp):
    """有倒數的階段改為每 1 秒檢查一次。"""
    _poll(code, token, kind, fp)


def poller(code, token, kind, fp, phase=""):
    (poller_fast if phase in ("s1", "s2", "s3", "s4", "s5", "s6") else poller_slow)(code, token, kind, fp)


CUT_SEC = 3.0   # 轉場動畫長度（秒）
BADGE_ICON = {"關鍵線索": "🔑", "帶大家整理": "🧭", "提出好問題": "❓", "讓氣氛放鬆": "🍃"}



def cut_in(scene, title, start_ts, now, sub=""):
    """環節開頭的轉場動畫。播放的約 3 秒內，畫面上只有動畫（呼叫的人看到回傳 True 就不要再畫其他內容），
    播完會自動重新整理並接上後面的活動。回傳 False 代表不在播放時間內。"""
    el = now - start_ts
    if not 0 <= el < CUT_SEC:
        return False
    with st.container(key="cutbox"):
        _cut(scene=scene, title=T(title), sub=T(sub), elapsed=round(el, 2), key=f"cut-{scene}-{int(start_ts)}")
    st.session_state["cut_until"] = start_ts + CUT_SEC       # 由每秒的輪詢在動畫結束時重新整理
    st.session_state["cut_paused"] = bool(_FROZEN.get("at"))
    return True


def timer_bar(end_ts, label, soft=False, frozen=None, act=None):
    """倒數框。soft=True 是較淡的樣式；frozen 有值代表遊戲暫停，時間凍結在該秒數；act 是旁邊的任務提示字。
    數字由手機端自己每 0.25 秒更新（不再讓伺服器每秒重畫），所以不會閃動，也不會出現重整錯誤。"""
    if frozen is None and _FROZEN.get("at"):
        frozen = max(0.0, end_ts - _FROZEN["at"])   # 玩家畫面：暫停中倒數停在暫停那一秒
    _timer(end=float(end_ts), now=time.time(), label=T(label), soft=bool(soft), act=act or "",
           frozen=None if frozen is None else float(frozen), left_word=T("剩餘"), tail=T("　（已暫停）") if frozen is not None else "",
           key="timer", default=None)


# ======================= 介面語言（第一版：介面外框中英切換） =======================
_FROZEN = {}          # 暫停中：{"at": 暫停時間}，倒數與畫面都停在這一秒
_LANG = {"v": "zh"}
EN = {
    "設定": "Settings", "房間": "Room", "基本設定": "Basics", "遊戲調整": "Game setup", "房間資訊": "Room info",
    "我的資料": "My info", "遊戲說明": "How to play", "背景音樂音量": "Music volume", "語言": "Language",
    "飄花動畫": "Falling petals", "儲存設定": "Save", "已儲存": "Saved", "加入房間": "Join a room", "建立新房間": "Create a new room",
    "房號（6 位數字）": "Room code (6 digits)", "前往": "Go", "建立": "Create", "宮廷謎題難度": "Puzzle difficulty",
    "入宮": "Join", "你的暱稱（最多 12 字）": "Your nickname (max 12)", "入　宮": "Enter the palace",
    "已入宮，等待房主開始": "You're in! Waiting for the host to start", "你的暱稱": "Nickname", "目前已入宮": "Players joined",
    "請保持這個畫面開啟，房主按下開始後會自動進入遊戲。請不要截圖分享網址。":
        "Keep this page open. The game starts automatically when the host presses Start. Please don't share your URL.",
    "房主暫停了遊戲": "The host paused the game", "請稍候，繼續後會從這裡接著進行": "Please wait — the game will resume right here",
    "剩餘": "left", "　（已暫停）": " (paused)", "（你）": " (you)", "房主控制台": "Host console",
    "找不到這個房號，請確認後再試一次。": "Room not found. Please check the code.",
    "這一場已經開始了，無法中途加入。": "This game has already started.", "房間已關閉，謝謝你的參與！": "The room is closed. Thanks for playing!",
    "回到首頁": "Home", "離開目前畫面前，建議先把目前網址加入書籤，之後才能回來。":
        "Bookmark the current URL first if you want to come back to this game.",
    "默契大考驗": "Mind Match", "傳遞密旨": "Pass the Decree", "宮廷謎題": "Palace Puzzle", "兵臨城下": "Siege",
    "論功行賞": "Rewards & Farewell", "冊封大典": "Ennoblement", "分宮揭曉": "Palace Reveal", "填寫名帖": "Name Card",
    "宮內相認": "Meet Your Palace", "中文": "中文", "English": "English",
    "（英文版目前只翻譯介面外框，題目與遊戲內容仍是中文）": "(Beta: only the interface frame is translated; game content is still in Chinese)",
}


def T(s):
    """依目前語言回傳文字（英文表沒有的字就維持中文）。"""
    return EN.get(s, s) if _LANG["v"] == "en" else s


def load_prefs(kind, code, token):
    """讀取個人設定：玩家／房主存在資料庫，還沒入宮的訪客只存在這個瀏覽器分頁。"""
    if kind == "host":
        p = db.get_prefs(code, host_token=token)
    elif kind == "player":
        p = db.get_prefs(code, token=token)
    else:
        p = dict(db.PREF_DEFAULT, **st.session_state.get("guest_prefs", {}))
    _LANG["v"] = p.get("lang", "zh")
    return p


def save_prefs(kind, code, token, prefs):
    if kind == "host":
        db.set_prefs(code, prefs, host_token=token)
    elif kind == "player":
        db.set_prefs(code, prefs, token=token)
    else:
        st.session_state["guest_prefs"] = dict(st.session_state.get("guest_prefs", {}), **prefs)


def basic_panel(kind, code, token, prefs):
    sec("🎵", T("背景音樂音量"), "0 = 靜音；第一次點畫面後開始播放" if _LANG["v"] == "zh" else "0 = mute; starts after your first tap")
    vol = st.slider(T("背景音樂音量"), 0, 100, int(prefs["vol"]), step=5, label_visibility="collapsed", key="pf_vol")
    sec("🌐", T("語言"), T("（英文版目前只翻譯介面外框，題目與遊戲內容仍是中文）"))
    lang = st.radio(T("語言"), ["zh", "en"], index=0 if prefs["lang"] == "zh" else 1, horizontal=True,
                    format_func=lambda x: "中文" if x == "zh" else "English", label_visibility="collapsed", key="pf_lang")
    sec("🌸", T("飄花動畫"), "畫面上的飄花，覺得干擾可以關掉" if _LANG["v"] == "zh" else "Turn off if distracting")
    pet = st.toggle(T("飄花動畫"), value=bool(prefs["petals"]), key="pf_pet")
    if st.button(T("儲存設定"), key="pf_save"):
        save_prefs(kind, code, token, {"vol": vol, "lang": lang, "petals": pet})
        st.rerun()


def cfg_panel(code, token):
    r = db.get_room(code)
    cfg = db.room_cfg(r)
    locked = r["phase"] != "lobby"
    if locked:
        st.info("遊戲已經開始，這一場的環節與局數不能再改。下一場建立新房間時可以調整。")
    sec("🧩", "宮廷謎題難度", "易／中／難，各有不同的謎題與限時")
    diff = st.radio("宮廷謎題難度", ["易", "中", "難"], index=["易", "中", "難"].index(r["difficulty"]) if r["difficulty"] in ("易", "中", "難") else 1,
                    horizontal=True, label_visibility="collapsed", key="cf_diff", disabled=locked)
    st.markdown('<div class="sub" style="margin-top:8px">心理測驗、冊封（分宮）、論功行賞是整場的骨架，固定進行。</div>', unsafe_allow_html=True)
    new = {}
    for ph, hint in (("s2", "每局約 1 分 40 秒"), ("s3", "每局約 1 分 40 秒"), ("s4", "目前一場只有一道謎題，約 5 分鐘"),
                     ("s5", "常識題騎兵競速，約 2 分 40 秒")):
        sec({"s2": "💭", "s3": "💣", "s4": "🔍", "s5": "⚔️"}[ph], game.PHASE_NAME[ph], hint)
        opts = list(range(game.CFG_MAX[ph] + 1))
        new[ph] = st.radio(game.PHASE_NAME[ph], opts, index=cfg[ph], horizontal=True, label_visibility="collapsed",
                           format_func=lambda n: "不進行" if n == 0 else f"{n} 局", key=f"cf_{ph}", disabled=locked)
    st.markdown(f'<div class="gcard" style="text-align:center">預估全程約 <b>{game.total_minutes(new)}</b> 分鐘'
                f'（{"→".join(game.PHASE_NAME[p] for p in ["quiz"] + [x for x in game.PHASE_ORDER if game.rounds(new, x) > 0])}）</div>',
                unsafe_allow_html=True)
    if not locked and st.button("儲存遊戲調整", key="cf_save"):
        ok, msg = db.set_cfg(code, token, new, diff)
        st.session_state["flash"] = msg if not ok else "已儲存遊戲調整"
        st.rerun()


def guide_panel(code):
    cfg = db.room_cfg(code)
    lines = [("心理測驗", "十道情境題，決定你的宮廷角色"), ("冊封", "分宮、填名帖、和隊友相認")]
    desc = {"s2": "選出最貼近你的答案，看看誰和你想的一樣", "s3": "炸彈在宮內傳遞，爆炸時拿著的人回答問題，隊友猜他的答案",
            "s4": "每人一張線索卡，口頭交換資訊，一起解謎", "s5": "所有宮同時出兵的常識題騎兵競速", "s6": "全宮受封、趣味稱號、結尾互動與人物誌"}
    for ph in game.PHASE_ORDER[1:]:
        n = game.rounds(cfg, ph)
        if n:
            lines.append((game.PHASE_NAME[ph] + (f"（{n} 局）" if ph in ("s2", "s3") else ""), desc[ph]))
    st.markdown('<div class="gcard">' + "".join(f'<div style="margin:6px 0"><b>{esc(a)}</b><br><span class="sub">{esc(b)}</span></div>'
                                              for a, b in lines) + '</div>', unsafe_allow_html=True)
    st.caption("每個人整場有一次角色技能；兵力最高的宮在論功行賞受封「定國宮」。")


@st.dialog("⚙ 設定", width="large")
def settings_dialog(kind, code, token):
    prefs = load_prefs(kind, code, token)
    menus = [("basic", "🎵 " + T("基本設定"))]
    if kind == "host":
        menus += [("cfg", "🎮 " + T("遊戲調整")), ("room", "🏠 " + T("房間資訊"))]
    elif kind == "player":
        menus += [("me", "👤 " + T("我的資料"))]
    menus += [("guide", "📖 " + T("遊戲說明"))]
    left, right = st.columns([1, 2.4])
    with left:
        pick = st.radio("設定選單", [m[0] for m in menus], format_func=dict(menus).get, label_visibility="collapsed", key="set_menu")
    with right:
        flash_box()
        if pick == "basic":
            basic_panel(kind, code, token, prefs)
        elif pick == "cfg":
            cfg_panel(code, token)
        elif pick == "room":
            r = db.get_room(code)
            st.markdown(f'<div class="gcard">房號<div class="big">{esc(code)}</div>'
                        f'<div class="sub">玩家加入網址：{esc(default_base().rstrip("/"))}/?room={esc(code)}</div>'
                        f'<div class="sub">目前真人 {len(db.humans(code))} 位・狀態：{esc(PHASE_LABEL.get(r["phase"], r["phase"]))}</div></div>',
                        unsafe_allow_html=True)
        elif pick == "me":
            p = db.get_player(token)
            tm = next((t for t in db.teams_view(code) if t["idx"] == p["team"]), None) if p and p["team"] is not None else None
            role = game.ROLE_NAMES.get(p["role_main"], "（心理測驗後揭曉）") if p else ""
            st.markdown(f'<div class="gcard"><div class="sub">暱稱</div><b>{esc(p["name"] if p else "")}</b>'
                        f'<div class="sub">房號 {esc(code)}</div><div class="sub">角色：{esc(role)}</div>'
                        + (f'<div class="sub">所屬：{esc(tm["name"])}・目前兵力 {tm["score"]}</div>' if tm else "") + '</div>',
                        unsafe_allow_html=True)
        else:
            guide_panel(code)


@st.dialog("🏠 房間")
def room_dialog():
    pick = st.radio("房間", ["join", "create"], horizontal=True, label_visibility="collapsed", key="rm_pick",
                    format_func=lambda x: T("加入房間") if x == "join" else T("建立新房間"))
    st.caption(T("離開目前畫面前，建議先把目前網址加入書籤，之後才能回來。"))
    if pick == "join":
        code = st.text_input(T("房號（6 位數字）"), max_chars=6, key="rm_code")
        if st.button(T("前往"), key="rm_go"):
            r = db.get_room(code.strip())
            if not r or r["closed"]:
                st.error(T("找不到這個房號，請確認後再試一次。"))
            else:
                st.query_params.clear()
                st.query_params["room"] = code.strip()
                st.rerun()
    else:
        diff = st.radio(T("宮廷謎題難度"), ["易", "中", "難"], index=1, horizontal=True, key="rm_diff")
        if st.button(T("建立"), key="rm_new"):
            code, ht = db.create_room(diff)
            st.query_params.clear()
            st.query_params["room"] = code
            st.query_params["h"] = ht
            st.rerun()


def chrome(kind, code, token, room_btn=True, petals_ok=True):
    """每一頁都有：飄花與背景音樂、右上角齒輪（設定）與小房間（加入／建立房間）。"""
    prefs = load_prefs(kind, code, token)
    with st.container(key="ambbox"):
        _amb(vol=int(prefs["vol"]), petals=bool(prefs["petals"]) and petals_ok, key="ambient", default=None)
    with st.container(key="gearbox"):
        if st.button("⚙️", key="gear", help=T("設定")):
            settings_dialog(kind, code, token)
        if room_btn and st.button("🏠", key="roombtn", help=T("房間")):
            room_dialog()
    return prefs


def pause_overlay():
    st.markdown(f'<div class="pausefx"><div><b>⏸ {T("房主暫停了遊戲")}</b><small>{T("請稍候，繼續後會從這裡接著進行")}</small></div></div>',
                unsafe_allow_html=True)


def join_page(code):
    """掃 QR Code 或貼上房間連結進來：直接輸入暱稱，送出後在大廳等房主開始。"""
    chrome("guest", code, None, room_btn=False)
    title_block(T("入宮"))
    r = db.get_room(code)
    if not r or r["closed"]:
        st.warning(T("找不到這個房號，請確認後再試一次。") if not r else T("房間已關閉，謝謝你的參與！"))
        if st.button(T("回到首頁")):
            go_home()
        return
    if r["phase"] != "lobby":
        st.warning(T("這一場已經開始了，無法中途加入。"))
        if st.button(T("回到首頁")):
            go_home()
        return
    st.markdown(f'<div class="gcard" style="text-align:center">{T("房號（6 位數字）").split("（")[0]}<div class="big">{esc(code)}</div></div>',
                unsafe_allow_html=True)
    with st.form("join1", border=False):
        sec("🪪", T("你的暱稱（最多 12 字）"))
        name = st.text_input(T("你的暱稱（最多 12 字）"), max_chars=game.NAME_MAX, label_visibility="collapsed")
        go = st.form_submit_button(T("入　宮"))
    if go:
        ok, msg, token = db.join(code, name)
        if ok:
            gp = st.session_state.get("guest_prefs")
            if gp:
                db.set_prefs(code, gp, token=token)
            st.query_params["p"] = token
            st.rerun()
        else:
            st.error(msg)
    st.caption(VERSION)


# ======================= 首頁 =======================
def landing(prefill=""):
    _palace(mode="home", height=600, nonce="home", key="home")
    tab_p, tab_h = st.tabs(["我是玩家：加入房間", "我是房主：建立房間"])
    with tab_p:
        with st.form("join"):
            code = st.text_input("房號（6 位數字）", value=prefill or "", max_chars=6)
            name = st.text_input("你的暱稱（最多 12 字）", max_chars=game.NAME_MAX)
            go = st.form_submit_button("入　宮")
        if go:
            ok, msg, token = db.join(code.strip(), name)
            if ok:
                st.query_params["room"] = code.strip()
                st.query_params["p"] = token
                st.rerun()
            else:
                st.error(msg)
    with tab_h:
        st.write("房主只負責「開始、暫停、關閉」；遊戲開始後全程自動進行。")
        diff = st.radio("宮廷謎題難度（建房時選一次）", ["易", "中", "難"], index=1, horizontal=True)
        if st.button("建立房間"):
            code, ht = db.create_room(diff)
            st.query_params["room"] = code
            st.query_params["h"] = ht
            st.rerun()
    st.caption(VERSION)


# ======================= 房主 =======================
@st.dialog("人數不足")
def confirm_start(code, n):
    st.write(f"目前只有 {n} 位真人，不足 {game.MIN_PLAYERS} 人。")
    st.write(f"按下確認後，將由 {game.MIN_PLAYERS - n} 位機器人補足，然後開始遊戲。")
    c1, c2 = st.columns(2)
    if c1.button("確認開始"):
        db.start_game(code)
        st.rerun()
    if c2.button("取消"):
        st.rerun()


S1_NEXT = {"ennoble": "分宮揭曉", "reveal": "填寫名帖", "card": "宮內相認", "meet": "默契大考驗"}
S3_NEXT = {"relay": "接旨人作答", "answer": "隊友猜測", "guess": "公布與對話", "reveal": "緩衝", "buffer": "下一輪"}
S2_NEXT = {"vote": "全宮討論", "discuss": "改答案", "revise": "公布與對話"}


def next_step_label(r, ref):
    """房主「略過此環節」按鈕上顯示：按下後會進入哪一個環節。"""
    ph = r["phase"]
    cfg = db.room_cfg(r)
    nxt_name = game.PHASE_NAME.get(game.next_phase(ph, cfg), "") if ph in game.PHASE_ORDER else ""
    if ph == "quiz":
        return "冊封"
    if ph == "s1":
        return S1_NEXT.get(game.s1_step(ref - r["phase_start"])[0], nxt_name) if game.s1_step(ref - r["phase_start"])[0] != "meet" else nxt_name
    if ph == "s2":
        st_ = game.s2_step(ref, r["phase_start"])
        if st_["step"] in S2_NEXT:
            return S2_NEXT[st_["step"]]
        if st_["step"] == "reveal" and st_["rnd"] < game.rounds(cfg, "s2") - 1:
            return f"第 {st_['rnd'] + 2} 題　作答"
        return nxt_name
    if ph == "s3":
        st3 = game.s3_step(ref, r["phase_start"], db.s3_ts_room(r["code"]))
        last = st3["rnd"] >= game.rounds(cfg, "s3") - 1
        if st3["step"] == "buffer":
            return nxt_name if last else "下一輪"
        return S3_NEXT.get(st3["step"], nxt_name if last else "下一輪")
    if ph == "s5":
        st5 = game.s5_step(ref, r["phase_start"])
        if st5["step"] == "run":
            return f"第 {st5['q'] + 2} 題" if st5["q"] < game.S5_Q - 1 else "衝向城門"
        return S5_NEXT.get(st5["step"], nxt_name)
    if ph == "s4":
        key = db.s4_info(r["code"])["limit"]
        st4 = game.s4_step(ref, r["phase_start"], key)
        return {"prepare": "解謎", "solve": "貢獻分享"}.get(st4["step"], nxt_name)
    if ph == "s6":
        return {"rank": "趣味稱號", "title": "結尾互動", "ending": "宮廷人物誌", "card": "遊戲結束"}.get(game.s6_step(ref, r["phase_start"])["step"], "遊戲結束")
    return nxt_name


def teams_html(teams, show_bots=True):
    out = []
    for t in teams:
        rows = "".join(
            f'<div class="mem"><b>{esc(m["name"])}</b>'
            f'{"（NPC）" if m["is_bot"] else ""}　{esc(game.ROLE_ADJ.get(m["role_sub"], ""))}的'
            f'<span class="red">{esc(game.ROLE_NAMES.get(m["role_main"], ""))}</span></div>'
            for m in t["members"])
        out.append(f'<div class="gcard"><span class="badge">{esc(t["name"])}</span>'
                   f'<div style="margin:6px 0;font-style:italic">「{esc(t["slogan"])}」</div>{rows}</div>')
    return "".join(out)


def members_html(members, me_id=None, show_card=False):
    rows = []
    for m in members:
        extra = ""
        if show_card and not m["is_bot"]:
            d, h = db.card_of(m)
            x = db.card_extra(m)
            extra = (f'<br><span style="font-size:14px">愛喝：{esc(d)}　假日：{esc(h)}</span>'
                     + (f'<br><span style="font-size:14px">其他想說的：{esc(x)}</span>' if x else ""))
        tag = game.ROLE_TAG.get(m["role_main"], "")
        rows.append(
            f'<div class="mem"><b class="pn">{esc(m["name"])}</b>{"（你）" if m["id"] == me_id else ""}{"（NPC）" if m["is_bot"] else ""}'
            f'　{esc(game.ROLE_ADJ.get(m["role_sub"], ""))}的<span class="red">{esc(game.ROLE_NAMES.get(m["role_main"], ""))}</span>'
            f'<br><i style="font-size:14px">「{esc(tag)}」</i>{extra}</div>')
    return '<div class="gcard">' + "".join(rows) + "</div>"


def member_cards_html(members, me_id=None, show_card=False):
    """宮內相認用：每位隊友各自一張卡，名字前面是角色頭貼（內容文字與 members_html 相同）。"""
    out = []
    for m in members:
        extra = ""
        if show_card and not m["is_bot"]:
            d, h = db.card_of(m)
            x = db.card_extra(m)
            extra = (f'<br><span style="font-size:14px">愛喝：{esc(d)}　假日：{esc(h)}</span>'
                     + (f'<br><span style="font-size:14px">其他想說的：{esc(x)}</span>' if x else ""))
        tag = game.ROLE_TAG.get(m["role_main"], "")
        out.append(
            f'<div class="gcard mcard">{avatars.av(m["role_main"])}<div class="mi">'
            f'<b class="pn">{esc(m["name"])}</b>{"（你）" if m["id"] == me_id else ""}{"（NPC）" if m["is_bot"] else ""}'
            f'　{esc(game.ROLE_ADJ.get(m["role_sub"], ""))}的<span class="red">{esc(game.ROLE_NAMES.get(m["role_main"], ""))}</span>'
            f'<br><i style="font-size:14px">「{esc(tag)}」</i>{extra}</div></div>')
    return "".join(out)


def scores_table(rows, unit="題"):
    n = len(rows[0]["rounds"]) if rows else 3
    body = "".join(
        f'<tr><td>{esc(x["name"])}</td>'
        + "".join(f'<td>{"-" if v is None else v}</td>' for v in x["rounds"])
        + f'<td><b>{x["score"]}</b></td></tr>' for x in rows)
    head = "".join(f"<th>第{i + 1}{unit}</th>" for i in range(n))
    return f'<div class="gcard"><table class="tbl"><tr><th>宮</th>{head}<th>目前兵力</th></tr>' + body + "</table></div>"


def host_page(code, htoken):
    boot()
    if not db.check_host(code, htoken):
        st.error("房主連結無效或房間不存在。")
        if st.button("回到首頁"):
            go_home()
        return
    r = db.tick(code)
    fp = db.fingerprint(code, "host")
    chrome("host", code, htoken)
    flash_box()
    title_block(T("房主控制台"))
    st.markdown(f'<div class="gcard" style="text-align:center">房號<div class="big">{esc(code)}</div>'
                f'<div>謎題難度：{esc(r["difficulty"])}　狀態：<b>{esc(PHASE_LABEL.get(r["phase"], r["phase"]))}</b>'
                f'{"　（已暫停）" if r["paused"] else ""}</div></div>', unsafe_allow_html=True)
    hs = db.humans(code)
    if r["closed"]:
        st.warning("房間已關閉。")
        if st.button("回到首頁"):
            go_home()
        return
    now = time.time()
    paused = bool(r["paused"])
    ref = min(now, r["paused_at"]) if (paused and r["paused_at"]) else now   # 暫停中時間是靜止的

    def frozen_for(end_ts):
        return max(0.0, end_ts - ref) if paused else None

    if r["phase"] == "lobby":
        qr_box(f"{default_base().rstrip('/')}/?room={code}")
        cfg = db.room_cfg(r)
        st.markdown(f'<div class="gcard" style="text-align:center"><div class="sub">本場環節（右上角 ⚙️ →「遊戲調整」可修改）</div>'
                    f'{"→".join(game.PHASE_NAME[x] + (f"×{game.rounds(cfg, x)}" if x in ("s2", "s3") else "") for x in ["quiz"] + [y for y in game.PHASE_ORDER if game.rounds(cfg, y) > 0])}'
                    f'<div class="sub">預估約 {game.total_minutes(cfg)} 分鐘</div></div>', unsafe_allow_html=True)
        st.markdown(f"**目前真人 {len(hs)} 位**（最多 {game.MAX_PLAYERS} 位；不足 {game.MIN_PLAYERS} 位時由機器人補足）")
        for pl in hs:
            c1, c2 = st.columns([4, 2])
            c1.markdown(f'<span class="chip dk">{esc(pl["name"])}</span>', unsafe_allow_html=True)
            if c2.button("移除", key=f"rm{pl['id']}"):
                db.remove_player(code, pl["id"])
                st.rerun()
        if st.button("開始遊戲", disabled=len(hs) < 1):
            if len(hs) < game.MIN_PLAYERS:
                confirm_start(code, len(hs))
            else:
                db.start_game(code)
                st.rerun()
        if len(hs) < 1:
            st.caption("至少要有 1 位玩家加入才能開始。")
    elif r["phase"] == "quiz":
        done = sum(1 for x in hs if x["quiz_done"])
        st.markdown(f'<div class="gcard">真人 {len(hs)} 位，已完成心理測驗 <b>{done}</b> 位。</div>', unsafe_allow_html=True)
        timer_bar(r["deadline"], "心理測驗", frozen=frozen_for(r["deadline"]))   # 每秒更新（不再靠整頁重新整理）
    elif r["phase"] == "s1":
        name, idx, t0, t1 = game.s1_step(ref - r["phase_start"])
        label = {"ennoble": "冊封", "reveal": "分宮揭曉", "card": "填寫名帖", "meet": "宮內相認"}.get(name, "準備進入下一階段")
        timer_bar(r["phase_start"] + t1, label, frozen=frozen_for(r["phase_start"] + t1))
        st.markdown(teams_html(db.teams_view(code)), unsafe_allow_html=True)
    elif r["phase"] == "s2":
        st_ = game.s2_step(ref, r["phase_start"])
        label = {"vote": "作答", "discuss": "全宮討論", "revise": "改答案", "reveal": "公布與對話", "done": "結算"}[st_["step"]]
        qidx = __import__("json").loads(r["s2_q"] or "[]")
        q, qopts = game.S2_BANK[qidx[st_["rnd"]]] if len(qidx) > st_["rnd"] else ("", [])
        olist = "　".join(f'{game.S2_LETTER[i]} {esc(o)}' for i, o in enumerate(qopts))
        st.markdown(f'<div class="gcard"><div style="text-align:center"><b>第 {st_["rnd"] + 1} / {game.rounds(db.room_cfg(r), "s2")} 題</b></div>'
                    f'<div class="qtext">{esc(q)}</div><div class="sub">{olist}</div></div>', unsafe_allow_html=True)
        timer_bar(st_["step_end"], label, frozen=frozen_for(st_["step_end"]))
        st.markdown(scores_table(db.host_scores(code)), unsafe_allow_html=True)
        st.caption("各宮可因角色技能延長自己的討論或改答案時間，所以每宮進度可能略有不同。")
    elif r["phase"] == "s3":
        st3 = game.s3_step(ref, r["phase_start"], db.s3_ts_room(code))
        label = {"relay": "傳遞密旨", "answer": "接旨人作答", "guess": "隊友猜測",
                 "reveal": "公布與對話", "buffer": "緩衝", "done": "結算"}[st3["step"]]
        st.markdown(f'<div class="gcard" style="text-align:center"><b>第 {st3["rnd"] + 1} / {game.rounds(db.room_cfg(r), "s3")} 輪　密旨傳遞</b></div>',
                    unsafe_allow_html=True)
        timer_bar(st3["step_end"], label, frozen=frozen_for(st3["step_end"]))
        st.markdown(scores_table(db.host_scores3(code), "輪"), unsafe_allow_html=True)
        st.caption("各宮炸彈倒數的秒數不同（10–20 秒，只有伺服器知道），所以每宮進度可能略有不同。")
    elif r["phase"] == "s4":
        info = db.s4_info(code)
        st4 = game.s4_step(ref, r["phase_start"], info["limit"])
        label = {"prepare": "準備", "solve": "解謎", "share": "貢獻分享", "buffer": "緩衝", "done": "結算"}[st4["step"]]
        st.markdown(f'<div class="gcard" style="text-align:center"><b>宮廷謎題・{esc(info["title"])}</b>（難度：{esc(info["key"])}，限時 {info["limit"]} 秒）</div>',
                    unsafe_allow_html=True)
        timer_bar(st4["step_end"] if st4["step"] != "solve" else st4["b"]["solve_end"], label,
                  frozen=frozen_for(st4["step_end"] if st4["step"] != "solve" else st4["b"]["solve_end"]))
        rows = db.host_scores4(code)
        body = "".join(f'<tr><td>{esc(x["name"])}</td><td>{esc(x["state"])}</td><td>{x["wrong"]}</td><td>{"是" if x["hint"] else "-"}</td>'
                       f'<td>{"-" if x["puzzle"] is None else x["puzzle"]}</td><td><b>{x["score"]}</b></td></tr>' for x in rows)
        st.markdown('<div class="gcard"><table class="tbl"><tr><th>宮</th><th>狀態</th><th>答錯</th><th>提示</th><th>謎題分</th><th>目前兵力</th></tr>'
                    + body + "</table></div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        if c1.button("給提示（剩 60 秒內）", key="s4hint", disabled=paused):
            n, msg = db.s4_host_hint(code)
            st.session_state["flash"] = f"{msg}（{n} 宮）"
            st.rerun()
        if c2.button("作廢該題", key="s4void", disabled=paused):
            st.session_state["s4confirm"] = True
        if st.session_state.get("s4confirm"):
            st.warning("作廢後全場各宮謎題分固定 1000，無法復原。確定嗎？")
            if st.button("確定作廢", key="s4void2"):
                db.s4_host_void(code)
                st.session_state.pop("s4confirm", None)
                st.rerun()
        flash_box()
        with st.expander("主持人專用：答案與推理（逾時時公布用）"):
            ans = info["answer"]
            if info["kind"] == "assign":
                st.write("　".join(f"{a}＝{x}" for a, x in zip(info["people"], ans)))
            else:
                st.write("左→右：" + "、".join(ans))
            st.write("推理：" + info["logic"])
            st.write("提示卡：" + info["hint"])
        st.caption("各宮的解謎時間可能因嬤嬤技能略有不同；解出後該宮會直接進入貢獻分享。")
    elif r["phase"] == "s5":
        info = db.s5_info(code, ref)
        st5 = info["step"]
        label = S5_LABEL.get(st5["step"], "結算") + (f"　第 {st5['q'] + 1} / {game.S5_Q} 題" if st5["step"] in ("ask", "run") else "")
        st.markdown('<div class="gcard" style="text-align:center"><b>兵臨城下・萬馬奔騰</b>　所有宮同時出兵的常識題競速</div>', unsafe_allow_html=True)
        timer_bar(st5["step_end"], label, frozen=frozen_for(st5["step_end"]))
        race_box(info["race"], None, st5["step"], max(0.0, st5["el"] - (game.S5_INTRO + st5["q"] * game.S5_QSEC + game.S5_ANS)), st5["el"], lane=52)
        if st5["step"] in ("ask", "run"):
            d, text, opts, ans, flag = info["bank"]
            st.markdown(f'<div class="gcard"><div class="sub">（{d}）</div><div class="qtext">{esc(text)}</div>{flag_html(flag)}'
                        + "".join(f'<div>{game.S2_LETTER[i]}　{esc(o)}{"　✔" if i == ans else ""}</div>' for i, o in enumerate(opts))
                        + '<div class="sub">' + "　".join(f'{esc(t["name"])} {info["count"][t["idx"]][0]}/{info["count"][t["idx"]][1]}' for t in info["race"])
                        + ' 位已作答</div></div>', unsafe_allow_html=True)
        if info["result"]:
            st.markdown('<div class="gcard">' + "".join(
                f'<div>第 {v["rank"]} 名　{esc(next(t["name"] for t in info["race"] if t["idx"] == k))}　+{v["gain"]}（{v["before"]} → {v["after"]}）</div>'
                for k, v in sorted(info["result"].items(), key=lambda x: x[1]["rank"])) + '</div>', unsafe_allow_html=True)
        st.caption("每人答對得 50–100 分（越快越高，送出後不能改），宮的戰績＝全宮平均（沒作答算 0）；名次獎勵 800／500／300，其餘保底 200；侍衛可讓一題至少以 50% 計。")
    elif r["phase"] in ("s6", "end"):
        host_s6(r, code, htoken, ref, frozen_for)

    if r["phase"] in ("quiz", "s1", "s2", "s3", "s4", "s5", "s6"):
        nxt = next_step_label(r, ref)
        st.markdown(f'<div class="skipbox">房主快速鍵・略過目前環節</div>', unsafe_allow_html=True)
        if st.button(f"⏭ 略過此環節　→　{nxt}", key="skip", type="primary", disabled=paused):
            db.skip_step(code)
            st.rerun()
        if paused:
            st.caption("遊戲暫停中，請先按「繼續遊戲」才能略過。")
    if r["phase"] not in ("lobby", "end"):
        c1, c2 = st.columns(2)
        if r["paused"]:
            if c1.button("繼續遊戲"):
                db.resume(code)
                st.rerun()
        elif c1.button("暫停遊戲"):
            db.pause(code)
            st.rerun()
        if c2.button("產生備份檔"):
            st.session_state["backup"] = db.backup_bytes()
        if st.session_state.get("backup"):
            st.download_button("下載備份檔（.db）", data=st.session_state["backup"], file_name=f"gongdou_{code}.db")
    if st.button("關閉房間"):
        db.close_room(code)
        st.rerun()
    poller(code, None, "host", fp, r["phase"])


# ======================= 玩家：階段 1、2 =======================
def flash_box():
    """操作結果提示（例如「已使用某技能」）：古風卷軸樣式。"""
    msg = st.session_state.pop("flash", None)
    if msg:
        st.markdown(f'<div class="scrollnote"><span class="sn-l"></span><div class="sn-t"><b>諭</b>{esc(msg)}</div><span class="sn-r"></span></div>',
                    unsafe_allow_html=True)


def sec(icon, title, hint=""):
    """表單裡的小標題：圖示＋標題＋一行說明。"""
    st.markdown(f'<div class="sec"><span class="ic">{icon}</span><b>{esc(title)}</b>'
                + (f'<small>{esc(hint)}</small>' if hint else "") + '</div>', unsafe_allow_html=True)


def view_s1(p, r, token, now):
    flash_box()
    name, idx, t0, t1 = game.s1_step(now - r["phase_start"])
    end_ts = r["phase_start"] + t1
    code = r["code"]
    team = db.my_team(p)
    cut = {"ennoble": ("ennoble", "冊封大典", "聽旨"), "reveal": ("reveal", "分宮揭曉", "看看你被分到哪一宮"),
           "card": ("card", "填寫名帖", "寫下你自己"), "meet": ("meet", "宮內相認", "認識你的隊友")}.get(name)
    if cut and cut_in(cut[0], cut[1], r["phase_start"] + t0, now, sub=cut[2]):
        return
    if name == "ennoble":
        timer_bar(end_ts, "冊封", act="觀看")
        _palace(mode="result", scores=json.loads(p["sc"] or "{}"), p2=json.loads(p["p2"] or "{}"), height=900,
                nonce=f"res-{p['id']}", key=f"r-{p['id']}")
    elif name == "reveal":
        timer_bar(end_ts, "分宮揭曉", act="認識隊友")
        st.markdown(f"### 你被分到「{esc(team['name'])}」" if team else "### 分宮中……")
        if team:
            st.markdown(members_html(team["members"], p["id"]), unsafe_allow_html=True)
            st.markdown(f'<div class="gcard" style="text-align:center"><i>「{esc(team["slogan"])}」</i></div>', unsafe_allow_html=True)
    elif name == "card":
        submitted = db.card_done(p)
        timer_bar(end_ts, "填寫名帖", soft=True, act="等待" if submitted else "請選擇")
        d0, h0 = db.card_of(p)
        x0 = db.card_extra(p)
        if submitted:
            # 送出後直接換成「已送出」畫面，讓玩家清楚知道成功了
            more = f'<div class="sub">其他想說的：{esc(x0)}</div>' if x0 else ""
            st.markdown(f'<div class="gcard" style="text-align:center"><div class="qtext">名帖已送出！</div>'
                        f'<div class="ask">等待其他玩家作答，時間到會自動進入下一個環節。</div>'
                        f'<div class="sub">你的名帖　愛喝：{esc(d0)}　假日：{esc(h0)}</div>{more}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="gcard namecard"><div class="nc-seal">名</div><div class="qtext">你的名帖</div>'
                        '<div class="sub">讓大家多了解一下你吧</div></div>', unsafe_allow_html=True)
            with st.form("card", border=False):
                sec("🍵", "愛喝的飲料", "選一個最像你的")
                d = st.radio("愛喝的飲料", game.DRINKS, index=game.DRINKS.index(d0) if d0 in game.DRINKS else 0,
                             horizontal=True, label_visibility="collapsed")
                sec("🏯", "假日型態", "放假的時候你通常⋯⋯")
                h = st.radio("假日型態", game.HOLIDAYS, index=game.HOLIDAYS.index(h0) if h0 in game.HOLIDAYS else 0,
                             horizontal=True, label_visibility="collapsed")
                sec("✍️", "其他想做的介紹", "選填：想讓大家多知道你的什麼？")
                x = st.text_input("其他想做的介紹", value=x0, max_chars=game.CARD_EXTRA_MAX, label_visibility="collapsed",
                                  placeholder=f"例如：最近迷上桌遊（{game.CARD_EXTRA_MAX} 字內，可不填）")
                ok = st.form_submit_button("送出名帖")
            if ok:
                if not db.save_card(token, d, h, x):
                    st.session_state["flash"] = "名帖送出失敗（時間可能已到或遊戲暫停中），請再試一次。"
                st.rerun()
            st.caption("沒有送出也沒關係，會顯示為「不透露」。")
    elif name == "meet":
        timer_bar(end_ts, "宮內相認", soft=True, act="分享")
        st.markdown('<div class="gcard" style="text-align:center">請輪流用一句話說說：<br><b>你的角色卡，哪裡最像你？</b></div>',
                    unsafe_allow_html=True)
        st.markdown("### 認識一下你的隊友")
        if team:
            st.markdown(member_cards_html(team["members"], p["id"], show_card=True), unsafe_allow_html=True)
    else:
        st.info(f'準備進入{game.PHASE_NAME.get(game.next_phase("s1", db.room_cfg(r)), "下一個環節")}……')


# 技能框配色：底色上、底色下、標題列（取自各角色卡的配色）
SKILL_THEME = {"嬤": ("#e9f0e6", "#a9cdbd", "#2f6f6a"), "內": ("#e8eefb", "#9fb1dd", "#4a5d8f"),
               "妃": ("#fde9e4", "#f0a99b", "#a3182a"), "嬪": ("#fff1ea", "#f9b9c6", "#c2414a"),
               "衛": ("#e6efe9", "#8fb7a5", "#26302e")}


def skill_panel(p, sv, token):
    role = p["role_main"]
    if role not in game.SKILLS:
        return
    name, desc = game.SKILLS[role]
    used = bool(sv["skill_used"])
    a, b, c = SKILL_THEME.get(role, ("#fbf0dc", "#f0dcc0", "#8a1a1a"))
    st.markdown(
        f'<div class="skill" style="--a:{a};--b:{b};--c:{c}"><div class="sh">{avatars.av(role)}'
        f'<div class="st"><small>{esc(game.ROLE_NAMES.get(role, ""))}・角色技能</small><b>{esc(name)}</b></div>'
        f'<span class="seal{" off" if used else ""}">{"已使用" if used else "可使用"}</span></div>'
        f'<div class="sb"><i>「{esc(game.ROLE_TAG.get(role, ""))}」</i>{esc(desc)}{"" if used else "（整場一次）"}</div></div>',
        unsafe_allow_html=True)
    if st.button(f"使用「{name}」", key=f"skill-{sv['rnd']}-{sv['step']}", disabled=sv["skill_used"]):
        ok, msg = db.use_skill(token)
        st.session_state["flash"] = msg
        st.rerun()


def view_s2(p, r, token, now):
    flash_box()
    sv = db.s2_view(p, now)
    members = sv["members"]
    names = {m["id"]: m["name"] for m in members}
    step, rnd = sv["step"], sv["rnd"]
    if step == "done":
        st.info("默契大考驗結束，正在結算……")
        return
    opts = sv["options"]
    letter = game.S2_LETTER
    if cut_in("s2", "默契大考驗", r["phase_start"], now, sub="選出你的答案，看看誰和你想的一樣"):   # 整個階段只播一次
        return
    label = {"vote": "作答", "discuss": "全宮討論", "revise": "改答案", "reveal": "公布與對話"}[step]
    head = f'{sv["team"]["name"]}　第 {rnd + 1} / {sv["n_rounds"]} 題'
    agree = (f'貴妃的號令：全宮 {sv["agree"][1]} 位已作答的人裡，最多有 {sv["agree"][0]} 位選同一個答案。' if sv["agree"] else "")

    def my_ans(i):
        return f'{letter[i]}　{esc(opts[i])}' if i is not None else "（還沒作答）"

    picking = None
    if step == "vote" and not sv["my_voted"]:
        picking = ("vote", "選一個最像你的答案（沒有對錯，選你真的會做的）", db.save_pick)
    elif step == "revise":
        picking = ("rev", "聽完討論，可以改答案，不改也可以" + ("　" + agree if agree else ""), db.save_revise)

    # 倒數條在最上方（樣式較淡），旁邊的小橢圓提示這一環節要做什麼
    act = {"vote": "請選擇", "discuss": "討論", "revise": "請選擇", "reveal": "分享"}[step]
    if step == "vote" and sv["my_voted"]:
        act = "等待"
    timer_bar(sv["step_end"], label, soft=True, act=act)

    def q_card(ask="", sub=""):
        """題目（主）＋這一環節要做什麼（次）合在同一個框裡。"""
        st.markdown(f'<div class="gcard"><div style="text-align:center"><b>{esc(head)}</b></div>'
                    f'<div class="qtext">{esc(sv["question"])}</div>'
                    + (f'<div class="ask">{ask}</div>' if ask else "")
                    + (f'<div class="sub">{sub}</div>' if sub else "") + '</div>', unsafe_allow_html=True)

    def pick_box(prefix, hint, fn):
        """題目＋提示＋四個選項合在同一個框裡，點選項就作答。"""
        val = _pick(title=head, question=sv["question"], hint=hint,
                    options=[{"id": i, "name": o, "letter": letter[i]} for i, o in enumerate(opts)],
                    mine=sv["my_final"] if prefix == "rev" else None,
                    nonce=f"{prefix}-{rnd}", key=f"pk-{prefix}-{rnd}")
        if isinstance(val, dict) and val.get("type") == "pick":
            seen_key = f"pks-{prefix}-{rnd}"
            if val.get("n", 0) > st.session_state.get(seen_key, 0):   # 同一次點擊只處理一次
                st.session_state[seen_key] = val["n"]
                ok, msg = fn(token, rnd, val.get("id"))
                if not ok:
                    st.session_state["flash"] = msg
                st.rerun()

    if picking:
        pick_box(*picking)
        if step == "revise":
            if sv["my_final"] is not None:
                st.markdown(f'<div class="gcard" style="text-align:center">你目前的答案：<b class="green">{my_ans(sv["my_final"])}</b>'
                            f'<div class="sub">{"已經改過，還可以再改" if sv["changed"] else "時間到就以這個答案計分"}</div></div>',
                            unsafe_allow_html=True)
            skill_panel(p, sv, token)
    elif step == "vote":
        q_card(ask="已作答，等待其他人……", sub=f'你選了 <b class="green">{my_ans(sv["my_final"])}</b>')
    elif step == "discuss":
        q_card(ask="和隊友說說：你為什麼選這個答案？", sub=sv["hint"])
        st.markdown(f'<div class="gcard" style="text-align:center">你的答案：<b class="green">{my_ans(sv["my_final"])}</b>'
                    f'<div class="sub">等一下還可以改答案，先聽聽大家怎麼說。</div></div>', unsafe_allow_html=True)
        team = db.my_team(p)
        if team:
            st.markdown(members_html(team["members"], p["id"], show_card=True), unsafe_allow_html=True)
        skill_panel(p, sv, token)
    elif step == "reveal":
        res = sv["result"]
        if not res:
            q_card(sub="計分中……")
            return
        if res["kind"] == "none":
            q_card(ask="大家想法很不一樣！", sub="請每人說一句：你為什麼選這個？")
        else:
            q_card(ask="選到同一個答案的人，互相擊個掌！", sub="再請選最少人的那位說說他的理由。")
        # 結果只用一個框：每個選項多少人、誰選的、得分與兵力
        rows = ""
        for i, o in enumerate(opts):
            n = res["dist"].get(str(i), 0)
            who = "、".join(esc(names.get(x, "?")) for x in res["who"].get(str(i), []))
            win = i in res["winners"] and res["kind"] == "normal"
            rows += (f'<div class="orow{" win" if win else ""}"><span class="lt">{letter[i]}</span>'
                     f'<span class="on">{esc(o)}<i>{who or "沒有人選"}</i></span>'
                     f'<span class="oc">{n}</span></div>')
        mine = f'<br>你選的是 <b class="green">{my_ans(sv["my_final"])}</b>' if sv["my_final"] is not None else ""
        hit = ("　<b>你和最多人同一邊！</b>" if sv["my_hit"] else "") if res["kind"] == "normal" else ""
        st.markdown(f'<div class="gcard">{rows}<div style="border-top:1px dashed #d9b36a;margin-top:10px;padding-top:8px;text-align:center">'
                    f'<span class="nw">最多人選的有 <b class="red">{res["top"]}</b> 位</span>　'
                    f'<span class="nw">本題本宮得 <b class="red">{res["score"]}</b> 分</span>　'
                    f'<span class="nw">目前兵力 <b class="red">{sv["score"]}</b></span>{mine}{hit}</div></div>',
                    unsafe_allow_html=True)


def view_s3(p, r, token, now):
    flash_box()
    sv = db.s3_view(p, now)
    members = sv["members"]
    names = {m["id"]: m["name"] for m in members}
    step, rnd = sv["step"], sv["rnd"]
    if step == "done":
        st.info("密旨傳遞結束，正在結算……")
        return
    me = p["id"]
    nm = lambda i: f'<span class="pn">{esc(names.get(i, "?"))}</span>'
    head = f'{sv["team"]["name"]}　第 {rnd + 1} / {sv["n_rounds"]} 輪'
    recv, is_recv, free = sv["receiver"], sv["is_recv"], sv["free"]
    label = {"relay": "傳遞密旨", "answer": "接旨人作答", "guess": "隊友猜測",
             "reveal": "公布與對話", "buffer": "準備下一輪" if rnd < sv["n_rounds"] - 1 else "準備下一個環節"}[step]
    if cut_in("s3", "傳遞密旨", r["phase_start"], now, sub="炸彈爆炸時，拿著密旨的人就是接旨人"):   # 整個階段只播一次
        return

    def act_of():
        if step == "relay":
            return "傳旨" if sv["i_hold"] else "等待"
        if step == "answer":
            if free:
                return "請選擇" if sv["my_guess"] is None else "等待"
            return "請選擇" if is_recv and not sv["answered"] else "等待"
        if step == "guess":
            if free or not is_recv:
                return "思考" if sv["my_guess"] is None else "等待"
            return "等待"
        if step == "reveal":
            return "分享"
        return "等待"

    timer_bar(sv["step_end"], label, soft=True, act=act_of())
    with st.container(key="fxbox"):   # 全螢幕透明特效層：隊友鼓掌＝浮起的 👏、追問＝彈幕
        _fx(claps=sv["claps"], asks=[game.S3_ASKS[k] for k in sv["asks"]], key=f"fx-{rnd}")

    def card(main="", ask="", sub=""):
        st.markdown(f'<div class="gcard"><div style="text-align:center"><b>{esc(head)}</b></div>'
                    + (f'<div class="qtext">{main}</div>' if main else "")
                    + (f'<div class="ask">{ask}</div>' if ask else "")
                    + (f'<div class="sub">{sub}</div>' if sub else "") + '</div>', unsafe_allow_html=True)

    def do(fn, *a):
        ok, msg = fn(token, *a)
        if not ok and msg:
            st.session_state["flash"] = msg
        st.rerun()

    def option_buttons(prefix, on_pick):
        for i, o in enumerate(sv["options"]):
            if st.button(f"{chr(65 + i)}　{o}", key=f"{prefix}-{rnd}-{i}"):
                on_pick(i)

    if step == "relay":
        el = max(0.0, now - sv["bounds"]["start"])
        # 宮內人物排成一排，炸彈會快速移到持旨者頭上，持旨者旁邊冒冷汗
        _bomb(members=[{"id": m["id"], "name": m["name"], "role": m["role_main"], "you": m["id"] == me} for m in members],
              holder=sv["holder"], avatars=avatars.all_uris(), elapsed=round(el, 1), key=f"bomb-{rnd}")
        if sv["i_hold"]:
            card(ask="密旨在你手上！趕快傳給隊友", sub="每次最多拿 5 秒，來不及會自動傳出；不能傳回給剛傳給你的人。")
            for m in members:
                if m["id"] == me or m["id"] == sv["prev"]:
                    continue
                if st.button(f"傳給　{m['name']}", key=f"pass-{rnd}-{m['id']}"):
                    do(db.pass_note, m["id"])
        else:
            card(ask="密旨正在隊友之間傳遞……", sub="炸彈隨時會爆，爆炸時拿著密旨的人就是接旨人！")
    elif step == "answer":
        bn = '<b class="red">密旨彈開了！</b>　' if sv["bounced"] else ""
        if free:
            card(main=esc(sv["qtext"]), ask="這輪改為「大家一起答」：選你自己的答案", sub="不計命中，全宮固定得 200 分，答案分布會顯示供閒聊。")
            if sv["my_guess"] is None:
                option_buttons("own", lambda i: do(db.s3_guess, i))
            else:
                st.markdown('<div class="gcard" style="text-align:center">已選好，等待其他人……</div>', unsafe_allow_html=True)
        elif is_recv:
            if sv["answered"]:
                card(main=esc(sv["qtext"]), ask="已選好，等待隊友猜測……", sub=f'你選了 {chr(65 + sv["my_choice"])}　{esc(sv["options"][sv["my_choice"]])}')
            else:
                card(main=esc(sv["qtext"]), ask=bn + "你是接旨人！請暗中選一個答案", sub="只有你看得到；時間內沒選，視同免答（全宮得 200 分）。")
                option_buttons("ans", lambda i: do(db.s3_answer, i))
                if sv["can_swap"] and st.button("換題權（整場一次）", key=f"swap-{rnd}"):
                    do(db.s3_swap, None)
                if not sv["free_used"] and st.button("免答權（整場一次）：全宮一起答，固定 200 分", key=f"free-{rnd}"):
                    do(db.s3_free)
                if st.button("跳過這題（效果同免答，沒有任何懲罰）", key=f"skip-{rnd}"):
                    do(db.s3_free)
        else:
            card(ask=bn + f"接旨人是 {nm(recv)}，正在暗中作答……", sub="等一下請猜他會選哪個選項。")
    elif step == "guess":
        if free:
            card(main=esc(sv["qtext"]), ask="這輪改為「大家一起答」：選你自己的答案", sub=f"已有 {sv['n_guess']} 人作答。")
            if sv["my_guess"] is None:
                option_buttons("own", lambda i: do(db.s3_guess, i))
            else:
                st.markdown('<div class="gcard" style="text-align:center">已選好，等待其他人……</div>', unsafe_allow_html=True)
        elif is_recv:
            card(main=esc(sv["qtext"]), ask="隊友正在猜你的答案……", sub=f'已有 {sv["n_guess"]} 位隊友猜了。你選的是 {chr(65 + sv["my_choice"])}　{esc(sv["options"][sv["my_choice"]])}'
                 if sv["answered"] else "")
        else:
            if sv["my_guess"] is None:
                card(main=esc(sv["qtext"]), ask=f"猜猜看：{nm(recv)} 會選哪一個？", sub=f'已有 {sv["n_guess"]} 位猜了。')
                option_buttons("g", lambda i: do(db.s3_guess, i))
            else:
                card(main=esc(sv["qtext"]), ask="已提交猜測，等待公布……",
                     sub=f'你猜 {chr(65 + sv["my_guess"])}　{esc(sv["options"][sv["my_guess"]])}')
    else:   # reveal / buffer
        res = sv["result"]
        if not res:
            card(sub="計分中……")
            return
        opts = res["options"]
        if res["mode"] == "free":
            card(main=esc(sv["qtext"]), ask="大家一起聊聊：為什麼這樣選？")
            dist = "".join(f'<div class="rowc"><span>{chr(65 + i)}　{esc(o)}</span><b>{res["dist"].get(str(i), 0)} 人</b></div>'
                           for i, o in enumerate(opts))
            st.markdown(f'<div class="gcard">{dist}<div style="border-top:1px dashed #d9b36a;margin-top:8px;padding-top:8px;text-align:center">'
                        f'<span class="nw">本輪全宮得 <b class="red">{res["score"]}</b> 分</span>　'
                        f'<span class="nw">目前兵力 <b class="red">{sv["score"]}</b></span></div></div>',
                        unsafe_allow_html=True)
        else:
            ans = res["answer"]
            ask = "請接旨人補充一句，大家可以鼓掌或追問" if not is_recv else ("可以補充一句（選填，+100 分）" if not sv["supp"] else "謝謝你的補充！")
            card(main=esc(sv["qtext"]), ask=ask)
            hit = ""
            if not is_recv and sv["my_guess"] is not None:
                hit = f'<br>你的猜測：<b>{"命中！" if sv["my_guess"] == ans else "沒有命中"}</b>'
            supp = f'<br>接旨人補充：「{esc(sv["supp"])}」' if sv["supp"] else ""
            total = res["score"] + (game.S3_SUPP_SCORE if sv["supp"] else 0)
            st.markdown(
                f'<div class="gcard" style="text-align:center">答案：<b>{chr(65 + ans)}　{esc(opts[ans])}</b>（{nm(recv)}）<br>'
                f'{res["hits"]} / {res["guessers"]} 位猜中{hit}{supp}'
                f'<div style="border-top:1px dashed #d9b36a;margin-top:8px;padding-top:8px">'
                f'<span class="nw">本輪得 <b class="red">{total}</b> 分</span>　'
                f'<span class="nw">目前兵力 <b class="red">{sv["score"]}</b></span></div></div>', unsafe_allow_html=True)
            if is_recv and not sv["supp"]:
                for j, t in enumerate(game.S3_SUPP_PRESET):
                    if st.button(t, key=f"sp-{rnd}-{j}"):
                        do(db.s3_supp, t)
                with st.form(f"supp-{rnd}"):
                    txt = st.text_input("或自己寫一句（30 字內）", max_chars=game.S3_SUPP_MAX)
                    if st.form_submit_button("送出補充"):
                        do(db.s3_supp, txt)
            if not is_recv:
                c1, c2 = st.columns([1, 2])
                if c1.button("👏 鼓掌" + (" ✓" if sv["my_clap"] else ""), key=f"clap-{rnd}", disabled=sv["my_clap"]):
                    do(db.s3_clap)
                if not sv["my_ask"]:
                    for k, t in enumerate(game.S3_ASKS):
                        if st.button(t, key=f"ask-{rnd}-{k}"):
                            do(db.s3_ask, k)


def wrap_clue(t):
    """長句子在「，」之後或「（」之前換行，避免只有一兩個字掉到下一行。"""
    t = str(t)
    if len(t) > 14:
        i = t.find("，")
        if 0 < i < len(t) - 2:
            return esc(t[:i + 1]) + "<br>" + esc(t[i + 1:])
        j = t.find("（")
        if 6 < j < len(t) - 2:
            return esc(t[:j]) + "<br>" + esc(t[j:])
    return esc(t)


def names_of(members):
    return {m["id"]: m["name"] for m in members}


def view_s4(p, r, token, now):
    flash_box()
    sv = db.s4_view(p, now)
    step = sv["step"]
    if step == "done":
        st.info("宮廷謎題結束，正在結算……")
        return
    me = p["id"]
    members = sv["members"]
    head = f'{sv["team"]["name"]}　宮廷謎題・{sv["title"]}'
    nm = lambda t: f'<span class="pn">{esc(t)}</span>'
    if cut_in("s4", "宮廷謎題", r["phase_start"], now, sub="五張線索卡，缺一不可"):   # 整個階段只播一次
        return
    act = {"prepare": "閱讀", "solve": "討論", "share": "請選擇", "buffer": "等待"}[step]
    if step == "solve" and sv["solved"]:
        act = "等待"
    if step == "share" and sv["badge_done"]:
        act = "等待"
    if sv["void"]:
        act = "等待"
    timer_bar(sv["step_end"], {"prepare": "準備", "solve": "解謎", "share": "貢獻分享", "buffer": "結算"}[step], soft=True, act=act)

    def card(main="", ask="", sub=""):
        st.markdown(f'<div class="gcard"><div style="text-align:center"><b>{esc(head)}</b></div>'
                    + (f'<div class="qtext">{main}</div>' if main else "")
                    + (f'<div class="ask">{ask}</div>' if ask else "")
                    + (f'<div class="sub">{sub}</div>' if sub else "") + '</div>', unsafe_allow_html=True)

    def do(fn, *a):
        ok, msg = fn(token, *a)
        if not ok and msg:
            st.session_state["flash"] = msg
        st.rerun()

    if sv["void"]:
        card(main="本題已作廢", ask="全宮謎題分固定 1000 分", sub=f'目前兵力 <b class="red">{sv["score"]}</b>')
        return

    def clues():
        if not sv["has_cards"]:
            return
        st.markdown('<div class="gcard clue"><div class="cn">你的線索卡</div>'
                    '<div class="cs">只有你看得到，請用說的告訴隊友</div>'
                    + "".join(f'<div class="ct">卡 {i + 1}　{wrap_clue(t)}</div>' for i, t in enumerate(sv["my_cards"]))
                    + '</div>', unsafe_allow_html=True)
        if sv["hint"]:
            st.markdown(f'<div class="gcard clue" style="text-align:center"><div class="ct">{wrap_clue(sv["hint"])}</div>'
                        f'<div class="cs">全宮都看得到</div></div>', unsafe_allow_html=True)

    if step == "prepare":
        card(main=esc(sv["story"]) + f'<div class="q2">{esc(sv["task"])}</div>',
             ask="5 張線索卡缺一不可<br>每個人只拿到其中幾張",
             sub=(esc(sv["note"]) + "<br>" if sv["note"] else "") + "等一下請用說的交換線索，不能複製或截圖。")
        clues()
        return
    if step == "solve":
        wr = f'已答錯 {sv["wrong"]} 次：還不正確。' if sv["wrong"] else "還沒有人提出答案。"
        if sv["solved"]:
            card(main="解出了！", ask="等待進入貢獻分享……", sub=f'答案：{esc(sv["answer"] or "")}')
            return
        card(main=esc(sv["story"]) + f'<div class="q2">{esc(sv["task"])}</div>',
             ask="和隊友口頭交換線索<br>一起推理出答案",
             sub=(esc(sv["note"]) + "<br>" if sv["note"] else "") + esc(wr))
        clues()
        sv_skill = {"skill_used": sv["skill_used"], "rnd": 0, "step": step}
        prop = sv["proposal"]
        if prop:
            ag = "、".join(nm(x) for x in prop["agree"])
            st.markdown(f'<div class="gcard"><div class="ask">{nm(prop["by"])} 提出答案</div>'
                        f'<div class="qtext" style="font-size:18px">{"　".join(esc(x) for x in prop["ans"])}</div>'
                        f'<div class="sub">已同意：{ag}（超過一半的人同意才會送出，目前需要 {prop["need"]} 人）</div></div>', unsafe_allow_html=True)
            if not prop["i_agreed"] and st.button("同意送出", key="s4agree", type="primary"):
                do(db.s4_agree)
        st.markdown("### 提出答案")
        labels = sv["people"]
        opts = ["（請選擇）"] + sv["items"]
        with st.form("s4ans"):
            picks = [st.selectbox(lb if sv["kind"] == "assign" else lb, opts, key=f"s4sel-{i}") for i, lb in enumerate(labels)]
            go = st.form_submit_button("提出答案")
        if go:
            if any(x == opts[0] for x in picks) or len(set(picks)) != 5:
                st.session_state["flash"] = "每個位置都要選，而且不能重複。"
                st.rerun()
            do(db.s4_propose, [sv["items"].index(x) for x in picks])
        if p["role_main"] in ("嬤", "內", "嬪"):
            skill_panel(p, sv_skill, token)
        return
    if step == "share":
        lead = "解開了！剛剛誰幫了你一把？" if sv["solved"] else "辛苦了！剛剛誰讓你覺得「還好有他」？"
        if sv["answer"] and sv["solved"]:
            card(main=esc(lead) + '<div class="q2">送一枚徽章謝謝他吧</div>', sub=f'本宮已解出：{esc(sv["answer"])}')
        else:
            card(main=esc(lead) + '<div class="q2">送一枚徽章謝謝他吧</div>', sub="時間到了，這題沒有解出。")
            st.markdown(f'<div class="gcard"><div class="ask">答案：{esc(sv["answer"] or "")}</div><div class="sub">{esc(sv["logic"] or "")}</div></div>',
                        unsafe_allow_html=True)
        if not sv["badge_done"]:
            mates = [m for m in members if m["id"] != me] + [next(m for m in members if m["id"] == me)]
            by_id = {m["id"]: m for m in mates}
            with st.form("s4badge", border=False):
                sec("💌", "送給誰", "撐住全場的話，也可以送給自己")
                who = st.radio("送給誰", [m["id"] for m in mates], horizontal=True, label_visibility="collapsed",
                               format_func=lambda i: by_id[i]["name"] + ("（你自己）" if i == me else ""))
                sec("🏅", "選一枚徽章", "挑一個最貼近他的貢獻")
                bd = st.radio("徽章", game.S4_BADGES, horizontal=True, label_visibility="collapsed",
                              format_func=lambda b: f'{BADGE_ICON.get(b, "🏅")} {b}')
                sec("💬", "想說的一句感謝", "選填，20 字內")
                th = st.text_input("想說的一句感謝", max_chars=game.S4_THANKS_MAX, label_visibility="collapsed",
                                   placeholder="例如：剛剛那句話幫大忙了")
                ok = st.form_submit_button("送出徽章")
            if ok:
                do(db.s4_badge, who, bd, th)
        else:
            st.markdown(f'<div class="gcard" style="text-align:center">已送出徽章，等待其他人……<div class="sub">全宮已完成 {sv["badges_done"]} / {sv["n_humans"]} 位</div></div>',
                        unsafe_allow_html=True)
        if p["role_main"] == "嬪":
            skill_panel(p, {"skill_used": sv["skill_used"], "rnd": 0, "step": step}, token)
        return
    # buffer：結算
    res = sv["result"]
    if not res:
        card(sub="結算中……")
        return
    if res["mode"] == "solved":
        parts = f'基本 1200　時間獎勵 +{res["time_bonus"]}'
        if res["wrong"] and not res["waived"]:
            parts += f'　答錯 −{min(300, 100 * res["wrong"])}'
        if res["waived"] and res["wrong"]:
            parts += "　（嬪妃技能免除試錯扣分）"
    else:
        parts = "時間內沒有解出，基本分 500"
    if res["bonus"]:
        parts += "　貢獻分享 +300"
    card(main=f'<span class="nw">本宮謎題得 <b class="red">{res["score"]}</b> 分</span>',
         ask=f'<span class="nw">目前兵力 <b class="red">{sv["score"]}</b></span>', sub=esc(parts))
    if not res["bonus"]:
        st.markdown(f'<div class="gcard" style="text-align:center"><div class="sub">貢獻分享：完成 {res["badges_done"]} 人，需要 {res["badges_need"]} 人才有加成</div></div>',
                    unsafe_allow_html=True)
    if sv["got"]:
        _badge(title="你收到的徽章", items=sv["got"], key=f"bd-{me}-{len(sv['got'])}")
    elif sv["my_badge"]:
        st.markdown(f'<div class="gcard" style="text-align:center"><div class="sub">你把「{esc(sv["my_badge"]["badge"])}」徽章送給了 '
                    f'{nm(names_of(members).get(sv["my_badge"]["to_pid"], "?"))}</div></div>', unsafe_allow_html=True)


# ======================= 階段 5：兵臨城下・萬馬奔騰 =======================
_race = components.declare_component("race", path=str(Path(__file__).parent / "frontend_race"))
FLAG_SVG = {
    "fr": '<rect width="30" height="60" fill="#0055a4"/><rect x="30" width="30" height="60" fill="#fff"/><rect x="60" width="30" height="60" fill="#ef4135"/>',
    "it": '<rect width="30" height="60" fill="#009246"/><rect x="30" width="30" height="60" fill="#fff"/><rect x="60" width="30" height="60" fill="#ce2b37"/>',
    "ch": '<rect width="90" height="60" fill="#d52b1e"/><rect x="39" y="14" width="12" height="32" fill="#fff"/><rect x="29" y="24" width="32" height="12" fill="#fff"/>',
    "se": '<rect width="90" height="60" fill="#006aa7"/><rect x="25" width="11" height="60" fill="#fecc00"/><rect y="24.5" width="90" height="11" fill="#fecc00"/>',
    "pl": '<rect width="90" height="30" fill="#fff"/><rect y="30" width="90" height="30" fill="#dc143c"/>',
    "th": '<rect width="90" height="60" fill="#a51931"/><rect y="10" width="90" height="40" fill="#f4f5f8"/><rect y="20" width="90" height="20" fill="#2d2a4a"/>',
}
S5_LABEL = {"intro": "出兵準備", "ask": "作答", "run": "騎兵奔馳", "charge": "衝向城門", "result": "戰果"}
S5_NEXT = {"intro": "第 1 題", "ask": "騎兵奔馳", "charge": "戰果"}


def race_box(sv_race, my_team, step, run_t=0.0, el=0.0, lane=46):
    mode = {"run": "run", "charge": "charge"}.get(step, "idle")
    elapsed = run_t if step == "run" else max(0.0, el - game.S5_RACE_END) if step == "charge" else 0.0
    _race(teams=[dict(t, me=(t["idx"] == my_team)) for t in sv_race], mode=mode, elapsed=round(elapsed, 2),
          dur=6 if step == "run" else 3.5, lane=lane, key="race", default=None)


def flag_html(code):
    if not code:
        return ""
    return (f'<div style="text-align:center;margin:8px 0"><svg viewBox="0 0 90 60" width="150" height="100" '
            f'style="border:2px solid #4a2b17;border-radius:4px">{FLAG_SVG[code]}</svg></div>')


def view_s5(p, r, token, now):
    flash_box()
    sv = db.s5_view(p, now)
    step, q = sv["step"], sv["q"]
    if step == "done":
        st.info("兵臨城下結束，正在結算……")
        return
    if cut_in("s5", "兵臨城下", r["phase_start"], now, sub="萬馬奔騰，答對越多衝得越快"):
        return
    act = {"intro": "準備", "ask": "請選擇", "run": "觀看", "charge": "觀看", "result": "觀看"}[step]
    timer_bar(sv["step_end"], S5_LABEL[step] + (f"　第 {q + 1} / {sv['n_q']} 題" if step in ("ask", "run") else ""), soft=True, act=act)
    L = game.S2_LETTER

    def do(fn, *a):
        ok, msg = fn(token, *a)
        if msg:
            st.session_state["flash"] = msg if not ok else msg
        st.rerun()

    if step == "intro":
        st.markdown('<div class="gcard" style="text-align:center"><div class="qtext">萬馬奔騰・默契衝鋒</div>'
                    '<div class="ask">所有宮同時出兵！共 5 題常識題，答對越多、答得越快，騎兵衝得越遠</div>'
                    '<div class="sub">最先撞開城門的宮奪得「破城頭功」；每一宮都拿得到兵力，最後一名也有保底</div></div>', unsafe_allow_html=True)
        race_box(sv["race"], sv["team_idx"], step)
        return
    if step in ("ask", "run"):
        # 題目上方只放前兩名的宮：名次變動時小卡片上下滑動（每題公布答案時更新，所有人同步）
        _top2(teams=[{"idx": t["idx"], "name": t["name"], "color": t["color"], "score": round(t["to"] * game.S5_Q * 100),
                      "me": t["idx"] == sv["team_idx"]} for t in sv["race"]], key="top2", default=None)
        st.markdown(f'<div class="gcard"><div style="text-align:center"><b>第 {q + 1} / {sv["n_q"]} 題</b></div>'
                    f'<div class="qtext">{esc(sv["question"])}</div>{flag_html(sv["flag"])}</div>', unsafe_allow_html=True)
        if step == "ask":
            if sv["my"] is None:
                for i, o in enumerate(sv["options"]):
                    if st.button(f"{L[i]}　{o}", key=f"s5a-{q}-{i}"):
                        do(db.s5_answer, q, i)
                st.markdown('<div class="sub" style="text-align:center">越快答對分數越高；答案送出後不能修改</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="gcard" style="text-align:center"><div class="sub">你的答案（已鎖定）</div>'
                            f'<div class="qtext">{L[sv["my"]]}　{esc(sv["options"][sv["my"]])}</div><div class="sub">等待公布答案……</div></div>',
                            unsafe_allow_html=True)
            st.markdown(f'<div class="sub" style="text-align:center">本宮已有 {sv["done"]} / {sv["n_humans"]} 位作答</div>',
                        unsafe_allow_html=True)
            if p["role_main"] == "衛" and not sv["skill_used"] and sv["guard_q"] is None:
                if st.button("使用「橫刀立馬」：這題本宮至少以 50% 計", key="s5sk-g"):
                    do(db.s5_skill)
            if sv["guard_q"] == q:
                st.markdown('<div class="gcard" style="text-align:center">本宮侍衛橫刀立馬：這一題至少以 50% 計</div>', unsafe_allow_html=True)
        else:
            ok = sv["my"] == sv["answer"]
            mine = "你答對了！" if ok else ("這題沒作答" if sv["my"] is None else "差一點，下一題加油")
            st.markdown(f'<div class="gcard" style="text-align:center"><div class="sub">正確答案</div>'
                        f'<div class="qtext">{L[sv["answer"]]}　{esc(sv["options"][sv["answer"]])}</div>'
                        f'<div class="ask">{mine}</div><div class="sub">本宮這題戰績 +{round(sv["team_ratio"] * 100)}，騎兵往前衝！</div></div>',
                        unsafe_allow_html=True)
            race_box(sv["race"], sv["team_idx"], step, sv["run_t"], sv["el"], lane=34)
        return
    res = sv["result"]
    if step == "charge":
        st.markdown('<div class="gcard" style="text-align:center"><div class="qtext">衝向城門！</div>'
                    '<div class="ask">煙塵滾滾，看誰先撞開城門</div></div>', unsafe_allow_html=True)
        race_box(sv["race"], sv["team_idx"], step, el=sv["el"])
        with st.container(key="stampbox"):        # 手機畫面下方：全螢幕寬的兵馬奔騰
            _stamp(h=360, key="stampede", default=None)
        return
    race_box(sv["race"], sv["team_idx"], step)
    if res:
        head = "破城頭功！" if res["rank"] == 1 else f"第 {res['rank']} 名"
        st.markdown(f'<div class="gcard" style="text-align:center"><div class="qtext">{head}</div>'
                    f'<div class="ask">本宮兵力 +{res["gain"]}</div>'
                    f'<div class="sub"><span class="nw">兵力 {res["before"]} → <b class="red">{res["after"]}</b></span></div></div>',
                    unsafe_allow_html=True)


# ======================= 階段 6：論功行賞與遊戲結尾 =======================
S6_LABEL = {"rank": "全宮受封", "title": "趣味稱號", "ending": "結尾互動", "card": "宮廷人物誌", "done": "遊戲結束"}
HIDE_FIELDS = [("card", "名帖"), ("stats", "正向數據"), ("thanks", "隊友留言"), ("ending", "結尾互動結果")]


# 趣味稱號的小插圖（活潑的小圖，會輕輕擺動）
TITLE_ART = {
    "最神秘": '<svg viewBox="0 0 60 60"><circle cx="30" cy="34" r="18" fill="#3a2a5a"/><path d="M12 30 q18-12 36 0 v6 q-18-8-36 0z" fill="#111"/>'
             '<circle cx="23" cy="33" r="3" fill="#fff"/><circle cx="37" cy="33" r="3" fill="#fff"/><path d="M24 42 q6 3 12 0" stroke="#fff" stroke-width="2" fill="none"/>'
             '<text x="44" y="16" font-size="16" font-weight="900" fill="#7d3c98">?</text><text x="6" y="14" font-size="12" font-weight="900" fill="#7d3c98">?</text></svg>',
    "最會演": '<svg viewBox="0 0 60 60"><path d="M8 12 h26 v16 q0 14-13 14 q-13 0-13-14z" fill="#f5c542" stroke="#8a5a12" stroke-width="2"/>'
             '<circle cx="15" cy="22" r="2.5" fill="#4a2b17"/><circle cx="27" cy="22" r="2.5" fill="#4a2b17"/><path d="M14 31 q7 6 14 0" stroke="#4a2b17" stroke-width="2.5" fill="none"/>'
             '<path d="M30 22 h24 v14 q0 13-12 13 q-12 0-12-13z" fill="#e74c3c" stroke="#8a1a1a" stroke-width="2"/>'
             '<circle cx="37" cy="31" r="2.3" fill="#fff"/><circle cx="47" cy="31" r="2.3" fill="#fff"/><path d="M36 43 q6 -5 12 0" stroke="#fff" stroke-width="2.5" fill="none"/>'
             '<text x="2" y="58" font-size="11">👏</text><text x="46" y="12" font-size="11">✨</text></svg>',
    "最懂你": '<svg viewBox="0 0 60 60"><circle cx="30" cy="26" r="18" fill="#bfe3f5" stroke="#2f6f9f" stroke-width="2.5"/>'
             '<path d="M30 34 l-8-8 a5 5 0 0 1 8-6 a5 5 0 0 1 8 6z" fill="#e74c3c"/><rect x="16" y="44" width="28" height="9" rx="3" fill="#8a5a3a"/>'
             '<circle cx="21" cy="17" r="3" fill="#fff" opacity=".8"/><text x="46" y="12" font-size="10">💭</text></svg>',
    "最佳軍師": '<svg viewBox="0 0 60 60"><path d="M30 52 L8 20 q22-16 44 0z" fill="#fff8e8" stroke="#8a5a12" stroke-width="2"/>'
              '<path d="M30 52 L16 16 M30 52 L24 12 M30 52 L36 12 M30 52 L44 16" stroke="#c9a46a" stroke-width="1.5"/>'
              '<rect x="27" y="48" width="6" height="10" rx="2" fill="#6b3e1f"/><text x="2" y="58" font-size="12">📜</text><text x="44" y="58" font-size="12">💡</text></svg>',
    "最佳默契宮": '<svg viewBox="0 0 60 60"><text x="4" y="40" font-size="30">🙌</text><text x="40" y="18" font-size="14">💞</text></svg>',
    "最強合作宮": '<svg viewBox="0 0 60 60"><path d="M6 14 h18 v6 a5 5 0 0 0 0 10 v6 h-18z" fill="#2e8b57"/><path d="M28 14 h18 v22 h-18 v-6 a5 5 0 0 1 0-10z" fill="#e3b04b"/>'
              '<text x="16" y="56" font-size="16">🤝</text></svg>',
}


def award_html(ranking):
    """全宮受封上方的頒獎動畫：頒獎台由低到高升起，皇冠落在第一名上方，彩紙飄落。"""
    top = ranking[:3]
    pods = []
    for cls, t in zip(("p1", "p2", "p3"), top):
        pods.append(f'<div class="pod {cls}"><b>{t["rank"]}</b><small>{esc(t["name"])}<br>{esc(t["title"])}</small></div>')
    conf = "".join(f'<i style="left:{(i * 37) % 100}%;background:{["#e74c3c", "#f1c40f", "#2ecc71", "#3498db", "#e67e22"][i % 5]};'
                   f'animation-delay:-{(i * 0.37) % 2.6:.2f}s"></i>' for i in range(18))
    return f'<div class="award">{conf}<div class="crown">👑</div>{"".join(pods)}</div>'


def rank_cards(ranking, my_team=None, team_titles=None):
    out = []
    for t in ranking:
        tt = "".join(f'<span class="fh">{esc(x)}</span>' for x in (team_titles or {}).get(str(t["idx"]), []))
        out.append(f'<div class="rankcard{" me" if t["idx"] == my_team else ""}"><div class="rk">{t["rank"]}</div>'
                   f'<div><b style="font-size:19px">{esc(t["name"])}</b>　受封「{esc(t["title"])}」{tt}'
                   f'<div style="font-size:14px">最終兵力 {t["score"]}</div></div></div>')
    return "".join(out)


def wall_html(items):
    if not items:
        return '<div class="wall"><div class="sub">盟約牆還沒有公開的話。</div></div>'
    return '<div class="wall"><div class="ask" style="text-align:center">🤝 盟約牆</div>' + "".join(
        f'<div>{esc(x["line"])}</div>' for x in items) + '</div>'


def profile_card(p, sv):
    hide = set(sv["prefs"].get("hide", []))
    me = sv["me"]
    d, h = sv["card"]
    rows = []
    if "card" not in hide:
        extra = f'<br>其他想說的：{esc(sv["extra"])}' if sv["extra"] else ""
        rows.append(f'<div class="row"><b>名帖</b>　愛喝：{esc("神秘" if d == "不透露" else d)}　假日：{esc("神秘" if h == "不透露" else h)}{extra}</div>')
    if "stats" not in hide:
        bits = []
        bits.append(f"默契指數 {me.get('sync', game.sync_index(0, 0))}")
        if me["badges"]:
            bits.append(f"收到貢獻徽章 {me['badges']} 枚")
        if me.get("fun"):
            bits.append(f"趣味稱號：{me['title']}")
        if bits:
            rows.append('<div class="row"><b>正向數據</b><br>' + "<br>".join(esc(b) for b in bits) + '</div>')
    if "thanks" not in hide and sv["thanks"]:
        rows.append('<div class="row"><b>隊友留言</b><br>' + "<br>".join(
            esc(f'{x["from"]} 送你「{x["badge"]}」' + (f'：{x["text"]}' if x["text"] else "")) for x in sv["thanks"]) + '</div>')
    if "ending" not in hide and sv["end_to_me"]:
        rows.append('<div class="row"><b>結尾互動</b><br>' + "<br>".join(esc(x) for x in sv["end_to_me"]) + '</div>')
    team = sv["team"]
    st.markdown(f'<div class="end-card"><div style="display:flex;gap:12px;align-items:center">{avatars.av(p["role_main"])}'
                f'<div><div class="sub" style="color:#7a3b2a">宮廷人物誌</div><h3>{esc(p["name"])}</h3>'
                f'<span class="ttl">{esc(me["title"])}</span>'
                f'<div style="font-size:14px">{esc(team["name"]) if team else ""}・受封「{esc(team["title"]) if team else ""}」</div></div></div>'
                + "".join(rows) + '</div>', unsafe_allow_html=True)
    st.caption("這張卡可以直接截圖留念。")
    with st.expander("隱藏人物誌上的欄位"):
        keep = [k for k, _ in HIDE_FIELDS if k not in hide]
        new_keep = []
        for k, lab in HIDE_FIELDS:
            if st.checkbox(f"顯示{lab}", value=k in keep, key=f"hd_{k}"):
                new_keep.append(k)
        if st.button("套用", key="hd_save"):
            db.set_prefs(p["room"], {"hide": [k for k, _ in HIDE_FIELDS if k not in new_keep]}, token=p["token"])
            st.rerun()


def ending_form(p, token, sv):
    mine = sv["mine"]
    if mine:
        nm = {x["id"]: x["name"] for x in sv["others"]}
        txt = {"thanks": f'感謝 {nm.get(mine["target"], "")}：{mine["text"]}', "common": f'和 {nm.get(mine["target"], "")} {mine["text"]}',
               "meet": f'想認識 {nm.get(mine["target"], "")}（{mine["text"]}）', "skip": "今天先不分享"}[mine["kind"]]
        st.markdown(f'<div class="gcard" style="text-align:center"><div class="sub">你目前的選擇（時間內可以改）</div><b>{esc(txt)}</b></div>',
                    unsafe_allow_html=True)
    kind = st.radio("結尾互動", list(game.END_KINDS), horizontal=True, format_func=game.END_KINDS.get, label_visibility="collapsed", key="e_kind")

    def send(*a):
        ok, msg = db.s6_submit(token, *a)
        st.session_state["flash"] = "已送出，時間內都可以改" if ok else msg
        st.rerun()

    if kind == "thanks":
        if not sv["mates"]:
            st.info("你們宮沒有其他真人隊友，可以選別的項目。")
            return
        with st.form("e_thanks", border=False):
            sec("💌", "想感謝哪位隊友", "只有他看得到，除非你勾選公開")
            who = st.radio("對象", [m["id"] for m in sv["mates"]], format_func=lambda i: next(m["name"] for m in sv["mates"] if m["id"] == i),
                           horizontal=True, label_visibility="collapsed")
            sec("💬", "想說的話", "選一句，或在下面自己寫（20 字內）")
            ph = st.radio("短語", game.THANKS_PHRASES, horizontal=True, label_visibility="collapsed")
            own = st.text_input("自己寫", max_chars=game.END_TEXT_MAX, placeholder="自己寫一句（會取代上面的短語）", label_visibility="collapsed")
            pub = st.checkbox("公開到盟約牆")
            ok = st.form_submit_button("送出感謝")
        if ok:
            send("thanks", who, own.strip() or ph, pub)
    elif kind == "common":
        if not sv["cands"]:
            st.info("系統沒有找到你和其他人的共同點，可以選別的項目。")
            return
        with st.form("e_common", border=False):
            sec("🔗", "選一條你們的共同點", "系統從名帖、心理測驗、默契大考驗找出來的")
            i = st.radio("共同點", list(range(len(sv["cands"]))), label_visibility="collapsed",
                         format_func=lambda k: f'和 {sv["cands"][k]["name"]}：{sv["cands"][k]["line"]}')
            pub = st.radio("公開方式", ["只給對方", "公開到盟約牆"], horizontal=True, label_visibility="collapsed")
            ok = st.form_submit_button("送出共同點")
        if ok:
            c = sv["cands"][i]
            send("common", c["pid"], c["line"], pub == "公開到盟約牆")
    elif kind == "meet":
        if not sv["others"]:
            st.info("沒有其他真人玩家。")
            return
        with st.form("e_meet", border=False):
            sec("🌱", "想進一步認識誰", "任何宮都可以；對方只會看到「有人想認識你」，雙方互選才會揭曉名字")
            who = st.radio("對象", [m["id"] for m in sv["others"]], format_func=lambda i: next(m["name"] for m in sv["others"] if m["id"] == i),
                           horizontal=True, label_visibility="collapsed")
            sec("❓", "想問他的小問題")
            q = st.radio("問題", game.MEET_QS, horizontal=True, label_visibility="collapsed")
            ok = st.form_submit_button("送出")
        if ok:
            send("meet", who, q)
    else:
        st.markdown('<div class="gcard" style="text-align:center">不需要理由，也不影響任何分數或稱號。</div>', unsafe_allow_html=True)
        if st.button("今天先不分享", key="e_skip"):
            send("skip")


def view_s6(p, r, token, now):
    flash_box()
    sv = db.s6_view(p, now)
    step = sv["step"]
    if r["phase"] == "s6" and cut_in("s6", "論功行賞", r["phase_start"], now, sub="全宮受封，結為盟友"):
        return
    if step == "done":
        title_block("遊戲結束")
        st.markdown('<div class="gcard" style="text-align:center"><div class="qtext">謝謝你今天入宮！</div>'
                    '<div class="ask">今天認識的人，下次見面打聲招呼吧</div></div>', unsafe_allow_html=True)
        profile_card(p, sv)
        st.markdown(wall_html(sv["wall"]), unsafe_allow_html=True)
        st.markdown(f'<div class="oath">{game.OATH}</div>', unsafe_allow_html=True)
        st.caption("全場一起念出盟誓，遊戲圓滿結束。")
        return
    act = {"rank": "觀看", "title": "觀看", "ending": "請選擇", "card": "截圖"}[step]
    timer_bar(sv["step_end"], S6_LABEL[step], soft=True, act=act)
    if step == "rank":
        st.markdown(award_html(sv["ranking"]), unsafe_allow_html=True)      # 頒獎動畫：頒獎台升起、皇冠落下、彩紙飄落
        st.markdown(rank_cards(sv["ranking"], p["team"], sv["team_titles"]), unsafe_allow_html=True)
    elif step == "title":
        mt = sv["me"]["title"]
        pic = TITLE_ART.get(mt, "") if sv["me"].get("fun") else ""
        st.markdown(f'<div class="gcard" style="text-align:center"><div class="sub">你的稱號</div>'
                    + (f'<div class="tline" style="justify-content:center">{pic}</div>' if pic else "")
                    + f'<div class="qtext ttl-red">{esc(mt)}</div>'
                    + (f'<div class="sub">{esc(game.TITLE_DESC.get(mt, ""))}</div>' if sv["me"].get("fun") else "") + '</div>',
                    unsafe_allow_html=True)

        def tline(t, who):
            return (f'<div class="tline">{TITLE_ART.get(t, "")}<div><b class="ttl-red">{esc(t)}</b>　{esc(who)}'
                    f'<div class="sub" style="text-align:left">{esc(game.TITLE_DESC[t])}</div></div></div>')
        lines = [tline(t, n) for n, t in sorted(sv["titles"].items(), key=lambda x: game.TITLE_ORDER.index(x[1]))]
        tn = {str(t["idx"]): t["name"] for t in sv["ranking"]}
        for idx, ts in sv["team_titles"].items():
            for t in ts:
                lines.append(tline(t, tn.get(idx, "")))
        st.markdown('<div class="gcard"><div class="ask" style="text-align:center">本場趣味稱號</div>' + "".join(lines) + '</div>',
                    unsafe_allow_html=True)
    elif step == "ending":
        st.markdown('<div class="gcard" style="text-align:center"><div class="qtext">結尾互動</div>'
                    '<div class="ask">四選一：感謝隊友／共同點／想進一步認識／今天先不分享</div></div>', unsafe_allow_html=True)
        ending_form(p, token, sv)
    else:
        profile_card(p, sv)
        st.markdown(wall_html(sv["wall"]), unsafe_allow_html=True)


def host_s6(r, code, htoken, ref, frozen_for):
    data = db.s6_data(code)
    if r["phase"] == "s6":
        st6 = game.s6_step(ref, r["phase_start"])
        timer_bar(st6["step_end"], S6_LABEL[st6["step"]], frozen=frozen_for(st6["step_end"]))
    else:
        st.markdown('<div class="gcard" style="text-align:center"><div class="qtext">遊戲結束</div>'
                    f'<div class="ask">請帶領全場同聲念盟誓：「{game.OATH}」</div></div>', unsafe_allow_html=True)
    st.markdown(rank_cards(data["ranking"], None, data["team_titles"]), unsafe_allow_html=True)
    nm = {x["id"]: x["name"] for x in db.list_players(code)}
    titles = [f'{t}：{nm.get(int(pid), "")}' for pid, t in sorted(data["titles"].items(), key=lambda x: game.TITLE_ORDER.index(x[1]))]
    st.markdown('<div class="gcard"><div class="ask">個人趣味稱號</div><div class="sub">' + ("、".join(esc(x) for x in titles) or "（本場沒有符合條件的人）")
                + '</div></div>', unsafe_allow_html=True)
    n_end = len(db._read("SELECT pid FROM s6_end WHERE room=?", (code,)))
    st.markdown(f'<div class="gcard">結尾互動已完成 {n_end} / {len(db.humans(code))} 位</div>', unsafe_allow_html=True)
    al = db.allies(code)
    if al:
        st.markdown('<div class="gcard"><div class="ask">🤝 新盟友</div>' + "".join(f'<div>{esc(a)} ✕ {esc(b)}</div>' for a, b in al) + '</div>',
                    unsafe_allow_html=True)
    items = db.wall(code, include_hidden=True)
    st.markdown('<div class="ask">盟約牆（可單則隱藏）</div>', unsafe_allow_html=True)
    if not items:
        st.caption("還沒有公開的話。")
    for x in items:
        c1, c2 = st.columns([5, 2])
        c1.markdown(("~~" if x["hidden"] else "") + esc(x["line"]) + ("~~" if x["hidden"] else ""))
        if c2.button("恢復" if x["hidden"] else "隱藏", key=f"wh-{x['pid']}"):
            db.s6_hide(code, htoken, x["pid"], not x["hidden"])
            st.rerun()
    st.download_button("下載結果 CSV（暱稱、宮、角色、稱號、兵力）", data=db.final_csv(code), file_name=f"gongdou_{code}_result.csv",
                       mime="text/csv", key="csv")

# ======================= 玩家 =======================
def player_page(code, token):
    boot()
    p = db.get_player(token)
    if not p or p["room"] != code:
        st.warning("找不到你的玩家資料，可能已被房主移出房間。")
        if st.button("回到首頁"):
            go_home()
        return
    db.touch(token)
    r = db.tick(code)
    if not r:
        st.error("找不到房間。")
        return
    fp = db.fingerprint(code, "player", token)
    p = db.get_player(token) or p
    now = time.time()
    chrome("player", code, token, petals_ok=r["phase"] != "quiz")   # 心理測驗畫面本身就有飄花，避免重複
    if r["closed"]:
        title_block()
        st.warning(T("房間已關閉，謝謝你的參與！"))
        return
    paused = bool(r["paused"]) and r["phase"] not in ("lobby", "end")
    if paused and r["paused_at"]:
        now = min(now, r["paused_at"])      # 暫停中：畫面停在暫停那一秒，不跳走
        _FROZEN["at"] = now

    if r["phase"] == "lobby":
        title_block(T("已入宮，等待房主開始"))
        ps = db.list_players(code)
        chips = "".join(f'<span class="chip"><span class="pn">{esc(x["name"])}</span>{T("（你）") if x["id"] == p["id"] else ""}</span>' for x in ps)
        st.markdown(f'<div class="gcard">{T("你的暱稱")}：<span class="pn">{esc(p["name"])}</span><br>'
                    f'{T("目前已入宮")} {len(ps)}：<br>{chips}</div>', unsafe_allow_html=True)
        st.caption(T("請保持這個畫面開啟，房主按下開始後會自動進入遊戲。請不要截圖分享網址。"))
    elif r["phase"] == "quiz":
        if p["quiz_done"]:
            _palace(mode="wait", text="你已答完，等待其他人入宮……", height=520,
                    nonce=f"w-{p['id']}-{r['epoch']}", key=f"w-{p['id']}-{r['epoch']}")
        else:
            # 測驗畫面「暫停／繼續」時不重新載入：元件一直留在原地（key 與 nonce 不含 epoch），
            # 暫停只是蓋上「暫停中」並凍結倒數，所以做到一半的題目與答案都還在。
            paused = bool(r["paused"])
            rem = (r["deadline"] - r["paused_at"]) if (paused and r["paused_at"]) else (r["deadline"] - now)
            val = _palace(mode="quiz", questions=game.QUESTIONS, rem=round(max(0.0, rem), 1), paused=paused,
                          answers=json.loads(p["answers"] or "[]"), height=780,
                          nonce=f"snap-{p['id']}", key=f"q-{p['id']}")
            if isinstance(val, dict) and val.get("type") == "quiz":
                db.save_answers(token, val.get("answers"), bool(val.get("done")))
                if val.get("done"):
                    st.rerun()
    elif r["phase"] == "s1":
        view_s1(p, r, token, now)
    elif r["phase"] == "s2":
        view_s2(p, r, token, now)
    elif r["phase"] == "s3":
        view_s3(p, r, token, now)
    elif r["phase"] == "s4":
        view_s4(p, r, token, now)
    elif r["phase"] == "s5":
        view_s5(p, r, token, now)
    elif r["phase"] in ("s6", "end"):
        view_s6(p, r, token, now)
    if paused and not (r["phase"] == "quiz" and not p["quiz_done"]):   # 測驗畫面自己有暫停遮罩
        pause_overlay()
    poller(code, token, "player", fp, r["phase"])


# ======================= 入口 =======================
def main():
    boot()
    st.markdown(CSS.replace("</style>", avatars.css() + "</style>"), unsafe_allow_html=True)
    q = st.query_params
    room, h, p = q.get("room"), q.get("h"), q.get("p")
    if room and h:
        host_page(room, h)
    elif room and p:
        player_page(room, p)
    elif room:
        join_page(room)          # 掃 QR Code／貼上房間連結：直接輸入暱稱
    else:
        landing("")


try:
    main()
except Exception as _e:   # noqa: BLE001
    if type(_e).__name__ in ("RerunException", "StopException", "Rerun", "Stop"):
        raise                  # Streamlit 換頁／停止的訊號，照常交給 Streamlit
    # 偶發的暫時性錯誤（例如資料庫剛好忙碌）：不顯示整段英文錯誤訊息，改成一行提示並在 1 秒後自動重試。
    # （Streamlit 換頁用的 rerun／stop 不是 Exception，不會被這裡攔下。）
    import traceback
    traceback.print_exc()      # 錯誤細節仍印在黑色視窗，方便除錯
    st.markdown('<div class="scrollnote"><span class="sn-l"></span><div class="sn-t"><b>諭</b>連線忙碌，畫面重新整理中……</div>'
                '<span class="sn-r"></span></div>', unsafe_allow_html=True)
    time.sleep(1)
    st.rerun()
