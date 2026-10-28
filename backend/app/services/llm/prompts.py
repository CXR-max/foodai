"""
Prompt 模板（想调教模型效果就改这里的文字）

两个 prompt 的关键设计：
1. 识别：只要"菜名+份量档位+克重"，不要模型算营养（营养由本地库算，口径统一、免费小模型也够用）
2. 方案：偏好优先 + 禁忌绝对排除 + 渐进式（day1 接近现状，逐日逼近目标）
"""
from app.services.llm import foods_data

# ---------- 食物识别 ----------


def build_food_recognition_prompt() -> str:
    """构造识别提示词：注入本地营养库菜名+标准份量，引导模型输出可精确匹配的菜名"""
    catalog = "、".join(
        f'{f["name"]}(标准{f["common_portion_g"]}g)' for f in foods_data.load_foods()
    )
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

# 运动每分钟消耗估算表（按 60kg 成年人）——mock 和换算共用
EXERCISE_METABOLIC = {
    "跑步": 10, "跳绳": 11, "游泳": 9, "骑行": 7,
    "力量训练": 6, "球类": 7, "快走": 5, "瑜伽": 3,
}

GOAL_LABEL = {"lose": "减脂", "maintain": "维持体重", "gain": "增肌"}
ACTIVITY_LABEL = {
    "sedentary": "久坐少动", "light": "轻度活动", "moderate": "中度活动",
    "active": "高度活动", "athlete": "运动员级",
}


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
        {{"meal_type": "breakfast", "items": [{{"name": "菜名", "portion_g": 克数}}], "calories": 本餐热量}},
        {{"meal_type": "lunch", "items": [{{"name": "菜名", "portion_g": 克数}}], "calories": 本餐热量}},
        {{"meal_type": "dinner", "items": [{{"name": "菜名", "portion_g": 克数}}], "calories": 本餐热量}},
        {{"meal_type": "snack", "items": [{{"name": "菜名", "portion_g": 克数}}], "calories": 本餐热量}}
      ],
      "exercise": {{"type": "运动名", "duration_min": 分钟, "calories_burned": 消耗千卡, "tips": "一句话要领"}}
    }}
  ]
}}

要求：每天 4 餐、每餐 1 道主菜；tips 不超过 15 字；只输出 JSON。"""

    user = f"""【用户档案】
- 身高：{profile_ctx.get('height_cm', '未填')} cm，体重：{profile_ctx.get('weight_kg', '未填')} kg，BMI：{profile_ctx.get('bmi', '未填')}
- 年龄：{profile_ctx.get('age', '未填')}，性别：{profile_ctx.get('gender', '未填')}
- 活动水平：{ACTIVITY_LABEL.get(profile_ctx.get('activity_level', ''), profile_ctx.get('activity_level', '未填'))}
- 目标：{GOAL_LABEL.get(profile_ctx.get('goal', ''), profile_ctx.get('goal', '未填'))}
- 每日热量目标：约 {profile_ctx.get('daily_target_calories', 2000)} 千卡（已按科学公式算好，允许 ±10% 微调）
- 口味偏好（优先满足）：{('、'.join(profile_ctx.get('taste_preferences', [])) or '无')}
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
- 口味偏好（优先满足）：{('、'.join(profile_ctx.get('taste_preferences', [])) or '无')}
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

输出格式（严格照抄字段名）：
{{
  "day_index": {day_index},
  "theme": "当天主题",
  "meals": [
    {{"meal_type": "breakfast", "items": [{{"name": "菜名", "portion_g": 克数}}], "calories": 本餐热量}},
    {{"meal_type": "lunch", "items": [{{"name": "菜名", "portion_g": 克数}}], "calories": 本餐热量}},
    {{"meal_type": "dinner", "items": [{{"name": "菜名", "portion_g": 克数}}], "calories": 本餐热量}},
    {{"meal_type": "snack", "items": [{{"name": "菜名", "portion_g": 克数}}], "calories": 本餐热量}}
  ],
  "exercise": {{"type": "运动名", "duration_min": 分钟, "calories_burned": 消耗千卡, "tips": "一句话"}}
}}
只输出 JSON。"""
    user = _profile_text(profile_ctx, note) + f"\n\n请只生成第 {day_index} 天，严格只输出 JSON。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------- 生成方案前的纯文字沟通 ----------

PLAN_CHAT_SYSTEM = """你是"识膳智行"的营养健康顾问，正在和用户聊天，了解他想要什么样的饮食+运动方案。

要求：
- 用简洁友好的中文像正常聊天一样回复，不要输出 JSON
- 【务必简短】每次回复不超过 80 字，直接给建议或问 1 个关键问题，不要列长清单、不要长篇大论
- 围绕口味偏好、饮食禁忌、运动兴趣、作息/就餐条件来了解需求
- 用户已经说清楚的信息不要重复追问"""


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
