# 智慧学伴 · 模型与网页生成代码

这套代码完成两件事：

1. **生成模型**：读取 40 名学生、两个班级、9 个知识点的作答明细，按统一口径算出错误率、掌握度、班级对比、学习预警、重点关注对象、教学建议和思维导图，输出机器可读的 `model/model.json`。
2. **生成网页**：把模型数据和知识点讲解注入单文件模板，输出离线可打开的 `web/index.html`，包含首页思维导图、学生端、教师端和知识点详情抽屉。

> 说明：这里的“训练”是可复现的**统计 / 规则模型**，不是神经网络。运行一次就是一次完整推断；换一批同结构数据，结果会按同一口径重新计算，不需要 GPU，也不需要联网。

---

## 一、目录结构

```
代码1/
├─ 代码1.py                一键运行入口（依次跑下面三个脚本）
├─ engine.py               分析引擎：数据清洗 + 聚合 + 预警 + 教学建议 + 思维导图
├─ content_bank.py         知识点内容库：例题 / 推导 / 易错 / 课后习题
├─ build_site.py           把模型注入模板，生成单文件网页
├─ site_template.html      网页模板（配色、布局、交互逻辑）
├─ data/                   输入数据
│  ├─ responses_raw.json   作答明细（学生 × 知识点 × 得分/满分）
│  ├─ students.json        学生名单与班级
│  ├─ knowledge_points.json 9 个知识点的元数据
│  ├─ advice_modules.json  学习建议模块
│  ├─ questions.json       54 道配套题
│  └─ evidence_brief.json  教材原文切片
├─ model/                  输出：模型与内容
│  ├─ model.json           主模型（网页、文档、PPT 都读它）
│  ├─ student_analysis.json 学生端分析
│  ├─ teacher_analysis.json 教师端分析
│  ├─ warnings.json        学习预警
│  ├─ teaching_advice.json 教学建议
│  ├─ mindmap.json         思维导图节点与连线
│  ├─ summary.json / .md   汇总摘要
│  ├─ knowledge_content.json 知识点讲解
│  ├─ schema.json          JSON Schema（机器阅读）
│  └─ sample_input.json    外部数据接入示例
└─ web/
   └─ index.html           最终网页（双击即可离线打开）
```

---

## 二、怎么运行

在本目录打开终端：

```bash
python 代码1.py              # 全流程：模型 -> 内容 -> 网页
python 代码1.py --skip-web   # 只重算模型和内容
python 代码1.py --only-web   # 只重新生成网页
```

也可以分步跑：

```bash
python engine.py             # 默认读 data/，写到 model/
python content_bank.py       # 读 data/，写 model/knowledge_content.json
python build_site.py         # 读 model/ + data/，写 web/index.html
```

依赖：Python 3.9+，只用标准库（`json` / `csv` / `subprocess`），无需 `pip install`。

---

## 三、核心口径

| 指标 | 定义 | 默认值 |
|---|---|---|
| 错误率 | `1 - 得分 / 满分`，按知识点汇总 | — |
| 关注基线 | 错误率高于此值视为需要关注 | 30% |
| 重点分层 | 错误率降序，前 30% / 中 40% / 后 30% | 9 个点 → 3 / 4 / 2 |
| 掌握度 | ≤20% 掌握；20%—40% 部分掌握；>40% 未掌握 | 20% / 40% |
| 学习预警 | 错误率最高 3 个点，沿思维导图向上找**直接前置节点** | 3 个 |
| 重点关注对象 | 作答次数 ≥2 且存在薄弱知识点 | 2 次 / 40% |

思维导图的红色 = **记忆型**，蓝色 = **逻辑型（知识型）**；灰色外圈节点 = 思维导图之外的先备基础（F01—F05）。

---

## 四、可调节参数总表

### 1. `engine.py` — 分析口径

文件顶部的 `CONFIG`：

| 参数 | 作用 | 默认 |
|---|---|---|
| `baseline_error_rate` | 预警基线 | `0.30` |
| `warning_top_n` | 学习预警取前几个知识点 | `3` |
| `tiers` | 重点 / 次重点 / 注意点比例 | `0.30 / 0.40 / 0.30` |
| `mastery.mastered_max_error` | 判为“掌握”的错误率上限 | `0.20` |
| `mastery.partial_max_error` | 判为“部分掌握”的错误率上限 | `0.40` |
| `attention.min_attempts` | 进入重点关注名单的最少作答次数 | `2` |
| `attention.weak_error_rate` | 判定“薄弱知识点”的错误率阈值 | `0.40` |

其他可调结构：

| 位置 | 作用 | 改它会怎样 |
|---|---|---|
| `PARENTS` | 每个知识点的“直接前置节点” | 改变学生端“学习仍有欠缺”和教师端“再讲一下吧”的追溯方向 |
| `FOUNDATION_NODES` | 思维导图外的先备知识 F01—F05 | 增删基础节点 |
| `EDGES` | 思维导图连线类型 | 改变导图结构与连线样式 |
| `POSITIONS` | 思维导图节点坐标 | 调整导图布局 |
| 命令行参数 | `--responses` / `--students` / `--knowledge` / `--advice` / `--out` | 换输入文件或输出目录 |

命令行示例：

```bash
python engine.py --responses data/responses_raw.json --out model
python engine.py --responses new.csv --students new_students.json --out model_new
```

### 2. `content_bank.py` — 知识点内容

| 位置 | 作用 | 怎么改 |
|---|---|---|
| `CONTENT[知识点编号]` | 9 个知识点的手写讲解 | 改 `example` / `derivation` / `common_errors` / `tips` |
| `EXTRA_EXERCISES[知识点编号]` | 补充课后习题 | 往列表里加题：`id / type / stem / answer / explanation / full / level` |
| `build()` 里的三处 `load_json` | 输入文件 | 换成自己的文件名 |
| `build()` 末尾的 `model/knowledge_content.json` | 输出路径 | 按需修改 |

知识点编号必须与 `data/knowledge_points.json` 的「指标编号」一致（`T01I1`—`T03I3`）。

### 3. `build_site.py` — 网页数据注入

| 位置 | 作用 | 怎么改 |
|---|---|---|
| `main()` 开头的 `load(...)` | 读取哪些文件 | 换数据源时改路径 |
| `data` 字典 | 注入前端的完整数据包 | 新增字段后，在模板 JS 里用 `D.字段名` 读取 |
| `template.replace("__APP_DATA__", payload)` | 占位符 | 模板里的占位符必须同名 |
| 末尾 `web/index.html` | 输出路径 | 按需修改 |

### 4. `site_template.html` — 配色与交互

**配色**只改 `:root` 里的 CSS 变量：

| 变量 | 含义 |
|---|---|
| `--red` / `--red-bg` / `--red-soft` | 记忆型主色与浅底 |
| `--blue` / `--blue-bg` | 逻辑型主色与浅底 |
| `--ink` / `--ink2` / `--muted` | 文字层级 |
| `--paper` / `--card` | 页面底色 / 卡片底色 |
| `--gold` / `--teal` | 强调色 / 教师端辅助色 |
| `--serif` / `--sans` | 字体族 |

**交互逻辑**按脚本内分段注释定位：

| 分段 | 作用 |
|---|---|
| `utils` | 格式化、聚合、掌握度判定 |
| `analysis` | `computeModel`：浏览器内按同一口径重算 |
| `mind map` | 首页思维导图（inline SVG） |
| `home` / `student` / `teacher` | 三端页面渲染 |
| `drawer` | 知识点详情抽屉 |
| `import` | JSON / CSV 机器接入 |
| `route` | 页面切换与初始化 |

**初始状态**在 `STATE` 里改：默认页面 `view`、默认学生 `student`、默认班级 `teacherClass`。

### 5. `代码1.py` — 流水线

`STEPS` 列表就是执行顺序；注释掉某一行即可跳过对应步骤。也支持 `--skip-web` / `--only-web`。

---

## 五、换一批数据怎么接

### 方式 A：命令行（推荐）

```bash
python engine.py --responses 新作答.json --students 新名单.json --out model_new
```

作答明细接受 JSON 或 CSV。JSON 每条记录字段：

```json
{"student": "D41", "indicator": "T01I1", "score": 1, "full": 3}
```

学生名单字段：

```json
{"id": "D41", "name": "学生41", "class": "1班"}
```

### 方式 B：网页内导入

打开 `web/index.html`，在“机器接入”区域粘贴 JSON 或上传 CSV，页面会用 `computeModel` 在浏览器内按同一口径重算，不经过后端。

字段规范见 `model/schema.json`，可参考 `model/sample_input.json`。

---

## 六、常见调整场景

| 想做的事 | 改哪里 |
|---|---|
| 预警从 3 个改成 5 个 | `engine.py` → `CONFIG["warning_top_n"] = 5`，重跑 |
| 掌握线从 20% 改成 25% | `engine.py` → `CONFIG["mastery"]["mastered_max_error"] = 0.25`，重跑 |
| 换一个知识点前置关系 | `engine.py` → `PARENTS[编号]`，重跑 |
| 换网页主色 | `site_template.html` → `:root` 的 `--red` / `--blue`，重跑 `build_site.py` |
| 换一道教材例题 | `content_bank.py` → `CONTENT[编号]["example"]`，重跑 |
| 新增一个知识点 | 同步改 `data/knowledge_points.json`、`content_bank.py` 的 `CONTENT`、`engine.py` 的 `PARENTS` / `POSITIONS` / `EDGES` |
| 换学生数据 | 替换 `data/responses_raw.json` 与 `data/students.json`，重跑全流程 |

---

## 七、输出说明

- `model/model.json`：主模型，包含 `config`、`definitions`、`points`、`students`、`classes`、`warnings`、`teaching_advice`、`mindmap`、`data_quality`。
- `model/student_analysis.json`：每个学生的个人薄弱点、掌握度、学习建议、学习预警。
- `model/teacher_analysis.json`：班级维度、知识点维度、重点关注名单、教学建议、学习预警。
- `model/warnings.json`：错误率前三 → 直接前置节点。
- `model/teaching_advice.json`：学生建议模块按频次聚合出的班课动作。
- `model/summary.md`：人读摘要。
- `web/index.html`：离线单文件网页，不依赖 CDN、不发起网络请求。

---


