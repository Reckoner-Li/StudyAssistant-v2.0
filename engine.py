# -*- coding: utf-8 -*-
"""
智慧学伴 · 七年级数学学情分析引擎

吃进四份数据——知识点元数据、学习建议模块、学生名单、作答明细（JSON 或 CSV），
吐出每个知识点的错误率、掌握度、班级差异，挑出该预警的点和该重点关注的人，
最后写成一堆 JSON 丢进 model/。

    python engine.py --out model
    python engine.py --responses new.json --students new_students.json --out model_new
    python engine.py --responses new.csv --out model_new

先把口径讲清楚，后面看代码才不晕：
    错误率 = 1 - 得分 / 满分，个人、班级、知识点都走这同一个式子
    30% 是关注基线，没过线的就不拿出来讲
    9 个知识点按错误率降序切 3/4/2：重点、次重点、注意点
    预警取最差的 3 个点，沿思维导图往上找直接前置节点，说成"可能没听懂的地方"

这不是训练神经网络——没有权重，也没有梯度。跑一次就是把数据重算一遍：
换批数据结果跟着变；同一批数据重跑，除了生成时间那条，其余完全一样。

默认产出（都在 --out 指定的目录里）：
    model.json          主模型，网页、文档、PPT 都读它
    student_analysis    学生端：个人薄弱点、掌握度、建议
    teacher_analysis    教师端：班级、知识点、重点关注名单
    warnings            预警：错误率前三 -> 直接前置节点
    teaching_advice     教学建议：学生端建议按频次拼成班课动作
    mindmap             思维导图的节点与连线
    summary.json/.md    汇总摘要

常改的就三处：CONFIG 管阈值和比例，PARENTS 管前置关系，POSITIONS/EDGES 管导图怎么画。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------- 全局口径 ----------
# 这几个数一动，model.json、网页、策划书、PPT 里的结论全跟着变，别顺手改。
# baseline_error_rate  错误率超过它才算"薄弱"，进不进预警也看它
# warning_top_n        预警取前几个点
# tiers                三个比例加起来必须正好是 1；9 个点默认切成 3/4/2
# mastery              <=20% 掌握，20%—40% 部分掌握，>40% 未掌握
# attention            至少作答 2 次、错误率 >=40% 才进重点关注候选
CONFIG = {
    "baseline_error_rate": 0.30,
    "warning_top_n": 3,
    "tiers": [
        {"name": "重点", "ratio": 0.30},
        {"name": "次重点", "ratio": 0.40},
        {"name": "注意点", "ratio": 0.30},
    ],
    "mastery": {"mastered_max_error": 0.20, "partial_max_error": 0.40},
    "attention": {"min_attempts": 2, "weak_error_rate": 0.40},
}

# ---------- 前置关系 ----------
# key 是知识点，parent 指向它的直接前置——预警里那句"可能没听懂的地方"就是从这来的。
# external=True 表示前置在导图之外（F01—F05 那批先备知识），网页上画成灰圈。
# 改这里会同时影响学生端"学习仍有欠缺"和教师端"再讲一下吧"。
PARENTS = {
    "T01I1": {"id": "F01", "name": "字母表示数、代数式的概念", "external": True,
              "reason": "列代数式的前提是用字母表示数，并识别代数式的书写规范。"},
    "T01I2": {"id": "F02", "name": "整式概念与去括号法则", "external": True,
              "reason": "合并同类项、去括号是整式加减的两块基石。"},
    "T01I3": {"id": "T01I1", "name": "表示数量关系并代入求值", "external": False,
              "reason": "先用代数式准确表示数量关系，才谈得上用整式解释问题。"},
    "T02I1": {"id": "F03", "name": "等式的性质", "external": True,
              "reason": "解一元一次方程的每一步变形，依据都是等式的性质。"},
    "T02I2": {"id": "T02I1", "name": "运用等式性质解一元一次方程", "external": False,
              "reason": "二元一次方程组通过消元转化为一元一次方程。"},
    "T02I3": {"id": "T02I2", "name": "用消元法解二元一次方程组", "external": False,
              "reason": "列方程组解决实际问题，最后仍要落到解方程组。"},
    "T03I1": {"id": "T02I1", "name": "运用等式性质解一元一次方程", "external": False,
              "reason": "不等式性质与等式性质对照学习，负数变号是新增难点。"},
    "T03I2": {"id": "T03I1", "name": "运用不等式性质求解", "external": False,
              "reason": "先会解单个不等式，才能在数轴上求公共部分。"},
    "T03I3": {"id": "T03I2", "name": "求两个一元一次不等式解集的交集", "external": False,
              "reason": "根据实际限制列不等式后，解集与取值范围的表达依赖不等式组。"},
}

# ---------- 外圈的先备知识 ----------
# 要加 F06、F07 的话，PARENTS 和 EDGES 里也得补，否则节点画出来是孤零零一个。
FOUNDATION_NODES = [
    {"id": "F01", "name": "字母表示数", "detail": "字母表示数、代数式的概念", "external": True},
    {"id": "F02", "name": "整式与去括号", "detail": "整式概念、有理数运算、去括号法则", "external": True},
    {"id": "F03", "name": "等式的性质", "detail": "等式两边同加同减、同乘同除", "external": True},
    {"id": "F04", "name": "数轴与解集", "detail": "用数轴表示解集、端点开闭", "external": True},
    {"id": "F05", "name": "实际问题审题", "detail": "找等量关系与不等关系", "external": True},
]

# ---------- 连线 ----------
# main 同一主题里的学习顺序（实线），cross 跨主题支撑（虚线），
# foundation 指向前置知识（点线）。
EDGES = [
    # 学习顺序（实线）
    ("T01I1", "T01I2", "main"),
    ("T01I2", "T01I3", "main"),
    ("T02I1", "T02I2", "main"),
    ("T02I2", "T02I3", "main"),
    ("T03I1", "T03I2", "main"),
    ("T03I2", "T03I3", "main"),
    # 跨主题支撑（虚线）
    ("T01I2", "T02I1", "cross"),
    ("T02I1", "T03I1", "cross"),
    # 前置基础（点线）
    ("F01", "T01I1", "foundation"),
    ("F02", "T01I2", "foundation"),
    ("F03", "T02I1", "foundation"),
    ("F04", "T03I2", "foundation"),
    ("F05", "T02I3", "foundation"),
    ("F05", "T03I3", "foundation"),
]

# ---------- 画布坐标 ----------
# 单位是 SVG 像素，画布 1260 x 800。换布局就挪 x/y。
# 新增知识点必须在这里补一行，漏了网页就不画这个节点。
POSITIONS = {
    "F01": {"x": 250, "y": 82, "kind": "foundation"},
    "F02": {"x": 78, "y": 410, "kind": "foundation"},
    "F03": {"x": 650, "y": 82, "kind": "foundation"},
    "F04": {"x": 1050, "y": 82, "kind": "foundation"},
    "F05": {"x": 850, "y": 722, "kind": "foundation"},
    "T01I1": {"x": 250, "y": 214, "kind": "point"},
    "T01I2": {"x": 250, "y": 410, "kind": "point"},
    "T01I3": {"x": 250, "y": 606, "kind": "point"},
    "T02I1": {"x": 650, "y": 214, "kind": "point"},
    "T02I2": {"x": 650, "y": 410, "kind": "point"},
    "T02I3": {"x": 650, "y": 606, "kind": "point"},
    "T03I1": {"x": 1050, "y": 214, "kind": "point"},
    "T03I2": {"x": 1050, "y": 410, "kind": "point"},
    "T03I3": {"x": 1050, "y": 606, "kind": "point"},
}


# ---------- 小工具 ----------
def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def dump_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def pct(x, digits=1):
    return f"{x * 100:.{digits}f}%"


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def split_tags(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [t.strip() for t in re.split(r"[、,，/;；]+", str(value)) if t.strip()]


def unwrap(data, keys):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
        # 也可能是只传了一条记录，包成列表
        if any(k in data for k in ("student", "student_id", "indicator", "point")):
            return [data]
    return []


def normalize_responses(raw):
    rows = unwrap(raw, ["responses", "records", "data", "rows"])
    clean, issues = [], []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            issues.append({"row": i + 1, "reason": "not an object"})
            continue
        student = r.get("student") or r.get("student_id") or r.get("studentId") or r.get("uid")
        indicator = r.get("indicator") or r.get("indicator_id") or r.get("point") or r.get("knowledge_id")
        if not student or not indicator:
            issues.append({"row": i + 1, "reason": "missing student or indicator"})
            continue
        try:
            score = float(r.get("score", 0))
            full = float(r.get("full", r.get("max_score", r.get("full_score", 0))) or 0)
        except (TypeError, ValueError):
            issues.append({"row": i + 1, "reason": "score/full not numeric"})
            continue
        if full <= 0:
            issues.append({"row": i + 1, "reason": "full <= 0"})
            continue
        if score < 0 or score > full:
            issues.append({"row": i + 1, "reason": f"score {score} out of range [0,{full}]", "student": str(student), "indicator": str(indicator)})
            continue
        clean.append({
            "rid": r.get("rid") or f"R{i + 1:04d}",
            "student": str(student).strip(),
            "indicator": str(indicator).strip(),
            "item": r.get("item") or r.get("question_id") or "",
            "score": score,
            "full": full,
            "type_tag": r.get("type_tag") or "",
            "submitted": r.get("submitted") or r.get("time") or "",
        })
    return clean, issues


def normalize_students(raw):
    rows = unwrap(raw, ["students", "records", "data"])
    out, seen = [], set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        sid = r.get("id") or r.get("student") or r.get("student_id")
        if not sid or str(sid) in seen:
            continue
        seen.add(str(sid))
        out.append({
            "id": str(sid),
            "class": str(r.get("class") or r.get("class_id") or "未分班"),
            "grade": r.get("grade", 7),
        })
    return out


def read_csv_responses(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def derive_students_from_responses(responses):
    ids = sorted({r["student"] for r in responses})
    return [{"id": i, "class": "未分班", "grade": 7} for i in ids]


# ---------- 聚合 ----------
def mastery_of(error_rate, cfg=CONFIG):
    if error_rate <= cfg["mastery"]["mastered_max_error"]:
        return "掌握"
    if error_rate <= cfg["mastery"]["partial_max_error"]:
        return "部分掌握"
    return "未掌握"


def aggregate(responses, key, filter_fn=None):
    out = defaultdict(lambda: {"score": 0.0, "full": 0.0, "attempts": 0})
    for r in responses:
        if filter_fn and not filter_fn(r):
            continue
        a = out[key(r)]
        a["score"] += r["score"]
        a["full"] += r["full"]
        a["attempts"] += 1
    return out


def stats_of(a):
    score, full, attempts = a["score"], a["full"], a["attempts"]
    rate = score / full if full else 0.0
    return {
        "attempts": attempts,
        "score": round(score, 2),
        "full": round(full, 2),
        "score_rate": round(rate, 4),
        "error_rate": round(1 - rate, 4),
        "mastery": mastery_of(1 - rate),
    }


def tier_cut(n, cfg=CONFIG):
    top = max(1, int(round(n * cfg["tiers"][0]["ratio"])))
    mid = max(1, int(round(n * cfg["tiers"][1]["ratio"])))
    if top + mid >= n:
        mid = max(0, n - top - 1)
    return top, mid, n - top - mid


def distribute(rates):
    labels = [("不及格", 0, 0.60), ("及格", 0.60, 0.70), ("良好", 0.70, 0.85), ("优秀", 0.85, 1.01)]
    out = []
    for name, lo, hi in labels:
        cnt = sum(1 for r in rates if lo <= r < hi)
        out.append({"label": name, "range": f"{int(lo*100)}-{min(100,int(hi*100))}%", "count": cnt})
    return out


def class_metrics(student_rows):
    rates = [s["score_rate"] for s in student_rows if s["attempts"] > 0]
    if not rates:
        return {"avg_student_rate": 0, "pass_rate": 0, "excellent_rate": 0, "median_rate": 0, "distribution": []}
    return {
        "avg_student_rate": round(sum(rates) / len(rates), 4),
        "pass_rate": round(sum(1 for r in rates if r >= 0.60) / len(rates), 4),
        "excellent_rate": round(sum(1 for r in rates if r >= 0.85) / len(rates), 4),
        "median_rate": round(median(rates), 4),
        "distribution": distribute(rates),
    }


def build_students(clean, students, kps, cfg=CONFIG):
    kp_by_id = {k["指标编号"]: k for k in kps}
    by_student = defaultdict(list)
    for r in clean:
        by_student[r["student"]].append(r)

    result = []
    for stu in students:
        rows = by_student.get(stu["id"], [])
        pstats = []
        for pid, kp in kp_by_id.items():
            sub = [r for r in rows if r["indicator"] == pid]
            if not sub:
                continue
            a = {"score": sum(r["score"] for r in sub), "full": sum(r["full"] for r in sub), "attempts": len(sub)}
            st = stats_of(a)
            st.update({
                "id": pid,
                "name": kp["观察指标"],
                "theme": kp["主题"],
                "tags": split_tags(kp["类型标签"]),
                "importance": kp["重要性"],
                "primary_type": primary_type(kp),
            })
            pstats.append(st)

        total_score = sum(p["score"] for p in pstats)
        total_full = sum(p["full"] for p in pstats)
        total_rate = total_score / total_full if total_full else 0
        weak_sorted = sorted(
            [p for p in pstats if p["attempts"] > 0 and p["error_rate"] > 0.0001],
            key=lambda p: (-p["error_rate"], -(p["full"] - p["score"]), -p["attempts"], p["id"]),
        )
        weak = [p for p in weak_sorted if p["error_rate"] >= cfg["baseline_error_rate"]][:3]
        if len(weak) < 3:
            for p in weak_sorted:
                if p not in weak:
                    weak.append(p)
                if len(weak) == 3:
                    break
        strong = sorted(pstats, key=lambda p: (p["error_rate"], -p["attempts"], p["id"]))[:3]
        mastery_counts = Counter(p["mastery"] for p in pstats)

        for p in pstats:
            p["advice"] = kp_by_id[p["id"]]["拼接学习建议"]
            p["advice_modules"] = module_tags(kp_by_id[p["id"]])
        for p in weak:
            p["advice"] = kp_by_id[p["id"]]["拼接学习建议"]
            p["advice_modules"] = module_tags(kp_by_id[p["id"]])

        result.append({
            "id": stu["id"],
            "class": stu["class"],
            "grade": stu.get("grade", 7),
            "attempts": len(rows),
            "score": round(total_score, 2),
            "full": round(total_full, 2),
            "score_rate": round(total_rate, 4),
            "error_rate": round(1 - total_rate, 4),
            "points_attempted": len(pstats),
            "mastery_counts": {k: mastery_counts.get(k, 0) for k in ("掌握", "部分掌握", "未掌握")},
            "points": pstats,
            "weak_points": weak,
            "strong_points": strong,
            "advice_summary": student_advice_summary(weak),
            "warnings": [],
        })

    # 班内排名：同班按得分率排，记下名次和班内人数
    by_class = defaultdict(list)
    for s in result:
        by_class[s["class"]].append(s)
    for c, rows in by_class.items():
        ranked = sorted(rows, key=lambda s: (-s["score_rate"], s["id"]))
        for i, s in enumerate(ranked, 1):
            s["class_rank"] = i
            s["class_size"] = len(rows)
    return result


def primary_type(kp):
    tags = split_tags(kp.get("类型标签"))
    for t in tags:
        if t in ("逻辑型", "记忆型"):
            return t
    return tags[-1] if tags else "逻辑型"


def module_tags(kp):
    out = [{"category": "重要性", "label": kp["重要性"]}]
    for t in split_tags(kp["类型标签"]):
        out.append({"category": "知识点类型", "label": t})
    for t in split_tags(kp["错因倾向"]):
        out.append({"category": "错因倾向", "label": t})
    out.append({"category": "上级知识点追溯", "label": "有上级" if kp.get("上级知识点") else "无上级"})
    return out


def student_advice_summary(weak):
    if not weak:
        return "当前作答覆盖的知识点较少，建议先完成一轮完整练习后再生成个性化建议。"
    names = "」「".join(p["name"] for p in weak[:3])
    return f"优先补「{names}」。先按上面的逐条建议改错，再用同型题各练 3—5 道，做完对照步骤自查。"


def build_classes(student_rows):
    by_class = defaultdict(list)
    for s in student_rows:
        by_class[s["class"]].append(s)
    out = {}
    for c, rows in sorted(by_class.items()):
        metrics = class_metrics(rows)
        out[c] = {
            "id": c,
            "student_count": len(rows),
            "attempts": sum(s["attempts"] for s in rows),
            "score": round(sum(s["score"] for s in rows), 2),
            "full": round(sum(s["full"] for s in rows), 2),
            **metrics,
            "students": rows,
        }
    return out


def build_points(clean, classes, kps, students, cfg=CONFIG):
    kp_by_id = {k["指标编号"]: k for k in kps}
    cls_of = {s["id"]: s["class"] for s in students}
    all_stats = aggregate(clean, lambda r: r["indicator"])
    cls_stats = aggregate(clean, lambda r: (r["indicator"], cls_of.get(r["student"], "未分班")))
    points = []
    for pid, kp in kp_by_id.items():
        st = stats_of(all_stats[pid]) if pid in all_stats else {
            "attempts": 0, "score": 0, "full": 0, "score_rate": 0, "error_rate": 0, "mastery": "数据不足"
        }
        class_rates = {}
        for c in classes:
            a = cls_stats.get((pid, c))
            if a and a["full"]:
                class_rates[c] = {
                    "attempts": a["attempts"], "score": round(a["score"], 2), "full": round(a["full"], 2),
                    "score_rate": round(a["score"] / a["full"], 4),
                    "error_rate": round(1 - a["score"] / a["full"], 4),
                }
        parent = PARENTS.get(pid, {"id": None, "name": kp.get("上级知识点", ""), "external": True, "reason": ""})
        points.append({
            "id": pid,
            "name": kp["观察指标"],
            "theme": kp["主题"],
            "theme_id": kp["主题编号"],
            "theme_goal": kp["主题目标"],
            "tags": split_tags(kp["类型标签"]),
            "primary_type": primary_type(kp),
            "importance": kp["重要性"],
            "error_tendency": split_tags(kp["错因倾向"]),
            "pages": kp["教材正文页"],
            "course_standard": kp["课标文件页／正文页"],
            "parent_id": parent["id"],
            "parent_name": parent["name"],
            "parent_external": parent["external"],
            "parent_reason": parent["reason"],
            "xlsx_parent": kp.get("上级知识点", ""),
            "advice": kp["拼接学习建议"],
            **st,
            "class_rates": class_rates,
        })

    # 按错误率降序切成重点 / 次重点 / 注意点，9 个点就是 3/4/2
    ranked = sorted(points, key=lambda p: (-p["error_rate"], -p["attempts"], p["id"]))
    top, mid, _ = tier_cut(len(ranked), cfg)
    for i, p in enumerate(ranked):
        p["rank"] = i + 1
        p["tier"] = "重点" if i < top else ("次重点" if i < top + mid else "注意点")
    return sorted(points, key=lambda p: p["rank"])


def build_warnings(points, top_n=3):
    ranked = sorted([p for p in points if p["attempts"] > 0], key=lambda p: (-p["error_rate"], p["id"]))[:top_n]
    out = []
    for i, p in enumerate(ranked, 1):
        parent_name = p["parent_name"]
        out.append({
            "rank": i,
            "point_id": p["id"],
            "point_name": p["name"],
            "error_rate": p["error_rate"],
            "error_rate_text": pct(p["error_rate"]),
            "parent_id": p["parent_id"],
            "parent_name": parent_name,
            "parent_external": p["parent_external"],
            "reason": p["parent_reason"],
            "student_text": f"「{parent_name}」学习仍有欠缺",
            "teacher_text": f"「{parent_name}」再讲一下吧",
            "action": f"先补「{parent_name}」，再回练「{p['name']}」",
        })
    return out


def warnings_from_stats(stats, points, top_n=3):
    by_id = {p["id"]: p for p in points}
    ranked = sorted([s for s in stats if s["attempts"] > 0], key=lambda s: (-s["error_rate"], s["id"]))[:top_n]
    out = []
    for i, s in enumerate(ranked, 1):
        p = by_id[s["id"]]
        out.append({
            "rank": i,
            "point_id": p["id"],
            "point_name": p["name"],
            "error_rate": s["error_rate"],
            "error_rate_text": pct(s["error_rate"]),
            "parent_id": p["parent_id"],
            "parent_name": p["parent_name"],
            "parent_external": p["parent_external"],
            "reason": p["parent_reason"],
            "student_text": f"「{p['parent_name']}」学习仍有欠缺",
            "teacher_text": f"「{p['parent_name']}」再讲一下吧",
            "action": f"先补「{p['parent_name']}」，再回练「{p['name']}」",
        })
    return out


def build_attention(student_rows, cfg=CONFIG):
    rows = []
    for s in student_rows:
        weak = [p for p in s["points"] if p["attempts"] >= cfg["attention"]["min_attempts"] and p["error_rate"] >= cfg["attention"]["weak_error_rate"]]
        rows.append({
            "id": s["id"], "class": s["class"], "score_rate": s["score_rate"],
            "weak_count": len(weak),
            "weak_points": [{"id": p["id"], "name": p["name"], "error_rate": p["error_rate"]} for p in sorted(weak, key=lambda p: -p["error_rate"])[:4]],
            "class_rank": s.get("class_rank"), "class_size": s.get("class_size"),
        })
    rows.sort(key=lambda r: (-r["weak_count"], r["score_rate"], r["id"]))
    for i, r in enumerate(rows, 1):
        r["attention_rank"] = i
        r["level"] = "高" if r["weak_count"] >= 5 else ("中" if r["weak_count"] >= 3 else "低")
    return rows


# ---------- 教学建议 ----------
def module_text(advice_modules, category, label):
    for m in advice_modules:
        if m["模块类别"] == category and m["模块标签"] == label:
            return m["可拼接建议话术"]
    return ""


def build_teaching_advice(student_rows, advice_modules, points, cfg=CONFIG):
    counter = Counter()
    examples = {}
    for s in student_rows:
        seen = set()
        for p in s["weak_points"]:
            for m in p.get("advice_modules", []):
                key = (m["category"], m["label"])
                if key in seen:
                    continue
                seen.add(key)
                counter[key] += 1
                examples.setdefault(key, []).append(s["id"])

    total = len(student_rows) or 1
    top_modules = []
    for (cat, label), count in counter.most_common():
        top_modules.append({
            "category": cat,
            "label": label,
            "count": count,
            "share": round(count / total, 4),
            "text": module_text(advice_modules, cat, label),
            "examples": examples.get((cat, label), [])[:5],
        })

    def pick(cat, n=2):
        return [m for m in top_modules if m["category"] == cat][:n]

    importance = pick("重要性", 1)
    types = pick("知识点类型", 1)
    errors = pick("错因倾向", 1)

    parts = []
    if importance:
        parts.append(importance[0]["text"])
    for m in types:
        parts.append(m["text"])
    for m in errors:
        parts.append(m["text"])

    focus = [m for m in top_modules if m["category"] in ("知识点类型", "错因倾向")][:3]
    actions = []
    if focus:
        actions.append("班课先按「" + "」「".join(m["label"] for m in focus) + "」组织一轮 10 分钟微专题。")
    top_points = sorted(points, key=lambda p: -p["error_rate"])[:3]
    actions.append("用「" + "」「".join(p["name"] for p in top_points) + "」做当堂变式，先补前置节点再回练。")
    actions.append("对重点关注对象按个人建议单独立项，三天后回收同型题正确率。")

    ranked = [next((m for m in top_modules if m["category"] == cat), None) for cat in ("重要性", "知识点类型", "错因倾向")]
    ranked = [m for m in ranked if m]
    lead = ""
    if ranked:
        lead = "全班 " + str(len(student_rows)) + " 人的建议汇总起来，「" + "」「".join(m["label"] for m in ranked) + "」这几类最突出。"
    return {
        "sample_size": len(student_rows),
        "top_modules": top_modules[:12],
        "by_category": {
            "importance": importance,
            "types": types,
            "errors": errors,
        },
        "paragraph": lead + "\n".join(parts),
        "actions": actions,
        "top_points": [{"id": p["id"], "name": p["name"], "error_rate": p["error_rate"], "tier": p["tier"]} for p in top_points],
    }


# ---------- 思维导图 ----------
def build_mindmap(points, detail=False):
    by_id = {p["id"]: p for p in points}
    nodes = []
    for p in points:
        pos = POSITIONS[p["id"]]
        nodes.append({
            "id": p["id"], "name": p["name"], "label": p["name"], "theme": p["theme"],
            "theme_id": p["theme_id"], "kind": "point", "type": p["primary_type"],
            "tags": p["tags"], "importance": p["importance"], "tier": p["tier"],
            "error_rate": p["error_rate"], "error_rate_text": pct(p["error_rate"]),
            "parent_id": p["parent_id"], "parent_name": p["parent_name"],
            "pages": p["pages"], "x": pos["x"], "y": pos["y"],
        })
    for f in FOUNDATION_NODES:
        pos = POSITIONS[f["id"]]
        nodes.append({
            "id": f["id"], "name": f["name"], "label": f["name"], "detail": f["detail"],
            "kind": "foundation", "type": "基础", "x": pos["x"], "y": pos["y"],
        })

    edges = []
    for a, b, kind in EDGES:
        edge = {"from": a, "to": b, "kind": kind}
        if kind == "foundation":
            edge["label"] = "前置"
        elif kind == "cross":
            edge["label"] = "迁移"
        edges.append(edge)

    themes = []
    for tid, tname, goal in [
        ("T01", "代数式与整式运算", "能用代数式表示数量关系、代入求值，并进行整式加减与解释。"),
        ("T02", "方程与方程组", "能解一元一次方程和二元一次方程组，依据等量关系建模并检验。"),
        ("T03", "不等式与不等式组", "能解一元一次不等式（组），表示解集，并解决范围约束问题。"),
    ]:
        sub = [p for p in points if p["theme_id"] == tid]
        themes.append({
            "id": tid, "name": tname, "goal": goal,
            "points": [p["id"] for p in sub],
            "error_rate": round(sum(p["score"] for p in sub) / sum(p["full"] for p in sub), 4) if sum(p["full"] for p in sub) else 0,
            "color": "memory" if sum(1 for p in sub if p["primary_type"] == "记忆型") >= 2 else "logic",
        })
    return {
        "canvas": {"width": 1260, "height": 800},
        "legend": [
            {"color": "memory", "label": "记忆型", "desc": "概念、法则、性质为主"},
            {"color": "logic", "label": "逻辑型（知识型）", "desc": "运算、推理、建模为主"},
            {"color": "foundation", "label": "前置基础", "desc": "思维导图外的先备知识"},
        ],
        "themes": themes,
        "nodes": nodes,
        "edges": edges,
    }


# ---------- 模型装配 ----------
def analyze(clean, students, kps, advice_modules, config=None, meta=None):
    cfg = dict(CONFIG)
    if config:
        cfg.update(config)
    student_rows = build_students(clean, students, kps, cfg)
    classes = build_classes(student_rows)
    points = build_points(clean, classes.keys(), kps, students, cfg)
    warnings = build_warnings(points, cfg["warning_top_n"])
    for s in student_rows:
        s["warnings"] = warnings_from_stats(s["points"], points, cfg["warning_top_n"])

    class_analysis = {}
    for c, cinfo in classes.items():
        rows = cinfo.pop("students")
        cpoints = []
        for p in points:
            cr = p["class_rates"].get(c)
            if cr:
                cpoints.append({**{k: p[k] for k in ("id", "name", "theme", "tags", "primary_type", "importance", "tier", "pages", "parent_id", "parent_name")}, **cr, "mastery": mastery_of(cr["error_rate"])})
        cw = warnings_from_stats(cpoints, points, cfg["warning_top_n"])
        class_analysis[c] = {
            **cinfo,
            "points": cpoints,
            "warnings": cw,
            "teaching_advice": build_teaching_advice([s for s in student_rows if s["class"] == c], advice_modules, points, cfg),
        }

    attention = build_attention(student_rows, cfg)
    teaching_advice = build_teaching_advice(student_rows, advice_modules, points, cfg)

    # 两班对比：同一个知识点，两个班各错多少——教师端那张对比表就靠它
    class_ids = list(classes.keys())
    comparison = {"classes": class_ids, "points": []}
    for p in points:
        comparison["points"].append({
            "id": p["id"], "name": p["name"],
            "rates": {c: p["class_rates"].get(c, {}).get("error_rate") for c in class_ids},
        })

    model = {
        "schema_version": "2.0",
        "project": "智慧学伴 · 七年级数学学情分析",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "meta": meta or {},
        "config": cfg,
        "definitions": {
            "error_rate": "1 - score/full，按知识点汇总；个人、班级、年级用的是同一套算法。",
            "mastery": "错误率≤20% 掌握；20%—40% 部分掌握；>40% 未掌握。",
            "tier": "错误率降序，前30%为重点、中间40%为次重点、后30%为注意点。",
            "warning": "取错误率最高的3个知识点，沿思维导图前置关系向上找直接前置节点，作为“可能未听懂的地方”。",
        },
        "mindmap": build_mindmap(points),
        "points": points,
        "classes": class_analysis,
        "students": student_rows,
        "attention_students": attention[:10],
        "warnings": warnings,
        "teaching_advice": teaching_advice,
        "comparison": comparison,
        "data_quality": {
            "responses": len(clean),
            "students": len(student_rows),
            "classes": len(class_analysis),
            "points": len(points),
            "coverage": round(len({(r["student"], r["indicator"]) for r in clean}) / (len(student_rows) * len(points)), 4) if student_rows and points else 0,
        },
    }
    return model


# ---------- 人读摘要 ----------
def to_markdown(model):
    lines = []
    a = lines.append
    a(f"# {model['project']}\n")
    a(f"- 生成时间：{model['generated_at']}")
    a(f"- 学生：{model['data_quality']['students']} 人；作答：{model['data_quality']['responses']} 条；知识点：{model['data_quality']['points']} 个")
    a(f"- 错误率基线：{int(model['config']['baseline_error_rate']*100)}%\n")

    a("## 一、知识点分层\n")
    a("| 排名 | 指标 | 知识点 | 类型 | 错误率 | 层级 | 前置节点 |")
    a("|---|---|---|---|---|---|---|")
    for p in model["points"]:
        a(f"| {p['rank']} | {p['id']} | {p['name']} | {'/'.join(p['tags'])} | {pct(p['error_rate'])} | {p['tier']} | {p['parent_name']} |")
    a("")

    a("## 二、学习预警（错误率最高三个 → 前置节点）\n")
    for w in model["warnings"]:
        a(f"{w['rank']}. **{w['point_name']}** 错误率 {w['error_rate_text']} → 可能未听懂：**{w['parent_name']}**")
        a(f"   - 学生端：{w['student_text']}")
        a(f"   - 教师端：{w['teacher_text']}")
        a(f"   - 依据：{w['reason']}")
    a("")

    a("## 三、班级概览\n")
    a("| 班级 | 人数 | 平均得分率 | 及格率 | 优秀率 |")
    a("|---|---|---|---|---|")
    for c, v in model["classes"].items():
        a(f"| {c} | {v['student_count']} | {pct(v['avg_student_rate'])} | {pct(v['pass_rate'])} | {pct(v['excellent_rate'])} |")
    a("")

    a("## 四、教学建议（由学生端建议聚合）\n")
    ta = model["teaching_advice"]
    a(ta["paragraph"] or "暂无足够数据。")
    a("")
    for x in ta["actions"]:
        a(f"- {x}")
    a("")
    a("### 高频建议模块\n")
    a("| 类别 | 模块 | 出现次数 | 占学生比例 |")
    a("|---|---|---|---|")
    for m in ta["top_modules"][:10]:
        a(f"| {m['category']} | {m['label']} | {m['count']} | {pct(m['share'])} |")
    a("")

    a("## 五、重点关注对象（前 10）\n")
    a("| 排名 | 学生 | 班级 | 得分率 | 薄弱点数 | 主要薄弱点 |")
    a("|---|---|---|---|---|---|")
    for s in model["attention_students"]:
        names = "、".join(f"{x['name']}({pct(x['error_rate'])})" for x in s["weak_points"][:3])
        a(f"| {s['attention_rank']} | {s['id']} | {s['class']} | {pct(s['score_rate'])} | {s['weak_count']} | {names} |")
    a("")
    a("---\n")
    a("内容由AI大模型生成，请仔细甄别")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="智慧学伴 v2 学情分析引擎")
    ap.add_argument("--responses", default=os.path.join(HERE, "data", "responses_raw.json"))
    ap.add_argument("--students", default=os.path.join(HERE, "data", "students.json"))
    ap.add_argument("--knowledge", default=os.path.join(HERE, "data", "knowledge_points.json"))
    ap.add_argument("--advice", default=os.path.join(HERE, "data", "advice_modules.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "model"))
    args = ap.parse_args(argv)

    if args.responses.lower().endswith(".csv"):
        raw_responses = read_csv_responses(args.responses)
    else:
        raw_responses = load_json(args.responses)
    clean, issues = normalize_responses(raw_responses)
    kps = load_json(args.knowledge)
    advice = load_json(args.advice)
    if args.students and os.path.exists(args.students):
        students = normalize_students(load_json(args.students))
    else:
        students = derive_students_from_responses(clean)

    known_ids = {s["id"] for s in students}
    for r in clean:
        if r["student"] not in known_ids:
            students.append({"id": r["student"], "class": "未分班", "grade": 7})
            known_ids.add(r["student"])

    valid_points = {k["指标编号"] for k in kps}
    unknown = Counter(r["indicator"] for r in clean if r["indicator"] not in valid_points)
    if unknown:
        clean = [r for r in clean if r["indicator"] in valid_points]

    model = analyze(clean, students, kps, advice,
                    meta={"source_responses": os.path.basename(args.responses), "issues": issues[:20],
                          "issue_count": len(issues), "unknown_indicators": dict(unknown)})
    os.makedirs(args.out, exist_ok=True)
    dump_json(os.path.join(args.out, "model.json"), model)
    dump_json(os.path.join(args.out, "student_analysis.json"), model["students"])
    dump_json(os.path.join(args.out, "teacher_analysis.json"), {
        "classes": model["classes"], "points": model["points"],
        "attention_students": model["attention_students"],
        "teaching_advice": model["teaching_advice"], "warnings": model["warnings"],
        "comparison": model["comparison"],
    })
    dump_json(os.path.join(args.out, "warnings.json"), model["warnings"])
    dump_json(os.path.join(args.out, "teaching_advice.json"), model["teaching_advice"])
    dump_json(os.path.join(args.out, "mindmap.json"), model["mindmap"])
    dump_json(os.path.join(args.out, "summary.json"), {
        "project": model["project"], "generated_at": model["generated_at"],
        "data_quality": model["data_quality"], "warnings": model["warnings"],
        "top_points": model["points"][:3],
    })
    with open(os.path.join(args.out, "summary.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(model))

    print(f"OK students={len(model['students'])} responses={len(clean)} points={len(model['points'])} out={args.out}")
    print("WARN " + " | ".join(f"{w['point_id']}->{w['parent_name']}" for w in model["warnings"]))
    if issues:
        print(f"FILTERED {len(issues)} invalid rows")
    if unknown:
        print("UNKNOWN indicators:", dict(unknown))


if __name__ == "__main__":
    main()
