#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验言 · 确定性扫描层（L1）

零依赖，只用标准库。输入一段对话，输出结构化 JSON：
  - 逐句分角色（按赛道：占卜师/求测者，或 骗子/受害者）
  - 六维里可以纯规则判定的部分：含糊度、落地度、吓唬-卖药、依赖度、复读机
  - 每一处命中都带行号与原文，供报告引用

不做的事：不调用任何模型，不评估「牌对不对」（那需要牌面数据），不下最终判决。
最终判决由 judge.py（L2）或 --rule-only 模式给出。

赛道（rules/ 下每个子目录一个，改词表不用动代码）：
    mingli     占卜话术（默认，向后兼容 v0.1）
    shazhupan  杀猪盘/投资理财收割话术

用法：
    python scan.py examples/mingli/demo_chat.txt
    python scan.py examples/shazhupan/demo_chat.txt --track shazhupan
    python scan.py examples/mingli/demo_chat.txt --json out.json
    python scan.py examples/mingli/demo_chat.txt --cards "愚者,塔,月亮"
"""

import argparse
import json
import os
import re
import sys

# Windows 控制台默认 GBK，中文会炸
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_DIR = os.path.join(HERE, "rules")
DEFAULT_TRACK = "mingli"

# ---------------------------------------------------------------- 赛道

# 一个赛道 = rules/<name>/ 目录，里面必须有 track.json：
#   reader_aliases / client_aliases  说话人识别词表
#   special_check                    第六维（牌对不对 / 身份对不对）
#   fallback_questions               无模型时的三条反问
_TRACK_CACHE = {}


def available_tracks():
    out = []
    if os.path.isdir(RULES_DIR):
        for name in sorted(os.listdir(RULES_DIR)):
            if os.path.isfile(os.path.join(RULES_DIR, name, "track.json")):
                out.append(name)
    return out


def build_speaker_patterns(cfg):
    """从 track.json 的别名表构建说话人识别正则。

    长别名排前：正则交替是先到先得，把「算命先生」排在「先生」后面
    就永远轮不到它（两者都在行首时被短的抢先）。
    """
    def alt(words):
        words = sorted(set(words), key=len, reverse=True)
        return "|".join(re.escape(w) for w in words)

    prefix = r"^\s*(?:[\d\-/:.\s]{0,20})?"
    return [
        (prefix + "(?:" + alt(cfg["reader_aliases"]) + r")\s*[：:]\s*(.*)$", "reader"),
        (prefix + "(?:" + alt(cfg["client_aliases"]) + r")\s*[：:]\s*(.*)$", "client"),
    ]


def load_track(name):
    """加载赛道配置（含说话人正则），带缓存。未知赛道直接退出并列出可用项。"""
    if name in _TRACK_CACHE:
        return _TRACK_CACHE[name]
    path = os.path.join(RULES_DIR, name, "track.json")
    if not os.path.exists(path):
        raise SystemExit(
            f"未知赛道：{name}（找不到 {path}）。可用：{'、'.join(available_tracks())}"
        )
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("name", name)
    cfg["dir"] = os.path.dirname(path)
    cfg["speaker_patterns"] = build_speaker_patterns(cfg)
    _TRACK_CACHE[name] = cfg
    return cfg

# ---------------------------------------------------------------- 词表加载

# 词表以 Markdown 代码块形式维护（人可读、可改、可 diff）。
# 这里按「## 标题」分节，抽取每节里的代码块行。
SECTION_RE = re.compile(r"^##\s+([A-Z])\.\s*(.+)$")


def load_lexicon(path):
    """解析 巴纳姆词表.md -> {'A': [词...], 'B': [...], ...}"""
    groups = {}
    current = None
    in_code = False
    if not os.path.exists(path):
        return groups
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            m = SECTION_RE.match(line)
            if m:
                current = m.group(1)
                groups.setdefault(current, [])
                in_code = False
                continue
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code and current:
                for tok in line.split():
                    tok = tok.strip()
                    if tok and not tok.startswith("#"):
                        groups[current].append(tok)
    return groups


def load_chain_lexicon(path):
    """解析 恐吓-解药链.md -> {'fear': [...], 'remedy': [...], 'depend': [...]}"""
    groups = {"fear": [], "remedy": [], "depend": []}
    if not os.path.exists(path):
        return groups
    current = None
    in_code = False
    header_map = {"第一环": "fear", "第二环": "remedy", "第三环": "depend"}
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line.startswith("##"):
                for key, val in header_map.items():
                    if key in line:
                        current = val
                continue
            if line.startswith("```"):
                in_code = not in_code
                continue
            if in_code and current:
                for tok in line.split():
                    tok = tok.strip()
                    if tok and not tok.startswith("#"):
                        groups[current].append(tok)
    return groups


def read_text(path):
    """读文本，强制 utf-8-sig。

    Windows 的记事本、PowerShell 5.1 的 Set-Content -Encoding UTF8、Excel 导出
    都会写 UTF-8 BOM。用 encoding="utf-8" 读，BOM 会留在第一行开头，
    既污染第一句话的匹配，又会让「空文件」变成一个只有 BOM 的假句子。
    utf-8-sig 无 BOM 时等价于 utf-8，有 BOM 时自动剥掉。
    """
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


# ---------------------------------------------------------------- 对话解析

# 默认用 mingli 赛道的说话人词表（v0.1 兼容）：
#   我：xxx        占卜师：xxx       师傅：xxx
#   求测者：xxx     客户：xxx         A：xxx  B：xxx
#   2026-10-27 21:03 我：xxx
# 其他赛道（骗子/对方/网友/客服/导师……）由 track.json 提供，见 load_track()。


def parse_dialogue(text, patterns=None):
    """把原文切成 [{line, speaker, text}]，speaker in {reader, client, unknown}"""
    if patterns is None:
        patterns = load_track(DEFAULT_TRACK)["speaker_patterns"]
    out = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        speaker, body = "unknown", line
        for pat, who in patterns:
            m = re.match(pat, line)
            if m:
                speaker, body = who, m.group(1).strip()
                break
        out.append({"line": i, "speaker": speaker, "text": body})
    return out


# ---------------------------------------------------------------- 断句

SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")


def split_sentences(entry):
    """把一条发言切成句子，保留原始行号"""
    parts = [s.strip() for s in SENT_SPLIT.split(entry["text"]) if s.strip()]
    return [{"line": entry["line"], "speaker": entry["speaker"], "text": s} for s in parts]


# ---------------------------------------------------------------- 锚点判定

# 具体时间：这周/月底/三个月内/下个月/今年/3月/15号
TIME_ANCHOR = re.compile(
    r"(这周|本周|下周|这个月|本月|下个月|月底|月初|三个月内|半年内|一年内|"
    r"最近三天|这几天|今晚|明天|后天|今年|明年|"
    r"\d{1,2}\s*月|\d{1,2}\s*号|\d{1,2}\s*日|\d+\s*(?:天|周|个月|年)内)"
)
# 中文数字：占卜场景里报价几乎都用中文（「一千八百块」），
# 早期只写 \d+ 导致金额检测漏掉最典型的一句。
CN_NUM = r"[一二三四五六七八九十百千万零两]+"
# 数字锚点：金额、次数、天数
NUM_ANCHOR = re.compile(
    r"(\d+\s*(?:次|遍|天|周|个月|年|元|块|万)|" + CN_NUM + r"\s*(?:次|遍|天|周|个月|年|元|块|万|百))"
)
# 可执行动作：动词短语，求测者能真去做
ACTION_ANCHOR = re.compile(
    r"(去问|去谈|去查|去体检|去道歉|写下来|列个清单|列出来|约一次|当面说|发消息|"
    r"把.{1,8}(?:说开|写下来|列出来|问清楚)|先别|暂时不|缓一缓|"
    r"试一次|做一次|做一件事|打个电话|找.{1,6}聊|投.{1,4}简历)"
)
# 金额 / 收款：必须同时认中文数字，否则「一千八百块」这种最典型的报价会漏掉。
# v0.2 起加充值/提现/保证金/解冻金：杀猪盘的收款词和占卜不一样，但都算钱。
MONEY = re.compile(
    r"(\d+\s*(?:元|块|万)|" + CN_NUM + r"\s*(?:元|块|万)|"
    r"转账|收款|打款|红包|付款|微信支付|支付宝|扫码|定金|尾款|"
    r"充值|提现|入金|保证金|解冻金|激活费)"
)
# 正确废话（看着像建议，实际无法执行）
EMPTY_ADVICE = re.compile(r"(注意沟通|保持平衡|放平心态|顺其自然|相信自己|多沟通|调整心态|保持耐心|静观其变)")

# 否定语境：占卜师主动声明「我不做这些」时，命中的解药词不算卖药。
# 这是本工具最大的误报来源——把「我拒绝做改运」判成「他在卖改运」。
# 判定范围：同一句内，否定词出现在命中词之前 12 字以内。
NEGATION = re.compile(r"(不做|不会|不搞|不接|不收|不碰|不给人|从来不|没有做|不需要|别信|不要信|不建议)")
NEG_WINDOW = 12


def is_negated(sentence, word):
    """命中词是否处在否定语境里（否定词在它前面 NEG_WINDOW 字内）"""
    idx = sentence.find(word)
    if idx < 0:
        return False
    left = sentence[max(0, idx - NEG_WINDOW):idx]
    return bool(NEGATION.search(left))


def has_anchor(sentence):
    """返回命中的锚点类型列表"""
    hits = []
    if TIME_ANCHOR.search(sentence):
        hits.append("time")
    if NUM_ANCHOR.search(sentence):
        hits.append("num")
    if ACTION_ANCHOR.search(sentence):
        hits.append("action")
    return hits


# ---------------------------------------------------------------- 主扫描


def scan(text, cards=None, track=DEFAULT_TRACK):
    tcfg = load_track(track)
    tdir = tcfg["dir"]
    special = tcfg.get("special_check") or {}
    lexicon = load_lexicon(os.path.join(tdir, "巴纳姆词表.md"))
    chain = load_chain_lexicon(os.path.join(tdir, "恐吓-解药链.md"))

    # 反巴纳姆的 C 组（钓鱼句）单独拎出来，权重不同
    barnum = lexicon.get("A", []) + lexicon.get("B", [])
    fishing = lexicon.get("C", [])
    vague_time = lexicon.get("E", [])
    circular = lexicon.get("D", [])

    entries = parse_dialogue(text, tcfg["speaker_patterns"])
    sentences = []
    for e in entries:
        sentences.extend(split_sentences(e))

    # 只评估占卜师说的话；如果一句都没识别出角色，就全量评估（并在报告里说明）
    reader_sents = [s for s in sentences if s["speaker"] == "reader"]
    role_detected = len(reader_sents) > 0
    target = reader_sents if role_detected else sentences

    hits = {
        "barnum": [],       # 含糊度
        "fishing": [],      # 复读机
        "fear": [],
        "remedy": [],
        "depend": [],
        "empty_advice": [],
        "vague_time": [],
        "circular": [],
    }
    anchors = {"time": [], "num": [], "action": []}

    for s in target:
        t = s["text"]
        rec = {"line": s["line"], "text": t}

        for w in barnum:
            if w in t:
                hits["barnum"].append({**rec, "word": w})
        for w in fishing:
            if w in t:
                hits["fishing"].append({**rec, "word": w})
        for w in chain["fear"]:
            if w in t and not is_negated(t, w):
                hits["fear"].append({**rec, "word": w})
        for w in chain["remedy"]:
            if w in t and not is_negated(t, w):
                hits["remedy"].append({**rec, "word": w})
        for w in chain["depend"]:
            if w in t and not is_negated(t, w):
                hits["depend"].append({**rec, "word": w})
        for w in vague_time:
            if w in t:
                hits["vague_time"].append({**rec, "word": w})
        for w in circular:
            if w in t:
                hits["circular"].append({**rec, "word": w})
        if EMPTY_ADVICE.search(t):
            hits["empty_advice"].append({**rec, "word": EMPTY_ADVICE.search(t).group(0)})

        for kind in has_anchor(t):
            anchors[kind].append(rec)

    total_claims = len(target)

    # ---- 六维中可由规则判定的部分 ----
    dims = {}

    # 1. 含糊度：命中 A/B 且无锚点
    vague_sents = set()
    for h in hits["barnum"]:
        if not has_anchor(h["text"]):
            vague_sents.add(h["line"])
    dims["含糊度"] = round((1 - len(vague_sents) / total_claims) * 100) if total_claims else None

    # 2. 落地度：有锚点、且不是空话
    solid = set()
    for kind in ("time", "num", "action"):
        for a in anchors[kind]:
            if a["text"] not in [e["text"] for e in hits["empty_advice"]]:
                solid.add(a["line"])
    dims["落地度"] = round(len(solid) / total_claims * 100) if total_claims else None

    # 3. 吓唬-卖药：三环判定
    fear_lines = sorted({h["line"] for h in hits["fear"]})
    remedy_lines = sorted({h["line"] for h in hits["remedy"]})
    depend_lines = sorted({h["line"] for h in hits["depend"]})
    chain_state = "无"
    if fear_lines and remedy_lines:
        # 「链」要求解药出现在恐吓之后。若解药全在恐吓之前，说明是别的话题，
        # 不能算链——这是刻意收紧，宁可漏判也不误判。
        ordered = any(after > before for before in fear_lines for after in remedy_lines)
        if ordered:
            chain_state = "恐惧营销"
            if depend_lines:
                chain_state = "操控性话术"
        else:
            chain_state = "仅恐吓词"
    elif fear_lines:
        chain_state = "仅恐吓词"
    if chain_state == "无":
        dims["吓唬-卖药"] = 100
    elif chain_state == "仅恐吓词":
        dims["吓唬-卖药"] = 60
    elif chain_state == "恐惧营销":
        dims["吓唬-卖药"] = 25
    else:
        dims["吓唬-卖药"] = 0

    # 金额 / 收款
    money = MONEY.findall(text)
    if money and chain_state in ("恐惧营销", "操控性话术"):
        dims["吓唬-卖药"] = 0

    # 4. 依赖度
    dep = 100 - 25 * len(depend_lines)
    if any("别人说" in h["text"] or "问其他人" in h["text"] for h in hits["depend"]):
        dep -= 40
    give_back = len(re.findall(r"(你自己决定|你来决定|你可以改|选择权在你|牌只显示当下|仅供参考|你可以不)", text))
    dep = min(100, dep + 10 * give_back)
    dims["依赖度"] = max(0, dep)

    # 6. 复读机
    rep = 100 - 20 * len({h["line"] for h in hits["fishing"]})
    echo = len(re.findall(r"(你刚才说的|你前面提到|正如你说的|你自己也说了)", text))
    rep -= 25 * echo
    dims["复读机"] = max(0, rep)

    # 5. 第六维（赛道专属）：占卜是「牌对不对」，杀猪盘是「身份对不对」
    dim6 = special.get("dim_name", "牌对不对")
    dims[dim6] = None
    card_findings = []
    identity_findings = []
    if special.get("kind") == "cards" and cards:
        base_path = os.path.join(tdir, special.get("base", "牌义基准.json"))
        if os.path.exists(base_path):
            with open(base_path, encoding="utf-8") as f:
                base = json.load(f)
            by_name = {c["name"]: c for c in base.get("cards", [])}
            for name in cards:
                name = name.strip()
                if name not in by_name:
                    continue
                for misuse in by_name[name]["misuse"]:
                    for s in target:
                        if name in s["text"] and misuse in s["text"]:
                            card_findings.append(
                                {"line": s["line"], "card": name, "word": misuse, "text": s["text"]}
                            )
            if card_findings:
                dims[dim6] = max(0, 100 - 30 * len(card_findings))
    elif special.get("kind") == "identity":
        # 身份矛盾：自称 X，却在谈 X 绝不可能谈的事。
        # 军人不网上带人投资，交易所内部人员不拉散户，公检法不电话要验证码。
        # 信号和违禁词可以在不同行——骗子先立人设、后图穷匕见，隔着几十行也照样是矛盾。
        base_path = os.path.join(tdir, special.get("base", "身份基准.json"))
        if os.path.exists(base_path):
            with open(base_path, encoding="utf-8") as f:
                base = json.load(f)
            for ident in base.get("identities", []):
                sig_hits = [s for s in target if any(w in s["text"] for w in ident.get("signals", []))]
                bad_hits = [s for s in target if any(w in s["text"] for w in ident.get("forbidden", []))]
                if sig_hits and bad_hits:
                    identity_findings.append(
                        {
                            "identity": ident.get("name", "?"),
                            "note": ident.get("note", ""),
                            "signal_line": sig_hits[0]["line"],
                            "signal_text": sig_hits[0]["text"][:60],
                            "violation_line": bad_hits[0]["line"],
                            "violation_text": bad_hits[0]["text"][:60],
                        }
                    )
        # 无矛盾 = 100（身份自洽）；有矛盾按处扣分
        dims[dim6] = max(0, 100 - 40 * len(identity_findings)) if identity_findings else 100

    scored = [v for v in dims.values() if v is not None]
    average = round(sum(scored) / len(scored)) if scored else None

    # 没评估到任何句子时，所有分数都无意义。
    # 早期版本会让空输入拿到「六维全 100、均值 100」——等于把空文件报成「干净」，
    # 这是最危险的一类错误：沉默被当成了清白。这里统一置 None，让上层说「数据不足」。
    if total_claims == 0:
        dims = {k: None for k in dims}
        average = None

    return {
        "track": tcfg["name"],
        "track_title": tcfg.get("title", ""),
        "reader_label": tcfg.get("reader_label", "对方"),
        "client_label": tcfg.get("client_label", "我"),
        "source_chars": len(text),
        "sentences_total": len(sentences),
        "claims_evaluated": total_claims,
        "role_detected": role_detected,
        "chain_state": chain_state,
        "money_mentioned": bool(money),
        "dimensions": dims,
        "average": average,
        "hits": hits,
        "anchors": anchors,
        "card_findings": card_findings,
        "identity_findings": identity_findings,
    }


# ---------------------------------------------------------------- 报告

BAR = "█"


def bar(score):
    if score is None:
        return "  n/a"
    filled = round(score / 10)
    return BAR * filled + "·" * (10 - filled) + f" {score}"


def print_report(r, path):
    print("=" * 62)
    print(f"验言 · 确定性扫描报告（L1）｜赛道：{r.get('track_title') or r.get('track', 'mingli')}")
    print("=" * 62)
    print(f"文件      {path}")
    print(f"字数      {r['source_chars']}")
    print(f"句子      {r['sentences_total']} 句，其中评估 {r['claims_evaluated']} 句")
    if not r["role_detected"]:
        print(
            f"⚠ 未识别出说话人角色，已按全量评估。"
            f"用「{r.get('reader_label', '对方')}：」「{r.get('client_label', '我')}：」开头可提高准确率。"
        )
    print()
    print("六维（越高越健康，n/a = 缺数据不猜）")
    for k, v in r["dimensions"].items():
        print(f"  {k:<8} {bar(v)}")
    print()
    print(f"链条判定  {r['chain_state']}" + ("   ⚠ 出现金额/收款" if r["money_mentioned"] else ""))
    print()

    def show(title, key, limit=6):
        items = r["hits"][key]
        if not items:
            return
        print(f"【{title}】{len(items)} 处")
        for it in items[:limit]:
            print(f"  L{it['line']:<4} 「{it['word']}」 {it['text'][:44]}")
        if len(items) > limit:
            print(f"  … 另有 {len(items) - limit} 处，见 --json")
        print()

    show("含糊句（万能表达）", "barnum")
    show("钓鱼句（冷读手法，高危）", "fishing")
    show("恐吓词", "fear")
    show("解药（收费解法）", "remedy")
    show("依赖诱导", "depend")
    show("正确废话", "empty_advice")

    if r["card_findings"]:
        print(f"【牌义存疑】{len(r['card_findings'])} 处")
        for it in r["card_findings"][:6]:
            print(f"  L{it['line']:<4} {it['card']} + 「{it['word']}」")
        print()

    if r.get("identity_findings"):
        print(f"【身份矛盾】{len(r['identity_findings'])} 处")
        for it in r["identity_findings"][:6]:
            print(
                f"  自称{it['identity']}（L{it['signal_line']}），却在 "
                f"L{it['violation_line']} 谈「{it['violation_text']}」"
            )
        print()

    print("-" * 62)
    print(f"下一步：python judge.py <文件> --track {r.get('track', 'mingli')}   交给 LLM 判官出判决与三条反问")
    print("免责：本报告评估话术结构，不评判占卜是否灵验或投资是否赚钱，也不构成对任何人的指控。")


def main():
    tracks = available_tracks()
    ap = argparse.ArgumentParser(description="验言 · 话术解剖器（确定性扫描）")
    ap.add_argument("file", help="对话文本文件（UTF-8）")
    ap.add_argument("--track", default=DEFAULT_TRACK, choices=tracks,
                    help=f"话术赛道（默认 {DEFAULT_TRACK}，向后兼容 v0.1）")
    ap.add_argument("--json", help="把完整结果写入 JSON 文件")
    ap.add_argument("--cards", help="牌面，逗号分隔，如：愚者,塔,月亮（mingli 赛道）")
    ap.add_argument("--quiet", action="store_true", help="不打印报告")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"找不到文件：{args.file}", file=sys.stderr)
        return 2

    text = read_text(args.file)

    cards = [c for c in (args.cards or "").split(",") if c.strip()]
    result = scan(text, cards=cards, track=args.track)
    result["file"] = args.file

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"已写入 {args.json}\n")

    if not args.quiet:
        print_report(result, args.file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
