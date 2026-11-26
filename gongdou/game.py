"""宮鬥篇規則層（純 Python，不依賴 Streamlit，方便測試）。

內容：暱稱驗證、心理測驗計分、角色判定、宮數與分宮演算法、機器人名單、宮名池。
"""
import json
import random
import unicodedata
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent

# ---------- 常數（對應規格書） ----------
MIN_PLAYERS = 8          # 總人數不足 8 才補機器人
MAX_PLAYERS = 30         # 每房最多 30 位真人
QUIZ_SEC = 180           # 心理測驗總時間 3:00
INTRO_SEC = 9            # 穿越動畫與內侍對話（不計入 3:00 的緩衝）
OFFLINE_SEC = 10         # 超過 10 秒沒有輪詢視為離線
NAME_MAX = 12

ROLE_CODES = "嬤內妃嬪衛"
PRIORITY = "妃內嬤衛嬪"  # 同分時的優先序
ROLE_NAMES = {"嬤": "掌事嬤嬤", "內": "內廷總管", "妃": "貴妃", "嬪": "嬪妃", "衛": "御前侍衛"}
ROLE_ADJ = {"嬤": "謹慎", "內": "機靈", "妃": "霸氣", "嬪": "純真", "衛": "俠義"}

BOT_NAMES = ["襲人", "晴雯", "平兒", "鴛鴦", "紫鵑", "香菱", "麝月", "小紅"]

TEAM_POOL = [
    ("碎玉軒", "靜如止水，動若驚鴻"),
    ("翊坤宮", "鋒芒畢露，無懼風雨"),
    ("延禧宮", "厚積薄發，後來居上"),
    ("儲秀宮", "秀外慧中，運籌帷幄"),
    ("鍾粹宮", "同心同德，共襄盛舉"),
    ("瀟湘館", "詩酒相伴，清雅同行"),
    ("怡紅院", "笑語盈庭，歡樂不歇"),
    ("稻香村", "豐衣足食，平安是福"),
]

with open(BASE / "data" / "questions.json", encoding="utf8") as _f:
    QUESTIONS = json.load(_f)  # [[場景, 題目, [[選項, 主分角色, 副分角色] x4]] x10]


# ---------- 暱稱 ----------
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def name_key(raw: str) -> str:
    """比較用暱稱：全形半形統一、忽略大小寫與零寬字元、去除前後空白。"""
    s = unicodedata.normalize("NFKC", str(raw)).translate(_ZERO_WIDTH)
    return s.strip().casefold()


def validate_name(raw):
    """回傳 (ok, 訊息, 乾淨暱稱, 比較用 key)。"""
    if raw is None:
        return False, "請輸入暱稱", "", ""
    clean = unicodedata.normalize("NFKC", str(raw)).translate(_ZERO_WIDTH).strip()
    if not clean:
        return False, "暱稱不可空白", "", ""
    if len(clean) > NAME_MAX:
        return False, f"暱稱最多 {NAME_MAX} 個字", "", ""
    if any(ord(c) < 32 for c in clean):
        return False, "暱稱含有不能使用的字元", "", ""
    return True, "", clean, name_key(clean)


def valid_room_code(raw) -> bool:
    return isinstance(raw, str) and len(raw) == 6 and raw.isdigit()


# ---------- 心理測驗計分（主分 +2、副分 +1） ----------
def score_answers(answers):
    """answers：每題選了「原始順序」的第幾個選項（0–3），未答為 None。
    回傳 (sc, p2, 已答題數)。非法資料會被忽略，不會丟例外。"""
    sc = {r: 0 for r in ROLE_CODES}
    p2 = {r: 0 for r in ROLE_CODES}
    n = 0
    if not isinstance(answers, (list, tuple)):
        return sc, p2, 0
    for i, a in enumerate(answers[: len(QUESTIONS)]):
        if isinstance(a, bool) or not isinstance(a, int):
            continue
        opts = QUESTIONS[i][2]
        if not 0 <= a < len(opts):
            continue
        _, p, s = opts[a]
        sc[p] += 2
        sc[s] += 1
        p2[p] += 1
        n += 1
    return sc, p2, n


def rank_roles(sc, p2):
    """依規格：分數 → +2 次數 → 優先序。回傳角色代碼由高到低。"""
    return sorted(ROLE_CODES, key=lambda r: (-sc[r], -p2[r], PRIORITY.index(r)))


def system_roles(rng):
    """完全沒作答者：系統隨機指派主、副角色，並給一組合成分數讓畫面一致。"""
    main, sub = rng.sample(list(ROLE_CODES), 2)
    sc = {r: 0 for r in ROLE_CODES}
    p2 = {r: 0 for r in ROLE_CODES}
    sc[main], sc[sub], p2[main] = 2, 1, 1
    return main, sub, sc, p2


def random_answers(rng):
    return [rng.randrange(len(q[2])) for q in QUESTIONS]


# ---------- 分宮 ----------
def team_count(total):
    if not MIN_PLAYERS <= total <= MAX_PLAYERS:
        raise ValueError(f"總人數必須在 {MIN_PLAYERS}–{MAX_PLAYERS}，目前 {total}")
    if total == 8:
        return 2
    if total <= 15:
        return 3
    if total <= 20:
        return 4
    if total <= 25:
        return 5
    return 6


def team_sizes(total):
    k = team_count(total)
    base, rem = divmod(total, k)
    return [base + 1] * rem + [base] * (k - rem)  # 由大到小


def _dups(team):
    c = Counter(r for _, r in team)
    return sum(v - 1 for v in c.values() if v > 1)


def group_players(human_roles, n_bots, rng):
    """human_roles：{玩家id: 角色代碼}。回傳 (teams, bot_roles)。
    teams[i] = [(玩家id, 角色), ...]；bot_roles[i] = [機器人角色, ...]。
    真人平均分到各宮，同角色盡量分散，機器人補在最缺的角色。"""
    humans = list(human_roles.items())
    total = len(humans) + n_bots
    sizes = team_sizes(total)
    k = len(sizes)
    sizes = [max(sizes)] * k          # 每宮人數一樣：真人平均分（差距 ≤1），不足的宮用機器人補齊
    base, rem = divmod(len(humans), k)
    hcap = [base + (1 if i < rem else 0) for i in range(k)]
    assert all(hcap[i] <= sizes[i] for i in range(k))

    by_role = {r: [] for r in ROLE_CODES}
    for pid, r in humans:
        by_role[r].append(pid)
    for r in by_role:
        rng.shuffle(by_role[r])
    order = sorted(ROLE_CODES, key=lambda r: (-len(by_role[r]), PRIORITY.index(r)))

    teams = [[] for _ in range(k)]
    counts = [Counter() for _ in range(k)]
    for r in order:
        for pid in by_role[r]:
            cands = [i for i in range(k) if len(teams[i]) < hcap[i]]
            i = min(cands, key=lambda i: (counts[i][r], len(teams[i]), i))
            teams[i].append((pid, r))
            counts[i][r] += 1

    # 平衡修正：互換兩人若能減少「同宮同角色」重複，就換（最多 200 次）
    for _ in range(200):
        done = True
        for x in range(k):
            for y in range(x + 1, k):
                before = _dups(teams[x]) + _dups(teams[y])
                for ia, a in enumerate(teams[x]):
                    for ib, b in enumerate(teams[y]):
                        if a[1] == b[1]:
                            continue
                        teams[x][ia], teams[y][ib] = b, a
                        if _dups(teams[x]) + _dups(teams[y]) < before:
                            done = False
                            break
                        teams[x][ia], teams[y][ib] = a, b
                    if not done:
                        break
                if not done:
                    break
            if not done:
                break
        if done:
            break

    counts = [Counter(r for _, r in t) for t in teams]
    bot_roles = [[] for _ in range(k)]
    for i in range(k):
        for _ in range(sizes[i] - len(teams[i])):
            r = min(ROLE_CODES, key=lambda r: (counts[i][r], PRIORITY.index(r)))
            counts[i][r] += 1
            bot_roles[i].append(r)
    return teams, bot_roles


def pick_team_names(k, rng):
    return rng.sample(TEAM_POOL, k)


# ======================= 階段 1（入宮冊封）與階段 2（默契大考驗） =======================
from fractions import Fraction  # noqa: E402

S1_STEPS = [("ennoble", 12), ("reveal", 8), ("card", 30), ("meet", 60)]  # 冊封（角色揭曉）、分宮揭曉、名帖、相認
S1_LEN = sum(s for _, s in S1_STEPS)  # 110 秒

S2_ROUNDS = 3
S2_ROUND = 100      # 每題 100 秒
S2_VOTE = 15        # 匿名投票
S2_DISC = 40        # 全宮討論
S2_REVISE = 10      # 改答案（聽完討論後可以改，也可以維持原答案）
S2_MIN_REVEAL = 15  # 技能延時後，公布與對話至少保留 15 秒
S2_LEN = S2_ROUND * S2_ROUNDS
START_SCORE = 1000      # 每宮初始兵力
VOTE_SCORE = {1: 200, 2: 400, 3: 800, 4: 1200, 5: 1600}   # 同宮同選項人數 → 該題兵力
SKILL_DISC_EXTRA = 20   # 嬤嬤：討論 +20 秒
SKILL_REV_EXTRA = 10    # 貴妃：改答案 +10 秒

# 默契大考驗題庫：(題目, [四個選項])。題目貼近現代生活，外層包一點宮廷情境，方便開口聊。
S2_BANK = [
    ("夜深了，御膳房還留著一盞燈。你會端走哪一樣宵夜？", ["炸雞排", "泡麵", "滷味", "洋芋片"]),
    ("奉旨到荒島行宮辦事，只能帶一樣東西上船。你帶什麼？", ["鏟子", "魚竿", "鍋子", "最好的朋友"]),
    ("早朝的鐘響了，你會賴床幾次？", ["立刻起身", "按一兩次就起來", "按到天荒地老", "設超多個鬧鐘，每個都按"]),
    ("出宮辦事遇上大雨，偏偏忘了帶傘。你的第一反應？", ["直接淋雨走回去", "找地方買一把", "待在原地等雨停", "順手拿走旁邊那把"]),
    ("若能向上天求一種法術，你會求什麼？", ["隱形", "時間暫停", "瞬間移動", "用壽命換金銀"]),
    ("宮裡要辦一場大宴，你最想負責哪件事？", ["試吃每一道菜", "排座位和流程", "上台表演一段", "躲在角落納涼"]),
    ("難得放一天假，不用當差。你會怎麼過？", ["睡到自然醒", "出宮逛街吃東西", "找朋友聚一聚", "把房間整理乾淨"]),
    ("被冤枉做錯事，你的第一反應是？", ["立刻解釋清楚", "先沉住氣，私下找證據", "找信得過的人訴苦", "算了，懶得爭"]),
]
S2_HINTS = ["說說你的理由，說不定有人跟你想的一樣。", "聽聽別人怎麼說，等一下還可以改答案。"]
S2_LETTER = "ＡＢＣＤ"

CARD_EXTRA_MAX = 30     # 名帖「其他想做的介紹」的字數上限
DRINKS = ["茶", "咖啡", "氣泡或果汁", "不透露"]
HOLIDAYS = ["宅家充電", "出門走走", "看心情", "不透露"]
ROLE_TAG = {
    "嬤": "宮裡的大小事，都在她腦中", "內": "宮牆再高，也擋不住他的耳朵", "妃": "這座後宮，我說了算",
    "嬪": "有我在的地方，就有笑聲", "衛": "我的劍，只為自己人而出",
}
SKILLS = {
    "嬤": ("從容不迫", "默契關討論中使用：本題討論 +20 秒（時間從公布與對話窗口扣除）；謎題關可讓解謎計時暫停 30 秒。"),
    "內": ("耳聽八方", "謎題關解謎中使用：取得 1 張提示卡（一項已確定的結論），全宮都看得到。"),
    "妃": ("號令六宮", "默契關「改答案」時使用：看到全宮目前最多人選的人數，改答案時間全宮 +10 秒。"),
    "嬪": ("福星庇護", "謎題關使用：免除本宮全部試錯扣分。"),
    "衛": ("橫刀立馬", "兵臨城下作答時使用：這一題本宮答對比例至少以 50% 計，騎兵不會落後太多。"),
}


def half_up10(x):
    """四捨五入到 10（0.5 進位）。不用內建 round，避免銀行家進位與浮點誤差。"""
    return int((Fraction(x) / 10 + Fraction(1, 2)) // 1) * 10


def s1_step(elapsed):
    """階段 1 目前步驟：回傳 (名稱, 序號, 步驟開始秒, 步驟結束秒)。"""
    t = 0
    for i, (name, sec) in enumerate(S1_STEPS):
        if elapsed < t + sec:
            return name, i, t, t + sec
        t += sec
    return "done", len(S1_STEPS), t, t


def s2_bounds(start, dx=0, px=0):
    vote_end = start + S2_VOTE
    disc_end = vote_end + S2_DISC + dx
    rev_end = disc_end + S2_REVISE + px
    return {"start": start, "vote_end": vote_end, "disc_end": disc_end, "rev_end": rev_end,
            "end": start + S2_ROUND}


def s2_step(now, phase_start, shifts=None):
    """階段 2 目前步驟。shifts：{題號: (dx, px)}（該宮因技能造成的延時）。"""
    shifts = shifts or {}
    if now >= phase_start + S2_LEN:
        return {"rnd": S2_ROUNDS - 1, "step": "done", "step_end": phase_start + S2_LEN,
                "b": s2_bounds(phase_start + S2_ROUND * (S2_ROUNDS - 1), *shifts.get(S2_ROUNDS - 1, (0, 0)))}
    rnd = max(0, min(S2_ROUNDS - 1, int((now - phase_start) // S2_ROUND)))
    b = s2_bounds(phase_start + S2_ROUND * rnd, *shifts.get(rnd, (0, 0)))
    for name, key in (("vote", "vote_end"), ("discuss", "disc_end"), ("revise", "rev_end")):
        if now < b[key]:
            return {"rnd": rnd, "step": name, "step_end": b[key], "b": b}
    return {"rnd": rnd, "step": "reveal", "step_end": b["end"], "b": b}


def reveal_len_ok(start, dx, px):
    b = s2_bounds(start, dx, px)
    return b["end"] - b["rev_end"] >= S2_MIN_REVEAL


def s2_vote_score(top):
    """默契關單題得分：同宮「選到同一個選項」的人數（最高票數）越多，兵力越多。
    1 位 200、2 位 400、3 位 800、4 位 1200；5 位（5 人宮）依序加 400 為 1600。沒有任何票則 0。"""
    if top <= 0:
        return 0
    return VOTE_SCORE[min(int(top), max(VOTE_SCORE))]


def s2_hits(winners, picks):
    """選到最多人選項的人數（只用來顯示，不影響兵力）。"""
    return sum(1 for x in picks if x in winners)


# ======================= 階段 3：密旨傳遞 =======================
S3_ROUNDS = 3
S3_ROUND = 100          # 每輪 100 秒
S3_SELECT = 0           # （已取消：難度改由系統隨機抽題，不再有選難度環節）
S3_ANSWER = 20          # 接旨人看題、（選用換題／免答）並作答
S3_GUESS = 15           # 隊友猜測
S3_REVEAL = 35          # 公布與對話
S3_HOLD = 5             # 每次持旨最久 5 秒
S3_T_MIN, S3_T_MAX = 10, 20   # 炸彈倒數（伺服器隨機，玩家看不到數字）
S3_LEN = S3_ROUND * S3_ROUNDS
S3_FREE_SCORE = 200     # 免答／逾時：全宮固定得分
S3_SUPP_SCORE = 100     # 接旨人補充一句加分
S3_SUPP_MAX = 30
S3_DIFFS = ("輕鬆", "中等", "大膽")
S3_V = {"輕鬆": 400, "中等": 600, "大膽": 800}          # 命中價值
S3_SUPP_PRESET = ["沒什麼特別原因，就是直覺", "這是我平常的習慣", "下次再告訴你們"]
S3_ASKS = ["為什麼這樣選？", "有沒有相關的小故事？", "這跟你平常很像嗎？"]
S3_BANK = {
    "輕鬆": [("E1", "早餐你比較愛", ["甜的", "鹹的"]), ("E2", "假日早上", ["睡到自然醒", "早起出門"]),
            ("E3", "追劇習慣", ["一口氣追完", "一集一集慢慢看"]), ("E4", "旅行風格", ["行程排滿", "隨性走走"]),
            ("E5", "聽歌習慣", ["單曲循環", "隨機播放"]), ("E6", "更愛的季節", ["夏天", "冬天"])],
    "中等": [("M1", "突然放假三天，你最可能", ["宅在家充電", "找朋友聚會", "說走就走去旅行"]),
            ("M2", "手機快沒電時，你會先", ["找充電器", "向別人借", "乾脆放下手機"]),
            ("M3", "發現好吃的店，你會", ["默默收藏", "立刻分享給朋友", "帶別人一起去"]),
            ("M4", "小組合作時你最常擔任", ["想點子", "整理細節", "帶氣氛"]),
            ("M5", "朋友遲到 30 分鐘，你會", ["先去逛逛", "傳訊息問問", "其實沒差"]),
            ("M6", "週五晚上最想", ["吃大餐", "看劇耍廢", "運動流汗"])],
    "大膽": [("B1", "如果中了樂透頭獎，第一件事", ["辭職去旅行", "買房", "請大家吃大餐", "先低調觀察"]),
            ("B2", "只能帶一樣東西穿越到古代", ["行動電源", "泡麵", "打火機", "一本百科"]),
            ("B3", "如果能擁有一種超能力", ["讀心", "瞬間移動", "時間暫停", "隱形"]),
            ("B4", "在宮鬥劇裡你最可能演", ["幕後軍師", "笑面虎", "吃瓜群眾", "正義主角"]),
            ("B5", "末日生存中你會擔任", ["領袖", "後勤", "偵察", "活絡氣氛的人"]),
            ("B6", "如果能和一位歷史人物吃飯", ["帝王", "詩人", "將軍", "發明家"])],
}


def s3_question(qid):
    """用題號找題目：回傳 (難度, 題目, 選項)。"""
    for d, lst in S3_BANK.items():
        for q in lst:
            if q[0] == qid:
                return d, q[1], q[2]
    return None


def s3_bounds(start, T):
    """某一輪的各步驟截止時間（T＝炸彈倒數秒數）。公布與對話固定 35 秒，剩下是緩衝。"""
    relay_end = start + T
    sel_end = relay_end + S3_SELECT
    ans_end = sel_end + S3_ANSWER
    gs_end = ans_end + S3_GUESS
    rev_end = gs_end + S3_REVEAL
    return {"start": start, "relay_end": relay_end, "sel_end": sel_end, "ans_end": ans_end,
            "guess_end": gs_end, "reveal_end": rev_end, "end": start + S3_ROUND}


def s3_step(now, phase_start, Ts=None):
    """階段 3 目前的輪與步驟。Ts＝{輪: T}；還沒決定的輪以最長 20 秒估計。"""
    Ts = Ts or {}
    if now >= phase_start + S3_LEN:
        b = s3_bounds(phase_start + S3_ROUND * (S3_ROUNDS - 1), Ts.get(S3_ROUNDS - 1, S3_T_MAX))
        return {"rnd": S3_ROUNDS - 1, "step": "done", "step_end": phase_start + S3_LEN, "b": b}
    rnd = max(0, min(S3_ROUNDS - 1, int((now - phase_start) // S3_ROUND)))
    b = s3_bounds(phase_start + S3_ROUND * rnd, Ts.get(rnd, S3_T_MAX))
    for step, key in (("relay", "relay_end"), ("select", "sel_end"), ("answer", "ans_end"),
                      ("guess", "guess_end"), ("reveal", "reveal_end"), ("buffer", "end")):
        if now < b[key]:
            return {"rnd": rnd, "step": step, "step_end": b[key], "b": b}
    return {"rnd": rnd, "step": "buffer", "step_end": b["end"], "b": b}


def s3_score(diff, hits, guessers):
    """接旨人答案被猜中的比例 r＝hits／guessers；得分＝價值 V × r（四捨五入到 10，0.5 進位）。"""
    if guessers <= 0:
        return S3_FREE_SCORE
    return half_up10(Fraction(S3_V[diff] * hits, guessers))


# ======================= 階段 4：宮廷謎題 =======================
S4_LEN = 300             # 階段總長（秒）
S4_PREP = 20             # 準備
S4_SHARE = 60            # 貢獻分享
S4_SETTLE = 10           # 最後一宮分享結束後的結算畫面秒數
S4_MIN_SHARE = 30        # 嬤嬤暫停後，分享時間至少還要有 30 秒才能使用
S4_PAUSE = 30            # 嬤嬤：解謎計時暫停秒數
S4_HINT_LEFT = 60        # 剩 60 秒時主持人才能「給提示」
S4_BASE, S4_TIME_MAX = 1200, 500
S4_WRONG, S4_WRONG_CAP = 100, 300
S4_BONUS, S4_UNSOLVED, S4_VOID = 300, 500, 1000
S4_HINT_PENALTY = 200
S4_BADGES = ["關鍵線索", "帶大家整理", "提出好問題", "讓氣氛放鬆"]
S4_THANKS_MAX = 20


def s4_step(now, phase_start, limit, dx=0.0, solved_at=None):
    """某宮目前的謎題步驟。dx＝嬤嬤暫停加的秒數；solved_at＝解出的時間（解出後立刻進入分享）。"""
    prep_end = phase_start + S4_PREP
    solve_end = prep_end + limit + dx
    share_start = solved_at if (solved_at is not None and solved_at <= solve_end) else solve_end
    end = phase_start + S4_LEN
    share_end = min(share_start + S4_SHARE, end)
    b = {"start": phase_start, "prep_end": prep_end, "solve_end": solve_end, "share_start": share_start,
         "share_end": share_end, "end": end}
    if now >= end:
        step, step_end = "done", end
    elif now < prep_end:
        step, step_end = "prepare", prep_end
    elif now < share_start:
        step, step_end = "solve", min(share_start, solve_end)
    elif now < share_end:
        step, step_end = "share", share_end
    else:
        step, step_end = "buffer", end
    return {"step": step, "step_end": step_end, "b": b}


def s4_time_bonus(remaining, limit, hint_penalty=False):
    """時間獎勵＝500 × 剩餘秒數 ÷ 限時（四捨五入）；被主持人給提示則再 −200（最少 0）。"""
    tb = int(Fraction(S4_TIME_MAX * max(0.0, remaining)).limit_denominator(1000) / limit + Fraction(1, 2))
    return max(0, tb - S4_HINT_PENALTY) if hint_penalty else tb


def s4_score(solved, remaining, limit, wrong, waived, hint_penalty, bonus):
    """謎題分：解出＝1200＋時間獎勵−試錯(每錯 100，最多 300；嬪妃技能免除)＋分享 300；未解出＝500＋分享 300。"""
    if not solved:
        return S4_UNSOLVED + (S4_BONUS if bonus else 0)
    pen = 0 if waived else min(S4_WRONG_CAP, S4_WRONG * wrong)
    return S4_BASE + s4_time_bonus(remaining, limit, hint_penalty) - pen + (S4_BONUS if bonus else 0)


def s4_need_badges(n):
    """貢獻分享加成條件：完成徽章人數 ≥ ⌈2N/3⌉。"""
    return -(-2 * n // 3)


# ======================= 階段 5：兵臨城下・萬馬奔騰（常識題騎兵競速） =======================
# 所有宮同時出兵：每題 3 選 1 常識題，宮內答對的比例越高，騎兵衝得越遠；5 題後最遠的宮撞開城門奪得「破城頭功」。
S5_INTRO, S5_Q, S5_ANS, S5_RUN, S5_CHARGE, S5_RESULT = 15, 5, 12, 8, 5, 5
S5_QSEC = S5_ANS + S5_RUN                                   # 每題 20 秒：作答 12 秒＋奔馳動畫 8 秒
S5_RACE_END = S5_INTRO + S5_Q * S5_QSEC                     # 115
S5_LEN = S5_RACE_END + S5_CHARGE + S5_RESULT                # 125
S5_RANK_SCORE = [800, 500, 300]                             # 名次獎勵；第 4 名以後都是保底 200
S5_FLOOR = 200
S5_GUARD_MIN = Fraction(1, 2)                               # 侍衛「橫刀立馬」：該題本宮答對比例至少以 50% 計
S5_DIFF_MIX = {"易": 2, "中": 2, "難": 1}                    # 每場 5 題：簡單 2、中等 2、困難 1
S5_COLORS = ["#c0392b", "#2f6f9f", "#2e8b57", "#b8860b", "#7d3c98", "#d35400"]

# 題庫：(難度, 題目, [三個選項], 正確選項, 國旗代碼或 None)。國旗用簡單圖形畫在畫面上。
S5_BANK = [
    ("易", "這是哪一個國家的國旗？", ["瑞士", "丹麥", "奧地利"], 0, "ch"),
    ("易", "水在攝氏幾度會開始結冰？", ["0 度", "4 度", "-10 度"], 0, None),
    ("易", "宮廷劇裡常說的「紫禁城」，現在多稱為？", ["故宮", "頤和園", "圓明園"], 0, None),
    ("易", "下列哪一種動物是哺乳類？", ["鯊魚", "海豚", "企鵝"], 1, None),
    ("易", "中秋節的傳說中，住在月亮上的是？", ["織女", "嫦娥", "白娘子"], 1, None),
    ("中", "這是哪一個國家的國旗？", ["荷蘭", "法國", "俄羅斯"], 1, "fr"),
    ("中", "這是哪一個國家的國旗？", ["義大利", "愛爾蘭", "墨西哥"], 0, "it"),
    ("中", "這是哪一個國家的國旗？", ["挪威", "芬蘭", "瑞典"], 2, "se"),
    ("中", "太陽系中最大的行星是？", ["土星", "木星", "海王星"], 1, None),
    ("中", "被稱為「詩仙」的唐朝詩人是？", ["杜甫", "白居易", "李白"], 2, None),
    ("中", "世界上面積最大的國家是？", ["俄羅斯", "加拿大", "中國"], 0, None),
    ("中", "《三國演義》中「草船借箭」的主角是？", ["周瑜", "諸葛亮", "曹操"], 1, None),
    ("難", "這是哪一個國家的國旗？", ["印尼", "波蘭", "奧地利"], 1, "pl"),
    ("難", "這是哪一個國家的國旗？", ["哥斯大黎加", "寮國", "泰國"], 2, "th"),
    ("難", "人體最大的器官是？", ["肝臟", "肺", "皮膚"], 2, None),
    ("難", "「光年」是什麼的單位？", ["時間", "距離", "亮度"], 1, None),
    ("難", "二十四節氣中，北半球白天最長的是？", ["夏至", "芒種", "立夏"], 0, None),
]


def s5_questions(code):
    """這一場的 5 題（簡單 2、中等 2、困難 1，順序打散；用房號當種子，所以同一場每個人看到的都一樣）。"""
    import random as _r
    rng = _r.Random(f"s5race-{code}")
    pick = []
    for d, n in S5_DIFF_MIX.items():
        pick += rng.sample([i for i, q in enumerate(S5_BANK) if q[0] == d], n)
    rng.shuffle(pick)
    return pick


def s5_step(now, phase_start):
    """目前步驟：intro 出兵準備／ask 作答（q＝第幾題）／run 奔馳／charge 衝向城門／result 戰果／done。"""
    el = now - phase_start
    if el < S5_INTRO:
        return {"step": "intro", "q": 0, "step_end": phase_start + S5_INTRO, "el": el}
    if el < S5_RACE_END:
        q, w = divmod(el - S5_INTRO, S5_QSEC)
        q = int(q)
        base = phase_start + S5_INTRO + q * S5_QSEC
        if w < S5_ANS:
            return {"step": "ask", "q": q, "step_end": base + S5_ANS, "el": el}
        return {"step": "run", "q": q, "step_end": base + S5_QSEC, "el": el}
    if el < S5_RACE_END + S5_CHARGE:
        return {"step": "charge", "q": S5_Q - 1, "step_end": phase_start + S5_RACE_END + S5_CHARGE, "el": el}
    if el < S5_LEN:
        return {"step": "result", "q": S5_Q - 1, "step_end": phase_start + S5_LEN, "el": el}
    return {"step": "done", "q": S5_Q - 1, "step_end": phase_start + S5_LEN, "el": el}


def s5_boundaries():
    b = [S5_INTRO]
    for q in range(S5_Q):
        b += [S5_INTRO + q * S5_QSEC + S5_ANS, S5_INTRO + (q + 1) * S5_QSEC]
    return b + [S5_RACE_END + S5_CHARGE, S5_LEN]


def s5_answer_score(correct, used_sec):
    """單人一題的得分（0–1）：答錯或沒作答 0；答對 0.5＋0.5×剩餘時間比例（越快越高，最快接近 1）。"""
    if not correct:
        return Fraction(0)
    used = min(max(Fraction(used_sec).limit_denominator(1000), Fraction(0)), Fraction(S5_ANS))
    return Fraction(1, 2) + Fraction(1, 2) * (Fraction(S5_ANS) - used) / S5_ANS


def s5_ratio(scores, total, guard=False):
    """宮的一題得分＝全宮得分平均（沒作答算 0，全宮人數為分母）；侍衛擋下的題目至少以 50% 計。"""
    r = sum(scores, Fraction(0)) / total if total else Fraction(0)
    return max(r, S5_GUARD_MIN) if guard else r


def s5_rank_scores(dist):
    """dist＝{宮: 總前進距離}。同距離並列同名次；回傳 {宮: (名次, 兵力獎勵)}。"""
    out = {}
    for rank, t, _v in rank_with_ties(list(dist.items())):
        out[t] = (rank, S5_RANK_SCORE[rank - 1] if rank <= len(S5_RANK_SCORE) else S5_FLOOR)
    return out


# ======================= 房主「遊戲調整」：每個環節玩幾局 =======================
# 心理測驗、冊封（分宮）、論功行賞是整場的骨架，固定進行；其餘可由房主調整（只能在大廳時調整）。
CFG_DEFAULT = {"s2": 3, "s3": 3, "s4": 1, "s5": 1}
CFG_MAX = {"s2": 3, "s3": 3, "s4": 1, "s5": 1}      # 謎題、兵臨城下目前各只有一局
PHASE_ORDER = ["s1", "s2", "s3", "s4", "s5", "s6"]
PHASE_NAME = {"quiz": "心理測驗", "s1": "冊封", "s2": "默契大考驗", "s3": "密旨傳遞", "s4": "宮廷謎題",
              "s5": "兵臨城下", "s6": "論功行賞", "end": "遊戲結束"}


def clean_cfg(cfg):
    """把房主送來的設定整理成合法值（不合法的用預設值）。"""
    out = dict(CFG_DEFAULT)
    for k, mx in CFG_MAX.items():
        try:
            v = int((cfg or {}).get(k, out[k]))
        except (TypeError, ValueError):
            continue
        out[k] = max(0, min(mx, v))
    return out


def rounds(cfg, ph):
    """某環節要玩幾局；s1／s6 固定 1。"""
    if ph in ("s1", "s6"):
        return 1
    return clean_cfg(cfg).get(ph, 0)


def phase_len(ph, cfg):
    n = rounds(cfg, ph)
    return {"s1": S1_LEN, "s2": S2_ROUND * n, "s3": S3_ROUND * n, "s4": S4_LEN, "s5": S5_LEN, "s6": S6_LEN}[ph]


def next_phase(ph, cfg):
    """目前環節的下一個要進行的環節（跳過局數為 0 的環節）；論功行賞之後是 end。"""
    if ph == "s6":
        return "end"
    i = PHASE_ORDER.index(ph) if ph in PHASE_ORDER else -1
    for nx in PHASE_ORDER[i + 1:]:
        if rounds(cfg, nx) > 0:
            return nx
    return "end"


def total_minutes(cfg):
    """預估整場時間（分鐘，不含大廳）：心理測驗約 3 分鐘＋各環節。"""
    sec = INTRO_SEC + QUIZ_SEC
    sec += sum(phase_len(ph, cfg) for ph in PHASE_ORDER if rounds(cfg, ph) > 0)
    return round(sec / 60)


# ======================= 階段 6：論功行賞 =======================
S6_RANK, S6_TITLE, S6_END, S6_CARD = 10, 15, 10, 30
S6_LEN = S6_RANK + S6_TITLE + S6_END + S6_CARD          # 65 秒
S6_STEPS = [("rank", S6_RANK), ("title", S6_TITLE), ("ending", S6_END), ("card", S6_CARD)]
RANK_TITLE = ["定國宮", "安邦宮", "興邦宮", "同心宮"]
TITLE_ORDER = ["最神秘", "最會演", "最懂你", "最佳軍師"]
TITLE_DESC = {"最神秘": "當接旨人時，讓隊友最難猜透", "最會演": "公布時收到最多掌聲", "最懂你": "猜中別人心思最多次",
              "最佳軍師": "收到最多貢獻徽章", "最佳默契宮": "默契大考驗得分最高", "最強合作宮": "最快解開宮廷謎題"}
END_KINDS = {"thanks": "感謝隊友", "common": "共同點", "meet": "想進一步認識", "skip": "今天先不分享"}
THANKS_PHRASES = ["謝謝你的關鍵線索", "你讓我很放鬆", "我欣賞你的想法", "有你在很安心", "謝謝你帶大家討論", "跟你同宮很開心"]
MEET_QS = ["想聽你推薦一部劇", "想知道你假日都去哪", "想聽你推薦一家好吃的", "想知道你最近在迷什麼"]
END_TEXT_MAX = 20
OATH = "今日相識，來日相助"


def s6_step(now, phase_start):
    """論功行賞目前的步驟：rank 全宮受封／title 趣味稱號／ending 結尾互動／card 宮廷人物誌／done。"""
    el = now - phase_start
    t = 0
    for name, sec in S6_STEPS:
        if el < t + sec:
            return {"step": name, "step_end": phase_start + t + sec, "el": el}
        t += sec
    return {"step": "done", "step_end": phase_start + S6_LEN, "el": el}


def s6_boundaries():
    out, t = [], 0
    for _, sec in S6_STEPS:
        t += sec
        out.append(t)
    return out


def rank_with_ties(items):
    """items＝[(key, 分數)]，依分數由高到低；同分並列同名次。回傳 [(名次, key, 分數)]。"""
    s = sorted(items, key=lambda x: -x[1])
    out, prev, rank = [], None, 0
    for i, (k, v) in enumerate(s, 1):
        if v != prev:
            rank, prev = i, v
        out.append((rank, k, v))
    return out


def assign_titles(stats, rng):
    """個人稱號：依 最神秘→最會演→最懂你→最佳軍師 指派；每人最多 1 個；同分隨機；
    最佳者都已有稱號時該稱號空缺（不遞補）。stats＝{稱號: {pid: 分數}}，分數越高越符合
    （最神秘請傳「越難猜分數越高」）；分數 <= 0 的人不列入候選。回傳 {pid: 稱號}。"""
    got = {}
    for t in TITLE_ORDER:
        sc = {pid: v for pid, v in (stats.get(t) or {}).items() if v > 0}
        if not sc:
            continue
        top = max(sc.values())
        cands = sorted(pid for pid, v in sc.items() if v == top and pid not in got)
        if cands:
            got[rng.choice(cands)] = t
    return got


def role_title(role_main, role_sub):
    """沒有拿到個人稱號的人：副角色形容詞＋主角色（例：純真的貴妃）。"""
    return f'{ROLE_ADJ.get(role_sub or role_main, "")}的{ROLE_NAMES.get(role_main, "宮人")}'


BANNED = ["幹", "靠北", "白癡", "白痴", "智障", "去死", "垃圾", "廢物", "醜", "胖", "笨蛋", "滾"]


def clean_short(text, limit=END_TEXT_MAX):
    """結尾互動的自由輸入：去空白、限長；含不雅字回傳 None（請玩家改寫）。"""
    t = " ".join(str(text or "").split())[:limit]
    if any(b in t for b in BANNED):
        return None
    return t


def sync_index(hits, tries):
    """默契指數（玩家只看到分數）：60＋40×(猜中與同答次數＋1)÷(總次數＋2)，四捨五入；一定在 60–100 之間，沒有負面數字。"""
    return int(Fraction(60) + Fraction(40) * (hits + 1) / (tries + 2) + Fraction(1, 2))
