# 营养库数据源与导入说明

## 目录约定

外部原始数据放在项目根目录的 `data_sources/`，该目录已被 `.gitignore` 排除，不要提交到公开仓库。

```text
data_sources/
  usda_fndds_2021_2023/
  tfda/
  cofid/
  mext/
  kfind/
  cfs/
  cdc/
```

## 源与许可

| 源 | 用途 | 许可与注意 |
|---|---|---|
| USDA FNDDS 2021-2023 | 西式/混合菜、份量克重、表结构参考 | 美国政府作品，通常视为公共领域；保留引用 |
| 台湾 TFDA | 中式食材、俗名、废弃率 | 政府资料开放授权，需署名；可商用 |
| 英国 CoFID | 西式 composite dishes | OGL v3，需署名；可商用 |
| 日本 MEXT | 日本食材/加工食品 | 免费，需署名；再分发前核对条款 |
| 韩国 K-FIND | 韩餐菜品、份量参考 | 公共数据，需署名；商用/再分发前核对条款 |
| 香港 CFS | 港式/中式菜品校准 | 仅非商业；不要提交原始数据到公开仓库；商用需授权 |
| 中国 CDC | 大陆食材参考/校验 | 无批量下载；不要爬取；商用需许可 |

个人、非商业、公开部署时：代码可以开源；CFS/CDC 原始数据只放本地，不打包进仓库。公开展示时保留来源与署名。商用需逐一取得授权。以上是工程判断，不是法律意见。

## 当前状态

- 内置种子：108 条 active（八大菜系 + 家常菜 + 轻食 + 单个食物/饮品）。
- 台湾 TFDA：2,213 条 + 5,262 条别名已导入 staging（`active=false`）。
- 英国 CoFID：2,853 条已导入 staging（`active=false`）。
- 外部源原始文件在本地 `data_sources/`，不提交公开仓库。

## 当前需要你做什么

| 源 | 是否需要你操作 | 你要做什么 | 放到哪里 |
|---|---|---|---|
| TFDA | 不需要 | 已下载并导入 | 已完成 |
| CoFID | 不需要 | 已下载并导入 | 已完成 |
| USDA FNDDS | 可选 | 下载 Excel；我也可以代下 | `data_sources/usda_fndds_2021_2023/` |
| 日本 MEXT | 可选 | 下载 Excel；我也可以代下 | `data_sources/mext/` |
| 韩国 K-FIND | 需要 | 注册 data.go.kr、申请 API key；或下载 CSV/XLSX | key 放 `backend/.env`；文件放 `data_sources/kfind/` |
| 香港 CFS | 需要 | 手动下载/导出 Excel 或按模板整理 CSV | `data_sources/cfs/` |
| 中国 CDC | 需要 | 手工按模板整理 CSV；不要爬虫 | `data_sources/cdc/` |

## 1. 韩国 K-FIND

1. 打开 https://www.data.go.kr/data/15100070/standard.do
2. 注册并登录 data.go.kr。
3. 在数据集页面点 `활용신청`（使用申请），用途选个人/研究，提交申请。多数公共数据会自动批准。
4. 进入 `마이페이지` → `개발계정` / `인증키`，复制一般 인증키（Encoding 或 Decoding 都可以）。
5. 把 key 写到 `backend/.env`，只加一行：

```text
DATA_GO_KR_API_KEY=你的key
```

不要把 key 发到聊天，也不要提交 `.env`。写好后告诉我“K-FIND key 已写入”，我来读本地配置。

如果你不想申请 key：在 data.go.kr 或 K-FIND 页面下载 CSV/JSON/XLSX，放到 `data_sources/kfind/`，并告诉我文件名。

## 2. 香港 CFS（仅非商业）

1. 打开 https://www.cfs.gov.hk/tc_chi/nutrient/searchmenu.php
2. 按食物类别（点心、烧味、粉面饭、汤、素菜等）搜索。
3. 如果页面提供 Excel/CSV 下载，直接下载；如果没有，按 `backend/app/data/import_template.csv` 的表头整理你需要的条目。
4. 保存为 `data_sources/cfs/cfs_foods.csv`（或 Excel），告诉我文件路径。

注意：CFS 仅限非商业使用，原始数据不要提交到公开仓库；公开部署时保留署名。商用需要向 CFS 申请授权。

## 3. 中国 CDC

1. 打开 https://nlc.chinanutri.cn/fq/
2. 按食材或菜品逐条查询。
3. 复制名称、类别、每 100g 热量/蛋白/脂肪/碳水/钠等到 `backend/app/data/import_template.csv` 的对应列。
4. 保存为 `data_sources/cdc/cdc_foods.csv`。

CDC 没有批量下载，不要写爬虫。只整理你真正要用的条目；商用需要取得许可。

## 4. USDA FNDDS / 日本 MEXT

这两个是公开下载，不需要账号：

- USDA：https://www.ars.usda.gov/northeast-area/beltsville-md-bhnrc/beltsville-human-nutrition-research-center/food-surveys-research-group/docs/fndds-download-databases/
  下载 `Foods and Beverages.xlsx`、`Portions and Weights.xlsx`、`FNDDS Nutrient Values.xlsx`，放到 `data_sources/usda_fndds_2021_2023/`。
- MEXT：https://www.mext.go.jp/a_menu/syokuhinseibun/mext_00001.html
  下载食品成分表 2023 的 Excel，放到 `data_sources/mext/`。

你也可以把公开直链发我，我来下载；如果文件很大或需要同意条款，最好你手动下载。

## 5. 怎么把文件/授权交给我

推荐直接把文件放在本机项目目录：

```text
E:\allprograms\foodai\data_sources\
  usda_fndds_2021_2023\
  mext\
  kfind\
  cfs\
  cdc\
```

然后告诉我：

```text
我已经把 CFS 放到 data_sources/cfs/cfs_foods.csv
```

我会读取本地文件、跑 dry-run、给你报告，再决定是否 `--commit`。

- 公开直链：把 URL 发我，我在执行阶段下载。
- K-FIND key：只写进 `backend/.env`，不要发聊天。
- 不要把账号密码、Cookie、token 发给我。
- `data_sources/` 已被 `.gitignore` 排除，不会提交；CFS/CDC 原始数据不要公开分发。

## 6. 重启、迁移、启用

```powershell
cd E:\allprograms\foodai\backend

# 新机器或表结构变更后执行
.\.venv\Scripts\python.exe -m alembic upgrade head

# 只有 foods 为空或需要重置内置种子时执行
.\.venv\Scripts\python.exe seed_foods.py
```

重启后端后，检查营养库规模（需要登录）：

```text
http://localhost:8000/docs → GET /api/foods/stats
```

启用 staging 条目：先 dry-run，确认后加 `--commit`。

```powershell
.\.venv\Scripts\python.exe -m scripts.nutrition_import.activate --source tfda --food-type single_food
.\.venv\Scripts\python.exe -m scripts.nutrition_import.activate --source tfda --food-type single_food --commit
```

停用：

```powershell
.\.venv\Scripts\python.exe -m scripts.nutrition_import.activate --source tfda --food-type single_food --deactivate --commit
```

## 规范字段

统一使用每 100g 可食部；熟/生、可食部、份量必须标清。  
可直接复制 [`import_template.csv`](import_template.csv) 填写；示例行仅供格式参考，导入时请删掉或改成 `active=false`。

```text
name_zh, name_en, category_l1, category_l2,
food_type, dish_style, region, cuisine, state,
edible_ratio, serving_g, common_portion_g,
energy_kcal, protein_g, fat_g, carb_g, fiber_g, sodium_mg, sugar_g,
ingredients, taste_tags, dietary_tags, suitable_meals,
source_id, verified, active
```

- `food_type`: ingredient / single_food / dish / light_meal / packaged / beverage
- `state`: raw / cooked / processed / ready_to_eat
- 列表字段用 `|` 或逗号分隔
- 缺失值留空，不要填 0
- `kcal_per_serving = energy_kcal * serving_g / 100`

## 导入命令

### 台湾 TFDA（已接入适配器）

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.nutrition_import.tfda `
  --csv ..\data_sources\tfda\extracted\20_2.csv `
  --out ..\data_sources\tfda\canonical_foods.csv `
  --aliases-out ..\data_sources\tfda\aliases.json

.\.venv\Scripts\python.exe -m scripts.nutrition_import.importer `
  ..\data_sources\tfda\canonical_foods.csv `
  --source tfda --version 2026-10 --license OGDL-1.0 --commit

.\.venv\Scripts\python.exe -m scripts.nutrition_import.aliases `
  ..\data_sources\tfda\aliases.json --commit
```

### 英国 CoFID（已接入适配器）

```powershell
.\.venv\Scripts\python.exe -m scripts.nutrition_import.cofid `
  --xlsx ..\data_sources\cofid\cofid_2021.xlsx `
  --out ..\data_sources\cofid\canonical_foods.csv

.\.venv\Scripts\python.exe -m scripts.nutrition_import.importer `
  ..\data_sources\cofid\canonical_foods.csv `
  --source cofid --version 2021 --license OGL-3.0 --commit
```

TFDA/CoFID 默认 `active=false`，只进 staging；确认无误后再挑选条目启用。

```powershell
.\.venv\Scripts\python.exe -m scripts.nutrition_import.activate --source tfda --food-type single_food
.\.venv\Scripts\python.exe -m scripts.nutrition_import.activate --source tfda --food-type single_food --commit
.\.venv\Scripts\python.exe -m scripts.nutrition_import.activate --source tfda --name "富士蘋果" --commit
.\.venv\Scripts\python.exe -m scripts.nutrition_import.activate --source tfda --food-type single_food --deactivate --commit
```

### 自建菜谱（食材克重 → 菜品营养）

```powershell
.\.venv\Scripts\python.exe -m scripts.nutrition_import.recipes recipe.json
.\.venv\Scripts\python.exe -m scripts.nutrition_import.recipes recipe.json --commit
.\.venv\Scripts\python.exe -m scripts.nutrition_import.recipes recipe.json --commit --activate
```

`recipe.json` 格式：

```json
[
  {
    "name": "青椒肉丝",
    "cuisine": "川",
    "category_l1": "荤菜",
    "dish_style": "家常菜",
    "portion_g": 250,
    "yield_g": 250,
    "ingredients": [
      {"name": "猪肉", "grams": 100},
      {"name": "青椒", "grams": 150}
    ]
  }
]
```

生成结果写入 `foods` + `food_recipes` + `food_recipe_items`；`source_type=recipe_estimate`，默认 `active=false`。食材会先查 active 池，再查 staging；找不到会列在 `missing_ingredients`，commit 时该菜谱跳过。

### 通用 CSV/XLSX

先做 dry-run：

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.nutrition_import.importer data_sources/tfda/foods.csv --source tfda --version 2026 --license OGDL-1.0
```

确认无误后写入：

```powershell
.\.venv\Scripts\python.exe -m scripts.nutrition_import.importer data_sources/tfda/foods.csv --source tfda --version 2026 --license OGDL-1.0 --commit
```

覆盖已有记录：

```powershell
.\.venv\Scripts\python.exe -m scripts.nutrition_import.importer data_sources/tfda/foods.csv --source tfda --version 2026 --license OGDL-1.0 --commit --force
```

生成易混淆候选 CSV 供人工审核：

```powershell
.\.venv\Scripts\python.exe -m scripts.nutrition_import.relations
```

报告默认写到 `backend/import_reports/confusable_candidates.csv`。

## 内置种子

```powershell
cd backend
.\.venv\Scripts\python.exe seed_foods.py
.\.venv\Scripts\python.exe seed_foods.py --force
```

应用启动时，如果 `foods` 表为空会自动导入内置种子。内置种子覆盖八大菜系、家常菜、轻食、单个食物和基础饮品，但营养值为种子估算，`verified=false`；正式数据导入后应逐步替换/校验。
