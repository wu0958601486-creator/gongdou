"""資料層：SQLite 存放房間、玩家、宮；並負責階段推進（tick）與備份。

所有資料都存在 data/gongdou.db 這一個檔案，同一次遊戲期間自動保存。
"""
import json
import random
import secrets
import sqlite3
import threading
import time
from collections import Counter
from fractions import Fraction
from contextlib import contextmanager
from pathlib import Path

import game

DB_PATH = game.BASE / "data" / "gongdou.db"
BACKUP_DIR = game.BASE / "backups"
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms(
  code TEXT PRIMARY KEY, host_token TEXT NOT NULL, difficulty TEXT DEFAULT '中',
  phase TEXT DEFAULT 'lobby', epoch INTEGER DEFAULT 0, deadline REAL DEFAULT 0,
  paused INTEGER DEFAULT 0, paused_at REAL DEFAULT 0, closed INTEGER DEFAULT 0,
  created REAL, phase_start REAL DEFAULT 0, bots INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS players(
  id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL, name_key TEXT NOT NULL, is_bot INTEGER DEFAULT 0, last_seen REAL,
  answers TEXT DEFAULT '[]', quiz_done INTEGER DEFAULT 0,
  role_main TEXT, role_sub TEXT, sc TEXT, p2 TEXT, sys_assigned INTEGER DEFAULT 0, team INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS ux_player_name ON players(room, name_key);
CREATE TABLE IF NOT EXISTS teams(
  room TEXT NOT NULL, idx INTEGER NOT NULL, name TEXT, slogan TEXT, score INTEGER DEFAULT 1000,
  PRIMARY KEY(room, idx));
CREATE TABLE IF NOT EXISTS team_round(
  room TEXT NOT NULL, team INTEGER NOT NULL, stage INTEGER NOT NULL, rnd INTEGER NOT NULL,
  dx INTEGER DEFAULT 0, px INTEGER DEFAULT 0, seed TEXT, result TEXT, settled INTEGER DEFAULT 0,
  PRIMARY KEY(room, team, stage, rnd));
-- 默契大考驗：每人每題選一個選項；c1＝第一次選的，c2＝聽完討論後改的（可以不改）
CREATE TABLE IF NOT EXISTS s2_pick(room TEXT NOT NULL, rnd INTEGER NOT NULL, pid INTEGER NOT NULL,
  c1 INTEGER NOT NULL, c2 INTEGER, PRIMARY KEY(room, rnd, pid));
-- 階段 3：每宮每輪一列（T＝炸彈倒數秒數，由伺服器決定，不給玩家看）
CREATE TABLE IF NOT EXISTS s3_round(
  room TEXT NOT NULL, team INTEGER NOT NULL, rnd INTEGER NOT NULL, T INTEGER NOT NULL,
  holder INTEGER, since REAL, prev INTEGER, visited TEXT DEFAULT '[]',
  receiver INTEGER, bounced INTEGER DEFAULT 0, diff TEXT, qid TEXT, choice INTEGER,
  free INTEGER DEFAULT 0, swapped INTEGER DEFAULT 0, settled INTEGER DEFAULT 0, result TEXT, supp TEXT,
  PRIMARY KEY(room, team, rnd));
CREATE TABLE IF NOT EXISTS s3_guess(room TEXT NOT NULL, team INTEGER NOT NULL, rnd INTEGER NOT NULL, pid INTEGER NOT NULL,
  choice INTEGER NOT NULL, PRIMARY KEY(room, team, rnd, pid));
CREATE TABLE IF NOT EXISTS s3_clap(room TEXT NOT NULL, team INTEGER NOT NULL, rnd INTEGER NOT NULL, pid INTEGER NOT NULL,
  PRIMARY KEY(room, team, rnd, pid));
CREATE TABLE IF NOT EXISTS s3_ask(room TEXT NOT NULL, team INTEGER NOT NULL, rnd INTEGER NOT NULL, pid INTEGER NOT NULL,
  kind INTEGER NOT NULL, PRIMARY KEY(room, team, rnd, pid));
CREATE TABLE IF NOT EXISTS s3_rights(room TEXT NOT NULL, pid INTEGER NOT NULL, swap INTEGER DEFAULT 0, free INTEGER DEFAULT 0,
  PRIMARY KEY(room, pid));
-- 階段 4：每宮一列（線索卡只存「卡號」，文字依版本 A/B 在畫面上代換）
CREATE TABLE IF NOT EXISTS s4_team(
  room TEXT NOT NULL, team INTEGER NOT NULL, version TEXT NOT NULL, cards TEXT NOT NULL,
  dx REAL DEFAULT 0, solved_at REAL, wrong INTEGER DEFAULT 0, hint INTEGER DEFAULT 0, hint_penalty INTEGER DEFAULT 0,
  waive INTEGER DEFAULT 0, void INTEGER DEFAULT 0, proposal TEXT, settled INTEGER DEFAULT 0, result TEXT,
  PRIMARY KEY(room, team));
CREATE TABLE IF NOT EXISTS s4_badge(room TEXT NOT NULL, pid INTEGER NOT NULL, to_pid INTEGER NOT NULL, badge TEXT NOT NULL,
  thanks TEXT DEFAULT '', PRIMARY KEY(room, pid));
-- 階段 5（萬馬奔騰）：每宮一列（侍衛擋題、戰果），每人每題一列作答
CREATE TABLE IF NOT EXISTS s5_rteam(room TEXT NOT NULL, team INTEGER NOT NULL, before INTEGER, guard_q INTEGER,
  result TEXT, settled INTEGER DEFAULT 0, PRIMARY KEY(room, team));
CREATE TABLE IF NOT EXISTS s5_rans(room TEXT NOT NULL, q INTEGER NOT NULL, pid INTEGER NOT NULL, choice INTEGER, at REAL,
  used REAL DEFAULT 0, PRIMARY KEY(room, q, pid));
-- 階段 6：每宮的封號與稱號計算結果（一場一列），結尾互動每人一列
CREATE TABLE IF NOT EXISTS s6_state(room TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS s6_end(room TEXT NOT NULL, pid INTEGER NOT NULL, kind TEXT NOT NULL, target INTEGER,
  text TEXT DEFAULT '', public INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0, at REAL, PRIMARY KEY(room, pid));
CREATE TABLE IF NOT EXISTS skill_use(room TEXT NOT NULL, pid INTEGER NOT NULL, skill TEXT, stage INTEGER, rnd INTEGER, used_at REAL,
  PRIMARY KEY(room, pid));
"""

MIGRATIONS = [
    ("rooms", "s2_q", "ALTER TABLE rooms ADD COLUMN s2_q TEXT DEFAULT '[]'"),
    ("players", "card", "ALTER TABLE players ADD COLUMN card TEXT DEFAULT '{}'"),
    ("teams", "score", "ALTER TABLE teams ADD COLUMN score INTEGER DEFAULT 1000"),
    ("rooms", "cfg", "ALTER TABLE rooms ADD COLUMN cfg TEXT DEFAULT '{}'"),
    ("rooms", "hprefs", "ALTER TABLE rooms ADD COLUMN hprefs TEXT DEFAULT '{}'"),
    ("players", "prefs", "ALTER TABLE players ADD COLUMN prefs TEXT DEFAULT '{}'"),
    ("s5_rans", "used", "ALTER TABLE s5_rans ADD COLUMN used REAL DEFAULT 0"),
]


def set_path(path):
    """測試用：改用別的資料庫檔案。"""
    global DB_PATH, BACKUP_DIR
    DB_PATH = Path(path)
    BACKUP_DIR = DB_PATH.parent / "backups"


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    return con


@contextmanager
def tx():
    with _lock:
        con = _connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except BaseException:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            con.close()


def _read(sql, args=()):
    con = _connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = _connect()
    try:
        con.executescript(SCHEMA)
        for table, col, ddl in MIGRATIONS:   # 舊版資料庫自動補欄位
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                con.execute(ddl)
        con.execute("UPDATE rooms SET phase='s1' WHERE phase='teams'")
    finally:
        con.close()
    _cleanup_old()


def _cleanup_old(hours=48):
    cut = time.time() - hours * 3600
    with tx() as c:
        olds = [r["code"] for r in c.execute("SELECT code FROM rooms WHERE created < ?", (cut,))]
        for code in olds:
            c.execute("DELETE FROM players WHERE room=?", (code,))
            c.execute("DELETE FROM teams WHERE room=?", (code,))
            c.execute("DELETE FROM rooms WHERE code=?", (code,))


def pause_all_on_boot():
    """程式重啟時，進行中的房間一律暫停，房主按「繼續」才會恢復。"""
    with tx() as c:
        c.execute(
            "UPDATE rooms SET paused=1, paused_at=?, epoch=epoch+1 "
            "WHERE closed=0 AND paused=0 AND phase IN ('quiz','s1','s2','s3','s4','s5','s6')", (time.time(),))


# ---------- 房間與玩家 ----------
def create_room(difficulty="中"):
    if difficulty not in ("易", "中", "難"):
        difficulty = "中"
    with tx() as c:
        while True:
            code = f"{random.randint(0, 999999):06d}"
            if not c.execute("SELECT 1 FROM rooms WHERE code=?", (code,)).fetchone():
                break
        host_token = secrets.token_urlsafe(9)
        c.execute("INSERT INTO rooms(code,host_token,difficulty,created) VALUES(?,?,?,?)",
                  (code, host_token, difficulty, time.time()))
    return code, host_token


def get_room(code):
    rows = _read("SELECT * FROM rooms WHERE code=?", (code,))
    return rows[0] if rows else None


def check_host(code, token):
    r = get_room(code)
    return bool(r) and secrets.compare_digest(str(r["host_token"]), str(token or ""))


def join(code, raw_name):
    """回傳 (ok, 訊息, token)。"""
    if not game.valid_room_code(code):
        return False, "房號必須是 6 位數字", None
    ok, msg, clean, key = game.validate_name(raw_name)
    if not ok:
        return False, msg, None
    with tx() as c:
        r = c.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
        if not r or r["closed"]:
            return False, "找不到這個房間，請確認房號", None
        if r["phase"] != "lobby":
            return False, "本局已經開始，無法加入", None
        n = c.execute("SELECT COUNT(*) FROM players WHERE room=? AND is_bot=0", (code,)).fetchone()[0]
        if n >= game.MAX_PLAYERS:
            return False, f"房間已滿（最多 {game.MAX_PLAYERS} 人）", None
        token = secrets.token_urlsafe(9)
        try:
            c.execute("INSERT INTO players(room,token,name,name_key,last_seen) VALUES(?,?,?,?,?)",
                      (code, token, clean, key, time.time()))
        except sqlite3.IntegrityError:
            return False, "這個暱稱已有人使用，請加個字", None
    return True, "", token


def get_player(token):
    rows = _read("SELECT * FROM players WHERE token=?", (token,))
    return rows[0] if rows else None


def list_players(code):
    return _read("SELECT * FROM players WHERE room=? ORDER BY id", (code,))


def humans(code):
    return [p for p in list_players(code) if not p["is_bot"]]


def touch(token, now=None):
    now = now or time.time()
    row = _read("SELECT last_seen FROM players WHERE token=?", (token,))
    if row and now - (row[0]["last_seen"] or 0) < 2:
        return
    with tx() as c:
        c.execute("UPDATE players SET last_seen=? WHERE token=?", (now, token))


def is_online(p, now=None):
    return (now or time.time()) - (p["last_seen"] or 0) <= game.OFFLINE_SEC


def remove_player(code, pid):
    """房主只能在大廳階段移除真人玩家。"""
    with tx() as c:
        r = c.execute("SELECT phase FROM rooms WHERE code=?", (code,)).fetchone()
        if not r or r["phase"] != "lobby":
            return False
        return c.execute("DELETE FROM players WHERE id=? AND room=? AND is_bot=0",
                         (pid, code)).rowcount == 1


# ---------- 房主三個動作：開始、暫停／繼續、關閉 ----------
def start_game(code, rng=None, now=None):
    """回傳 (ok, 訊息, 補入的機器人數)。真人不足 8 人才補機器人（最少 1 位真人）。"""
    rng = rng or random.Random()
    now = now or time.time()
    with tx() as c:
        r = c.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
        if not r or r["closed"]:
            return False, "找不到房間", 0
        if r["phase"] != "lobby":
            return False, "遊戲已經開始", 0
        hs = c.execute("SELECT name_key FROM players WHERE room=? AND is_bot=0", (code,)).fetchall()
        if len(hs) < 1:
            return False, "至少需要 1 位玩家才能開始", 0
        nb = max(0, game.MIN_PLAYERS - len(hs))
        used = {h["name_key"] for h in hs}
        pool = [n for n in game.BOT_NAMES if game.name_key(n) not in used]
        names = rng.sample(pool, min(nb, len(pool)))
        while len(names) < nb:
            names.append(f"宮女{len(names) + 1}")
        for n in names:
            c.execute(
                "INSERT INTO players(room,token,name,name_key,is_bot,last_seen,quiz_done) VALUES(?,?,?,?,1,?,1)",
                (code, "bot-" + secrets.token_hex(6), n, game.name_key(n), now))
        c.execute(
            "UPDATE rooms SET phase='quiz', epoch=epoch+1, phase_start=?, deadline=?, bots=? WHERE code=?",
            (now, now + game.INTRO_SEC + game.QUIZ_SEC, nb, code))
    backup("start")
    return True, "", nb


def pause(code, now=None):
    now = now or time.time()
    with tx() as c:
        return c.execute(
            "UPDATE rooms SET paused=1, paused_at=?, epoch=epoch+1 "
            "WHERE code=? AND paused=0 AND closed=0 AND phase IN ('quiz','s1','s2','s3','s4','s5','s6')",
            (now, code)).rowcount == 1


def resume(code, now=None):
    now = now or time.time()
    with tx() as c:
        r = c.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
        if not r or not r["paused"] or r["closed"]:
            return False
        shift = max(0.0, now - (r["paused_at"] or now))
        dl = r["deadline"] + shift if r["deadline"] else 0
        c.execute("UPDATE rooms SET paused=0, deadline=?, phase_start=phase_start+?, epoch=epoch+1 WHERE code=?",
                  (dl, shift if r["phase_start"] else 0, code))
        c.execute("UPDATE s3_round SET since=since+? WHERE room=? AND receiver IS NULL", (shift, code))
        c.execute("UPDATE s4_team SET solved_at=solved_at+? WHERE room=? AND solved_at IS NOT NULL", (shift, code))
        return True


def skip_step(code, now=None, rng=None):
    """房主跳過目前環節，直接進入下一個環節。回傳 (ok, 訊息)。

    - 心理測驗：立刻結算（沒答的人由系統指派角色），進入冊封。
    - 階段 1：把時間軸往前推到目前步驟結束，下一步立刻開始（最後一步會接著進入階段 2）。
    - 階段 2：把時間軸推到「各宮最早的下一個步驟邊界」，所以不會有宮被跳過整個步驟。
    暫停中、大廳不能跳過。"""
    now = now or time.time()
    rng = rng or random.Random()
    r = get_room(code)
    if not r or r["closed"]:
        return False, "找不到房間"
    if r["paused"]:
        return False, "遊戲暫停中，請先按「繼續遊戲」"
    ph = r["phase"]
    if ph == "quiz":
        return (True, "") if _finalize_quiz(code, now, rng) else (False, "現在不能跳過")
    if ph == "s1":
        el = now - r["phase_start"]
        name, _, _, t1 = game.s1_step(el)
        if name == "done":
            return False, "這個階段已經結束"
        shift = t1 - el + 0.001
    elif ph == "s2":
        ends = []
        for t in _read("SELECT idx FROM teams WHERE room=?", (code,)):
            st = game.s2_step(now, r["phase_start"], shifts(code, t["idx"]))
            if st["step"] != "done":
                ends.append(st["step_end"])
        if not ends:
            return False, "這個階段已經結束"
        shift = max(min(ends) - now, 0.0) + 0.001
    elif ph == "s3":
        ends = []
        with tx() as c:
            for t in c.execute("SELECT idx FROM teams WHERE room=?", (code,)).fetchall():
                Ts = _s3_ts(c, code, t["idx"])
                st3 = game.s3_step(now, r["phase_start"], Ts)
                if st3["step"] != "done":
                    ends.append(st3["step_end"])
        if not ends:
            return False, "這個階段已經結束"
        shift = max(min(ends) - now, 0.0) + 0.001
    elif ph == "s4":
        ends = []
        with tx() as c:
            for t in c.execute("SELECT idx FROM teams WHERE room=?", (code,)).fetchall():
                row = _s4_ensure(c, r, t["idx"], rng)
                st4 = _s4_step_of(r, row, now)
                if st4["step"] not in ("done",):
                    ends.append(st4["b"]["share_start"] if st4["step"] == "solve" else st4["step_end"])
        if not ends:
            return False, "這個階段已經結束"
        shift = max(min(ends) - now, 0.0) + 0.001
    elif ph in ("s5", "s6"):
        el = now - r["phase_start"]
        nxt = [b for b in (game.s5_boundaries() if ph == "s5" else game.s6_boundaries()) if b > el]
        if not nxt:
            return False, "這個階段已經結束"
        shift = nxt[0] - el + 0.001
    else:
        return False, "這個階段目前沒有可以跳過的環節"
    with tx() as c:
        n = c.execute(
            "UPDATE rooms SET phase_start=phase_start-?, deadline=deadline-?, epoch=epoch+1 "
            "WHERE code=? AND phase=? AND paused=0 AND closed=0", (shift, shift, code, ph)).rowcount
    return (True, "") if n == 1 else (False, "現在不能跳過")


def close_room(code):
    with tx() as c:
        c.execute("UPDATE rooms SET closed=1, epoch=epoch+1 WHERE code=?", (code,))


# ---------- 心理測驗答案 ----------
def save_answers(token, answers, done=False):
    """儲存玩家目前的答案（只增不減）。回傳是否成功。"""
    if not isinstance(answers, list):
        return False
    clean = []
    for a in answers[: len(game.QUESTIONS)]:
        clean.append(a if (isinstance(a, int) and not isinstance(a, bool) and 0 <= a < 4) else None)
    with tx() as c:
        p = c.execute("SELECT * FROM players WHERE token=? AND is_bot=0", (token,)).fetchone()
        if not p:
            return False
        r = c.execute("SELECT * FROM rooms WHERE code=?", (p["room"],)).fetchone()
        if not r or r["phase"] != "quiz" or r["paused"] or r["closed"]:
            return False
        old = json.loads(p["answers"] or "[]")
        if len(clean) < len(old):
            clean = old
        full = len([a for a in clean if a is not None]) >= len(game.QUESTIONS)
        flag = 1 if (done or full or p["quiz_done"]) else 0
        c.execute("UPDATE players SET answers=?, quiz_done=? WHERE id=?", (json.dumps(clean), flag, p["id"]))
    return True


# ---------- 階段推進：有人來問就檢查 ----------
def tick(code, now=None, rng=None):
    """每次輪詢都呼叫：到時間或全員完成就推進階段（資料庫交易保證只推進一次）。"""
    now = now or time.time()
    rng = rng or random.Random()
    r = get_room(code)
    if not r or r["closed"] or r["paused"]:
        return r
    if r["phase"] == "quiz":
        hs = humans(code)
        all_done = bool(hs) and all(p["quiz_done"] for p in hs)
        if all_done or now >= r["deadline"]:
            _finalize_quiz(code, now, rng)
            r = get_room(code)
    if r["phase"] == "s1" and now >= r["deadline"]:
        _advance_next(code, "s1")
        r = get_room(code)
    if r["phase"] == "s2":
        _tick_s2(r, now, rng)
        r = get_room(code)
    if r["phase"] == "s3":
        _tick_s3(r, now, rng)
        r = get_room(code)
    if r["phase"] == "s4":
        _tick_s4(r, now, rng)
        r = get_room(code)
    if r["phase"] == "s5":
        _tick_s5(r, now)
        r = get_room(code)
    if r["phase"] == "s6":
        _tick_s6(r, now, rng)
        r = get_room(code)
    return r


def _advance(code, frm, to, length):
    """階段推進：新階段的開始時間＝舊階段的結束時間（不受輪詢延遲影響）。"""
    with tx() as c:
        r = c.execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()
        if not r or r["phase"] != frm:
            return False
        start = r["deadline"] or time.time()
        c.execute("UPDATE rooms SET phase=?, phase_start=?, deadline=?, epoch=epoch+1 WHERE code=? AND phase=?",
                  (to, start, start + length if length else 0, code, frm))
    backup(f"{frm}_done")
    return True


def room_cfg(code_or_room):
    """這一場的「遊戲調整」設定（每個環節玩幾局）。"""
    r = code_or_room if isinstance(code_or_room, dict) else get_room(code_or_room)
    try:
        return game.clean_cfg(json.loads((r or {}).get("cfg") or "{}"))
    except ValueError:
        return dict(game.CFG_DEFAULT)


def set_cfg(code, host_token, cfg, difficulty=None):
    """房主調整本場要玩的環節與局數（只能在大廳時調整）。回傳 (ok, 訊息)。"""
    cfg = game.clean_cfg(cfg)
    with tx() as c:
        r = c.execute("SELECT * FROM rooms WHERE code=? AND host_token=?", (code, host_token)).fetchone()
        if not r:
            return False, "找不到房間"
        if r["phase"] != "lobby":
            return False, "遊戲已經開始，這一場不能再調整（下一場建立新房間時可以調整）"
        diff = difficulty if difficulty in ("易", "中", "難") else r["difficulty"]
        c.execute("UPDATE rooms SET cfg=?, difficulty=? WHERE code=?", (json.dumps(cfg), diff, code))
    return True, "已儲存"


PREF_DEFAULT = {"vol": 40, "lang": "zh", "petals": True}


def _clean_prefs(d):
    out = dict(PREF_DEFAULT)
    d = d or {}
    try:
        out["vol"] = max(0, min(100, int(d.get("vol", out["vol"]))))
    except (TypeError, ValueError):
        pass
    out["lang"] = d.get("lang") if d.get("lang") in ("zh", "en") else out["lang"]
    out["petals"] = bool(d.get("petals", out["petals"]))
    for k in ("hide",):
        if isinstance(d.get(k), list):
            out[k] = [str(x) for x in d[k]][:10]
    return out


def get_prefs(code, token=None, host_token=None):
    """個人設定（音量、語言、飄花、人物誌隱藏欄位）。玩家存在 players，房主存在 rooms。"""
    if host_token:
        rows = _read("SELECT hprefs v FROM rooms WHERE code=? AND host_token=?", (code, host_token))
    else:
        rows = _read("SELECT prefs v FROM players WHERE token=?", (token,))
    try:
        return _clean_prefs(json.loads(rows[0]["v"] or "{}")) if rows else dict(PREF_DEFAULT)
    except ValueError:
        return dict(PREF_DEFAULT)


def set_prefs(code, prefs, token=None, host_token=None):
    old = get_prefs(code, token, host_token)
    old.update(prefs or {})
    v = json.dumps(_clean_prefs(old), ensure_ascii=False)
    with tx() as c:
        if host_token:
            return c.execute("UPDATE rooms SET hprefs=? WHERE code=? AND host_token=?", (v, code, host_token)).rowcount == 1
        return c.execute("UPDATE players SET prefs=? WHERE token=?", (v, token)).rowcount == 1


def _advance_next(code, frm):
    """進入下一個要進行的環節（房主關掉或設為 0 局的環節直接跳過）。"""
    cfg = room_cfg(code)
    to = game.next_phase(frm, cfg)
    return _advance(code, frm, to, game.phase_len(to, cfg) if to != "end" else 0)


def shifts(code, team, c=None):
    sql = "SELECT rnd,dx,px FROM team_round WHERE room=? AND team=? AND stage=2"
    rows = [dict(x) for x in c.execute(sql, (code, team))] if c else _read(sql, (code, team))
    return {x["rnd"]: (x["dx"], x["px"]) for x in rows}


def _tick_s2(r, now, rng):
    code = r["code"]
    teams = _read("SELECT idx FROM teams WHERE room=?", (code,))
    done = {(x["team"], x["rnd"]) for x in
            _read("SELECT team,rnd FROM team_round WHERE room=? AND stage=2 AND settled=1", (code,))}
    n2 = game.rounds(room_cfg(r), "s2")
    for t in teams:
        sh = shifts(code, t["idx"])
        for rnd in range(n2):
            if (t["idx"], rnd) in done:
                continue
            b = game.s2_bounds(r["phase_start"] + game.S2_ROUND * rnd, *sh.get(rnd, (0, 0)))
            if now >= b["rev_end"] or now >= r["deadline"]:
                _settle_s2(code, t["idx"], rnd, now, rng)
    if now >= r["deadline"]:
        _advance_next(code, "s2")


def _settle_s2(code, team, rnd, now, rng):
    """結算某宮某題（只會結算一次）：機器人隨機選、統計每個選項的人數、算分、加到宮兵力。"""
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO team_round(room,team,stage,rnd) VALUES(?,?,2,?)", (code, team, rnd))
        row = c.execute("SELECT * FROM team_round WHERE room=? AND team=? AND stage=2 AND rnd=?",
                        (code, team, rnd)).fetchone()
        if row["settled"]:
            return False
        members = [dict(x) for x in c.execute("SELECT * FROM players WHERE room=? AND team=? ORDER BY id", (code, team))]
        hs = [m for m in members if not m["is_bot"]]
        hids = {m["id"] for m in hs}
        nopt = len(game.S2_BANK[0][1])
        picks = {}
        for x in c.execute("SELECT * FROM s2_pick WHERE room=? AND rnd=?", (code, rnd)):
            if x["pid"] in hids:
                picks[x["pid"]] = x["c2"] if x["c2"] is not None else x["c1"]
        for m in members:                      # 機器人隨機選一個選項
            if m["is_bot"]:
                picks[m["id"]] = rng.randrange(nopt)
        counts = Counter(picks.values())
        top = max(counts.values()) if counts else 0
        winners = sorted(i for i, n in counts.items() if n == top) if top else []
        n_on = len([h for h in hs if (now - (h["last_seen"] or 0) <= game.OFFLINE_SEC) or h["id"] in picks])
        answered = [h for h in hs if h["id"] in picks]
        # 沒有人作答，或作答的人不到一半，或最多也只有 1 個人選同一個 → 視為「大家想法很不一樣」
        no_cons = n_on == 0 or len(answered) < -(-n_on // 2) or top <= 1
        who = {}
        for pid, ci in picks.items():
            who.setdefault(str(ci), []).append(pid)
        score = game.s2_vote_score(top)   # 同宮選到同一個選項的人越多，兵力越多
        result = {"kind": "none" if no_cons else "normal", "winners": winners, "top": top,
                  "total": sum(counts.values()), "n_on": n_on, "score": score,
                  "dist": {str(i): counts.get(i, 0) for i in range(nopt)}, "who": who,
                  "hits": game.s2_hits(winners, list(picks.values()))}
        c.execute("UPDATE team_round SET settled=1, result=? WHERE room=? AND team=? AND stage=2 AND rnd=?",
                  (json.dumps(result), code, team, rnd))
        c.execute("UPDATE teams SET score=score+? WHERE room=? AND idx=?", (score, code, team))
        return True


def _finalize_quiz(code, now, rng):
    with tx() as c:
        n = c.execute(
            "UPDATE rooms SET phase='s1', epoch=epoch+1, phase_start=?, deadline=?, s2_q=? "
            "WHERE code=? AND phase='quiz'",
            (now, now + game.S1_LEN, json.dumps(rng.sample(range(len(game.S2_BANK)), game.S2_ROUNDS)), code)).rowcount
        if n != 1:
            return False
        hs = [dict(x) for x in c.execute("SELECT * FROM players WHERE room=? AND is_bot=0 ORDER BY id", (code,))]
        roles = {}
        for p in hs:
            sc, p2, cnt = game.score_answers(json.loads(p["answers"] or "[]"))
            if cnt == 0:
                main, sub, sc, p2 = game.system_roles(rng)
                sysf = 1
            else:
                ks = game.rank_roles(sc, p2)
                main, sub, sysf = ks[0], ks[1], 0
            c.execute("UPDATE players SET role_main=?, role_sub=?, sc=?, p2=?, sys_assigned=?, quiz_done=1 WHERE id=?",
                      (main, sub, json.dumps(sc), json.dumps(p2), sysf, p["id"]))
            roles[p["id"]] = main
        bots = [dict(x) for x in c.execute("SELECT id FROM players WHERE room=? AND is_bot=1 ORDER BY id", (code,))]
        teams, bot_roles = game.group_players(roles, len(bots), rng)
        for i, (name, slogan) in enumerate(game.pick_team_names(len(teams), rng)):
            c.execute("INSERT INTO teams(room,idx,name,slogan,score) VALUES(?,?,?,?,?)",
                      (code, i, name, slogan, game.START_SCORE))
        for i, team in enumerate(teams):
            for pid, _ in team:
                c.execute("UPDATE players SET team=? WHERE id=?", (i, pid))
        need = sum(len(rl) for rl in bot_roles) - len(bots)
        if need > 0:   # 每宮人數要一樣：人數較少的宮另外補機器人
            used = {x["name_key"] for x in c.execute("SELECT name_key FROM players WHERE room=?", (code,))}
            pool = [n for n in game.BOT_NAMES if game.name_key(n) not in used]
            k = 1
            for _ in range(need):
                if pool:
                    nm = pool.pop(0)
                else:
                    while game.name_key(f"宮女{k}") in used:
                        k += 1
                    nm = f"宮女{k}"
                used.add(game.name_key(nm))
                c.execute("INSERT INTO players(room,token,name,name_key,is_bot,last_seen,quiz_done) VALUES(?,?,?,?,1,?,1)",
                          (code, "bot-" + secrets.token_hex(6), nm, game.name_key(nm), now))
            c.execute("UPDATE rooms SET bots=bots+? WHERE code=?", (need, code))
            bots = [dict(x) for x in c.execute("SELECT id FROM players WHERE room=? AND is_bot=1 ORDER BY id", (code,))]
        bi = 0
        for i, rl in enumerate(bot_roles):
            for r in rl:
                sub = rng.choice([x for x in game.ROLE_CODES if x != r])
                c.execute("UPDATE players SET team=?, role_main=?, role_sub=? WHERE id=?",
                          (i, r, sub, bots[bi]["id"]))
                bi += 1
        assert bi == len(bots)
    backup("quiz_done")
    return True


# ---------- 讀取畫面用資料 ----------
def teams_view(code):
    ts = _read("SELECT * FROM teams WHERE room=? ORDER BY idx", (code,))
    ps = list_players(code)
    for t in ts:
        t["members"] = [p for p in ps if p["team"] == t["idx"]]
    return ts


def my_team(p):
    for t in teams_view(p["room"]):
        if t["idx"] == p["team"]:
            return t
    return None


def fingerprint(code, kind="player", token=None, now=None):
    """輪詢時用來判斷「畫面是否需要重新整理」。"""
    now = now or time.time()
    r = get_room(code)
    if not r:
        return ("none",)
    if r["paused"] and r["paused_at"]:
        now = min(now, r["paused_at"])   # 暫停中時間是靜止的，畫面指紋不應該跟著走
    ph = r["phase"]
    base = (ph, r["paused"], r["epoch"], r["closed"])
    p = get_player(token) if (kind == "player" and token) else None
    if kind == "player" and token:
        base += (p is None,)
    if ph == "lobby":
        return base + (tuple(x["id"] for x in list_players(code)),)
    if kind == "host":
        if ph == "quiz":
            hs = humans(code)
            return base + (sum(1 for x in hs if x["quiz_done"]),)   # 倒數由畫面上的計時元件每秒更新，不必重整整頁
        if ph == "s1":
            return base + (game.s1_step(now - r["phase_start"])[1],)
        if ph == "s2":
            st = game.s2_step(now, r["phase_start"])
            n = _read("SELECT (SELECT COUNT(*) FROM s2_pick WHERE room=?) a,"
                      "(SELECT COUNT(*) FROM s2_pick WHERE room=? AND c2 IS NOT NULL) b,"
                      "(SELECT COUNT(*) FROM team_round WHERE room=? AND settled=1) c", (code, code, code))[0]
            return base + (st["rnd"], st["step"], n["a"], n["b"], n["c"])
        if ph == "s3":
            n = _read("SELECT COUNT(*) a, SUM(settled) b, SUM(receiver IS NOT NULL) c FROM s3_round WHERE room=?", (code,))[0]
            st3 = game.s3_step(now, r["phase_start"])
            return base + (st3["rnd"], n["a"], n["b"], n["c"], int((now - r["phase_start"]) // 5))
        if ph == "s4":
            n = _read("SELECT COUNT(*) a, SUM(settled) b, SUM(solved_at IS NOT NULL) c, SUM(wrong) w, SUM(hint) h, SUM(void) v "
                      "FROM s4_team WHERE room=?", (code,))[0]
            return base + (n["a"], n["b"], n["c"], n["w"], n["h"], n["v"], int((now - r["phase_start"]) // 5))
        if ph == "s5":
            st5 = game.s5_step(now, r["phase_start"])
            n = _read("SELECT COUNT(*) a FROM s5_rans WHERE room=? AND q=?", (code, st5["q"]))[0]
            m = _read("SELECT SUM(settled) b FROM s5_rteam WHERE room=?", (code,))[0]
            return base + (st5["step"], st5["q"], n["a"], m["b"])
        if ph in ("s6", "end"):
            n = _read("SELECT COUNT(*) a, SUM(hidden) b, SUM(public) c FROM s6_end WHERE room=?", (code,))[0]
            return base + ((game.s6_step(now, r["phase_start"])["step"] if ph == "s6" else "end"), n["a"], n["b"], n["c"])
        return base
    if p and ph == "s1":
        return base + (game.s1_step(now - r["phase_start"])[1], p["card"])
    if p and ph == "s2" and p["team"] is not None:
        sh = shifts(code, p["team"])
        st = game.s2_step(now, r["phase_start"], sh)
        rnd = st["rnd"]
        mine = _read("SELECT c1,c2 FROM s2_pick WHERE room=? AND rnd=? AND pid=?", (code, rnd, p["id"]))
        n = _read("SELECT COUNT(*) n FROM s2_pick WHERE room=? AND rnd=?", (code, rnd))[0]["n"]
        tr = _read("SELECT settled FROM team_round WHERE room=? AND team=? AND stage=2 AND rnd=?",
                   (code, p["team"], rnd))
        used = bool(_read("SELECT 1 FROM skill_use WHERE room=? AND pid=?", (code, p["id"])))
        return base + (rnd, st["step"], sh.get(rnd), n,
                       (mine[0]["c1"], mine[0]["c2"]) if mine else None,
                       (tr[0]["settled"],) if tr else None, used)
    if p and ph == "s3" and p["team"] is not None:
        return base + _s3_fp(code, p, now)
    if p and ph == "s4" and p["team"] is not None:
        return base + _s4_fp(code, p, now)
    if p and ph == "s5" and p["team"] is not None:
        return base + _s5_fp(code, p, now)
    if p and ph in ("s6", "end") and p["team"] is not None:
        return base + _s6_fp(code, p, now)
    return base


# ---------- 備份 ----------
def backup(tag="manual", keep=5):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.db"
    src = _connect()
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(BACKUP_DIR.glob("*.db"))[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def backup_bytes():
    return backup("manual").read_bytes()


# ======================= 階段 1：名帖 =======================
def _card_text(v, allowed):
    """名帖的固定選項：不在選項裡的一律不收。"""
    v = str(v or "").strip()
    return v if v in allowed else None


def save_card(token, drink, holiday, extra=""):
    """名帖：飲料、假日型態只能選固定選項；最後的「其他想做的介紹」是選填的自由文字（去空白、限長）。"""
    drink, holiday = _card_text(drink, game.DRINKS), _card_text(holiday, game.HOLIDAYS)
    extra = " ".join(str(extra or "").split())[:game.CARD_EXTRA_MAX]
    if not drink or not holiday:
        return False
    with tx() as c:
        p = c.execute("SELECT * FROM players WHERE token=? AND is_bot=0", (token,)).fetchone()
        if not p:
            return False
        r = c.execute("SELECT phase,paused FROM rooms WHERE code=?", (p["room"],)).fetchone()
        if not r or r["phase"] not in ("s1", "s2") or r["paused"]:
            return False
        c.execute("UPDATE players SET card=? WHERE id=?",
                  (json.dumps({"drink": drink, "holiday": holiday, "extra": extra}, ensure_ascii=False), p["id"]))
    return True


def card_done(p):
    """這位玩家是否已送出名帖。"""
    try:
        return "drink" in json.loads(p.get("card") or "{}")
    except ValueError:
        return False


def _card_dict(p):
    try:
        return json.loads(p.get("card") or "{}")
    except ValueError:
        return {}


def card_of(p):
    d = _card_dict(p)
    return d.get("drink", "不透露"), d.get("holiday", "不透露")


def card_extra(p):
    """名帖最後的「其他想做的介紹」，沒寫就是空字串。"""
    return _card_dict(p).get("extra", "") or ""


# ======================= 階段 2：默契大考驗 =======================
def _s2_ctx(c, token, now):
    """共用檢查：回傳 (玩家, 房間, 目前步驟 dict, 該宮 shifts) 或 (None, 錯誤訊息)。"""
    p = c.execute("SELECT * FROM players WHERE token=? AND is_bot=0", (token,)).fetchone()
    if not p or p["team"] is None:
        return None, "找不到玩家資料"
    r = c.execute("SELECT * FROM rooms WHERE code=?", (p["room"],)).fetchone()
    if not r or r["closed"] or r["phase"] != "s2" or r["paused"]:
        return None, "現在不是可以操作的時間"
    sh = shifts(p["room"], p["team"], c)
    return (dict(p), dict(r), game.s2_step(now, r["phase_start"], sh), sh), ""


def _valid_opt(idx):
    return isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(game.S2_BANK[0][1])


def save_pick(token, rnd, idx, now=None):
    """第一次作答：每題一次。回傳 (ok, 訊息)。"""
    now = now or time.time()
    with tx() as c:
        ctx, err = _s2_ctx(c, token, now)
        if not ctx:
            return False, err
        p, r, st, sh = ctx
        if st["rnd"] != rnd or st["step"] != "vote":
            return False, "現在不是作答時間"
        if not _valid_opt(idx):
            return False, "選項不正確"
        try:
            c.execute("INSERT INTO s2_pick(room,rnd,pid,c1) VALUES(?,?,?,?)", (p["room"], rnd, p["id"], idx))
        except sqlite3.IntegrityError:
            return False, "這一題你已經作答了"
    return True, ""


def save_revise(token, rnd, idx, now=None):
    """改答案：討論後的改答案時間內可以改，也可以維持原答案（在這個時間內可以改很多次，以最後一次為準）。"""
    now = now or time.time()
    with tx() as c:
        ctx, err = _s2_ctx(c, token, now)
        if not ctx:
            return False, err
        p, r, st, sh = ctx
        if st["rnd"] != rnd or st["step"] != "revise":
            return False, "現在不是改答案的時間"
        if not _valid_opt(idx):
            return False, "選項不正確"
        row = c.execute("SELECT * FROM s2_pick WHERE room=? AND rnd=? AND pid=?", (p["room"], rnd, p["id"])).fetchone()
        if not row:   # 第一次作答時間內沒選的人，在這裡選就算他的答案
            c.execute("INSERT INTO s2_pick(room,rnd,pid,c1,c2) VALUES(?,?,?,?,?)", (p["room"], rnd, p["id"], idx, idx))
        else:
            c.execute("UPDATE s2_pick SET c2=? WHERE room=? AND rnd=? AND pid=?", (idx, p["room"], rnd, p["id"]))
    return True, ""


def use_skill(token, now=None):
    """使用角色技能（每人整場一次）。被限制時不消耗技能。回傳 (ok, 訊息)。"""
    now = now or time.time()
    with tx() as c:
        p = c.execute("SELECT * FROM players WHERE token=? AND is_bot=0", (token,)).fetchone()
        if not p:
            return False, "找不到玩家資料"
        role = p["role_main"]
        name = game.SKILLS.get(role, ("技能", ""))[0]
        r0 = c.execute("SELECT * FROM rooms WHERE code=?", (p["room"],)).fetchone()
        if r0 and r0["phase"] == "s4":
            return _s4_skill(c, dict(p), dict(r0), now)
        if role not in ("嬤", "妃"):
            return False, f"「{name}」要到後面的關卡才能使用"
        if c.execute("SELECT 1 FROM skill_use WHERE room=? AND pid=?", (p["room"], p["id"])).fetchone():
            return False, "你的技能已經用過了"
        ctx, err = _s2_ctx(c, token, now)
        if not ctx:
            return False, "這個技能要在默契大考驗進行中使用"
        p, r, st, sh = ctx
        rnd = st["rnd"]
        dx, px = sh.get(rnd, (0, 0))
        start = st["b"]["start"]
        if role == "嬤":
            if st["step"] != "discuss":
                return False, "嬤嬤的技能要在「全宮討論」時使用"
            if dx > 0:
                return False, "本宮這一題已有人使用過延時技能，你的技能保留"
            if not game.reveal_len_ok(start, dx + game.SKILL_DISC_EXTRA, px):
                return False, "本題時間已用滿，你的技能保留"
            dx += game.SKILL_DISC_EXTRA
        else:
            if st["step"] != "revise":
                return False, "貴妃的技能要在「改答案」時使用"
            if px > 0:
                return False, "本宮這一題已有人使用過，你的技能保留"
            if not game.reveal_len_ok(start, dx, px + game.SKILL_REV_EXTRA):
                return False, "本題時間已被技能用滿，你的技能保留"
            px += game.SKILL_REV_EXTRA
        c.execute("INSERT OR IGNORE INTO team_round(room,team,stage,rnd) VALUES(?,?,2,?)", (p["room"], p["team"], rnd))
        c.execute("UPDATE team_round SET dx=?, px=? WHERE room=? AND team=? AND stage=2 AND rnd=?",
                  (dx, px, p["room"], p["team"], rnd))
        c.execute("INSERT INTO skill_use(room,pid,skill,stage,rnd,used_at) VALUES(?,?,?,?,?,?)",
                  (p["room"], p["id"], name, 2, rnd, now))
    return True, f"已使用「{name}」"


def s2_view(p, now=None):
    """玩家畫面需要的階段 2 資料（只含本人與本宮可以看的內容）。"""
    now = now or time.time()
    code, team = p["room"], p["team"]
    r = get_room(code)
    sh = shifts(code, team)
    st = game.s2_step(now, r["phase_start"], sh)
    rnd = st["rnd"]
    qidx = json.loads(r["s2_q"] or "[]")
    members = [m for m in list_players(code) if m["team"] == team]
    hids = {m["id"] for m in members if not m["is_bot"]}
    picks = {x["pid"]: x for x in _read("SELECT * FROM s2_pick WHERE room=? AND rnd=?", (code, rnd)) if x["pid"] in hids}
    rows = {x["rnd"]: x for x in _read("SELECT * FROM team_round WHERE room=? AND team=? AND stage=2", (code, team))}
    cur = rows.get(rnd)
    result = json.loads(cur["result"]) if cur and cur["settled"] and cur["result"] else None
    mine = picks.get(p["id"])
    final = (mine["c2"] if mine and mine["c2"] is not None else (mine["c1"] if mine else None))
    agree = None
    dx, px = sh.get(rnd, (0, 0))
    if px > 0:                                   # 貴妃的號令：讓全宮看到目前最多人選的人數
        cs = [x["c2"] if x["c2"] is not None else x["c1"] for x in picks.values()]
        if cs:
            agree = (Counter(cs).most_common(1)[0][1], len(cs))
    round_scores = []
    n_rounds = game.rounds(room_cfg(code), "s2")
    for i in range(n_rounds):
        row = rows.get(i)
        round_scores.append(json.loads(row["result"])["score"] if row and row["settled"] and row["result"] else None)
    trow = _read("SELECT * FROM teams WHERE room=? AND idx=?", (code, team))[0]
    q, opts = game.S2_BANK[qidx[rnd]] if len(qidx) > rnd else ("", [""] * 4)
    return {
        "rnd": rnd, "n_rounds": n_rounds, "step": st["step"], "step_end": st["step_end"], "bounds": st["b"],
        "question": q, "options": list(opts),
        "hint": game.S2_HINTS[rnd % len(game.S2_HINTS)],
        "members": members, "team": trow, "dx": dx, "px": px,
        "my_voted": mine is not None, "n_voted": len(picks), "n_humans": len(hids),
        "my_c1": mine["c1"] if mine else None, "my_c2": mine["c2"] if mine else None, "my_final": final,
        "changed": bool(mine and mine["c2"] is not None and mine["c2"] != mine["c1"]),
        "agree": agree, "result": result,
        "my_hit": (final in result["winners"]) if (result and final is not None) else (False if result else None),
        "round_scores": round_scores, "score": trow["score"],
        "skill_used": bool(_read("SELECT 1 FROM skill_use WHERE room=? AND pid=?", (code, p["id"]))),
    }


def host_scores(code):
    """房主看的各宮階段 2 得分表。"""
    out = []
    rows = _read("SELECT team,rnd,result FROM team_round WHERE room=? AND stage=2 AND settled=1", (code,))
    for t in teams_view(code):
        rs = [None] * game.rounds(room_cfg(code), "s2")
        for x in rows:
            if x["team"] == t["idx"]:
                if x["rnd"] < len(rs):
                    rs[x["rnd"]] = json.loads(x["result"])["score"]
        out.append({"idx": t["idx"], "name": t["name"], "rounds": rs, "score": t["score"]})
    return out


# ======================= 階段 3：密旨傳遞 =======================
def _s3_ts(c, code, team):
    """這個宮已決定的各輪炸彈倒數秒數 {輪: T}。"""
    return {x["rnd"]: x["T"] for x in c.execute("SELECT rnd,T FROM s3_round WHERE room=? AND team=?", (code, team))}


def _s3_members(c, code, team):
    return [dict(x) for x in c.execute("SELECT * FROM players WHERE room=? AND team=? ORDER BY id", (code, team))]


def _s3_receivers(c, code, team, before_rnd):
    return {x["receiver"] for x in c.execute(
        "SELECT receiver FROM s3_round WHERE room=? AND team=? AND rnd<? AND receiver IS NOT NULL", (code, team, before_rnd))}


def _s3_ensure(c, r, team, rnd, rng):
    """建立某宮某輪的資料：決定炸彈秒數與第一位持旨者（優先選還沒當過接旨人的）。"""
    row = c.execute("SELECT * FROM s3_round WHERE room=? AND team=? AND rnd=?", (r["code"], team, rnd)).fetchone()
    if row:
        return dict(row)
    ids = [m["id"] for m in _s3_members(c, r["code"], team)]
    if not ids:
        return None
    fresh = [i for i in ids if i not in _s3_receivers(c, r["code"], team, rnd)] or ids
    first = rng.choice(fresh)
    c.execute("INSERT INTO s3_round(room,team,rnd,T,holder,since,prev,visited) VALUES(?,?,?,?,?,?,?,?)",
              (r["code"], team, rnd, rng.randint(game.S3_T_MIN, game.S3_T_MAX), first,
               r["phase_start"] + game.S3_ROUND * rnd, None, json.dumps([first])))
    return dict(c.execute("SELECT * FROM s3_round WHERE room=? AND team=? AND rnd=?", (r["code"], team, rnd)).fetchone())


def _s3_relay(c, r, row, now, rng):
    """推進接力：持旨超過 5 秒自動傳出；倒數歸零時決定接旨人（已當過且還有人沒當過就「彈開」）。"""
    if row["receiver"] is not None:
        return row
    code, team, rnd = r["code"], row["team"], row["rnd"]
    ids = [m["id"] for m in _s3_members(c, code, team)]
    end_t = r["phase_start"] + game.S3_ROUND * rnd + row["T"]
    holder, since, prev = row["holder"], row["since"], row["prev"]
    visited = json.loads(row["visited"] or "[]")
    while len(ids) > 1:
        expire = since + game.S3_HOLD
        if expire >= end_t or expire > now:
            break
        cand = [i for i in ids if i not in visited and i != holder] \
            or [i for i in ids if i != holder and i != prev] or [i for i in ids if i != holder]
        prev, holder, since = holder, rng.choice(cand), expire
        visited.append(holder)
    recv, bounced = None, 0
    if now >= end_t:
        recv = holder
        earlier = _s3_receivers(c, code, team, rnd)
        fresh = [i for i in ids if i not in earlier]
        if recv in earlier and fresh:
            recv, bounced = rng.choice(fresh), 1
    c.execute("UPDATE s3_round SET holder=?, since=?, prev=?, visited=?, receiver=?, bounced=? WHERE room=? AND team=? AND rnd=?",
              (holder, since, prev, json.dumps(visited), recv, bounced, code, team, rnd))
    return dict(c.execute("SELECT * FROM s3_round WHERE room=? AND team=? AND rnd=?", (code, team, rnd)).fetchone())


def _s3_draw(c, code, team, rnd, diff, rng, keep=None):
    """依難度抽一題：同宮三輪不重複，盡量也不和同場其他宮重複。"""
    used_team = {x["qid"] for x in c.execute("SELECT qid FROM s3_round WHERE room=? AND team=? AND qid IS NOT NULL", (code, team))}
    used_room = {x["qid"] for x in c.execute("SELECT qid FROM s3_round WHERE room=? AND qid IS NOT NULL", (code,))}
    pool = [q[0] for q in game.S3_BANK[diff] if q[0] not in used_team and q[0] != keep]
    pool = [q for q in pool if q not in used_room] or pool or [q[0] for q in game.S3_BANK[diff]]
    qid = rng.choice(pool)
    c.execute("UPDATE s3_round SET diff=?, qid=? WHERE room=? AND team=? AND rnd=?", (diff, qid, code, team, rnd))
    return qid


def _s3_progress(c, r, team, rnd, now, rng):
    """把某宮某輪推進到「現在」：接力、逾時自動抽題、機器人行動、結算。回傳最新的列。"""
    code = r["code"]
    row = _s3_ensure(c, r, team, rnd, rng)
    if not row:
        return None
    row = _s3_relay(c, r, row, now, rng)
    if row["receiver"] is None:
        return row
    b = game.s3_bounds(r["phase_start"] + game.S3_ROUND * rnd, row["T"])
    members = {m["id"]: m for m in _s3_members(c, code, team)}
    recv = members.get(row["receiver"])
    bot = bool(recv and recv["is_bot"])
    if row["qid"] is None:   # 難度與題目全由系統隨機決定，玩家不需要知道難度
        _s3_draw(c, code, team, rnd, rng.choice(game.S3_DIFFS), rng)
        row = dict(c.execute("SELECT * FROM s3_round WHERE room=? AND team=? AND rnd=?", (code, team, rnd)).fetchone())
    if row["qid"] and bot and row["choice"] is None and not row["free"]:
        n = len(game.s3_question(row["qid"])[2])
        c.execute("UPDATE s3_round SET choice=? WHERE room=? AND team=? AND rnd=?", (rng.randrange(n), code, team, rnd))
        row["choice"] = 0
    if now >= b["guess_end"] and not row["settled"]:
        _s3_settle(c, r, team, rnd, now, rng)
    return dict(c.execute("SELECT * FROM s3_round WHERE room=? AND team=? AND rnd=?", (code, team, rnd)).fetchone())


def _s3_settle(c, r, team, rnd, now, rng):
    code = r["code"]
    row = dict(c.execute("SELECT * FROM s3_round WHERE room=? AND team=? AND rnd=?", (code, team, rnd)).fetchone())
    if row["settled"]:
        return
    members = _s3_members(c, code, team)
    mid = {m["id"]: m for m in members}
    diff, _, opts = game.s3_question(row["qid"])
    picks = {x["pid"]: x["choice"] for x in c.execute(
        "SELECT pid,choice FROM s3_guess WHERE room=? AND team=? AND rnd=?", (code, team, rnd))}
    recv = row["receiver"]
    free = bool(row["free"]) or (row["choice"] is None and not mid[recv]["is_bot"])
    for m in members:                       # 機器人隨機作答／猜測，不影響真人
        if m["is_bot"] and m["id"] not in picks and (free or m["id"] != recv):
            picks[m["id"]] = rng.randrange(len(opts))
    if free:
        dist = Counter(picks.values())
        result = {"mode": "free", "diff": diff, "qid": row["qid"], "options": opts, "dist": {str(k): v for k, v in dist.items()},
                  "score": game.S3_FREE_SCORE, "hits": 0, "guessers": len(picks)}
    else:
        gs = {pid: ch for pid, ch in picks.items() if pid != recv}
        hits = sum(1 for ch in gs.values() if ch == row["choice"])
        # 實際在線猜測人數：機器人＋在線的真人（沒猜也算在分母，猜了的人一定算）
        humans_on = [pid for pid, m in mid.items() if not m["is_bot"] and pid != recv and (
            pid in gs or now - (m["last_seen"] or 0) <= game.OFFLINE_SEC)]
        bots_g = [pid for pid, m in mid.items() if m["is_bot"] and pid != recv]
        guessers = len(set(humans_on) | set(bots_g))
        score = game.s3_score(diff, hits, guessers)
        result = {"mode": "scored", "diff": diff, "qid": row["qid"], "options": opts, "answer": row["choice"],
                  "hits": hits, "guessers": guessers, "score": score,
                  "dist": {str(k): v for k, v in Counter(gs.values()).items()}}
    c.execute("UPDATE s3_round SET settled=1, result=? WHERE room=? AND team=? AND rnd=?",
              (json.dumps(result), code, team, rnd))
    c.execute("UPDATE teams SET score=score+? WHERE room=? AND idx=?", (result["score"], code, team))


def _tick_s3(r, now, rng):
    code = r["code"]
    with tx() as c:
        for t in c.execute("SELECT idx FROM teams WHERE room=?", (code,)).fetchall():
            for rnd in range(game.rounds(room_cfg(r), "s3")):
                if now >= r["phase_start"] + game.S3_ROUND * rnd:
                    _s3_progress(c, r, t["idx"], rnd, now, rng)
    if now >= r["deadline"]:
        _advance_next(code, "s3")


def _s3_ctx(c, token, now, rng=None):
    """共用檢查：回傳 ((玩家, 房間, 該輪資料, 步驟 dict), "") 或 (None, 錯誤訊息)。"""
    rng = rng or random.Random()
    p = c.execute("SELECT * FROM players WHERE token=? AND is_bot=0", (token,)).fetchone()
    if not p or p["team"] is None:
        return None, "找不到玩家資料"
    r = c.execute("SELECT * FROM rooms WHERE code=?", (p["room"],)).fetchone()
    if not r or r["closed"] or r["phase"] != "s3" or r["paused"]:
        return None, "現在不是可以操作的時間"
    r = dict(r)
    st = game.s3_step(now, r["phase_start"], _s3_ts(c, r["code"], p["team"]))
    if st["step"] == "done":
        return None, "這個階段已經結束"
    row = _s3_progress(c, r, p["team"], st["rnd"], now, rng)
    if not row:
        return None, "找不到本宮資料"
    st = game.s3_step(now, r["phase_start"], _s3_ts(c, r["code"], p["team"]))
    return (dict(p), r, row, st), ""


def pass_note(token, target, now=None, rng=None):
    """持旨者把密旨傳給隊友（不能傳給剛傳給自己的人）。回傳 (ok, 訊息)。"""
    now = now or time.time()
    with tx() as c:
        ctx, err = _s3_ctx(c, token, now, rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if st["step"] != "relay" or row["receiver"] is not None:
            return False, "密旨已經有人接下了"
        if row["holder"] != p["id"]:
            return False, "密旨不在你手上"
        ids = [m["id"] for m in _s3_members(c, p["room"], p["team"])]
        if target not in ids or target == p["id"]:
            return False, "請選擇一位隊友"
        if target == row["prev"]:
            return False, "不能傳回給剛傳給你的人"
        vis = json.loads(row["visited"] or "[]")
        if target not in vis:
            vis.append(target)
        c.execute("UPDATE s3_round SET holder=?, prev=?, since=?, visited=? WHERE room=? AND team=? AND rnd=?",
                  (target, p["id"], now, json.dumps(vis), p["room"], p["team"], row["rnd"]))
    return True, ""


def _recv_ctx(c, token, now, steps, rng=None):
    ctx, err = _s3_ctx(c, token, now, rng)
    if not ctx:
        return None, err
    p, r, row, st = ctx
    if row["receiver"] != p["id"]:
        return None, "你不是這一輪的接旨人"
    if st["step"] not in steps:
        return None, "現在不是這個操作的時間"
    return ctx, ""


def _rights(c, room, pid):
    c.execute("INSERT OR IGNORE INTO s3_rights(room,pid) VALUES(?,?)", (room, pid))
    return dict(c.execute("SELECT * FROM s3_rights WHERE room=? AND pid=?", (room, pid)).fetchone())


def s3_swap(token, diff=None, now=None, rng=None):
    """換題權：整場一次，系統隨機重抽另一題（難度也隨機），換題後不可再換。"""
    now = now or time.time()
    rng = rng or random.Random()
    with tx() as c:
        ctx, err = _recv_ctx(c, token, now, ("answer",), rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if not row["qid"]:
            return False, "還沒有題目"
        if row["swapped"] or row["free"]:
            return False, "這一輪已經換過題或免答了"
        if _rights(c, p["room"], p["id"])["swap"]:
            return False, "你的換題權已經用過了"
        if row["choice"] is not None:
            return False, "你已經作答，不能換題了"
        _s3_draw(c, p["room"], p["team"], row["rnd"], (rng or random.Random()).choice(game.S3_DIFFS), rng or random.Random(), keep=row["qid"])
        c.execute("UPDATE s3_round SET swapped=1 WHERE room=? AND team=? AND rnd=?", (p["room"], p["team"], row["rnd"]))
        c.execute("UPDATE s3_rights SET swap=1 WHERE room=? AND pid=?", (p["room"], p["id"]))
    return True, "已換題"


def s3_free(token, now=None, rng=None):
    """免答權：本輪不作答，全宮改為「大家一起答」，全宮固定得 200。整場一次；用完仍可在作答時按「跳過」。"""
    now = now or time.time()
    rng = rng or random.Random()
    with tx() as c:
        ctx, err = _recv_ctx(c, token, now, ("answer",), rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        rt = _rights(c, p["room"], p["id"])
        if rt["free"]:
            # 免答權已用完：仍可跳過，效果相同、不消耗任何東西
            pass
        if not row["qid"]:
            _s3_draw(c, p["room"], p["team"], row["rnd"], "輕鬆", rng or random.Random())
        c.execute("UPDATE s3_round SET free=1 WHERE room=? AND team=? AND rnd=?", (p["room"], p["team"], row["rnd"]))
        if not rt["free"]:
            c.execute("UPDATE s3_rights SET free=1 WHERE room=? AND pid=?", (p["room"], p["id"]))
    return True, ""


def s3_answer(token, idx, now=None, rng=None):
    """接旨人暗中選一個選項。"""
    now = now or time.time()
    with tx() as c:
        ctx, err = _recv_ctx(c, token, now, ("answer",), rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if row["free"] or not row["qid"]:
            return False, "這一輪不用作答"
        n = len(game.s3_question(row["qid"])[2])
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < n:
            return False, "選項不正確"
        if row["choice"] is not None:
            return False, "你已經選好了"
        c.execute("UPDATE s3_round SET choice=? WHERE room=? AND team=? AND rnd=?", (idx, p["room"], p["team"], row["rnd"]))
    return True, ""


def _skipped(row, now, b, recv_is_bot=False):
    """這一輪是否改為「大家一起答」：用了免答權、或接旨人逾時沒選。"""
    return bool(row["free"]) or (row["choice"] is None and not recv_is_bot and now >= b["ans_end"] and row["qid"] is not None)


def s3_guess(token, idx, now=None, rng=None):
    """隊友猜接旨人的選擇；「大家一起答」時每人選自己的答案（接旨人也選）。只接受第一次。"""
    now = now or time.time()
    with tx() as c:
        ctx, err = _s3_ctx(c, token, now, rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if row["receiver"] is None or not row["qid"]:
            return False, "現在不是猜測的時間"
        b = st["b"]
        free = _skipped(row, now, b)
        if st["step"] == "guess" or (free and st["step"] in ("answer", "guess")):
            pass
        else:
            return False, "現在不是猜測的時間"
        if row["receiver"] == p["id"] and not free:
            return False, "接旨人不用猜"
        n = len(game.s3_question(row["qid"])[2])
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < n:
            return False, "選項不正確"
        if c.execute("SELECT 1 FROM s3_guess WHERE room=? AND team=? AND rnd=? AND pid=?",
                     (p["room"], p["team"], row["rnd"], p["id"])).fetchone():
            return False, "你已經選過了"
        c.execute("INSERT INTO s3_guess(room,team,rnd,pid,choice) VALUES(?,?,?,?,?)",
                  (p["room"], p["team"], row["rnd"], p["id"], idx))
    return True, ""


def _reveal_ctx(c, token, now, rng=None):
    ctx, err = _s3_ctx(c, token, now, rng)
    if not ctx:
        return None, err
    p, r, row, st = ctx
    if st["step"] not in ("reveal", "buffer") or not row["settled"]:
        return None, "現在不是公布與對話的時間"
    return ctx, ""


def s3_supp(token, text, now=None, rng=None):
    """接旨人補充一句（最多 30 字）：自願，補充後加 100 分。"""
    now = now or time.time()
    text = " ".join(str(text or "").split())[:game.S3_SUPP_MAX]
    if not text:
        return False, "請寫一句話"
    with tx() as c:
        ctx, err = _reveal_ctx(c, token, now, rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if row["receiver"] != p["id"]:
            return False, "只有接旨人可以補充"
        if row["supp"]:
            return False, "已經補充過了"
        if json.loads(row["result"])["mode"] != "scored":
            return False, "這一輪是大家一起答，不用補充"
        c.execute("UPDATE s3_round SET supp=? WHERE room=? AND team=? AND rnd=?", (text, p["room"], p["team"], row["rnd"]))
        c.execute("UPDATE teams SET score=score+? WHERE room=? AND idx=?", (game.S3_SUPP_SCORE, p["room"], p["team"]))
    return True, ""


def s3_clap(token, now=None, rng=None):
    now = now or time.time()
    with tx() as c:
        ctx, err = _reveal_ctx(c, token, now, rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if row["receiver"] == p["id"]:
            return False, "不能替自己鼓掌"
        c.execute("INSERT OR IGNORE INTO s3_clap(room,team,rnd,pid) VALUES(?,?,?,?)", (p["room"], p["team"], row["rnd"], p["id"]))
    return True, ""


def s3_ask(token, kind, now=None, rng=None):
    """追問：只會顯示「有人想追問：…」，不顯示是誰。每人每輪一次。"""
    now = now or time.time()
    if kind not in (0, 1, 2):
        return False, "追問不正確"
    with tx() as c:
        ctx, err = _reveal_ctx(c, token, now, rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if row["receiver"] == p["id"]:
            return False, "接旨人不用追問自己"
        c.execute("INSERT OR IGNORE INTO s3_ask(room,team,rnd,pid,kind) VALUES(?,?,?,?,?)",
                  (p["room"], p["team"], row["rnd"], p["id"], kind))
    return True, ""


def _s3_fp(code, p, now):
    r = get_room(code)
    Ts = {x["rnd"]: x["T"] for x in _read("SELECT rnd,T FROM s3_round WHERE room=? AND team=?", (code, p["team"]))}
    st = game.s3_step(now, r["phase_start"], Ts)
    rnd = st["rnd"]
    row = _read("SELECT * FROM s3_round WHERE room=? AND team=? AND rnd=?", (code, p["team"], rnd))
    row = row[0] if row else {}
    g = _read("SELECT COUNT(*) n, SUM(pid=?) me FROM s3_guess WHERE room=? AND team=? AND rnd=?", (p["id"], code, p["team"], rnd))[0]
    k = _read("SELECT (SELECT COUNT(*) FROM s3_clap WHERE room=? AND team=? AND rnd=?) a,"
              "(SELECT COUNT(*) FROM s3_ask WHERE room=? AND team=? AND rnd=?) b",
              (code, p["team"], rnd, code, p["team"], rnd))[0]
    return (rnd, st["step"], row.get("holder"), row.get("receiver"), row.get("qid"), row.get("choice") is not None,
            row.get("free"), row.get("settled"), row.get("supp"), g["me"], k["a"], k["b"])


def s3_view(p, now=None):
    """玩家畫面需要的階段 3 資料（只含本人與本宮可以看的內容；接旨人的答案在公布前只有接旨人自己知道）。"""
    now = now or time.time()
    code, team = p["room"], p["team"]
    r = get_room(code)
    with tx() as c:   # 取得最新狀態（輪詢本來就會推進；這裡是保險）
        st0 = game.s3_step(now, r["phase_start"], _s3_ts(c, code, team))
        if st0["step"] != "done":
            _s3_progress(c, r, team, st0["rnd"], now, random.Random())
    Ts = {x["rnd"]: x["T"] for x in _read("SELECT rnd,T FROM s3_round WHERE room=? AND team=?", (code, team))}
    st = game.s3_step(now, r["phase_start"], Ts)
    rnd = st["rnd"]
    members = [m for m in list_players(code) if m["team"] == team]
    byid = {m["id"]: m for m in members}
    rows = {x["rnd"]: x for x in _read("SELECT * FROM s3_round WHERE room=? AND team=?", (code, team))}
    row = rows.get(rnd) or {}
    res = json.loads(row["result"]) if row.get("result") else None
    recv = row.get("receiver")
    b = st["b"]
    q = game.s3_question(row["qid"]) if row.get("qid") else None
    is_recv = recv == p["id"]
    bot_recv = bool(recv and byid.get(recv, {}).get("is_bot"))
    free = _skipped(row, now, b, bot_recv) if row else False
    myg = _read("SELECT choice FROM s3_guess WHERE room=? AND team=? AND rnd=? AND pid=?", (code, team, rnd, p["id"]))
    ng = _read("SELECT COUNT(*) n FROM s3_guess WHERE room=? AND team=? AND rnd=?", (code, team, rnd))[0]["n"]
    claps = _read("SELECT COUNT(*) n FROM s3_clap WHERE room=? AND team=? AND rnd=?", (code, team, rnd))[0]["n"]
    my_clap = bool(_read("SELECT 1 FROM s3_clap WHERE room=? AND team=? AND rnd=? AND pid=?", (code, team, rnd, p["id"])))
    asks = [x["kind"] for x in _read("SELECT kind FROM s3_ask WHERE room=? AND team=? AND rnd=? ORDER BY rowid", (code, team, rnd))]
    my_ask = any(x["pid"] == p["id"] for x in _read("SELECT pid FROM s3_ask WHERE room=? AND team=? AND rnd=? AND pid=?",
                                                    (code, team, rnd, p["id"])))
    rt = _read("SELECT * FROM s3_rights WHERE room=? AND pid=?", (code, p["id"]))
    rt = rt[0] if rt else {"swap": 0, "free": 0}
    round_scores = []
    n_rounds = game.rounds(room_cfg(code), "s3")
    for i in range(n_rounds):
        x = rows.get(i)
        round_scores.append(None if not (x and x.get("settled")) else
                            json.loads(x["result"])["score"] + (game.S3_SUPP_SCORE if x.get("supp") else 0))
    trow = _read("SELECT * FROM teams WHERE room=? AND idx=?", (code, team))[0]
    revealed = bool(res)
    return {
        "rnd": rnd, "n_rounds": n_rounds, "step": st["step"], "step_end": st["step_end"], "bounds": b, "members": members, "team": trow,
        "holder": row.get("holder"), "prev": row.get("prev"), "receiver": recv, "bounced": bool(row.get("bounced")),
        "i_hold": row.get("holder") == p["id"] and recv is None,
        "hold_end": (row["since"] + game.S3_HOLD) if row.get("since") else None,
        "is_recv": is_recv, "free": free, "diff": row.get("diff"),
        "qtext": q[1] if q else "", "options": q[2] if q else [],
        "my_choice": row.get("choice") if is_recv else None, "answered": row.get("choice") is not None,
        "my_guess": myg[0]["choice"] if myg else None, "n_guess": ng,
        "result": res, "supp": row.get("supp"), "claps": claps, "my_clap": my_clap, "asks": asks, "my_ask": my_ask,
        "swap_used": bool(rt["swap"]), "free_used": bool(rt["free"]), "swapped": bool(row.get("swapped")),
        "round_scores": round_scores, "score": trow["score"], "revealed": revealed,
        "can_swap": is_recv and bool(row.get("qid")) and not row.get("swapped") and not free and not rt["swap"],
    }


def host_scores3(code):
    """房主看的各宮階段 3 得分表（含補充一句加分）。"""
    out = []
    rows = _read("SELECT team,rnd,result,supp FROM s3_round WHERE room=? AND settled=1", (code,))
    for t in teams_view(code):
        rs = [None] * game.rounds(room_cfg(code), "s3")
        for x in rows:
            if x["team"] == t["idx"]:
                if x["rnd"] < len(rs):
                    rs[x["rnd"]] = json.loads(x["result"])["score"] + (game.S3_SUPP_SCORE if x["supp"] else 0)
        out.append({"idx": t["idx"], "name": t["name"], "rounds": rs, "score": t["score"]})
    return out


def s3_ts_room(code):
    """全場已決定的炸彈秒數（房主畫面估計進度用：各宮不同，取最早出現者；還沒有就用預設）。"""
    out = {}
    for x in _read("SELECT rnd,MIN(T) T FROM s3_round WHERE room=? GROUP BY rnd", (code,)):
        out[x["rnd"]] = x["T"]
    return out


# ======================= 階段 4：宮廷謎題 =======================
import puzzles  # noqa: E402


def _s4_key(r):
    return r["difficulty"] if r["difficulty"] in puzzles.PUZZLES else "中"


def _s4_ensure(c, r, team, rng):
    """建立各宮謎題資料：隨機 A/B 版；把 5 張線索卡分給真人（每人至少 1 張，只存卡號）。"""
    row = c.execute("SELECT * FROM s4_team WHERE room=? AND team=?", (r["code"], team)).fetchone()
    if row:
        return dict(row)
    hs = [m["id"] for m in _s3_members(c, r["code"], team) if not m["is_bot"]]
    cards = {}
    if hs:
        order = hs[:]
        rng.shuffle(order)
        idx = list(range(5))
        rng.shuffle(idx)
        for k, ci in enumerate(idx):
            cards.setdefault(str(order[k % len(order)]), []).append(ci)
    c.execute("INSERT INTO s4_team(room,team,version,cards) VALUES(?,?,?,?)",
              (r["code"], team, rng.choice("AB"), json.dumps(cards)))
    return dict(c.execute("SELECT * FROM s4_team WHERE room=? AND team=?", (r["code"], team)).fetchone())


def _s4_step_of(r, row, now):
    return game.s4_step(now, r["phase_start"], puzzles.PUZZLES[_s4_key(r)]["limit"], row["dx"], row["solved_at"])


def _s4_humans(c, code, team):
    return [m for m in _s3_members(c, code, team) if not m["is_bot"]]


def _s4_settle(c, r, row, now):
    """結算某宮謎題分並加進兵力（每宮只算一次）。"""
    if row["settled"]:
        return
    code, team = r["code"], row["team"]
    lim = puzzles.PUZZLES[_s4_key(r)]["limit"]
    hs = _s4_humans(c, code, team)
    hid = {m["id"] for m in hs}
    done = sum(1 for x in c.execute("SELECT pid FROM s4_badge WHERE room=?", (code,)) if x["pid"] in hid)
    bonus = bool(hs) and done >= game.s4_need_badges(len(hs))
    solve_end = r["phase_start"] + game.S4_PREP + lim + row["dx"]
    solved = row["solved_at"] is not None and row["solved_at"] <= solve_end
    remaining = (solve_end - row["solved_at"]) if solved else 0   # solve_end 已含嬤嬤暫停的秒數，所以暫停不計入耗時
    score = game.s4_score(solved, remaining, lim, row["wrong"], bool(row["waive"]), bool(row["hint_penalty"]), bonus)
    res = {"mode": "solved" if solved else "unsolved", "score": score, "wrong": row["wrong"], "bonus": bonus,
           "badges_done": done, "badges_need": game.s4_need_badges(len(hs)) if hs else 0,
           "waived": bool(row["waive"]), "hint_penalty": bool(row["hint_penalty"]),
           "remaining": round(max(0, remaining), 1) if solved else 0,
           "time_bonus": game.s4_time_bonus(remaining, lim, bool(row["hint_penalty"])) if solved else 0}
    c.execute("UPDATE s4_team SET settled=1, result=? WHERE room=? AND team=?", (json.dumps(res), code, team))
    c.execute("UPDATE teams SET score=score+? WHERE room=? AND idx=?", (score, code, team))


def _tick_s4(r, now, rng):
    code = r["code"]
    with tx() as c:
        for t in c.execute("SELECT idx FROM teams WHERE room=?", (code,)).fetchall():
            row = _s4_ensure(c, r, t["idx"], rng)
            if row["settled"]:
                continue
            st = _s4_step_of(r, row, now)
            if st["step"] in ("buffer", "done") or now >= r["deadline"]:
                _s4_settle(c, r, row, now)
    if now >= _s4_end(r, now):
        _advance_next(code, "s4")


def _s4_end(r, now=None):
    """宮廷謎題的結束時間：最後一宮貢獻分享結束後再 10 秒結算（不超過原本的階段上限）。"""
    rows = _read("SELECT * FROM s4_team WHERE room=?", (r["code"],))
    if not rows:
        return r["deadline"]
    last = max(_s4_step_of(r, row, now or time.time())["b"]["share_end"] for row in rows)
    return min(r["deadline"], last + game.S4_SETTLE)


def _s4_ctx(c, token, now, rng=None):
    rng = rng or random.Random()
    p = c.execute("SELECT * FROM players WHERE token=? AND is_bot=0", (token,)).fetchone()
    if not p or p["team"] is None:
        return None, "找不到玩家資料"
    r = c.execute("SELECT * FROM rooms WHERE code=?", (p["room"],)).fetchone()
    if not r or r["closed"] or r["phase"] != "s4" or r["paused"]:
        return None, "現在不是可以操作的時間"
    r = dict(r)
    row = _s4_ensure(c, r, p["team"], rng)
    if row["void"]:
        return None, "本題已作廢"
    return (dict(p), r, row, _s4_step_of(r, row, now)), ""


def _s4_eval(c, r, row, now):
    """依目前提案決定是否送出：同意人數（含提出者）> N/2。答錯只回報「還不正確」。"""
    prop = json.loads(row["proposal"]) if row["proposal"] else None
    if not prop:
        return
    hs = _s4_humans(c, r["code"], row["team"])
    if len(set(prop["agree"])) * 2 <= len(hs):
        return
    key = _s4_key(r)
    pz = puzzles.PUZZLES[key]
    ans = tuple(pz["items"][i] for i in prop["ans"])
    if puzzles.check(key, ans):
        c.execute("UPDATE s4_team SET solved_at=?, proposal=NULL WHERE room=? AND team=?", (now, r["code"], row["team"]))
    else:
        c.execute("UPDATE s4_team SET wrong=wrong+1, proposal=NULL WHERE room=? AND team=?", (r["code"], row["team"]))


def s4_propose(token, ans, now=None, rng=None):
    """提出答案（ans＝依序選的項目編號，長度 5 且不重複）。換新提案會取代舊的。"""
    now = now or time.time()
    with tx() as c:
        ctx, err = _s4_ctx(c, token, now, rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if st["step"] != "solve" or row["solved_at"] is not None:
            return False, "現在不能提出答案"
        if not isinstance(ans, (list, tuple)) or len(ans) != 5 \
                or any(isinstance(x, bool) or not isinstance(x, int) for x in ans) or sorted(ans) != [0, 1, 2, 3, 4]:
            return False, "每個位置要選不同的項目"
        c.execute("UPDATE s4_team SET proposal=? WHERE room=? AND team=?",
                  (json.dumps({"by": p["id"], "ans": list(ans), "agree": [p["id"]]}), p["room"], p["team"]))
        row = dict(c.execute("SELECT * FROM s4_team WHERE room=? AND team=?", (p["room"], p["team"])).fetchone())
        _s4_eval(c, r, row, now)
    return True, ""


def s4_agree(token, now=None, rng=None):
    now = now or time.time()
    with tx() as c:
        ctx, err = _s4_ctx(c, token, now, rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if st["step"] != "solve" or row["solved_at"] is not None or not row["proposal"]:
            return False, "目前沒有可以同意的答案"
        prop = json.loads(row["proposal"])
        if p["id"] not in prop["agree"]:
            prop["agree"].append(p["id"])
        c.execute("UPDATE s4_team SET proposal=? WHERE room=? AND team=?", (json.dumps(prop), p["room"], p["team"]))
        row["proposal"] = json.dumps(prop)
        _s4_eval(c, r, row, now)
    return True, ""


def s4_badge(token, to_pid, badge, thanks="", now=None, rng=None):
    """貢獻徽章：選一位隊友（不能是自己）＋一種徽章＋選填感謝（≤20 字）。每人一次。"""
    now = now or time.time()
    thanks = " ".join(str(thanks or "").split())[:game.S4_THANKS_MAX]
    if badge not in game.S4_BADGES:
        return False, "徽章不正確"
    with tx() as c:
        ctx, err = _s4_ctx(c, token, now, rng)
        if not ctx:
            return False, err
        p, r, row, st = ctx
        if st["step"] != "share":
            return False, "現在不是貢獻分享的時間"
        if to_pid not in [m["id"] for m in _s3_members(c, p["room"], p["team"])]:
            return False, "請選本宮的一個人"
        if c.execute("SELECT 1 FROM s4_badge WHERE room=? AND pid=?", (p["room"], p["id"])).fetchone():
            return False, "你已經送出徽章了"
        c.execute("INSERT INTO s4_badge(room,pid,to_pid,badge,thanks) VALUES(?,?,?,?,?)",
                  (p["room"], p["id"], to_pid, badge, thanks))
    return True, ""


def _s4_skill(c, p, r, now):
    """階段 4 的角色技能：嬤嬤＝解謎計時暫停 30 秒；內廷總管＝提示卡；嬪妃＝免除全部試錯扣分。每人整場一次。"""
    role = p["role_main"]
    name = game.SKILLS.get(role, ("技能", ""))[0]
    if role not in ("嬤", "內", "嬪"):
        return False, f"「{name}」不在謎題關使用"
    if c.execute("SELECT 1 FROM skill_use WHERE room=? AND pid=?", (p["room"], p["id"])).fetchone():
        return False, "你的技能已經用過了"
    if p["team"] is None or r["paused"] or r["closed"]:
        return False, "現在不能使用"
    row = _s4_ensure(c, r, p["team"], random.Random())
    if row["void"]:
        return False, "本題已作廢"
    st = _s4_step_of(r, row, now)
    if role == "嬤":
        if st["step"] != "solve" or row["solved_at"] is not None:
            return False, "嬤嬤的技能要在「解謎中」使用"
        if row["dx"] > 0:
            return False, "本宮已有人使用過暫停，你的技能保留"
        if st["b"]["end"] - (st["b"]["solve_end"] + game.S4_PAUSE) < game.S4_MIN_SHARE:
            return False, "暫停後分享時間會不夠，你的技能保留"
        c.execute("UPDATE s4_team SET dx=dx+? WHERE room=? AND team=?", (game.S4_PAUSE, p["room"], p["team"]))
    elif role == "內":
        if st["step"] != "solve" or row["solved_at"] is not None:
            return False, "內廷總管的技能要在「解謎中」使用"
        if row["hint"]:
            return False, "本宮的提示卡已經公布了，你的技能保留"
        c.execute("UPDATE s4_team SET hint=1 WHERE room=? AND team=?", (p["room"], p["team"]))
    else:
        if st["step"] not in ("solve", "share") or row["settled"]:
            return False, "嬪妃的技能要在謎題關結算前使用"
        c.execute("UPDATE s4_team SET waive=1 WHERE room=? AND team=?", (p["room"], p["team"]))
    c.execute("INSERT INTO skill_use(room,pid,skill,stage,rnd,used_at) VALUES(?,?,?,?,?,?)",
              (p["room"], p["id"], name, 4, 0, now))
    return True, f"已使用「{name}」"


def s4_host_hint(code, now=None, rng=None):
    """主持人「給提示」：對剩餘時間 ≤ 60 秒、還沒解出、還沒有提示卡的宮公布提示卡（該宮時間獎勵 −200）。回傳 (宮數, 訊息)。"""
    now = now or time.time()
    rng = rng or random.Random()
    r = get_room(code)
    if not r or r["phase"] != "s4" or r["paused"] or r["closed"]:
        return 0, "現在不能給提示"
    n = 0
    with tx() as c:
        for t in c.execute("SELECT idx FROM teams WHERE room=?", (code,)).fetchall():
            row = _s4_ensure(c, dict(r), t["idx"], rng)
            st = _s4_step_of(r, row, now)
            if row["void"] or row["hint"] or row["solved_at"] is not None or st["step"] != "solve":
                continue
            if st["b"]["solve_end"] - now <= game.S4_HINT_LEFT:
                c.execute("UPDATE s4_team SET hint=1, hint_penalty=1 WHERE room=? AND team=?", (code, t["idx"]))
                n += 1
    return n, ("已公布提示卡" if n else "目前沒有剩餘時間 ≤ 60 秒且尚未解出的宮")


def s4_host_void(code, now=None, rng=None):
    """主持人「作廢該題」：全場各宮謎題分固定 1000，流程繼續。"""
    rng = rng or random.Random()
    r = get_room(code)
    if not r or r["phase"] != "s4" or r["closed"]:
        return False
    with tx() as c:
        for t in c.execute("SELECT idx FROM teams WHERE room=?", (code,)).fetchall():
            row = _s4_ensure(c, dict(r), t["idx"], rng)
            if row["settled"]:
                continue
            c.execute("UPDATE s4_team SET void=1, settled=1, result=? WHERE room=? AND team=?",
                      (json.dumps({"mode": "void", "score": game.S4_VOID}), code, t["idx"]))
            c.execute("UPDATE teams SET score=score+? WHERE room=? AND idx=?", (game.S4_VOID, code, t["idx"]))
    return True


def _s4_fp(code, p, now):
    r = get_room(code)
    row = _read("SELECT * FROM s4_team WHERE room=? AND team=?", (code, p["team"]))
    if not row:
        return ("s4-new",)
    row = row[0]
    st = _s4_step_of(r, row, now)
    # 只算本宮的徽章數：別宮有人送徽章不該讓本宮的畫面重新整理（會讓正在填的表單跳回頂端）
    nb = _read("SELECT COUNT(*) n FROM s4_badge b JOIN players q ON q.id=b.pid WHERE b.room=? AND q.team=?",
               (code, p["team"]))[0]["n"]
    mine = bool(_read("SELECT 1 FROM s4_badge WHERE room=? AND pid=?", (code, p["id"])))
    used = bool(_read("SELECT 1 FROM skill_use WHERE room=? AND pid=?", (code, p["id"])))
    return (st["step"], row["wrong"], row["solved_at"] is not None, row["proposal"], row["hint"], row["dx"], row["waive"],
            row["void"], row["settled"], nb, mine, used)


def s4_view(p, now=None):
    """玩家畫面需要的階段 4 資料：只含本人的線索卡；答案要到時間結束或解出才會給（平時只回報「還不正確」）。"""
    now = now or time.time()
    code, team = p["room"], p["team"]
    r = get_room(code)
    with tx() as c:
        _s4_ensure(c, dict(r), team, random.Random())
    row = _read("SELECT * FROM s4_team WHERE room=? AND team=?", (code, team))[0]
    key = _s4_key(r)
    pz = puzzles.PUZZLES[key]
    ver = row["version"]
    st = _s4_step_of(r, row, now)
    b = st["b"]
    members = [m for m in list_players(code) if m["team"] == team]
    humans = [m for m in members if not m["is_bot"]]
    names = {m["id"]: m["name"] for m in members}
    cards = json.loads(row["cards"]).get(str(p["id"]), [])
    prop = json.loads(row["proposal"]) if row["proposal"] else None
    solved = row["solved_at"] is not None and row["solved_at"] <= b["solve_end"]
    over = now >= b["solve_end"] and not solved
    res = json.loads(row["result"]) if row["result"] else None
    items = puzzles.render_items(pz["items"], ver, key)
    people = puzzles.render_items(pz["people"], ver, key) if pz["kind"] == "assign" else [f"第 {i + 1} 位" for i in range(5)]
    show_ans = over or solved
    ans_items = puzzles.render_items(list(pz["answer"]), ver, key)
    ans_text = "、".join((f"{a}＝{x}" if pz["kind"] == "assign" else x) for a, x in zip(people, ans_items)) if show_ans else None
    hid = {m["id"] for m in humans}
    badges = [x for x in _read("SELECT * FROM s4_badge WHERE room=?", (code,)) if x["pid"] in names]
    got = [{"from": names.get(x["pid"], "?"), "badge": x["badge"], "thanks": x["thanks"]} for x in badges if x["to_pid"] == p["id"]]
    mine = next((x for x in badges if x["pid"] == p["id"]), None)
    trow = _read("SELECT * FROM teams WHERE room=? AND idx=?", (code, team))[0]
    return {
        "key": key, "title": pz["title"], "story": puzzles.render(pz["story"], ver, key), "kind": pz["kind"], "limit": pz["limit"],
        "task": pz["task"], "note": puzzles.render(pz["note"], ver, key),
        "step": st["step"], "step_end": _s4_end(r, now) if st["step"] == "buffer" else st["step_end"], "bounds": b, "version": ver,
        "my_cards": [puzzles.render(pz["clues"][i][0], ver, key) for i in cards], "has_cards": bool(cards),
        "items": items, "people": people, "n_humans": len(humans),
        "hint": puzzles.render("提示卡：" + pz["hint"], ver, key) if row["hint"] else None,
        "wrong": row["wrong"], "solved": solved, "over": over, "void": bool(row["void"]),
        "dx": row["dx"], "waive": bool(row["waive"]),
        "answer": ans_text, "logic": puzzles.render(pz["logic"], ver, key) if over else None,
        "proposal": None if not prop else {
            "by": names.get(prop["by"], "?"), "mine": prop["by"] == p["id"],
            "ans": [items[i] for i in prop["ans"]], "agree": [names.get(i, "?") for i in prop["agree"]],
            "i_agreed": p["id"] in prop["agree"], "need": len(humans) // 2 + 1},
        "members": members, "team": trow, "score": trow["score"], "result": res,
        "badge_done": mine is not None, "my_badge": mine,
        "badges_done": len([x for x in badges if x["pid"] in hid]),
        "badges_need": game.s4_need_badges(len(humans)) if humans else 0, "got": got,
        "skill_used": bool(_read("SELECT 1 FROM skill_use WHERE room=? AND pid=?", (code, p["id"]))),
    }


def host_scores4(code):
    out = []
    rows = {x["team"]: x for x in _read("SELECT * FROM s4_team WHERE room=?", (code,))}
    for t in teams_view(code):
        x = rows.get(t["idx"])
        if not x:
            state, sc = "準備中", None
        elif x["void"]:
            state, sc = "作廢", game.S4_VOID
        elif x["settled"]:
            res = json.loads(x["result"])
            state, sc = ("已解出" if res["mode"] == "solved" else "未解出"), res["score"]
        elif x["solved_at"] is not None:
            state, sc = "已解出（分享中）", None
        else:
            state, sc = "解謎中", None
        out.append({"idx": t["idx"], "name": t["name"], "state": state, "wrong": x["wrong"] if x else 0,
                    "hint": bool(x and x["hint"]), "puzzle": sc, "score": t["score"]})
    return out


def s4_info(code):
    """房主用：本場謎題與答案（只有房主畫面會顯示）。"""
    r = get_room(code)
    pz = puzzles.PUZZLES[_s4_key(r)]
    return {"title": pz["title"], "key": _s4_key(r), "limit": pz["limit"], "answer": list(pz["answer"]), "logic": pz["logic"],
            "hint": pz["hint"], "people": pz.get("people"), "kind": pz["kind"]}


# ======================= 階段 5：兵臨城下・萬馬奔騰 =======================
S5_BOT_P = {"易": 0.8, "中": 0.6, "難": 0.4}      # 機器人（NPC）答對的機率


def _s5_ensure(c, r):
    code = r["code"]
    if c.execute("SELECT 1 FROM s5_rteam WHERE room=? LIMIT 1", (code,)).fetchone():
        return
    for t in c.execute("SELECT idx,score FROM teams WHERE room=? ORDER BY idx", (code,)).fetchall():
        c.execute("INSERT INTO s5_rteam(room,team,before) VALUES(?,?,?)", (code, t["idx"], t["score"]))


def _s5_team_ratio(c, code, team, q, guard_q):
    """某宮第 q 題的答對比例（全宮人數為分母；沒作答算答錯；機器人依難度機率答對）。"""
    qi = game.s5_questions(code)[q]
    diff, _t, _o, ans, _f = game.S5_BANK[qi]
    members = c.execute("SELECT id,is_bot FROM players WHERE room=? AND team=?", (code, team)).fetchall()
    r = c.execute("SELECT phase_start FROM rooms WHERE code=?", (code,)).fetchone()
    q0 = r["phase_start"] + game.S5_INTRO + q * game.S5_QSEC          # 這一題開始作答的時間
    scores = []
    for m in members:
        if m["is_bot"]:
            rg = random.Random(f"{code}-s5-{q}-{m['id']}")
            scores.append(game.s5_answer_score(rg.random() < S5_BOT_P[diff], rg.uniform(3, game.S5_ANS)))
            continue
        x = c.execute("SELECT choice,used FROM s5_rans WHERE room=? AND q=? AND pid=?", (code, q, m["id"])).fetchone()
        scores.append(game.s5_answer_score(bool(x) and x["choice"] == ans, x["used"] if x else 0))
    return game.s5_ratio(scores, len(members), guard_q == q)


def _s5_positions(c, code, upto):
    """各宮跑完第 0..upto 題後的累積前進量（每題最多 1）。upto=-1 代表都還在起跑線。"""
    out = {}
    for t in c.execute("SELECT team,guard_q FROM s5_rteam WHERE room=?", (code,)).fetchall():
        out[t["team"]] = sum((_s5_team_ratio(c, code, t["team"], q, t["guard_q"]) for q in range(upto + 1)), Fraction(0))
    return out


def _s5_settle(c, r):
    code = r["code"]
    rows = {x["team"]: dict(x) for x in c.execute("SELECT * FROM s5_rteam WHERE room=?", (code,))}
    if not rows or all(x["settled"] for x in rows.values()):
        return
    dist = _s5_positions(c, code, game.S5_Q - 1)
    ranks = game.s5_rank_scores(dist)
    for t, x in rows.items():
        rank, gain = ranks[t]
        before = c.execute("SELECT score FROM teams WHERE room=? AND idx=?", (code, t)).fetchone()["score"]
        res = {"rank": rank, "gain": gain, "dist": float(dist[t]), "before": before, "after": before + gain,
               "ratios": [float(_s5_team_ratio(c, code, t, q, x["guard_q"])) for q in range(game.S5_Q)]}
        c.execute("UPDATE teams SET score=? WHERE room=? AND idx=?", (before + gain, code, t))
        c.execute("UPDATE s5_rteam SET result=?, settled=1 WHERE room=? AND team=?", (json.dumps(res), code, t))


def _tick_s5(r, now):
    r = dict(r)
    with tx() as c:
        _s5_ensure(c, r)
        if now - r["phase_start"] >= game.S5_RACE_END or now >= r["deadline"]:
            _s5_settle(c, r)
    if now >= r["deadline"]:
        _advance_next(r["code"], "s5")


def _s5_ctx(c, token, now):
    p = c.execute("SELECT * FROM players WHERE token=? AND is_bot=0", (token,)).fetchone()
    if not p or p["team"] is None:
        return None, "找不到玩家資料"
    r = c.execute("SELECT * FROM rooms WHERE code=?", (p["room"],)).fetchone()
    if not r or r["closed"] or r["phase"] != "s5" or r["paused"]:
        return None, "現在不是可以操作的時間"
    r = dict(r)
    _s5_ensure(c, r)
    return (dict(p), r, game.s5_step(now, r["phase_start"])), ""


def s5_answer(token, q, choice, now=None):
    """作答目前這一題（作答時間內可以改，以最後一次為準）。"""
    now = now or time.time()
    if not isinstance(choice, int) or isinstance(choice, bool) or not 0 <= choice < 3:
        return False, "選項不正確"
    with tx() as c:
        ctx, err = _s5_ctx(c, token, now)
        if not ctx:
            return False, err
        p, r, st = ctx
        if st["step"] != "ask" or q != st["q"]:
            return False, "這一題的作答時間已經過了"
        if c.execute("SELECT 1 FROM s5_rans WHERE room=? AND q=? AND pid=?", (p["room"], q, p["id"])).fetchone():
            return False, "答案送出後不能修改"
        used = now - (r["phase_start"] + game.S5_INTRO + q * game.S5_QSEC)     # 作答花了幾秒（越快分數越高）
        c.execute("INSERT INTO s5_rans(room,q,pid,choice,at,used) VALUES(?,?,?,?,?,?)", (p["room"], q, p["id"], choice, now, used))
    return True, ""


def s5_skill(token, now=None):
    """御前侍衛「橫刀立馬」：作答時間內使用，這一題本宮答對比例至少以 50% 計（每宮一次、每人整場一次）。"""
    now = now or time.time()
    with tx() as c:
        ctx, err = _s5_ctx(c, token, now)
        if not ctx:
            return False, err
        p, r, st = ctx
        name = game.SKILLS.get(p["role_main"], ("技能", ""))[0]
        if p["role_main"] != "衛":
            return False, f"「{name}」在這一關用不到，留給別的關卡"
        if st["step"] != "ask":
            return False, "要在作答時間內使用"
        if c.execute("SELECT 1 FROM skill_use WHERE room=? AND pid=?", (p["room"], p["id"])).fetchone():
            return False, "你的技能已經用過了"
        t = c.execute("SELECT guard_q FROM s5_rteam WHERE room=? AND team=?", (p["room"], p["team"])).fetchone()
        if t["guard_q"] is not None:
            return False, "本宮已經有人用過了，你的技能保留"
        c.execute("UPDATE s5_rteam SET guard_q=? WHERE room=? AND team=?", (st["q"], p["room"], p["team"]))
        c.execute("INSERT INTO skill_use(room,pid,skill,stage,rnd,used_at) VALUES(?,?,?,?,?,?)", (p["room"], p["id"], name, 5, st["q"], now))
    return True, f"已使用「{name}」：這一題本宮至少以 50% 計"


def _s5_race(c, code, r, st):
    """賽道資料：每宮從哪裡跑到哪裡（0–1 的比例）、顏色、名次。"""
    teams = c.execute("SELECT idx,name FROM teams WHERE room=? ORDER BY idx", (code,)).fetchall()
    step, q = st["step"], st["q"]
    if step == "intro":
        a, b = -1, -1
    elif step == "ask":
        a = b = q - 1
    elif step == "run":
        a, b = q - 1, q
    else:
        a = b = game.S5_Q - 1
    pa, pb = _s5_positions(c, code, a), _s5_positions(c, code, b)
    res = {x["team"]: json.loads(x["result"]) for x in c.execute("SELECT team,result FROM s5_rteam WHERE room=? AND settled=1", (code,))}
    return [{"idx": t["idx"], "name": t["name"], "color": game.S5_COLORS[t["idx"] % len(game.S5_COLORS)],
             "from": float(pa.get(t["idx"], 0)) / game.S5_Q, "to": float(pb.get(t["idx"], 0)) / game.S5_Q,
             "rank": res.get(t["idx"], {}).get("rank")} for t in teams]


def s5_view(p, now=None):
    """玩家畫面：目前題目、自己的作答、本宮作答人數、賽道位置；奔馳時公布答案，最後顯示戰果。"""
    now = now or time.time()
    code, team = p["room"], p["team"]
    r = get_room(code)
    with tx() as c:
        _s5_ensure(c, dict(r))
        st = game.s5_step(now, r["phase_start"])
        q = st["q"]
        qi = game.s5_questions(code)[q]
        diff, text, opts, ans, flag = game.S5_BANK[qi]
        mine = c.execute("SELECT choice FROM s5_rans WHERE room=? AND q=? AND pid=?", (code, q, p["id"])).fetchone()
        hum = [x["id"] for x in c.execute("SELECT id FROM players WHERE room=? AND team=? AND is_bot=0", (code, team))]
        done = c.execute(f"SELECT COUNT(*) n FROM s5_rans WHERE room=? AND q=? AND pid IN ({','.join('?' * len(hum)) or 'NULL'})",
                         (code, q, *hum)).fetchone()["n"]
        trow = dict(c.execute("SELECT * FROM s5_rteam WHERE room=? AND team=?", (code, team)).fetchone())
        race = _s5_race(c, code, r, st)
        ratio = float(_s5_team_ratio(c, code, team, q, trow["guard_q"])) if st["step"] in ("run", "charge", "result") else None
        used = bool(c.execute("SELECT 1 FROM skill_use WHERE room=? AND pid=?", (code, p["id"])).fetchone())
    reveal = st["step"] == "run"
    return {"step": st["step"], "q": q, "step_end": st["step_end"], "el": st["el"], "n_q": game.S5_Q,
            "question": text, "options": opts, "flag": flag, "answer": ans if reveal else None,
            "my": mine["choice"] if mine else None, "done": done, "n_humans": len(hum), "race": race,
            "team_ratio": ratio if reveal else None, "guard_q": trow["guard_q"], "skill_used": used,
            "result": json.loads(trow["result"]) if trow["result"] and st["step"] in ("charge", "result", "done") else None,
            "team_idx": team, "run_t": max(0.0, st["el"] - (game.S5_INTRO + q * game.S5_QSEC + game.S5_ANS)) if reveal else 0.0}


def _s5_fp(code, p, now):
    r = get_room(code)
    st = game.s5_step(now, r["phase_start"])
    mine = _read("SELECT q,choice FROM s5_rans WHERE room=? AND pid=? ORDER BY q", (code, p["id"]))
    n = _read("SELECT COUNT(*) n FROM s5_rans WHERE room=? AND q=?", (code, st["q"]))[0]["n"]
    t = _read("SELECT guard_q,settled FROM s5_rteam WHERE room=? AND team=?", (code, p["team"]))
    return (st["step"], st["q"], n, tuple((x["q"], x["choice"]) for x in mine), tuple(t[0].values()) if t else None)


def s5_info(code, now=None):
    """房主用：目前題目與答案、各宮作答人數與賽道。"""
    now = now or time.time()
    r = get_room(code)
    with tx() as c:
        _s5_ensure(c, dict(r))
        st = game.s5_step(now, r["phase_start"])
        qi = game.s5_questions(code)[st["q"]]
        race = _s5_race(c, code, r, st)
        cnt = {}
        for t in race:
            hum = [x["id"] for x in c.execute("SELECT id FROM players WHERE room=? AND team=? AND is_bot=0", (code, t["idx"]))]
            cnt[t["idx"]] = (c.execute(f"SELECT COUNT(*) n FROM s5_rans WHERE room=? AND q=? AND pid IN ({','.join('?' * len(hum)) or 'NULL'})",
                                       (code, st["q"], *hum)).fetchone()["n"], len(hum))
        res = {x["team"]: json.loads(x["result"]) for x in c.execute("SELECT team,result FROM s5_rteam WHERE room=? AND settled=1", (code,))}
    return {"step": st, "bank": game.S5_BANK[qi], "race": race, "count": cnt, "result": res}


# ======================= 階段 6：論功行賞 =======================
def _s6_compute(c, code, rng):
    """論功行賞一開始算一次：最終排名與封號、宮稱號、個人稱號、每人的正向數據。"""
    names = {x["idx"]: x["name"] for x in c.execute("SELECT idx,name FROM teams WHERE room=?", (code,))}
    score = {x["idx"]: x["score"] for x in c.execute("SELECT idx,score FROM teams WHERE room=?", (code,))}
    ranking = []
    for rank, t, v in game.rank_with_ties(list(score.items())):
        ranking.append({"rank": rank, "idx": t, "name": names[t], "score": v,
                        "title": game.RANK_TITLE[min(rank, len(game.RANK_TITLE)) - 1]})
    people = [dict(x) for x in c.execute("SELECT * FROM players WHERE room=? ORDER BY id", (code,))]
    human = {p["id"]: p for p in people if not p["is_bot"]}
    team_of = {p["id"]: p["team"] for p in people}
    # 宮稱號
    team_titles = {}
    s2 = Counter()
    for x in c.execute("SELECT team,result FROM team_round WHERE room=? AND stage=2 AND settled=1", (code,)):
        s2[x["team"]] += json.loads(x["result"])["score"]
    if s2:
        top = max(s2.values())
        for t, v in s2.items():
            if v == top and v > 0:
                team_titles.setdefault(t, []).append("最佳默契宮")
    best, who = None, []
    lim = puzzles.PUZZLES[_s4_key(get_room(code))]["limit"]
    for x in c.execute("SELECT team,result FROM s4_team WHERE room=? AND settled=1", (code,)):
        res = json.loads(x["result"])
        if res.get("mode") != "solved":
            continue
        ratio = (lim - res.get("remaining", 0)) / lim
        if best is None or ratio < best - 1e-9:
            best, who = ratio, [x["team"]]
        elif abs(ratio - best) <= 1e-9:
            who.append(x["team"])
    for t in who:
        team_titles.setdefault(t, []).append("最強合作宮")
    # 個人數據
    mystery, claps, know, tries, badges = {}, Counter(), Counter(), Counter(), Counter()
    rounds = [dict(x) for x in c.execute("SELECT * FROM s3_round WHERE room=? AND settled=1", (code,))]
    ratios = {}
    for rw in rounds:
        res = json.loads(rw["result"])
        recv = rw["receiver"]
        n = c.execute("SELECT COUNT(*) n FROM s3_clap WHERE room=? AND team=? AND rnd=?", (code, rw["team"], rw["rnd"])).fetchone()["n"]
        claps[recv] += n
        if res.get("mode") != "scored":
            continue
        if res.get("guessers"):
            ratios.setdefault(recv, []).append(Fraction(res["hits"], res["guessers"]))
        for g in c.execute("SELECT pid,choice FROM s3_guess WHERE room=? AND team=? AND rnd=?", (code, rw["team"], rw["rnd"])):
            if g["pid"] == recv:
                continue
            tries[g["pid"]] += 1
            know[g["pid"]] += 1 if g["choice"] == res.get("answer") else 0
    for pid, rs in ratios.items():
        mystery[pid] = 2 - sum(rs, Fraction(0)) / len(rs)          # 越難猜分數越高（一定大於 0）
    # 默契指數另外加上「默契大考驗與宮內最多數同答」的次數（只用來算指數，不影響「最懂你」稱號）
    agree, s2n = Counter(), Counter()
    for x in c.execute("SELECT team,rnd,result FROM team_round WHERE room=? AND stage=2 AND settled=1", (code,)):
        win = set(json.loads(x["result"]).get("winners") or [])
        for pk in c.execute("SELECT pid,c1,c2 FROM s2_pick WHERE room=? AND rnd=?", (code, x["rnd"])):
            if team_of.get(pk["pid"]) != x["team"]:
                continue
            ch = pk["c2"] if pk["c2"] is not None else pk["c1"]
            if ch is None:
                continue
            s2n[pk["pid"]] += 1
            agree[pk["pid"]] += 1 if ch in win else 0
    for x in c.execute("SELECT to_pid FROM s4_badge WHERE room=?", (code,)):
        badges[x["to_pid"]] += 1
    only = lambda cnt: {pid: v for pid, v in cnt.items() if pid in human}
    stats = {"最神秘": only(mystery), "最會演": only(claps), "最懂你": only(know), "最佳軍師": only(badges)}
    titles = game.assign_titles(stats, rng)
    per = {}
    for pid, p in human.items():
        per[str(pid)] = {"title": titles.get(pid) or game.role_title(p["role_main"], p["role_sub"]),
                         "fun": bool(titles.get(pid)), "know": know[pid], "tries": tries[pid], "badges": badges[pid],
                         "sync": game.sync_index(know[pid] + agree[pid], tries[pid] + s2n[pid])}
    return {"ranking": ranking, "team_titles": {str(k): v for k, v in team_titles.items()},
            "titles": {str(k): v for k, v in titles.items()}, "people": per}


def _s6_ensure(c, r, rng=None):
    code = r["code"]
    row = c.execute("SELECT data FROM s6_state WHERE room=?", (code,)).fetchone()
    if row:
        return json.loads(row["data"])
    data = _s6_compute(c, code, rng or random.Random(f"s6-{code}"))
    c.execute("INSERT INTO s6_state(room,data) VALUES(?,?)", (code, json.dumps(data, ensure_ascii=False)))
    return data


def _tick_s6(r, now, rng):
    with tx() as c:
        _s6_ensure(c, dict(r), rng)
    if now >= r["deadline"]:
        _advance_next(r["code"], "s6")


def s6_data(code):
    """論功行賞的計算結果（沒有就當場算一次）。"""
    rows = _read("SELECT data FROM s6_state WHERE room=?", (code,))
    if rows:
        return json.loads(rows[0]["data"])
    r = get_room(code)
    with tx() as c:
        return _s6_ensure(c, dict(r))


def common_points(code, me, other):
    """兩位玩家的共同點候選（名帖同選項、測驗同角色、默契大考驗同題同答）。"""
    out = []
    d1, h1 = card_of(me)
    d2, h2 = card_of(other)
    if d1 == d2 and d1 != "不透露":
        out.append(f"都愛喝{d1}")
    if h1 == h2 and h1 != "不透露":
        out.append(f"假日都是「{h1}」")
    if me.get("role_main") and me.get("role_main") == other.get("role_main"):
        out.append(f"測驗結果都是{game.ROLE_NAMES.get(me['role_main'], '')}")
    r = get_room(code)
    qidx = json.loads(r.get("s2_q") or "[]")
    picks = {}
    for x in _read("SELECT rnd,pid,c1,c2 FROM s2_pick WHERE room=? AND pid IN (?,?)", (code, me["id"], other["id"])):
        picks[(x["rnd"], x["pid"])] = x["c2"] if x["c2"] is not None else x["c1"]
    for rnd, qi in enumerate(qidx):
        a, b = picks.get((rnd, me["id"])), picks.get((rnd, other["id"]))
        if a is not None and a == b and qi < len(game.S2_BANK):
            q, opts = game.S2_BANK[qi]
            out.append(f"「{q}」都選了{opts[a]}")
    return out


def _s6_ctx(c, token, now):
    p = c.execute("SELECT * FROM players WHERE token=? AND is_bot=0", (token,)).fetchone()
    if not p:
        return None, "找不到玩家資料"
    r = c.execute("SELECT * FROM rooms WHERE code=?", (p["room"],)).fetchone()
    if not r or r["closed"] or r["phase"] != "s6" or r["paused"]:
        return None, "現在不是可以操作的時間"
    if game.s6_step(now, r["phase_start"])["step"] != "ending":
        return None, "結尾互動的時間已經過了"
    return (dict(p), dict(r)), ""


def s6_submit(token, kind, target=None, text="", public=False, now=None):
    """結尾互動四選一（時間內可以改，以最後一次為準）。回傳 (ok, 訊息)。"""
    now = now or time.time()
    if kind not in game.END_KINDS:
        return False, "請選一個項目"
    with tx() as c:
        ctx, err = _s6_ctx(c, token, now)
        if not ctx:
            return False, err
        p, r = ctx
        code = p["room"]
        tg = None
        if kind != "skip":
            tg = c.execute("SELECT * FROM players WHERE id=? AND room=? AND is_bot=0", (target, code)).fetchone()
            if not tg or tg["id"] == p["id"]:
                return False, "請選一位玩家"
            tg = dict(tg)
        if kind == "thanks":
            if tg["team"] != p["team"]:
                return False, "感謝的對象要是同宮隊友"
            if text not in game.THANKS_PHRASES:
                text = game.clean_short(text)
                if text is None:
                    return False, "這句話有不適合的字，請換個說法"
                if not text:
                    return False, "請選一句話，或自己寫一句"
        elif kind == "common":
            if text not in common_points(code, p, tg):
                return False, "請從列出的共同點裡選一條"
        elif kind == "meet":
            if text not in game.MEET_QS:
                return False, "請選一個小問題"
            public = False
        else:
            text, public = "", False
        c.execute("INSERT INTO s6_end(room,pid,kind,target,text,public,at) VALUES(?,?,?,?,?,?,?) "
                  "ON CONFLICT(room,pid) DO UPDATE SET kind=excluded.kind,target=excluded.target,text=excluded.text,"
                  "public=excluded.public,at=excluded.at,hidden=0",
                  (code, p["id"], kind, tg["id"] if tg else None, text, 1 if public else 0, now))
    return True, "已送出"


def _names(code):
    return {x["id"]: x["name"] for x in list_players(code)}


def wall(code, include_hidden=False):
    """盟約牆：只放玩家選擇「公開」的感謝與共同點，最新的在前，最多 20 則。"""
    nm = _names(code)
    rows = _read("SELECT * FROM s6_end WHERE room=? AND public=1 AND kind IN ('thanks','common') ORDER BY at DESC", (code,))
    out = []
    for x in rows:
        if x["hidden"] and not include_hidden:
            continue
        a, b = nm.get(x["pid"], ""), nm.get(x["target"], "")
        line = f"{a} → {b}：{x['text']}" if x["kind"] == "thanks" else f"{a} 和 {b}{x['text']}"
        out.append({"pid": x["pid"], "line": line, "hidden": bool(x["hidden"])})
    return out[:20]


def allies(code):
    """新盟友：互相選了「想進一步認識」的兩個人（單向選擇永不公開）。"""
    nm = _names(code)
    pick = {x["pid"]: x["target"] for x in _read("SELECT pid,target FROM s6_end WHERE room=? AND kind='meet'", (code,))}
    out = []
    for a, b in pick.items():
        if a < b and pick.get(b) == a:
            out.append((nm.get(a, ""), nm.get(b, "")))
    return out


def s6_hide(code, host_token, pid, hidden=True):
    """房主隱藏（或恢復）盟約牆上的某一則。"""
    with tx() as c:
        if not c.execute("SELECT 1 FROM rooms WHERE code=? AND host_token=?", (code, host_token)).fetchone():
            return False
        return c.execute("UPDATE s6_end SET hidden=? WHERE room=? AND pid=?", (1 if hidden else 0, code, pid)).rowcount == 1


def s6_view(p, now=None):
    """玩家畫面：排名與封號、稱號、結尾互動選項、自己的人物誌。"""
    now = now or time.time()
    code = p["room"]
    r = get_room(code)
    data = s6_data(code)
    st = game.s6_step(now, r["phase_start"]) if r["phase"] == "s6" else {"step": "done", "step_end": 0, "el": 0}
    nm = {x["id"]: x for x in list_players(code)}
    humans_ = [x for x in nm.values() if not x["is_bot"]]
    mine = _read("SELECT * FROM s6_end WHERE room=? AND pid=?", (code, p["id"]))
    mine = mine[0] if mine else None
    me_p = nm[p["id"]]
    cands = []
    for o in humans_:
        if o["id"] == p["id"]:
            continue
        for line in common_points(code, me_p, o):
            cands.append({"pid": o["id"], "name": o["name"], "line": line})
    to_me = _read("SELECT * FROM s6_end WHERE room=? AND target=?", (code, p["id"]))
    pick = {x["pid"]: x["target"] for x in _read("SELECT pid,target FROM s6_end WHERE room=? AND kind='meet'", (code,))}
    thanks = [{"from": nm[x["pid"]]["name"], "badge": x["badge"], "text": x["thanks"]}
              for x in _read("SELECT * FROM s4_badge WHERE room=? AND to_pid=?", (code, p["id"])) if x["pid"] in nm]
    end_to_me = []
    for x in to_me:
        who = nm.get(x["pid"], {}).get("name", "")
        if x["kind"] == "thanks":
            end_to_me.append(f"{who} 謝謝你：{x['text']}")
        elif x["kind"] == "common":
            end_to_me.append(f"你和 {who} {x['text']}")
        elif x["kind"] == "meet":
            if pick.get(p["id"]) == x["pid"]:
                end_to_me.append(f"你和 {who} 互相想認識彼此，結為新盟友！（{x['text']}）")
            else:
                end_to_me.append(f"有人想認識你：{x['text']}")
    team = next((t for t in data["ranking"] if t["idx"] == p["team"]), None)
    me = data["people"].get(str(p["id"]), {"title": game.role_title(p["role_main"], p["role_sub"]), "know": 0, "tries": 0, "badges": 0, "fun": False,
                                           "sync": game.sync_index(0, 0)})
    return {
        "step": st["step"], "step_end": st["step_end"], "ranking": data["ranking"], "team_titles": data["team_titles"],
        "titles": {nm[int(k)]["name"]: v for k, v in data["titles"].items() if int(k) in nm},
        "team": team, "me": me, "mine": mine,
        "mates": [x for x in humans_ if x["team"] == p["team"] and x["id"] != p["id"]],
        "others": [x for x in humans_ if x["id"] != p["id"]], "cands": cands,
        "thanks": thanks, "end_to_me": end_to_me, "card": card_of(me_p), "extra": card_extra(me_p),
        "prefs": get_prefs(code, token=p["token"]), "wall": wall(code),
    }


def _s6_fp(code, p, now):
    r = get_room(code)
    st = game.s6_step(now, r["phase_start"])["step"] if r["phase"] == "s6" else "end"
    mine = _read("SELECT kind,target,text,public FROM s6_end WHERE room=? AND pid=?", (code, p["id"]))
    to_me = _read("SELECT pid,kind,text FROM s6_end WHERE room=? AND target=? ORDER BY pid", (code, p["id"]))
    w = _read("SELECT COUNT(*) n, SUM(hidden) h FROM s6_end WHERE room=? AND public=1", (code,))[0]
    return (st, tuple(tuple(x.values()) for x in mine), tuple(tuple(x.values()) for x in to_me), w["n"], w["h"],
            _read("SELECT prefs FROM players WHERE id=?", (p["id"],))[0]["prefs"])


def final_csv(code):
    """主持人下載：暱稱、宮、角色、稱號、兵力（不含任何私人選擇）。"""
    import csv
    import io
    data = s6_data(code)
    tn = {t["idx"]: (t["name"], t["score"]) for t in teams_view(code)}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["暱稱", "宮", "角色", "稱號", "兵力"])
    for p in humans(code):
        info = data["people"].get(str(p["id"]), {})
        t = tn.get(p["team"], ("", ""))
        w.writerow([p["name"], t[0], game.ROLE_NAMES.get(p["role_main"], ""), info.get("title", ""), t[1]])
    return buf.getvalue().encode("utf-8-sig")
