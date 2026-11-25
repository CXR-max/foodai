# 人脸登录：对接与部署说明

## 1. 这套东西怎么跑

```
登录页输入用户名
   │  POST /api/auth/face/status      → 这个账号绑过脸吗
   ▼
点击「人脸登录」→ 摄像头授权 → 眨眼检测（端上）
   │  POST /api/auth/face/verify      → 现场照 vs 底库照 1:1 比对
   ▼
拿到一次性 ticket（2 分钟、只能用一次）
   │  POST /api/auth/face/login       → ticket 换正式 token
   ▼
登录成功，跳首页
```

底库照片存在 `backend/data/faces/{user_id}.jpg`，**不在** `/uploads` 静态目录里，不能被直接 URL 访问。

## 2. 两种模式

| 模式 | 配置 | 行为 | 用途 |
|---|---|---|---|
| mock | `FACE_PROVIDER=mock` | 不做真实比对，直接放行（页面会显示「演示模式」） | 没有云端 key 时跑通流程、答辩演示 |
| baidu | `FACE_PROVIDER=baidu` | 调百度人脸比对 v3，返回 0~100 相似度 | 真实使用 |

mock 默认放行是为了保证演示不翻车；**上线前必须切 baidu**，否则任何人输入用户名都能进。

## 3. 接百度智能云（真实模式）

1. 打开 <https://console.bce.baidu.com/ai/#/ai/face/overview/index>，创建「人脸识别」应用
2. 拿到 **API Key / Secret Key**
3. 写入 `backend/.env`：

```
FACE_PROVIDER=baidu
BAIDU_FACE_API_KEY=你的APIKey
BAIDU_FACE_SECRET_KEY=你的SecretKey
FACE_MATCH_THRESHOLD=80
```

4. 重启后端，`GET /api/llm/status` 之外可看日志里的 `人脸 Provider = baidu`
5. 到登录页 → 密码登录 → 「我的」→ 绑定人脸 → 退出 → 刷脸登录

> 百度接口是「人脸比对 v3」，一次请求两张图，返回 score。免费额度足够课程演示。
> 如果要用**百度人脸核身 H5 SDK**（金融级活体、服务端可验签），把 `app/services/face/provider.py`
> 里的 `compare()` 换成「用 SDK 返回的 token 去调核身结果查询接口」，其余调用方不用动。

## 4. 移动端 / WebView 要注意的

### 4.1 摄像头必须安全上下文

`getUserMedia` 只在 **HTTPS** 或 **localhost / 127.0.0.1** 下可用。

- 本地调试：电脑浏览器开 `http://127.0.0.1:5173` 可以
- 手机连电脑调试：`http://192.168.x.x:5173` **不行**，必须上 HTTPS
- 快速方案：`npx vite --host --https` + 自签证书；或内网穿透（ngrok / cpolar）拿 https 域名

### 4.2 安卓 WebView 权限

`AndroidManifest.xml`：

```xml
<uses-permission android:name="android.permission.CAMERA" />
<uses-permission android:name="android.permission.INTERNET" />
<!-- 只做演示、后端是 http 时才需要；正式环境请用 https -->
<application android:usesCleartextTraffic="true" ... />
```

WebView 侧必须处理 `onPermissionRequest`，否则 H5 调摄像头永远返回拒绝：

```java
webView.getSettings().setJavaScriptEnabled(true);
webView.getSettings().setDomStorageEnabled(true);          // localStorage 存 token
webView.getSettings().setMediaPlaybackRequiresUserGesture(false);

webView.setWebChromeClient(new WebChromeClient() {
    @Override
    public void onPermissionRequest(final PermissionRequest request) {
        runOnUiThread(() -> request.grant(request.getResources()));
    }
});
```

运行时还要动态申请 `Manifest.permission.CAMERA`，并且 WebView 的 URL 必须是 https。

### 4.3 iOS（如果以后要）

`Info.plist` 加 `NSCameraUsageDescription`；WKWebView 需要
`WKUIDelegate` 的 `requestMediaCapturePermissionFor` 回调里授权。

## 5. 安全边界（答辩会被问）

| 问题 | 现状 | 要做到生产级 |
|---|---|---|
| 活体 | 端上画面运动检测，前端上报 | 换百度人脸核身 H5，**服务端**验签，不信前端布尔值 |
| 防重放 | ticket 一次性 + 2 分钟过期 | 加设备指纹、限流按 IP |
| 枚举用户名 | `/face/status` 会暴露"是否绑脸" | 改成先验设备再查，或统一模糊返回 |
| 比对阈值 | 默认 80 分 | 按业务调，误识率/拒识率权衡 |
| 底库安全 | 本地目录，不入静态目录 | 加密存储、脱敏、可删除（已支持解绑） |

## 6. 自测命令

```bash
# 1) 注册一个账号
curl -X POST http://127.0.0.1:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"face_demo\",\"password\":\"123456\"}"

# 2) 用返回的 token 绑定人脸（图片传 base64）
curl -X POST http://127.0.0.1:8000/api/auth/face/enroll \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d "{\"image\":\"data:image/jpeg;base64,....\"}"

# 3) 查状态
curl -X POST http://127.0.0.1:8000/api/auth/face/status \
  -H "Content-Type: application/json" -d "{\"username\":\"face_demo\"}"

# 4) 比对 → 拿 ticket
curl -X POST http://127.0.0.1:8000/api/auth/face/verify \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"face_demo\",\"image\":\"data:image/jpeg;base64,....\"}"

# 5) ticket 换 token
curl -X POST http://127.0.0.1:8000/api/auth/face/login \
  -H "Content-Type: application/json" -d "{\"ticket\":\"...\"}"
```
