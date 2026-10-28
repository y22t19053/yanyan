#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验言 · LLM 判官层（L2）

在 scan.py 的确定性结果之上，让本地模型做三件规则做不到的事：
  1. 判「这句话是不是真的含糊」——规则只能看词，看不出上下文里它有没有锚点
  2. 判「牌义对不对」——需要牌面数据 + 语义比对
  3. 写人话：判决、一句话总结、三条反制反问

零第三方依赖：用标准库 urllib 直连 Ollama 或任意 OpenAI 兼容端点。
没模型也能跑：--rule-only 直接输出 L1 判决。

用法：
    python judge.py examples/demo_chat.txt                      # 自动找本地 Ollama
    python judge.py examples/demo_chat.txt --backend ollama --model qwen2.5:7b
    python judge.py examples/demo_chat.txt --backend openai --base-url http://localhost:8000/v1
    python judge.py examples/demo_chat.txt --rule-only           # 不用模型
    python judge.py examples/demo_chat.txt --out report.md
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scan as scanner  # noqa: E402

VERDICTS = [
    (80, "干净"),
    (60, "一般"),
    (40, "敷衍"),
    (20, "生意话术"),
    (0, "操控"),
]


def verdict_for(avg):
    if avg is None:
        return "数据不足"
    for floor, name in VERDICTS:
        if avg >= floor:
            return name
    return "操控"


# ---------------------------------------------------------------- 后端

def http_json(url, payload=None, timeout=120):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_backend():
    """探一下本机有没有 Ollama 在跑"""
    try:
        tags = http_json("http://127.0.0.1:11434/api/tags", timeout=3)
        models = [m.get("name", "") for m in tags.get("models", [])]
        return "ollama", models
    except Exception:
        return None, []


def call_ollama(model, prompt, base_url="http://127.0.0.1:11434"):
    r = http_json(
        f"{base_url}/api/generate",
        {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
    )
    return r.get("response", "")


def call_openai(model, prompt, base_url="http://127.0.0.1:8000/v1", api_key=None):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        r = json.loads(resp.read().decode("utf-8"))
    return r["choices"][0]["message"]["content"]


# ---------------------------------------------------------------- 提示词

PROMPT = """你是一个占卜话术质检员。判断这段对话里占卜师的话是**专业的**还是**在敷衍、操控、乱说**。

已有确定性扫描器的结果，可参考但**不要盲从**——它只看词，看不出语境。

## 规则扫描结果
{scan_summary}

## 对话原文
{dialogue}

## 输出格式

严格照抄下面的骨架，只替换尖括号里的内容。**不要输出任何分析过程，不要复述维度名称，不要解释你的判断依据。**

判决：<干净 / 一般 / 敷衍 / 生意话术 / 操控，五选一>
总结：<一句话，不超过 40 字，说人话，不要骂人，不要自相矛盾>

最该注意的三处：
1. 第<数字>行：「<照抄原文片段，不要写占位符>」——<为什么有问题>
2. 第<数字>行：「<照抄原文片段>」——<为什么有问题>
3. 第<数字>行：「<照抄原文片段>」——<为什么有问题>

三条反问：
1. <反问>
2. <反问>
3. <反问>

## 三条反问的硬性要求（最容易写错）

反问的目的是**把选择权拿回来**，不是骂回去，更**不是接受对方的前提**。

- ✗ 错：「我需要提前准备什么材料吗？」——默认了仪式要做
- ✗ 错：「有没有更便宜的方式？」——默认了要买，只是砍价
- ✓ 对：「这个判断是从哪张牌、哪个盘上看出来的？」——要依据
- ✓ 对：「如果我不做，你判断会发生什么？依据是什么？」——要可验证的后果
- ✓ 对：「这件事我可以去问别人吗？如果不行，为什么？」——试探隔离

每条反问必须是**疑问句**，必须让回答者**举证或暴露矛盾**。

## 判断标准

**你判的是对话本身，不是上面那份扫描结果。** 扫描器只看词、不看语境，它的分数可能偏高或偏低；如果你的判断和它冲突，**以你的判断为准**，并在总结里说明。

- 含糊：这句话能不能套在任何一个求测者身上？能，就是含糊。
- 落地：有没有具体时间、数字、可执行动作、可验证结果。
- 恐吓-卖药：先制造灾难，再卖解法。**必须形成链条才算**；只吓人不卖药，降一级。
- 依赖：让你觉得「离开他你就完了」，或让你别去问别人。
- 牌义：占卜师讲的牌义是否与公认牌义冲突。

**这些是专业的表现，不要判成问题：**
- 「我不做改运 / 不做化解」——拒绝卖药，加分
- 「牌只显示当下，不是结论，你可以改」——把选择权还给你，加分
- 「这个判断只是牌面显示的当下状态」——同上，加分
- 「一次问一个，我们慢慢来」——正常提问节奏，不是限制自由
- 给了具体时间、具体动作、且不卖解法——专业，不是含糊

**这些才是问题：**
- 让你别去问别人、说「说了就不灵了」——隔离手法，最典型
- 制造无法验证的灾难 + 紧跟收费解法——恐惧营销
- 把猜测包装成断言（「你是不是…」「我一看就知道」）——冷读

**注意**：`第8行` 这类行号要写**对话原文里的真实行号**，不是扫描结果里的序号。原文片段必须逐字照抄。

若没识别出占卜师说了什么，如实说「数据不足」，不要编。
"""


def build_scan_summary(r):
    lines = [f"- 评估句数：{r['claims_evaluated']}", f"- 链条判定：{r['chain_state']}"]
    if r["money_mentioned"]:
        lines.append("- ⚠ 出现金额或收款方式")
    for k, v in r["dimensions"].items():
        lines.append(f"- {k}：{'未评估' if v is None else v}")
    for key, label in (
        ("fishing", "钓鱼句"),
        ("fear", "恐吓词"),
        ("remedy", "解药"),
        ("depend", "依赖诱导"),
        ("barnum", "含糊句"),
    ):
        for h in r["hits"][key][:8]:
            lines.append(f"- [{label}] L{h['line']} 「{h['word']}」：{h['text'][:50]}")
    return "\n".join(lines)


PLACEHOLDER = re.compile(r"[<＜][^>＞]*[>＞]")


def parse_llm_output(text):
    """把模型的输出拆成结构。

    解析必须**分段**做：早期版本在整个输出里扫 `^\\d+\\.`，
    结果模型在反问区之前先写了一段维度推理，那些推理条目被当成反问抓走了。
    现在按「最该注意」「反问」两个小节各自截取，且反问只收疑问句。
    """
    out = {"verdict": None, "summary": None, "watch": [], "questions": [], "raw": text}

    m = re.search(r"判决[：:]\s*(.+)", text)
    if m:
        raw = m.group(1).strip()
        # 模型经常把模板的尖括号一起抄出来：<一般> / <干净>
        raw = PLACEHOLDER.sub(lambda mm: mm.group(0)[1:-1], raw).strip()
        raw = raw.strip("<>＜＞").strip()
        # 只接受五个合法判决之一，防止模型自创标签
        for _, name in VERDICTS:
            if name in raw:
                out["verdict"] = name
                break
        else:
            out["verdict"] = raw.split()[0].strip("。，,") if raw else None

    m = re.search(r"总结[：:]\s*(.+)", text)
    if m:
        s = m.group(1).strip()
        s = PLACEHOLDER.sub(lambda mm: mm.group(0)[1:-1], s).strip()
        out["summary"] = s.strip("<>＜＞").strip() or None

    # 按小节切分：找到「最该注意」和「反问」两个锚点
    watch_anchor = re.search(r"最该注意[^\n]*", text)
    ask_anchor = re.search(r"三条反问|反问[：:]|你可以这样问", text)

    watch_text, ask_text = "", ""
    if watch_anchor and ask_anchor and ask_anchor.start() > watch_anchor.start():
        watch_text = text[watch_anchor.end():ask_anchor.start()]
        ask_text = text[ask_anchor.end():]
    elif watch_anchor:
        watch_text = text[watch_anchor.end():]
    elif ask_anchor:
        ask_text = text[ask_anchor.end():]

    def numbered(block):
        items = []
        for m2 in re.finditer(r"^\s*\d+[.、)）]\s*(.+)$", block, re.M):
            item = m2.group(1).strip()
            # 丢掉模型没填、直接抄模板的行
            if item and not PLACEHOLDER.search(item):
                items.append(item)
        return items

    out["watch"] = numbered(watch_text)[:3]

    # 反问必须是疑问句——这一条就能挡掉绝大多数推理段
    for item in numbered(ask_text):
        if re.search(r"[？?]\s*$", item):
            out["questions"].append(item)
        elif re.search(r"[？?]", item) and len(item) < 120:
            out["questions"].append(item)
    out["questions"] = out["questions"][:3]

    return out


# ---------------------------------------------------------------- 报告

def render_report(path, r, judged, backend_note):
    avg = r["average"]
    verdict = judged["verdict"] if judged and judged.get("verdict") else verdict_for(avg)

    L = []
    L.append("# 验言 · 话术质检报告")
    L.append("")
    L.append(f"**文件**　`{path}`")
    L.append(f"**判决**　**{verdict}**" + (f"（六维均值 {avg}）" if avg is not None else ""))
    if r["money_mentioned"] and r["chain_state"] in ("恐惧营销", "操控性话术"):
        L.append("")
        L.append("> ⚠️ **出现金额或收款方式，且已构成恐吓-卖药链。** 建议保留完整记录。")
    L.append("")
    if judged and judged.get("summary"):
        L.append(f"> {judged['summary']}")
        L.append("")

    L.append("## 六维")
    L.append("")
    L.append("| 维度 | 分数 | 说明 |")
    L.append("|---|---|---|")
    for k, v in r["dimensions"].items():
        L.append(f"| {k} | {'未评估' if v is None else v} | |")
    L.append("")

    if judged and judged.get("watch"):
        L.append("## 最该注意的三处")
        L.append("")
        for i, w in enumerate(judged["watch"][:3], 1):
            L.append(f"{i}. {w}")
        L.append("")
    else:
        # 没有模型时，用规则命中的高危项顶上。
        # 按行去重：同一句话命中多个词只算一处，否则「最该注意」列表会被同一句塞满。
        L.append("## 最该注意的几处（规则命中）")
        L.append("")
        n = 0
        seen_lines = set()
        for key, label in (("depend", "依赖诱导"), ("remedy", "解药"), ("fear", "恐吓词"), ("fishing", "钓鱼句")):
            for h in r["hits"][key]:
                if h["line"] in seen_lines:
                    continue
                seen_lines.add(h["line"])
                n += 1
                L.append(f"{n}. **L{h['line']}**（{label}「{h['word']}」）　{h['text']}")
                if n >= 5:
                    break
            if n >= 5:
                break
        L.append("")

    if judged and judged.get("questions"):
        L.append("## 你可以这样问回去")
        L.append("")
        for i, q in enumerate(judged["questions"][:3], 1):
            L.append(f"{i}. {q}")
        L.append("")
    else:
        L.append("## 你可以这样问回去")
        L.append("")
        L.append("1. 你刚才说我「身上有问题」，请问这个判断是从哪张牌、哪个盘上看出来的？")
        L.append("2. 如果我不做你说的这个处理，你判断会发生什么？依据是什么？")
        L.append("3. 这件事我可以去问别人吗？如果不行，为什么？")
        L.append("")

    L.append("---")
    L.append("")
    L.append(f"*后端：{backend_note}*")
    L.append("")
    L.append("*免责：本工具评估的是**话术结构**，不评估占卜是否灵验，也不构成对任何人的指控。*")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="验言 · 占卜话术质检")
    ap.add_argument("file")
    ap.add_argument("--backend", choices=["auto", "ollama", "openai", "none"], default="auto")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=os.environ.get("YANYAN_API_KEY"))
    ap.add_argument("--cards", default=None, help="牌面，逗号分隔")
    ap.add_argument("--out", default=None, help="输出 Markdown 报告")
    ap.add_argument("--rule-only", action="store_true", help="不用模型，只出 L1 结果")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"找不到文件：{args.file}", file=sys.stderr)
        return 2

    text = scanner.read_text(args.file)

    cards = [c for c in (args.cards or "").split(",") if c.strip()]
    r = scanner.scan(text, cards=cards)
    r["file"] = args.file

    # ---- 选后端 ----
    backend, model, note = "none", None, "无（仅规则层）"
    if not args.rule_only and args.backend != "none":
        if args.backend in ("auto", "ollama"):
            found, models = detect_backend()
            if found == "ollama":
                model = args.model or (models[0] if models else "qwen2.5:7b")
                backend, note = "ollama", f"Ollama · {model}"
                if not models:
                    note += "（本机无模型，请先 ollama pull）"
            elif args.backend == "ollama":
                print("连不上 Ollama（127.0.0.1:11434）。", file=sys.stderr)
                return 3
        if backend == "none" and args.backend == "openai":
            if not args.base_url:
                print("--backend openai 需要 --base-url", file=sys.stderr)
                return 3
            model = args.model or "local-model"
            backend, note = "openai", f"OpenAI 兼容 · {args.base_url} · {model}"
        if backend == "none" and args.backend == "auto":
            note = "无（未发现本地模型，已降级为规则层）"

    judged = None
    if backend != "none":
        prompt = PROMPT.format(scan_summary=build_scan_summary(r), dialogue=text[:6000])
        try:
            if backend == "ollama":
                raw = call_ollama(model, prompt, args.base_url or "http://127.0.0.1:11434")
            else:
                raw = call_openai(model, prompt, args.base_url, args.api_key)
            judged = parse_llm_output(raw)
            note += "　✓ 判官已出结论"
        except urllib.error.URLError as e:
            print(f"调用模型失败：{e}　→ 降级为规则层", file=sys.stderr)
            note += f"　✗ 调用失败（{e}）"

    report = render_report(args.file, r, judged, note)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已写入 {args.out}")
    else:
        print(report)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"scan": r, "judge": judged, "backend": note}, f, ensure_ascii=False, indent=2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
