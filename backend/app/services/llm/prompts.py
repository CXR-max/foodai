"""
Prompt 模板（想调教模型效果就改这里的文字）

两个 prompt 的关键设计：
1. 识别：只要"菜名+份量档位+克重"，不要模型算营养（营养由本地库算，口径统一、免费小模型也够用）
2. 方案：偏好优先 + 禁忌绝对排除 + 渐进式（day1 接近现状，逐日逼近目标）
"""
from app.services.llm import foods_data

# ---------- 食物识别 ----------


def build_food_recognition_prompt() -> str:
    """构造识别提示词：注入本地营养库菜名+标准份量，引导模型输出可精确匹配的菜名。

    注意：活跃池现在有 1,800+ 条（含台湾食材），全量塞进提示词会撑爆模型上下文
    （实测 glm-4v-flash 报 inputs tokens + max_new_tokens 超限）。所以只放「菜品」，
    并按优先级截断到 300 条——食材由模型自然说出，不靠清单。
    """
    foods = [
        f for f in foods_data.load_foods()
        if f.get("food_type") in {"dish", "light_meal"}
    ]
    foods.sort(key=lambda f: (0 if f.get("source") == "recipe" else 1, f.get("id") or 0))
    foods = foods[:300]
    catalog = "、".join(f'{f["name"]}(标准{f["common_portion_g"]}g)' for f in foods)
    return f"""你是一个专业的营养分析专家，正在帮用户估算餐食的分量与热量。请仔细观察图片，严格只输出一个 JSON 对象，不要任何解释或 markdown 代码块。

观察步骤（在心里执行，不要输出过程）：
1. 先找参照物：餐具（碗/盘/勺/筷子）、手掌、易拉罐、手机等，用它们推断食物实际大小
2. 再判断盛具与份量：小碗装米饭约 150g，普通饭碗约 200g，标准餐盘一整格菜约 150~250g
3. 最后判断食材在容器中的堆叠高度与密度，给出份量档位

本地营养库收录的菜品（凡能对上的菜，dish_name 必须使用这些标准名称，括号内为该菜常见一份克数）：
{catalog}

输出格式：
{{
  "is_food": true,
  "dish_name": "菜品名（优先从上面清单选标准名，如宫保鸡丁、米饭；多道菜取最主要的一道；对不上清单时用通用菜名）",
  "category": "食材类别：主食/荤菜/素菜/汤粥/早餐/小吃/水果/饮品/其他",
  "size_class": "small | normal | large（相对该菜常见一份的大小，明显更小选 small，明显更大选 large）",
  "basis": "你观察到的参照物与判断依据，如：普通白瓷碗约八分满",
  "ingredients": [{{"name": "食材名", "portion_g": 估算克重}}],
  "portion_g": 0,
  "portion_desc": "分量描述，如：一碗约300g",
  "confidence": 0.85,
  "dishes": [
    {{"dish_name": "第1道菜的标准名", "category": "荤菜", "size_class": "normal", "portion_g": 200,
      "portion_desc": "一小盘约200g", "confidence": 0.85,
      "ingredients": [{{"name": "食材名", "portion_g": 估算克重}}]}}
  ],
  "description": "一句话描述这道菜"
}}

要求：
- size_class 以该菜常见一份为基准，拿不准时用 normal
- portion_g 是这份食物总克数的估算，必须与 size_class 判断一致
- ingredients 列出 2~6 种主要食材及估算克重，总和应接近 portion_g
- confidence 为 0~1 的识别置信度
- dishes 列出画面里所有能辨认的菜（最多 4 道），每道菜单独给名称/类别/份量/置信度；只有一道菜时数组里就放 1 项，且与顶层 dish_name/portion_g 一致
- 顶层 dish_name 仍填最主要的一道（通常是分量最大的），保留兼容
- 若图片中不是食物，is_food 设为 false，dishes 给空数组，其余字段给默认值（dish_name 填"非食物"）"""


# ---------- 方案生成 ----------

# 方案生成/运动排期用的参考 MET（按 60kg 成年人）——**经验近似值**，
# 逐项校准版见 services/met.py（那里带三档强度与来源标注）；此处只用于方案里的粗略估量
EXERCISE_METABOLIC = {
    "跑步": 10, "跳绳": 11, "游泳": 9, "骑行": 7,
    "力量训练": 6, "球类": 7, "快走": 5, "瑜伽": 3,
}

GOAL_LABEL = {"lose": "减脂", "maintain": "维持体重", "gain": "增肌", "healthy": "健康饮食"}
ACTIVITY_LABEL = {
    "sedentary": "久坐少动", "light": "轻度活动", "moderate": "中度活动",
    "active": "高度活动", "athlete": "运动员级",
}


def _targets_text(profile_ctx: dict) -> str:
    """当日七项硬区间

    只给总热量时，模型会按自己的口味配「高蛋白高脂低碳」，复核按区间一卡就整份
    拒收（蛋白 148 g / 脂肪 116 g 这种）。把区间原样写进提示词，一次就能排进去。
    """
    t = profile_ctx.get("targets") or {}
    if not t:
        return ""
    parts: list[str] = []

    def _span(lo_key: str, hi_key: str, label: str, unit: str) -> None:
        lo, hi = t.get(lo_key), t.get(hi_key)
        if lo is None or hi is None:
            return
        parts.append(f"{label} {float(lo):g}~{float(hi):g} {unit}")

    _span("calorie_min", "calorie_max", "热量", "kcal")
    _span("protein_min_g", "protein_max_g", "蛋白质", "g")
    _span("fat_min_g", "fat_max_g", "脂肪", "g")
    _span("carb_min_g", "carb_max_g", "碳水", "g")
    if t.get("fiber_min_g") is not None:
        parts.append(f"膳食纤维 ≥{float(t['fiber_min_g']):g} g")
    if t.get("sodium_max_mg") is not None:
        parts.append(f"钠 ≤{float(t['sodium_max_mg']):g} mg")
    if not parts:
        return ""
    return (
        "- 当日七项硬区间（四餐累加后必须同时落在区间内，超一点都不行）："
        + "、".join(parts)
        + "\n"
        + "- 搭配要求：每餐「主食 + 优质蛋白 + 蔬菜」组合，全天菜品 ≥10 种；"
        + "不要堆大份肉菜或油炸菜凑热量，蛋白别超过每公斤体重 1.6 g\n"
    )


def build_plan_messages(profile_ctx: dict, days: int, note: str = "") -> list[dict]:
    """
    构造方案生成的消息列表
    profile_ctx 由路由层组装（见 routers/plans.py），包含档案+已算好的热量目标
    """
    system = f"""你是注册营养师和健康管理师。请根据用户档案生成 {days} 天的个性化膳食+运动方案，严格只输出一个 JSON 对象，不要任何解释或 markdown 代码块。

核心原则：
1.【偏好优先】在满足健康目标的前提下，优先使用用户喜爱的口味和感兴趣的运动
2.【禁忌绝对排除】饮食禁忌涉及的食材绝不允许出现在任何一餐中
3.【渐进式调整】第 1 天热量接近用户当前水平，逐日向热量目标靠近；运动强度从低到高逐日递增
4.【精简输出】每餐只列 1 道主菜，不要"套餐名"，不要重复描述

输出格式（严格照抄字段名）：
{{
  "summary": "一句话总述",
  "daily_target_calories": 每日热量目标整数,
  "days": [
    {{
      "day_index": 1,
      "theme": "当天主题",
      "meals": [
        {{"meal_type": "breakfast", "items": [{{"name": "菜名", "portion_g": 克数, "ingredients": ["食材1", "食材2"]}}], "calories": 本餐热量}},
        {{"meal_type": "lunch", "items": [{{"name": "菜名", "portion_g": 克数, "ingredients": ["食材1", "食材2"]}}], "calories": 本餐热量}},
        {{"meal_type": "dinner", "items": [{{"name": "菜名", "portion_g": 克数, "ingredients": ["食材1", "食材2"]}}], "calories": 本餐热量}},
        {{"meal_type": "snack", "items": [{{"name": "菜名", "portion_g": 克数, "ingredients": ["食材1", "食材2"]}}], "calories": 本餐热量}}
      ],
      "exercise": {{"type": "运动名", "duration_min": 分钟, "calories_burned": 消耗千卡, "tips": "一句话要领"}}
    }}
  ]
}}

要求：每天 4 餐、每餐 1 道主菜；每道菜的 ingredients 列 2~6 种主要食材（不含油盐酱醋等调味料）；tips 不超过 15 字；只输出 JSON。"""

    user = f"""【用户档案】
- 身高：{profile_ctx.get('height_cm', '未填')} cm，体重：{profile_ctx.get('weight_kg', '未填')} kg，BMI：{profile_ctx.get('bmi', '未填')}
- 年龄：{profile_ctx.get('age', '未填')}，性别：{profile_ctx.get('gender', '未填')}
- 活动水平：{ACTIVITY_LABEL.get(profile_ctx.get('activity_level', ''), profile_ctx.get('activity_level', '未填'))}
- 目标：{GOAL_LABEL.get(profile_ctx.get('goal', ''), profile_ctx.get('goal', '未填'))}
- 每日热量目标：约 {profile_ctx.get('daily_target_calories', 2000)} 千卡（已按科学公式算好，允许 ±10% 微调）
{_targets_text(profile_ctx)}- 口味偏好（优先满足）：{('、'.join(profile_ctx.get('taste_preferences', [])) or '无')}
- 饮食禁忌（必须绝对排除）：{('、'.join(profile_ctx.get('dietary_restrictions', [])) or '无')}
- 运动偏好（优先安排）：{('、'.join(profile_ctx.get('exercise_preferences', [])) or '无')}
- 用户补充要求：{note or '无'}

请生成 {days} 天方案，严格只输出 JSON。"""

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------- 逐天流式生成：只生成某一天 ----------

def _profile_text(profile_ctx: dict, note: str = "") -> str:
    return f"""【用户档案】
- 身高：{profile_ctx.get('height_cm', '未填')} cm，体重：{profile_ctx.get('weight_kg', '未填')} kg，BMI：{profile_ctx.get('bmi', '未填')}
- 年龄：{profile_ctx.get('age', '未填')}，性别：{profile_ctx.get('gender', '未填')}
- 活动水平：{ACTIVITY_LABEL.get(profile_ctx.get('activity_level', ''), profile_ctx.get('activity_level', '未填'))}
- 目标：{GOAL_LABEL.get(profile_ctx.get('goal', ''), profile_ctx.get('goal', '未填'))}
- 每日热量目标：约 {profile_ctx.get('daily_target_calories', 2000)} 千卡（允许 ±10% 微调）
{_targets_text(profile_ctx)}- 口味偏好（优先满足）：{('、'.join(profile_ctx.get('taste_preferences', [])) or '无')}
- 饮食禁忌（必须绝对排除）：{('、'.join(profile_ctx.get('dietary_restrictions', [])) or '无')}
- 运动偏好（优先安排）：{('、'.join(profile_ctx.get('exercise_preferences', [])) or '无')}
- 用户补充要求：{note or '无'}"""


def build_day_messages(profile_ctx: dict, day_index: int, days: int,
                       prior_days: list[dict] | None = None, note: str = "") -> list[dict]:
    """构造"只生成第 day_index 天"的消息列表（逐天流式生成用）"""
    prior_days = prior_days or []
    used = [it.get("name") for d in prior_days
            for m in (d.get("meals") or []) for it in (m.get("items") or []) if it.get("name")]
    used_txt = "、".join(used) or "无"

    system = f"""你是注册营养师。请只生成第 {day_index} 天（共 {days} 天）的膳食+运动方案，严格只输出一个 JSON 对象，不要任何解释或 markdown。

原则：
1.【偏好优先】优先用户喜爱的口味与运动
2.【禁忌绝对排除】禁忌食材绝不出现
3.【渐进式】第 1 天热量接近当前水平，逐日向目标靠近；运动强度逐日递增
4.【精简】每餐只 1 道主菜，不要"套餐名"
5.【不重复】尽量避开前面已用过的菜：{used_txt}
6.【食材】每道菜的 ingredients 列 2~6 种主要食材，不含油盐酱醋等调味料

输出格式（严格照抄字段名）：
{{
  "day_index": {day_index},
  "theme": "当天主题",
  "meals": [
    {{"meal_type": "breakfast", "items": [{{"name": "菜名", "portion_g": 克数, "ingredients": ["食材1", "食材2"]}}], "calories": 本餐热量}},
    {{"meal_type": "lunch", "items": [{{"name": "菜名", "portion_g": 克数, "ingredients": ["食材1", "食材2"]}}], "calories": 本餐热量}},
    {{"meal_type": "dinner", "items": [{{"name": "菜名", "portion_g": 克数, "ingredients": ["食材1", "食材2"]}}], "calories": 本餐热量}},
    {{"meal_type": "snack", "items": [{{"name": "菜名", "portion_g": 克数, "ingredients": ["食材1", "食材2"]}}], "calories": 本餐热量}}
  ],
  "exercise": {{"type": "运动名", "duration_min": 分钟, "calories_burned": 消耗千卡, "tips": "一句话"}}
}}
只输出 JSON。"""
    user = _profile_text(profile_ctx, note) + f"\n\n请只生成第 {day_index} 天，严格只输出 JSON。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------- 生成方案前的纯文字沟通 ----------

PLAN_CHAT_SYSTEM = """你是「识膳智行」的营养健康顾问，正在和用户沟通饮食与运动方案。

【回答顺序，必须遵守】
1. 先直接回答问题：优先用下面给的用户数据算出具体数字再回答，**不要开场就反问**
2. 答完才允许追问，一次最多问 1 个问题
3. 追问必须自带默认值，例如「我先按清淡、不吃辣、每周 3 次快走排，不合适你再改」
4. 只有缺关键信息（身高/体重/年龄）时才追问；缺几项就一次问全，别挤牙膏
5. 已经知道的信息不要重复问

【依据口径】回答营养问题时给一句依据，且按性质区分说法：
- 指南类（盐<5g、添加糖<25g、蛋白推荐量）：说「按《中国居民膳食指南 2022》」或「按《中国超重/肥胖医学营养治疗指南 2021》」
- 推算类（热量缺口、每周减重速度）：说「按 TDEE 和 7700 kcal/kg 推算」
- 经验取值（区间的浮动范围、运动消耗 kcal）：说「按常规取个保守值」，**不要**说「依据指南」

【风格】简洁中文，不超过 90 字；直接说结论和数字，不列长清单、不用 markdown 标题、不写「首先其次最后」。"""


def _chat_profile_block(profile_ctx: dict | None, targets: dict | None) -> str:
    """把档案与当前目标压成几行喂给模型（没有数据就不写，避免模型编）"""
    p = profile_ctx or {}
    t = targets or {}

    def val(key, unit="", default="未知"):
        raw = p.get(key)
        return f"{raw}{unit}" if raw not in (None, "", 0) else default

    bits = []
    if p:
        bits.append(
            f"身高 {val('height_cm', ' cm')} / 体重 {val('weight_kg', ' kg')} / "
            f"{val('age', ' 岁')} / "
            f"{ {'male': '男', 'female': '女'}.get(p.get('gender'), '性别未知')} / "
            f"{ACTIVITY_LABEL.get(p.get('activity_level') or 'moderate', '中度活动')} / "
            f"体脂 {val('body_fat_pct', '%')}"
        )
        goal = p.get("goal") or "maintain"
        bmi = p.get("bmi")
        bits.append(
            f"诉求：{GOAL_LABEL.get(goal, goal)}"
            + (f"（BMI {bmi}）" if bmi else "")
        )
        for label, key in (("口味偏好", "taste_preferences"), ("饮食禁忌", "dietary_restrictions"),
                           ("运动偏好", "exercise_preferences")):
            items = p.get(key) or []
            if items:
                bits.append(f"{label}：{'、'.join(str(i) for i in items)}")

    if t:
        rx = t.get("prescription") or {}
        if rx:
            bits.append(f"当前策略：{rx.get('summary') or ''}")
            if rx.get("warnings"):
                bits.append("注意事项：" + "；".join(rx["warnings"][:2]))
        pairs = [
            ("热量", t.get("calorie_min"), t.get("calorie_max"), "kcal"),
            ("蛋白", t.get("protein_min_g"), t.get("protein_max_g"), "g"),
            ("脂肪", t.get("fat_min_g"), t.get("fat_max_g"), "g"),
            ("碳水", t.get("carb_min_g"), t.get("carb_max_g"), "g"),
            ("饮水", t.get("water_min_ml"), t.get("water_max_ml"), "ml"),
        ]
        shown = [f"{label} {lo}~{hi} {unit}" for label, lo, hi, unit in pairs if lo and hi]
        if shown:
            bits.append("当前每日目标：" + "、".join(shown))
    return "\n".join(bits)


def build_chat_system(
    profile_ctx: dict | None = None,
    targets: dict | None = None,
    round_no: int = 0,
) -> str:
    """聊天系统提示：基础规则 + 用户档案 + 当前目标（有数据才注入，避免模型空口猜）"""
    block = _chat_profile_block(profile_ctx, targets)
    parts = [PLAN_CHAT_SYSTEM]
    if block:
        parts.append("【用户档案（已填的不要重复问）】\n" + block)
    else:
        parts.append("【用户档案】还没填身高/体重/年龄——这是唯一值得先问的信息，一次问全三项。")
    if round_no >= 2:
        parts.append("【本轮】已经聊了 2 轮以上：答完之后可以主动收口，问一句「要不要现在就按这些生成方案」。")
    return "\n\n".join(parts)


# ---------- 打卡 AI 个性化点评 ----------

def build_checkin_praise_messages(ctx: dict) -> list[dict]:
    """构造「打卡后个性化点评」消息：结合连续天数/运动消耗/当日完成度生成点评。

    ctx 由 checkins.py 组装，字段：
    - action_text: 本次动作描述，如「完成方案任务：午餐」「手动运动打卡：跑步 30 分钟」
    - streak:      连续打卡天数
    - done/total:  当日任务完成进度（任务打卡才传；手动打卡不传 total）
    - all_done:    当日是否全部完成
    - points:      本次获得积分
    - goal:        用户目标中文（减脂/维持体重/增肌），由路由层补充
    - today_burn:  今日运动总消耗（千卡），由路由层补充
    """
    system = (
        "你是「识膳智行」的专业健康教练，用户刚完成一次健康打卡。"
        "请根据数据输出 1~2 句话（合计 25~60 字）的个性化点评，结构如下：\n"
        "- 第一句：点出本次数据里最值得肯定的亮点或进步（结合连续天数/消耗/完成度，不许空泛夸奖）\n"
        "- 第二句：给一个马上能做的小建议或下阶段小目标（贴合用户的减脂/增肌目标，具体可执行）\n"
        "- 语气像懂行的朋友，自然、有温度；禁止「加油」「坚持就是胜利」这类套话；emoji 最多 1 个"
    )
    lines = [
        f"用户目标：{ctx.get('goal', '维持体重')}",
        f"刚完成：{ctx.get('action_text', '一次打卡')}",
        f"当前连续打卡 {ctx.get('streak', 0)} 天",
        f"今日运动已消耗 {ctx.get('today_burn', 0)} 千卡",
        f"本次获得 {ctx.get('points', 0)} 积分",
    ]
    if ctx.get("total"):
        lines.append(
            f"当日已完成 {ctx.get('done', 0)}/{ctx.get('total')} 项任务"
            f"{'（当日任务已全部完成）' if ctx.get('all_done') else ''}"
        )
    lines.append("请输出点评（1~2 句，25~60 字）。")
    return [{"role": "system", "content": system},
            {"role": "user", "content": "\n".join(lines)}]


# ---------- 体检报告识别 ----------

# 目录里的 18 项，写进 prompt 让模型知道该找什么（但 name 仍要求照抄报告原文，
# 映射交回后端别名表做，避免模型把「丙氨酸氨基转移酶」硬猜成某个 key）
CHECKUP_ITEM_HINTS = (
    "身高、体重、BMI、体脂率、基础代谢、腰围；空腹血糖、糖化血红蛋白、尿酸、"
    "总胆固醇、甘油三酯、低密度脂蛋白、高密度脂蛋白；谷丙转氨酶、谷草转氨酶、"
    "肌酐、尿素氮；血红蛋白、促甲状腺激素"
)


def build_checkup_prompt(report_text: str = "") -> str:
    """体检报告抽取提示词。

    接的是化验单/体检单，字小、项目名带英文缩写、单位五花八门，所以强调三件事：
    照抄原文、不补全、看不清就跳过。换算与校验一律在后端做，不让模型算。
    """
    source = "下面是从 PDF 里抽出的报告文字：" if report_text else "图片是一份体检/化验报告。"
    body = f"\n{report_text}\n" if report_text else ""
    return f"""你是体检报告结构化助手。{source}{body}
请把报告上能看清的检查项逐项抽出来，严格只输出一个 JSON 对象，不要解释、不要 markdown 代码块。

关注这些项目（报告里常见，但不是每个都有）：
{CHECKUP_ITEM_HINTS}

规则：
1. 只抽报告上真实出现的项和你真实看清的数值，绝对不要推算、补全或编造。
2. name 照抄报告原文，包括括号里的英文缩写（如「谷丙转氨酶(ALT)」「GLU」），不要改写成别的名字。
3. value 只保留数字：不带单位、箭头、星号、正负号；写成「<0.5」时填 0.5；写成「5.2 ↑」时填 5.2。
4. unit 照抄报告上的单位（mmol/L、μmol/L、U/L、g/L、g/dL、%、mg/dL 等）。
5. 只有项目名没有数值、或被遮挡看不清的，直接跳过这一项，不要猜。
6. report_date 用 YYYY-MM-DD；hospital 是体检机构或医院名称。看不出来就留空字符串。
7. 如果这张图明显不是体检报告（自拍、食物、风景等），is_report 填 false、items 给空数组。
8. 最多输出 20 项，优先上面列举的项目（输出过长会被截断，反而丢数据）。

输出格式：
{{
  "is_report": true,
  "report_date": "2026-09-08",
  "hospital": "XX 市第一人民医院",
  "items": [
    {{"name": "谷丙转氨酶(ALT)", "value": 32, "unit": "U/L"}},
    {{"name": "空腹血糖", "value": 5.4, "unit": "mmol/L"}}
  ]
}}"""
