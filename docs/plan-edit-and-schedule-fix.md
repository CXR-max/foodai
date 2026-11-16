# 计划模块修改方案：菜品可编辑 + 导入日程可打卡 + 全模块贯通

> 面向本次三个反馈：
> 1. 修改计划时**不能改菜品**，且改完不会重算相关方案与数据；
> 2. 「导入日程」后**没连上、打不了卡**；
> 3. 计划的数据要**全部**给到日程/运动/我的，日程要方便打卡，日历要保留并看到每天的简约打卡计划。

---

## 一、问题定位（有据可查，不是猜的）

### 1. 菜品改不了：不是后端不给改，是前端没入口

| 层 | 现状 |
| --- | --- |
| 后端 `PUT /plans/{plan_id}/days/{day_index}` | 已支持 `meals`，且会按营养库重算每餐热量 |
| 前端 `MobilePlanDetail.vue` 编辑抽屉 | 只有「运动处方 / 睡眠 / 饮水」三块，**没有任何餐食编辑 UI** |
| 重算口径 | 只重算热量；蛋白/脂肪/碳水不算、不比对目标、不提示超限 |
| 未收录菜品 | `recalc_meal_calories` 只累加"匹配到营养库"的菜，同餐里没匹配的菜按 0 计 → 少算 |
| 结构变化 | 加/删一餐后，已导入日程的任务不会增删（`desired_tasks` 只在保存时同步，但前端根本改不了餐，所以从没触发过） |

### 2. 导入日程打不了卡：方案日期落在过去，任务建在了过去

数据库实证（当前库）：

```
diet_plans:  id=33 user=13 「7天方案（沿用）10-06」 start=2026-10-06 status=active
plan_days:   190~196 → 2026-10-06 ~ 2026-10-12
check_ins:   user=13 的任务全部落在 10-10 ~ 10-12（pending 一直没完成）
今天:        2026-11-16
```

也就是说：**导入成功了，任务也建出来了，但建在了 10 月**。用户今天打开「日程」，当天没有任何任务 → 表现为"没连上、打卡不了"。

根因两处：

- `plans.py::reuse_plan` 里 `start = max(原方案结束日) + 1 天`。历史方案是 9/10 月的，沿用出来就整天落在过去；
- `import` 从不校验日期是否已过期，过期方案照样按原日期写任务。

附带隐患：同一用户存在**多份 `status=active` 且日期重叠**的方案（9 月、10 月、11 月各一份），
`day_context.active_plan_day` 按 `created_at desc` 取第一条，谁最新谁生效，容易和用户在看的方案对不上。

### 3. 数据没贯通：日程接了，运动和我的没接全

| 模块 | 计划数据现状 |
| --- | --- |
| 日程 | 已接：目标条（热量/运动/饮水/睡眠）+ 四餐/运动/睡眠/饮水任务 |
| 运动 | **完全没接**（全文件无 `plan` 引用），只有手动记一次运动；「实时记录」还是占位 |
| 我的 | 只读了 `/day/summary` 的部分字段；「健康三环」是"待接入"占位 |
| 日历 | 只有 绿点(有方案) / 完成数，看不到"这天要做什么" |

---

## 二、方案总览（4 个改动包，按依赖顺序）

```
P0 日期对齐 ──┐
              ├─→ P1 菜品可编辑 + 联动重算 ──→ P3 全模块贯通（运动/我的）
P2 日程打卡体验 ┘（日历保留 + 每日简约计划 + 快捷打卡）
```

---

## 三、P0：让"导入"真的落到今天（先修打卡）

### 后端

1. `POST /plans/{plan_id}/import` 增加参数 `anchor`（默认 `today`）：
   - 计算 `shift = anchor - 方案第 1 天日期`；
   - `shift > 0` → 整份方案 `plan_days.date` 平移 `shift` 天，`diet_plans.start_date` 同步；
   - 返回新增字段 `shifted_days`、`from_date`、`to_date`，前端提示：
     `方案原本是 10-06 起，已顺延到 11-16 起（7 天）`。
2. 修复 `reuse_plan`：`start = max(原方案结束日 + 1, 今天)`，保证沿用出来的一定从今天或之后开始。
3. 冲突收敛：生成 / 沿用 / 导入时，把**日期区间与本次重叠的其它 `active` 方案置为 `archived`**（返回里带 `archived_plans` 数量），保证"当天生效的方案"唯一。
4. `active_plan_day` 保持"最新创建优先"，但补一条：同一天有多个时优先取**有打卡任务关联**的那份。

### 前端

- 计划详情顶部：若方案已过期，显示黄条 `本方案日期已过（10-06 起），导入时会自动顺延到今天`，按钮文案变「顺延并导入这周」。
- 日程页空态：`今天还没有任务` 下面加两个按钮：`去计划页`、`有 N 天计划未导入 → 一键顺延导入`（调 import 接口）。

### 验收

- 用 `cxr` 的 10 月旧方案导入 → 任务全部落到 11-16 起，日程当天可见、可打卡；
- 沿用 9 月方案 → 新方案 `start_date = 今天`；
- 生成新方案后，旧的重叠 active 方案变 archived。

---

## 四、P1：菜品可编辑 + 改完重算

### 4.1 后端：新增 `services/meal_recalc.py`

```
recalc_day_nutrition(meals) -> {
  meals: [{meal_type, calories, protein_g, fat_g, carb_g, matched, unmatched[]}],
  total: {calories, protein_g, fat_g, carb_g},
  coverage: {matched_items, total_items}
}
```

规则：
- 每道菜按 `foods_data.find_food(name)` 取 `per_100g` × 克重；**未收录的菜**用 `foods_data.category_average(同类)` 兜底估值（不再当 0），并在 `unmatched` 里列出菜名 → 前端标灰提示"估的"。
- 同餐热量 = 各菜之和；当日热量 = 各餐之和。
- 与 `day.targets` 比对，产出 `status: low | ok | high` 与 `suggestions`：
  - 超上限 >10%：`按比例缩小份量（主食优先）` / `加 X 分钟快走抵消 N kcal`；
  - 低于下限 >15%：`加一份加餐（推荐 200~250 kcal）`。

接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/plans/{id}/days/{idx}/preview` | 改菜**试算**（不落库），返回上面的结构，前端边改边显示 |
| `PUT` | `/plans/{id}/days/{idx}` | 保存；`meals` 变更后自动跑 `meal_recalc`，写回 `estimated_calories`、`targets.nutrition_snapshot`、`targets.calorie_source='manual_edit'`，返回 `warnings[]` |
| `PUT` | 同上 + `rebalance` | `rebalance=portion`：按目标热量等比缩放非主食克重；`rebalance=exercise`：把超出热量折算成分钟数追加到当日运动处方（MET 反算） |
| `GET` | `/foods/alternatives?name=&meal_type=` | "换一道"候选：同餐段、热量 ±20%、返回 5 个（菜名/克重/热量/蛋白） |

同步逻辑补强：
- `sync_plan_day_checkins` 已能按 `desired_tasks` 增删 pending 任务 → 加/删餐次后，日程任务自动跟着变（保留已打卡的）；
- 餐名变了不需要动任务（日程任务内容是实时读 `plan_day.meals` 的），已具备。

### 4.2 前端 `MobilePlanDetail.vue` 编辑抽屉改造

抽屉改名为「编辑这一天」，分三块：

1. **餐食**（新增，默认展开）
   - 每餐一张卡：`🌅 早餐  520 kcal`；
   - 菜品行：`菜名（可输入，带营养库联想）` + `克重 -/+ ` + 单菜热量 + `换 / 删`；
   - 卡片底部：`+ 加一道菜`；餐卡右上：`整餐换一套`（调 alternatives 批量替换）；
   - 可 `+ 加一餐（加餐）` / 删整餐。
2. **运动处方**（沿用现有：类型/时长/强度，增删项）。
3. **睡眠 + 饮水**（沿用现有）。

抽屉底部固定**实时对比条**（调 preview 接口防抖 400ms）：

```
热量  1830 / 1800~2100 kcal   蛋白 62 / 90 g
⚠ 超出上限 30 kcal → [缩小份量] [加 25 分钟快走]
```

保存时若 `status=high/low`，弹二次确认三选一：`按比例调整` / `加运动抵消` / `就这样保存`。

### 4.3 验收

- 改菜名/克重 → 单菜、单餐、当日热量即时变；
- 未收录菜（如"妈妈牌红烧肉"）不再算 0，标"估算"；
- 删掉晚餐 → 该天已导入的 `meal_dinner` pending 任务被删，已打卡的保留；
- 改完当天，日程页餐食文案跟着变（同一份数据，实时读）。

---

## 五、P2：日程日历保留 + 每日简约打卡计划 + 快捷打卡

### 接口

`GET /check-ins?month=YYYY-MM` 每天的 `DayStat` 增加：

```json
{
  "date": "2026-11-16",
  "total": 7, "done": 3, "pct": 43,
  "has_plan": true, "day_index": 1,
  "tasks": [
    {"key": "meal_breakfast", "label": "早餐", "status": "done", "short": "燕麦粥+鸡蛋"},
    {"key": "exercise", "label": "运动", "status": "pending", "short": "快走 35 分钟"}
  ]
}
```

### 前端日程页

1. **日历保留**（不删），格子信息升级：
   - 上排日期，下排 3 个微型点（早/午/晚 完成态）+ 右侧完成数 `3/7`；
   - 颜色：灰=无计划、浅绿=有计划未做、黄=部分、绿=全完成。
2. 点某天不直接跳走，而是在日历下方展开**「当日简约打卡计划」卡片**：
   - 一行 chips：`早餐✓ 午餐✓ 晚餐○ 加餐○ 运动○ 饮水○ 睡眠✓`，点 chip 直接打卡（睡眠→记睡眠、饮水→加水，其它走 `complete`）；
   - 右上角 `查看详情` → 相当于选中该天（现在的行为）。
3. 今日任务区顶部加一条**进度环/进度条 + 一键打卡未完成餐食**（可选，只对四餐生效，睡眠饮水仍走各自流程）。

---

## 六、P3：计划数据贯通到运动 / 我的

| 计划提供 | 日程 | 运动 | 我的 |
| --- | --- | --- | --- |
| 热量区间 / 蛋白下限 | ✅ 已有 | — | 三环「饮食」 |
| 四餐菜单 | ✅ 已有 | — | 今日餐单摘要 |
| 运动处方（类型/时长/预计消耗） | ✅ 任务 | 🆕 今日处方卡 + 一键打卡 | 三环「运动」 |
| 运动消耗下限 | ✅ 目标条 | 🆕 `已完成 X / ≥Y kcal` | 三环「运动」 |
| 睡眠方案（作息/时长） | ✅ 任务 | — | 三环「睡眠」+ 建议 |
| 饮水下限 | ✅ 任务 | — | 三环「饮水」 |
| 第几天/共几天 | ✅ 月历 | 🆕 页头 | 🆕 计划进度 |

具体：

- **运动页**：顶部加「今日运动处方」（读 `/plans/active` 的 `day.exercises`）：
  显示 `快走 35 分钟 · 预计 210 kcal`、`已完成 0 / ≥180 kcal`，按钮`按处方打卡`（复用 `complete`；当日无任务则建 manual checkin 并挂 `plan_day_id`）。没有方案时退回现在的"记一次运动"。
- **我的页**：去掉「健康三环 待接入」，用 `targets + day.summary` 画三环（饮食/运动/睡眠），并在下面加一行 `当前第 3/7 天 · 本周计划完成 12/21 项`。

---

## 七、数据结构变动

| 表 | 变动 | 是否需迁移 |
| --- | --- | --- |
| `plan_days` | `targets` JSON 内新增 `nutrition_snapshot`（当日实算营养） | 否（JSON） |
| `plan_days` | 导入时顺延会改 `date`；原起始日记入 `diet_plans.basis_snapshot.original_start_date` | 否 |
| `diet_plans` | 旧 active 冲突时置 `status='archived'` | 否 |
| `plan_meals`（无此表，JSON 存储） | 菜品项新增可选 `calories`（保存用户手填/库值，减少重算误差） | 否 |

**无需 Alembic 迁移**，只加字段、不改表结构。

---

## 八、测试

后端 pytest（新增 `tests/test_plan_edit_schedule.py`）：

1. 旧方案（日期已过）导入 → 任务落在今天，`shifted_days=7`；
2. 沿用 9 月方案 → `start_date == today`；
3. 改菜名 → 热量按库重算；未收录菜不按 0 计；
4. 删晚餐 → 已导入的 `meal_dinner` pending 任务消失，done 任务保留；
5. 超上限保存 → `warnings` 非空，`rebalance=portion` 后总量落回区间；
6. 月历接口返回 `tasks[]` 且顺序为 早/午/晚/加/运动/水/睡。

前端：手机视口 Playwright 跑 8 张截图 + 控制台零报错（沿用 `scripts/mobile_shots.py`），新增"编辑菜品"和"日历展开"两张。

---

## 九、工作量与顺序

| 包 | 内容 | 预估 |
| --- | --- | --- |
| P0 | 日期顺延 + 沿用修复 + 冲突归档 + 空态入口 | 半天 |
| P1 | meal_recalc + preview/alternatives 接口 + 抽屉餐食编辑 | 1.5 天 |
| P2 | 月历 tasks 扩展 + 简约计划卡 + 快捷打卡 | 1 天 |
| P3 | 运动处方卡 + 我的三环 | 1 天 |

建议先落 P0（当天就能打卡），再做 P1（用户最在意的"改菜"）。

---

## 十、需要你拍板的三个点

1. **旧 active 方案自动归档**：会从"我的计划"里移到已归档，可接受吗？（替代方案：保留 active，只在日程里加"当前方案"选择器）
2. **导入时顺延**：旧方案直接改 `plan_day.date`（历史记录的日期会变），还是保留原日期、只在日程侧映射？（推荐前者，实现简单且各页一致）
3. **超限处理默认档**：默认"按比例缩小份量"，还是默认"加运动抵消"？

---

## 十一、实施记录（已落地）

### 已确定的三个口径

1. **导入即生效**：把某份方案导入日程（单天 / 整周 / 从历史方案列表导入）→ 该方案 `status=active`（进行中），
   其余进行中的方案自动 `status=archived`（历史方案）。历史方案仍在「我的计划 → 历史方案」里，可 **查看 / 沿用 / 导入日程**。
2. **过期就顺延**：导入时若方案第 1 天早于今天，整份 `plan_days.date` 平移 `today - 第1天` 天，`start_date` 同步，
   原起始日记入 `basis_snapshot.original_start_date`。接口返回 `shift` 明细，前端提示"已顺延 15 天到 2026-11-16 起"。
3. **超限不偷偷改**：改菜后若超上限，保存时弹窗三选一 —— 按比例缩份量 / 加运动抵消（显示 +N 分钟）/ 就这样保存。
   默认不做任何自动改写。

### 改动的文件

| 层 | 文件 | 内容 |
| --- | --- | --- |
| 服务 | `services/plan_lifecycle.py`（新） | `activate_plan`、`shift_to_anchor`、`rebalance_by_exercise`、`suggest_exercise` |
| 服务 | `services/meal_recalc.py`（新） | 每菜/每餐/当日营养重算、目标区间评估、缩份量、当日营养快照 |
| 服务 | `services/day_context.py` | `active_plan_day` 优先取 `status=active` |
| 路由 | `routers/plans.py` | `import` 加 `anchor` + 顺延 + 激活；`reuse` 起始日夹到今天；`generate`/`-stream` 激活；新增 `POST /{id}/days/{i}/preview`；PUT 支持 `rebalance` 并返回 `nutrition`/`warnings` |
| 路由 | `routers/foods.py` | 新增 `GET /foods/alternatives`（换一道候选） |
| 路由 | `routers/checkins.py` | 月历每日返回 `tasks[]`（key/label/status/short），按 早→午→晚→加→运动→水→睡 排序 |
| 模型 | `schemas/plan.py` | `PlanDayUpdateIn.rebalance`、`PlanDayPreviewIn`、`PlanImportIn.anchor`、菜品项 `category` |
| 前端 | `api/plan.ts`、`api/checkin.ts`、`api/food.ts` | 新类型与接口 |
| 前端 | `MobilePlanDetail.vue` | 编辑抽屉重做（餐食/运动/睡眠饮水三块 + 实时营养对比条 + 换一道 + 超限弹窗 + 过期提示） |
| 前端 | `MobilePlanView.vue` | 进行中 / 历史方案分组，历史卡带 查看 / 沿用 / 导入日程 |
| 前端 | `MobileScheduleView.vue` | 日历完成点、当日简约计划 chips（可直接打卡）、空态一键导入 |
| 前端 | `MobileSportView.vue` | 今日运动处方 + 按处方打卡 + 消耗进度 |
| 前端 | `MobileMeView.vue` | 健康三环接真实目标（饮食/运动/睡眠/饮水） |

**不需要 Alembic 迁移**：只用了 JSON 字段（`targets.nutrition_snapshot`、`basis_snapshot.original_start_date`）与既有 `status` 取值。

### 验证结果

- `pytest tests`：**70 passed**。新增 `tests/test_plan_edit_lifecycle.py` 11 例，覆盖：过期方案导入顺延、导入激活并归档旧方案、
  历史方案仍可查看、沿用起始日不落回过去、改菜重算、试算不落库、未收录菜估算非 0、缩份量回到区间、加运动追加时长、
  删餐清理 pending 任务、月历 chips 顺序、换菜候选。
- `npm run build` 通过。
- `scripts/verify_plan_edit.py`：手机视口 9 张截图，零 JS 报错。实测改菜抽屉「热量 3117 / 2000~2340」红色超限提示、
  换一道 4 个候选、日历完成点与简约计划 chips、运动处方、三环均正常。
- `scripts/verify_history_import.py`：造一份 15 天前的历史方案 + 一份进行中方案，点「导入日程」→
  历史方案变进行中、原方案归档、日期顺延到今天、当天 6 条任务可打卡（实测 +3 分）。
- 截图目录：`.tmp_shots/edit/`、`.tmp_shots/history/`。

## 十二、补充：历史方案「已重新启用」提示（按用户确认）

用户确认要提示，所以导入历史方案后，列表与详情都留痕迹，避免"同一份方案怎么又出现在进行中"的困惑。

### 后端

- `plan_lifecycle.activate_plan`：进入时若方案原状态是 `archived`，在 `basis_snapshot` 写
  `reactivated_at`（ISO 时间）与 `reactivate_count`（累加），返回 `reactivated / reactivated_at`。
  新生成 / 本来就 active 的方案不会被标记（避免误报）。
- `plan_dict` 输出 `reactivated_at`、`reactivate_count`、`original_start_date`（顺延前最初起始日）。
- 导入接口 message 前加一句「该方案已重新启用为进行中；」，toast 直接可见。

### 前端

- 计划页「进行中」卡片：浅蓝提示条 `↻ 已重新启用 · 11-16 17:07`，多次启用时右侧显示「第 N 次」。
- 计划页「历史方案」卡片：日期行加蓝色小标签 `曾重新启用`（被再次归档后痕迹不丢）。
- 计划详情 hero：同样的提示条，并补一句「原 2026-11-01 起，已排到今天」。

### 测试

- `tests/test_plan_edit_lifecycle.py` 新增 2 例（共 72 passed）：
  归档方案导入 → `reactivated=True`、message 含「已重新启用」、`reactivate_count=1`；
  被新方案顶掉归档后 `reactivated_at` 仍在；新方案不会被误标。
- `scripts/verify_history_import.py` 增加 `05_reactivated.png` 与提示条断言；
  新增 `scripts/verify_reactivate_badge.py` 核对「曾重新启用」标签（`.tmp_shots/history/06_history_reactivated_flag.png`）。

## 十三、超限处理偏好（按用户确认落地）

用户要「弹窗提示超限的同时，在目标里加一个开关：手动选择 / 缩份量 / 加运动，按偏好生效」。
所以弹窗保留、不自动执行，只把默认选中项交给用户偏好决定。

### 数据

- `health_profiles` 新增 `over_limit_policy`（String(16)，默认 `ask`）：`ask` / `portion` / `exercise`。
- 迁移 `b8e4d17c9a52_add_over_limit_policy`（revises `f4a1c7b30d92`）。
- `GET/PUT /api/profile` 返回与保存该字段；非法值 422。
- 存档案而非方案：一次设置，所有方案通用。

### 前端（`MobilePlanDetail.vue`）

- 「每日目标」下方新增 **超限处理** 卡片：`手动选择 / 缩份量 / 加运动` 三段按钮，
  改完即时写档案，并按档位给一句人话说明（如「以后超限默认加运动抵消，弹窗里仍可临时改」）。
- 超限弹窗改为**单选卡片**（原来三个按钮）：
  - 打开时按偏好预选（`ask` → 默认「就这样保存」，`portion` / `exercise` → 对应项）；
  - 每项带副标题：缩份量「让总量回到区间」、加运动「追加 N 分钟跑步」、保存「只记录，不改餐也不加运动」；
  - 底部提示「默认选中「X」，可在下方「每日目标 → 超限处理」修改」；
  - 主按钮「按此处理」按当前选择执行，仍可临时改别的档；「继续改」不回写。

### 验证

- `pytest tests`：**73 passed**（+1：偏好保存/读取、只改偏好不动身体数据、非法值 422）。
- `npm run build` 通过。
- `scripts/verify_over_limit_policy.py`（`.tmp_shots/policy/`）：
  开关默认「手动选择」→ 切「加运动」→ 改菜超限 → 弹窗默认选中「加运动抵消（追加 60 分钟跑步）」→
  临时改「就这样保存」保存成功；脚本结束自动把偏好复位为 `ask`。

## 十四、方案执行评价（停用后留进历史方案）

用户要「每次生成的方案，按用户的更改和打卡完成情况给评价，方案停止使用后加在历史方案里」。

### 触发时机

- 方案被归档（被新方案顶掉 / 手动归档）时生成最终评价，写进 `diet_plans.review`。
- `GET /plans/{id}/review`：历史方案返回已存评价；进行中只算进度（`final=false`）；归档但没算过的补算并落库。
- 进行中不显示分数等级（避免第一天就 D 分），只给「完成 X/Y 项 · 连续 N 天全清」；
  分项得分与评语只在停用后出现。

### 打分口径（100 分，每项都能解释）

| 维度 | 分值 | 怎么算 |
| --- | --- | --- |
| 打卡完成 | 40 | 由方案导入的任务里 done/total |
| 目标达成 | 35 | 热量落区间 / 运动打卡 / 睡眠≥下限-20min / 饮水≥目标90%，四项达标率加权（45/20/20/15，无数据维度不计） |
| 连续坚持 | 15 | 最长「当天任务全清」连续天数 ÷ 有任务天数 |
| 调整质量 | 10 | 改过菜的天里，改完 `nutrition_snapshot.status=ok` 的比例；一次没改记 8 分 |

- 等级：S≥90 / A≥80 / B≥70 / C≥60 / D<60，配一句 headline。
- 完全没有打卡也没有饮食/睡眠/饮水记录 → `status=not_started`，不打分，只说明「这一期没有执行记录」。

### 数据与接口

- `diet_plans.review`（JSON）、`plan_days.edit_count / last_edited_at`（改过几次，给「调整质量」用），
  迁移 `c2f9a8b41d63`。
- `plan_dict` 列表返回 `review_score / review_grade / review_headline / review_status / review_task_rate`，
  详情额外带完整 `review`。
- `plan_lifecycle.activate_plan` 归档旧方案时自动 `store_review`（评价失败不影响导入）。

### 顺带修掉的一个数据 bug

方案顺延时只改了 `plan_days.date`，`check_ins.check_date` 没跟着走，导致旧日期上留一批 pending「幽灵任务」，
重复计入完成率（实测某方案 18 项被算成 35 项）。修法：
- `shift_to_anchor` 同步平移 pending 任务的 `check_date`（done 的不动，保留真实完成日）；
- `schedule_import` 导入时清掉同方案天、日期不符的 pending；
- `plan_review` 只统计 `check_date` 落在方案日期内的任务。
- 顺手清理了库里 54 条历史幽灵任务。

### 前端

- 计划页：历史卡片加评分条（等级徽章 + 分数 + headline）；进行中卡片加蓝色「执行中 X/Y 项 · 连续 N 天全清」。
- 详情页：hero 下方新增「本期评价」；停用后显示大分数 + 四维进度条 + 「做得好的 / 下一期建议」；
  进行中显示进度卡和一句「停用后给最终评价」。
- `utils/planFeedback.ts`：导入历史方案成功后弹浅蓝「↻ 已重新启用」说明框（顺延几天、归档几份），
  不再只靠 toast + 卡片小字。

### 验证

- `pytest tests`：**81 passed**（新增 `tests/test_plan_review.py` 8 例：执行到位高分、半途而废低分、
  无记录 not_started、改菜超区间扣分、归档生成评价、被顶掉也留评价、进行中只给进度、顺延不留幽灵任务）。
- `npm run build` 通过。
- `scripts/verify_plan_review.py`：造两份历史方案（一份 62 分 C、一份 23 分 D）→ 卡片显示评分 →
  详情评价板块 → 导入历史方案弹出蓝色「已重新启用」→ 旧方案归档后带评价。截图 `.tmp_shots/review/`。
- 新增小工具 `scripts/shot_mobile_page.py`（手机视口截任一页面，核对布局用）。

### 补充：重新启用后旧评价的处理

- 重新启用 = 新一期：`activate_plan` 把上一期的 `review` 挪到 `basis_snapshot.last_review` 留档，
  并清空 `diet_plans.review`，列表不再挂旧分数（否则「进行中」卡片会显示上一期的 62 分）。
- 测试补 1 例（共 82 passed）：归档出评价 → 重新导入 → 卡片无 `review_score`，但 `last_review` 在。
