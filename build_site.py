# -*- coding: utf-8 -*-
"""
把模型和数据塞进模板，吐出一个双击就能打开的网页。

做法很笨但够用：读几个 JSON -> 拼成一个 data 包 -> 用 JSON 字符串替换模板里的
__APP_DATA__ -> 写出 web/index.html。没有后端，也没有任何网络请求。

要读的东西（都在本目录下）：
    model/model.json              engine.py 算出来的
    model/knowledge_content.json  content_bank.py 拼出来的
    model/sample_input.json       给「机器接入」面板看的示例
    data/*.json                   知识点、建议模块、名单、作答明细

要加字段：data 里加一行，模板里 D.字段名 就能读到。
占位符名必须和模板里的 __APP_DATA__ 一字不差，对不上就注入失败，打开是白屏。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def load(p):
    with open(p, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def main():
    model = load(os.path.join(HERE, "model", "model.json"))
    content = load(os.path.join(HERE, "model", "knowledge_content.json"))
    kps = load(os.path.join(HERE, "data", "knowledge_points.json"))
    modules = load(os.path.join(HERE, "data", "advice_modules.json"))
    students = load(os.path.join(HERE, "data", "students.json"))["students"]
    raw = load(os.path.join(HERE, "data", "responses_raw.json"))

    # 知识点元数据重新摊平成前端好用的样子
    # 前置关系不在这里定义，来自 engine.PARENTS——要改得回去改那个
    import engine  # 复用前置关系
    points = []
    for kp in kps:
        pid = kp["指标编号"]
        parent = engine.PARENTS[pid]
        tags = [t.strip() for t in kp["类型标签"].split("、") if t.strip()]
        primary = next((t for t in tags if t in ("逻辑型", "记忆型")), tags[-1] if tags else "逻辑型")
        points.append({
            "id": pid,
            "name": kp["观察指标"],
            "theme": kp["主题"],
            "theme_id": kp["主题编号"],
            "theme_goal": kp["主题目标"],
            "tags": tags,
            "primary_type": primary,
            "importance": kp["重要性"],
            "error_tendency": [t.strip() for t in kp["错因倾向"].split("、") if t.strip()],
            "pages": kp["教材正文页"],
            "parent_id": parent["id"],
            "parent_name": parent["name"],
            "parent_external": parent["external"],
            "parent_reason": parent["reason"],
            "xlsx_parent": kp["上级知识点"],
            "advice": kp["拼接学习建议"],
        })

    # 作答明细换成短字段再进前端：s=学生，i=知识点，sc=得分，f=满分
    # 单文件网页里每个字节都要省，四个字段名能压掉不少
    # 不在这 9 个知识点里的记录直接扔掉，扔了多少记在 filtered
    responses = [{"s": r["student"], "i": r["indicator"], "sc": r["score"], "f": r["full"]} for r in raw]
    unknown = [r for r in responses if r["i"] not in {p["id"] for p in points}]
    responses = [r for r in responses if r["i"] in {p["id"] for p in points}]

    # 前端能看到的全部数据。想加字段就在这儿加，模板里用 D.字段名 取
    data = {
        "project": model["project"],
        "generated_at": model["generated_at"],
        "config": model["config"],
        "definitions": model["definitions"],
        "points": points,
        "modules": modules,
        "students": students,
        "responses": responses,
        "content": content,
        "mindmap": model["mindmap"],
        "sample": load(os.path.join(HERE, "model", "sample_input.json")),
        "precomputed": {
            "data_quality": model["data_quality"],
            "warnings": model["warnings"],
        },
        "filtered": len(unknown),
    }

    # 注入：分隔符去掉空格，单文件网页能小几十 KB
    # 占位符名和模板里的 __APP_DATA__ 必须一致
    template = open(os.path.join(HERE, "site_template.html"), "r", encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = template.replace("__APP_DATA__", payload)
    os.makedirs(os.path.join(HERE, "web"), exist_ok=True)
    out = os.path.join(HERE, "web", "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("OK site", out, len(html), "chars", "responses", len(responses), "filtered", len(unknown))


if __name__ == "__main__":
    main()
