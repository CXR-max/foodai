# 识膳智行 前端（Vue3 + Vite + TS + Element Plus + ECharts）

## 启动

```bash
# 首次
npm install

# 之后每天开发（需后端 8000 已启动）
npm run dev
```

打开 http://localhost:5173 （已配置 /api、/uploads 代理到后端，免跨域）

## 目录结构（一个页面 = views 一个文件）

```
frontend/src/
├── main.ts                 # 入口：pinia + router + Element Plus(中文)
├── App.vue                 # 只渲染 <router-view>
├── router/index.ts         # ★ 路由表（加页面 = views 建文件 + 这里加一行）
├── api/
│   ├── http.ts             # ★ axios 封装：自动带 token / 401 跳登录 / 统一报错
│   ├── auth.ts             # 每个后端模块对应一个 api 文件（类型化的接口函数）
│   ├── profile.ts / food.ts / plan.ts / checkin.ts / stats.ts / points.ts / rewards.ts
├── stores/
│   ├── auth.ts             # 登录态（token 存 localStorage，刷新不掉线）
│   └── profile.ts
├── layouts/MainLayout.vue  # 侧边栏 8 菜单 + 顶栏（积分徽章/退出）
├── views/                  # ★ 10 个页面
│   ├── LoginView / RegisterView
│   ├── DashboardView       # 仪表盘
│   ├── ProfileView         # 健康档案（BMI/偏好/体重曲线）
│   ├── RecognitionView     # 食物识别（拖拽上传→结果→保存记录）
│   ├── PlansView           # 养生方案（生成/7天tabs/编辑/导入日历）
│   ├── CheckInView         # 打卡日历（完成度 n/n、逐项打卡、手动运动）
│   ├── StatsView           # 数据追踪（热量/体重/营养三张图）
│   ├── MallView            # 积分商城（分类/兑换/核销码）
│   └── LeaderboardView     # 排行榜（金银铜/实时）
├── components/
│   ├── StatTile.vue        # 统计卡片
│   └── TrendChart.vue      # ECharts 通用图表（传 option 即用）
└── utils/date.ts           # 日期工具
```

## 常见修改"去哪改"速查表

| 你想… | 去哪改 |
|---|---|
| **加新页面** | `views/XxxView.vue` 建文件 → `router/index.ts` 加一行（侧边栏菜单在 `layouts/MainLayout.vue` 的 `menus` 数组加一项） |
| **调接口超时** | `api/http.ts`（默认 30s；识别/生成方案单独 120s 在各自 api 文件里） |
| **改图表样式** | 各页面里的 `xxxOption` 配置（配置项参考 echarts 官网） |
| **改主题色** | 全局搜 `#67c23a`（Element Plus 绿）替换即可 |
| **加接口调用** | `api/` 对应模块文件加一个函数，组件里直接 import 用 |

## 演示账号

排行榜预置了 5 个演示用户（后端 `seed_demo.py` 生成），密码均为 `demo123456`，可登录看不同积分段的效果。
