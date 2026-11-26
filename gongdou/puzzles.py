"""宮廷謎題資料與驗證器（規格書附錄 B、C）。

每組 5 張線索卡，全部必要；解唯一。版本 B 只做名稱代換，邏輯與答案結構完全相同。
validate(key) 會對全部 120 種排列窮舉：5 張同時成立恰好 1 解、拿掉任何 1 張解的數量 > 1。
"""
import itertools
import re

# ---------- 易：御花園列隊（排出左→右順序） ----------
EASY_ITEMS = ["紅", "藍", "綠", "黃", "紫"]            # 宮女衣色
# 一個「解」＝ 五個顏色的站位（tuple，左→右）


def _pos(sol, color):
    return sol.index(color) + 1


EASY = {
    "key": "易", "limit": 150, "title": "御花園列隊",
    "story": "五位宮女在御花園列隊拜見皇上，從左到右站成一排。",
    "task": "請排出她們從左到右的順序。",
    "note": "五人的衣色分別是紅、藍、綠、黃、紫。",
    "kind": "order", "items": EASY_ITEMS,
    "clues": [
        ("綠衣站在藍衣的左邊（不一定相鄰）", lambda s: _pos(s, "綠") < _pos(s, "藍")),
        ("綠衣站在最左邊或最右邊", lambda s: _pos(s, "綠") in (1, 5)),
        ("藍衣站在左起第 4 位", lambda s: _pos(s, "藍") == 4),
        ("紫衣站在左起第 3 位", lambda s: _pos(s, "紫") == 3),
        ("黃衣站在紫衣的左邊（不一定相鄰）", lambda s: _pos(s, "黃") < _pos(s, "紫")),
    ],
    "answer": ("綠", "黃", "紫", "藍", "紅"),
    "hint": "紅衣站在最右邊",
    "logic": "卡 3、卡 4 先定藍＝4、紫＝3；綠在最左或最右且在藍左邊，所以綠＝1；黃在紫左邊，只有位置 2 可行，黃＝2；紅＝5。",
    "rename": {"紅": "橙", "藍": "青", "綠": "粉", "黃": "白", "紫": "黑"},
}

# ---------- 中／難：賞賜分配（五位貴人 ↔ 五件寶物） ----------
PEOPLE = ["婉兒", "靜姝", "如意", "清歌", "秋月"]
GIFTS = ["玉如意", "金步搖", "錦緞", "東珠", "瑪瑙"]
_RENAME_GIFT = {"婉兒": "玉瑤", "靜姝": "若蘭", "如意": "碧雲", "清歌": "霜華", "秋月": "明珠",
                "玉如意": "翡翠簪", "金步搖": "銀鈴", "錦緞": "蜀錦", "東珠": "南珠", "瑪瑙": "琥珀"}


def _g(sol, person):
    """sol＝五件寶物依 PEOPLE 順序；回傳某人得到的寶物。"""
    return sol[PEOPLE.index(person)]


MID = {
    "key": "中", "limit": 180, "title": "賞賜分配",
    "story": "皇上賞賜五位貴人五件寶物，每人恰好得到一件。",
    "task": "請找出每個人得到哪一件。",
    "note": "",
    "kind": "assign", "items": GIFTS, "people": PEOPLE,
    "clues": [
        ("靜姝得到金步搖或東珠", lambda s: _g(s, "靜姝") in ("金步搖", "東珠")),
        ("婉兒得到東珠或瑪瑙", lambda s: _g(s, "婉兒") in ("東珠", "瑪瑙")),
        ("清歌沒有得到玉如意", lambda s: _g(s, "清歌") != "玉如意"),
        ("秋月得到金步搖或錦緞", lambda s: _g(s, "秋月") in ("金步搖", "錦緞")),
        ("婉兒得到金步搖或東珠", lambda s: _g(s, "婉兒") in ("金步搖", "東珠")),
    ],
    "answer": ("東珠", "金步搖", "玉如意", "瑪瑙", "錦緞"),
    "hint": "秋月得到錦緞",
    "logic": "卡 2 與卡 5 的交集只有東珠，婉兒＝東珠；靜姝＝金步搖；秋月只剩錦緞；清歌不是玉如意，所以清歌＝瑪瑙、如意＝玉如意。",
    "rename": _RENAME_GIFT,
}

HARD = {
    "key": "難", "limit": 210, "title": "真相只有一個",
    "story": "皇上賞賜五位貴人五件寶物，每人恰好得到一件。",
    "task": "請找出每個人得到哪一件。",
    "note": "線索裡的「如果……那麼……」，只有在條件成立時結果才必定成立。",
    "kind": "assign", "items": GIFTS, "people": PEOPLE,
    "clues": [
        ("如果秋月得到玉如意，那麼清歌得到瑪瑙", lambda s: _g(s, "秋月") != "玉如意" or _g(s, "清歌") == "瑪瑙"),
        ("如意得到東珠或瑪瑙", lambda s: _g(s, "如意") in ("東珠", "瑪瑙")),
        ("如果清歌得到錦緞，那麼婉兒得到東珠", lambda s: _g(s, "清歌") != "錦緞" or _g(s, "婉兒") == "東珠"),
        ("婉兒得到玉如意或瑪瑙", lambda s: _g(s, "婉兒") in ("玉如意", "瑪瑙")),
        ("秋月得到玉如意或瑪瑙", lambda s: _g(s, "秋月") in ("玉如意", "瑪瑙")),
    ],
    "answer": ("玉如意", "錦緞", "東珠", "金步搖", "瑪瑙"),
    "hint": "清歌得到金步搖",
    "logic": "卡 4、卡 5 讓婉兒與秋月拿走玉如意與瑪瑙，如意＝東珠；若秋月＝玉如意則清歌＝瑪瑙矛盾，所以秋月＝瑪瑙、婉兒＝玉如意；若清歌＝錦緞則婉兒＝東珠矛盾，所以清歌＝金步搖、靜姝＝錦緞。",
    "rename": _RENAME_GIFT,
}

PUZZLES = {"易": EASY, "中": MID, "難": HARD}


def space(pz):
    """所有可能的答案（全部排列）。"""
    return list(itertools.permutations(pz["items"]))


def solutions(pz, skip=None):
    """同時滿足所有線索（可略過第 skip 張）的答案。"""
    return [s for s in space(pz) if all(f(s) for i, (_, f) in enumerate(pz["clues"]) if i != skip)]


def validate(key):
    """規格書附錄 B/C 的驗證器：回傳 (ok, 說明)。5 張全成立恰好 1 解、且是登錄的答案；拿掉任何 1 張都 >1 解。"""
    pz = PUZZLES[key]
    full = solutions(pz)
    if full != [tuple(pz["answer"])]:
        return False, f"{key}：全部線索的解是 {full}"
    counts = [len(solutions(pz, skip=i)) for i in range(5)]
    if any(n <= 1 for n in counts):
        return False, f"{key}：有線索不是必要的 {counts}"
    return True, counts


_PAT = {}


def render(text, version, key):
    """版本 A 原樣；版本 B 以名稱代換（長詞優先，避免『玉如意』被『如意』誤換）。"""
    if version != "B":
        return text
    rn = PUZZLES[key]["rename"]
    if key not in _PAT:
        _PAT[key] = re.compile("|".join(sorted(map(re.escape, rn), key=len, reverse=True)))
    return _PAT[key].sub(lambda m: rn[m.group(0)], text)


def render_items(items, version, key):
    return [render(x, version, key) for x in items]


def check(key, answer):
    """answer＝依 items 排列的選擇（版本 A 的名稱，伺服器端以 A 版名稱存取）。"""
    return tuple(answer) == tuple(PUZZLES[key]["answer"])
