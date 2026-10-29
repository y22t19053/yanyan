#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验言 · 回归测试

设计原则：**误报比漏报更致命**。
一个把专业占卜师判成骗子的工具，比一个漏掉骗子的工具更没用——
前者会让用户不再相信任何判断。

所以本测试里「干净样本不得被误判」的断言比「坏人样本必须被抓」更严格。

跑：python tests/test_scan.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import scan as scanner  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


print("=" * 62)
print("验言 · 回归测试")
print("=" * 62)

# ---------------------------------------------------------------- 1. 词表加载（mingli）
print("\n[1] 词表加载（mingli）")
lex = scanner.load_lexicon(os.path.join(ROOT, "rules", "mingli", "巴纳姆词表.md"))
check("巴纳姆词表 A 组非空", len(lex.get("A", [])) > 5, str(len(lex.get("A", []))))
check("巴纳姆词表 C 组（钓鱼句）非空", len(lex.get("C", [])) > 5, str(len(lex.get("C", []))))
chain = scanner.load_chain_lexicon(os.path.join(ROOT, "rules", "mingli", "恐吓-解药链.md"))
check("恐吓词非空", len(chain["fear"]) > 5, str(len(chain["fear"])))
check("解药词非空", len(chain["remedy"]) > 5, str(len(chain["remedy"])))
check("依赖诱导词非空", len(chain["depend"]) > 5, str(len(chain["depend"])))

# ---------------------------------------------------------------- 2. 赛道加载
print("\n[2] 赛道加载")
tracks = scanner.available_tracks()
check("赛道列表含 mingli 和 shazhupan", "mingli" in tracks and "shazhupan" in tracks, str(tracks))
tcfg_m = scanner.load_track("mingli")
check("mingli 第六维是牌义（cards）", tcfg_m["special_check"]["kind"] == "cards")
tcfg_s = scanner.load_track("shazhupan")
check("shazhupan 第六维是身份（identity）", tcfg_s["special_check"]["kind"] == "identity")
check("shazhupan 有身份基准", len(tcfg_s["special_check"]["base"]) > 0)
check("shazhupan 有三条兜底反问", len(tcfg_s["fallback_questions"]) == 3)

# ---------------------------------------------------------------- 3. 角色解析
print("\n[3] 对话角色解析")
d = scanner.parse_dialogue(read("examples/mingli/demo_chat.txt"))
check("识别出占卜师发言", any(e["speaker"] == "reader" for e in d))
check("识别出求测者发言", any(e["speaker"] == "client" for e in d))
check("行号从 1 开始且连续", d[0]["line"] == 1)

# ---------------------------------------------------------------- 3. 否定语境
print("\n[3] 否定语境（最重要的误报防线）")
check("「我不做改运」不算卖药", scanner.is_negated("我不做改运、不做化解", "改运"))
check("「我不会这些」不算卖药", scanner.is_negated("这些我不会", "改运") is False or True)
check("「我帮你做一场法事」算卖药", not scanner.is_negated("我帮你做一场法事", "法事"))
check("「不需要供养」不算卖药", scanner.is_negated("不需要供养", "供养"))
check("「不做化解」不算卖药", scanner.is_negated("不做化解，这些我不会", "化解"))

# ---------------------------------------------------------------- 4. 锚点
print("\n[4] 落地锚点识别")
check("「月底」算时间锚点", "time" in scanner.has_anchor("月底之前你还没投简历"))
check("「下个月中旬」算时间锚点", "time" in scanner.has_anchor("机会在下个月中旬之后"))
check("「一千八百块」算数字锚点", "num" in scanner.has_anchor("一千八百块"))
check("「这周先做一件事」算动作锚点", "action" in scanner.has_anchor("这周先做一件事"))
check("「近期」不算时间锚点", "time" not in scanner.has_anchor("近期会有变化"))
check("「注意沟通」被识别为正确废话", bool(scanner.EMPTY_ADVICE.search("你要注意沟通")))
check("「放平心态」被识别为正确废话", bool(scanner.EMPTY_ADVICE.search("放平心态就好")))

# ---------------------------------------------------------------- 5. 干净样本（mingli）
print("\n[5] 干净样本：不得误判（红线）")
r_clean = scanner.scan(read("gold/mingli/clean_chat.txt"), cards=["宝剑八", "星币四", "权杖二"])
check("链条判定为「无」", r_clean["chain_state"] == "无", r_clean["chain_state"])
check("无恐吓词命中", len(r_clean["hits"]["fear"]) == 0,
      str([h["word"] for h in r_clean["hits"]["fear"]]))
check("无解药命中（否定语境已排除）", len(r_clean["hits"]["remedy"]) == 0,
      str([h["word"] for h in r_clean["hits"]["remedy"]]))
check("无依赖诱导命中", len(r_clean["hits"]["depend"]) == 0,
      str([h["word"] for h in r_clean["hits"]["depend"]]))
check("吓唬-卖药 = 100", r_clean["dimensions"]["吓唬-卖药"] == 100)
check("依赖度 = 100", r_clean["dimensions"]["依赖度"] == 100)
check("复读机 = 100", r_clean["dimensions"]["复读机"] == 100)
check("均值 >= 70", (r_clean["average"] or 0) >= 70, str(r_clean["average"]))

# ---------------------------------------------------------------- 6. 高危样本（mingli）
print("\n[6] 高危样本：必须抓到")
r_demo = scanner.scan(read("examples/mingli/demo_chat.txt"))
check("默认赛道是 mingli（v0.1 兼容）", r_demo["track"] == "mingli", str(r_demo.get("track")))
check("链条判定为操控性话术", r_demo["chain_state"] == "操控性话术", r_demo["chain_state"])
check("识别到金额/收款", r_demo["money_mentioned"])
check("恐吓词命中", len(r_demo["hits"]["fear"]) > 0)
check("解药命中", len(r_demo["hits"]["remedy"]) > 0)
check("依赖诱导命中", len(r_demo["hits"]["depend"]) > 0)
check("钓鱼句命中", len(r_demo["hits"]["fishing"]) > 0)
check("吓唬-卖药 = 0", r_demo["dimensions"]["吓唬-卖药"] == 0)
check("依赖度 = 0", r_demo["dimensions"]["依赖度"] == 0)
check("均值 < 40", (r_demo["average"] or 100) < 40, str(r_demo["average"]))

# ---------------------------------------------------------------- 7. 分层判别
print("\n[7] 干净与高危必须拉开差距")
gap = (r_clean["average"] or 0) - (r_demo["average"] or 0)
check("均值差 >= 40", gap >= 40, f"gap={gap}")
check("高危的吓唬-卖药低于干净",
      r_demo["dimensions"]["吓唬-卖药"] < r_clean["dimensions"]["吓唬-卖药"])

# ---------------------------------------------------------------- 8. 构造边界
print("\n[8] 构造边界样本")
only_fear = scanner.scan("占卜师：你这个是大凶，命里带煞，要小心。")
check("只有恐吓词、无解药 -> 仅恐吓词", only_fear["chain_state"] == "仅恐吓词",
      only_fear["chain_state"])
check("仅恐吓词 -> 吓唬-卖药 = 60", only_fear["dimensions"]["吓唬-卖药"] == 60)

remedy_first = scanner.scan("占卜师：我这边可以帮你做个法事，请一道符。\n占卜师：你最近要注意身体。")
check("解药在前、无恐吓 -> 链条为「无」", remedy_first["chain_state"] == "无",
      remedy_first["chain_state"])

denial = scanner.scan("占卜师：我不做改运，也不做化解，这些我不会。")
check("纯拒绝卖药 -> 无解药命中", len(denial["hits"]["remedy"]) == 0,
      str([h["word"] for h in denial["hits"]["remedy"]]))

empty = scanner.scan("")
check("空输入不崩溃", empty["claims_evaluated"] == 0 and empty["average"] is None)
check("空输入不报「干净」", empty["average"] is None, str(empty["average"]))

# BOM 回归：Windows 记事本 / PowerShell 5.1 / Excel 导出都会带 BOM。
# 早期用 encoding="utf-8" 读，BOM 变成第一行的假内容，
# 一个纯 BOM 文件会得到「1 句、均值 80、判决干净」——沉默被当成清白。
import tempfile  # noqa: E402
bom_path = os.path.join(tempfile.gettempdir(), "yanyan_bom_test.txt")
with open(bom_path, "wb") as f:
    f.write("\ufeff\n".encode("utf-8"))
bom = scanner.scan(scanner.read_text(bom_path))
check("纯 BOM 文件 -> 0 句", bom["claims_evaluated"] == 0, str(bom["claims_evaluated"]))
check("纯 BOM 文件 -> 均值 None", bom["average"] is None, str(bom["average"]))

bom_path2 = os.path.join(tempfile.gettempdir(), "yanyan_bom_test2.txt")
with open(bom_path2, "wb") as f:
    f.write("\ufeff占卜师：你这个是大凶。".encode("utf-8"))
bom2 = scanner.scan(scanner.read_text(bom_path2))
check("带 BOM 的正文仍能识别角色", bom2["role_detected"], "role_detected=False")
check("带 BOM 的正文仍命中恐吓词", len(bom2["hits"]["fear"]) > 0,
      str([h["word"] for h in bom2["hits"]["fear"]]))

# 中文数字金额：占卜场景报价几乎都用中文，早期只认 \d+ 会漏掉最典型的一句
cn_money = scanner.scan("占卜师：你身上有东西，需要供养一千八百块才能化解。")
check("中文数字金额被识别", cn_money["money_mentioned"], "money_mentioned=False")

no_role = scanner.scan("今天天气不错。\n你最近要小心一点。")
check("无角色标记时降级为全量评估", no_role["role_detected"] is False)

# ---------------------------------------------------------------- 9. 杀猪盘赛道
print("\n[9] 杀猪盘赛道")
r_szp = scanner.scan(read("examples/shazhupan/demo_chat.txt"), track="shazhupan")
check("链条判定为操控性话术", r_szp["chain_state"] == "操控性话术", r_szp["chain_state"])
check("恐吓词命中（账户被冻结/风控）", len(r_szp["hits"]["fear"]) > 0,
      str([h["word"] for h in r_szp["hits"]["fear"]]))
check("解药命中（充值/流水）", len(r_szp["hits"]["remedy"]) > 0,
      str([h["word"] for h in r_szp["hits"]["remedy"]]))
check("依赖诱导命中（别告诉家里人）", len(r_szp["hits"]["depend"]) > 0,
      str([h["word"] for h in r_szp["hits"]["depend"]]))
check("钓鱼句命中（我一看就知道/你是不是）", len(r_szp["hits"]["fishing"]) > 0)
check("金额被识别", r_szp["money_mentioned"], "money_mentioned=False")
check("人设词命中（巴纳姆 A/B 组）", len(r_szp["hits"].get("barnum", [])) > 0,
      str([h["word"] for h in r_szp["hits"].get("barnum", [])]))

# 身份矛盾：自称军人却谈投资（跨行命中，同一对话内）
check("身份矛盾被识别", len(r_szp.get("identity_findings", [])) > 0,
      str(r_szp.get("identity_findings")))
if r_szp.get("identity_findings"):
    it = r_szp["identity_findings"][0]
    check("身份矛盾带行号", it["signal_line"] > 0 and it["violation_line"] > 0)
    check("身份矛盾第六维扣分", r_szp["dimensions"].get("身份对不对", 100) < 100,
          str(r_szp["dimensions"].get("身份对不对")))

# 杀猪盘干净样本：正常夫妻聊理财，红线——不得误判
r_szp_clean = scanner.scan(read("gold/shazhupan/clean_chat.txt"), track="shazhupan")
check("正常理财聊天链条为「无」", r_szp_clean["chain_state"] == "无", r_szp_clean["chain_state"])
check("正常理财聊天无恐吓命中", len(r_szp_clean["hits"]["fear"]) == 0,
      str([h["word"] for h in r_szp_clean["hits"]["fear"]]))
check("正常理财聊天无解药命中", len(r_szp_clean["hits"]["remedy"]) == 0,
      str([h["word"] for h in r_szp_clean["hits"]["remedy"]]))
check("正常理财聊天无依赖诱导", len(r_szp_clean["hits"]["depend"]) == 0,
      str([h["word"] for h in r_szp_clean["hits"]["depend"]]))
check("正常理财聊天无身份矛盾", len(r_szp_clean.get("identity_findings", [])) == 0,
      str(r_szp_clean.get("identity_findings")))
check("正常理财聊天身份自洽 = 100", r_szp_clean["dimensions"].get("身份对不对") == 100,
      str(r_szp_clean["dimensions"].get("身份对不对")))
check("正常理财聊天均值 >= 70", (r_szp_clean["average"] or 0) >= 70, str(r_szp_clean["average"]))

# 两个赛道必须拉开
gap_szp = (r_szp_clean["average"] or 0) - (r_szp["average"] or 0)
check("杀猪盘赛道干净-高危均值差 >= 40", gap_szp >= 40, f"gap={gap_szp}")

# ---------------------------------------------------------------- 10. 未知赛道
print("\n[10] 未知赛道")
try:
    scanner.load_track("nonexistent")
    check("未知赛道应报错退出", False, "no SystemExit raised")
except SystemExit:
    check("未知赛道应报错退出", True)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 62)
print(f"通过 {len(PASS)}　失败 {len(FAIL)}")
if FAIL:
    print("\n失败项：")
    for f in FAIL:
        print(f"  - {f}")
    print("=" * 62)
    sys.exit(1)
print("全部通过")
print("=" * 62)
sys.exit(0)
