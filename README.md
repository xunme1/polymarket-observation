# 预测市场观察 · Polymarket

独立公开的原站截图观察页，供研究看板嵌入。只采集公开事件，不登录、不交易。
页面不请求 Polymarket 或第三方 CDN，所有截图采用 Chromium 原生 JPEG 并内嵌。

## 本地预览

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python observe.py
python3 -m http.server 8787 --directory dist
```

打开 `http://127.0.0.1:8787/`。本地命令不会发布网站。
单个事件预览：`observe.py --event oil --archive artifacts/oil-preview --output artifacts/oil-site`。
同日完整成功后重跑跳过；`--force` 执行当日修订。部分失败时重跑只补失败事件。

## GitHub Pages 上线

1. 仓库 Settings → Pages → Source 选择 **GitHub Actions**。
2. 手动运行 `Capture and publish observations`，保持 `publish=false`，先验收 `observation-preview` 产物。
3. 确认 6 个事件可访问、展开完整且图片清晰后，手动运行并勾选 `publish`。
4. 成功后在 Settings → Secrets and variables → Actions → Variables 创建 `DAILY_ENABLED=true`。
5. 每天 UTC 00:00（北京时间 08:00），包含周末。GitHub 调度可能延迟，以截图时间为准。

不需要 Polymarket API Key、浏览器 Cookie 或服务器密钥。工作流只用 GitHub 自带的短期 `GITHUB_TOKEN` 写本仓库历史及 Pages。

## 数据与失败处理

- `watchlist.json`：按数组顺序维护中文标题、主题和公开原站地址。
- `snapshots/YYYY-MM-DD.json`：逐事件截图时间、原站标题、JPEG 和图像摘要。事件结束后保留历史，由维护者替换清单。
- `dist/index.html`：最新报告；`dist/reports/YYYY-MM-DD.html`：历史报告。
- 部分成功发布部分更新；失败事件留空，不用历史截图冒充当天。全失败不覆盖历史或已部署页面。
- 定时任务失败后隔 5 分钟重试，单次工作流最多 3 轮，只补失败事件；访问限制或验证页面不绕过。
- 逐事件错误保存在 Actions 日志及 `artifacts/status.json`；验证产物保留 14 天。
- 单份 HTML 超过 8 MiB 停止生成。整站超过 800 MiB 停止发布并保留历史，届时需规划长期存储。
- 快照超过 26 小时提示过期；已经打开的页面需刷新才能看到新一期。
- 原站 DOM 变化时采集失败，不能把加载占位、验证页或缺少结果的页面当作成功。

GitHub 可能关闭长期不活跃的公开仓库定时任务。日常成功发布会提交真实截图历史，持续失败仍需维护者检查 Actions。

## 看板集成与回退

预期地址：`https://xunme1.github.io/polymarket-observation/`，以 Pages 实际部署结果为准。
研究看板新增独立入口，经 `/polymarket-observation-embed.html` 专用静态页嵌入；两层 iframe 均为 `sandbox="allow-scripts"`。
仅专用静态页的 Nginx 精确路由允许该 GitHub Pages 路径，全站 CSP 不变；同时提供独立打开链接。
原有报告权限保持原样；访客设备需能访问 GitHub Pages。

回退：停用 `DAILY_ENABLED`、移除主站入口，保留截图历史。
