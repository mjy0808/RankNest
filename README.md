# 全球潜力 App / 游戏日报

一个低成本、零第三方 Python 依赖的每日趋势雷达。它从多个国家和地区的 App Store 榜单、Steam 商店趋势以及 Hacker News 公开讨论中寻找异常增长，每天分别输出 App Top 8、手游 Top 6、Steam 游戏 Top 6，并可通过 SMTP 自动发送。

## 它现在会做什么

- 覆盖 12 个 App Store 市场的 App 免费榜、付费榜，以及游戏免费榜、畅销榜；
- 覆盖 6 个 Steam 市场、热销榜与热门新品榜；
- 通过 Apple 元数据补充评分、评论量、类别、发布日期和更新时间；
- 监控近 48 小时 Hacker News 产品提及；
- 将快照保存到 SQLite，按相同国家与榜单计算 1、3、7 日配对增速；
- App、手游和 Steam 游戏分别标准化与排名，避免平台数据规模互相挤压；
- 数据源区分健康、降级和失败，降级市场不参与当天增速；
- 同一天重复运行会覆盖同一个 run，不重复积累快照或发送邮件；
- 每份报告包含 run ID 和数据指纹，可以追溯到对应数据库快照；
- 生成适合邮件阅读的响应式 HTML 报告。

这是一套“发现线索”的系统。公开榜单无法提供精确下载量与收入，因此报告不会把潜力分伪装成商业数据估算。

## 本地运行

要求 Python 3.11 或更高版本，无需安装依赖：

```bash
python3 -m unittest discover -s tests -v
python3 -m app_radar --config config.json
```

生成文件：

```text
data/radar.db          历史快照
reports/latest.html    最新 HTML 日报
reports/latest.txt     最新纯文本日报
reports/latest.json    最新结构化结果
reports/YYYY-MM-DD.html
```

本地预览：

```bash
python3 -m http.server 8000 --directory reports
```

然后访问 `http://127.0.0.1:8000/latest.html`。

## 配置邮件

标准 SMTP 环境变量：

```bash
export EMAIL_TO="you@example.com"
export EMAIL_FROM="radar@example.com"
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="465"
export SMTP_USERNAME="radar@example.com"
export SMTP_PASSWORD="应用专用密码"
export SMTP_SECURITY="ssl"
python3 -m app_radar --config config.json --send
```

`SMTP_SECURITY` 可选 `ssl`、`starttls` 或 `none`。多个收件人用英文逗号分隔。普通本地运行缺少邮件配置时会安全跳过；自动任务使用 `--require-email`，配置缺失会明确失败。

## 每天自动运行

`.github/workflows/daily-radar.yml` 已设置每天北京时间 08:30 运行，也支持手动触发。在仓库的 Actions secrets 中配置：

- `EMAIL_TO`
- `EMAIL_FROM`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_SECURITY`

GitHub Actions cache 会保存 SQLite 历史，报告则作为 30 天 artifact 上传；数据库默认只保留 45 天快照，避免长期膨胀。首次运行是冷启动基线；之后依次启用 1、3、7 日变化。超过 7 天没有运行时缓存可能被清理，报告会明确重新进入历史积累状态。

真正启动每日邮件需要：

1. 收件邮箱 `EMAIL_TO`；
2. 一个支持 SMTP 的发件邮箱，以及服务器、端口、用户名和应用专用密码；
3. 在仓库 Actions secrets 中填写上述 7 个变量；
4. 在 Actions 页面手动运行一次 `Daily app and game radar`，确认收到首封邮件。

## 调整覆盖与评分

市场、榜单、候选数量和并发量均在 `config.json` 中。三个分榜使用不同权重，并在各自候选池内做百分位标准化：

```text
App：排名 35% + 口碑 25% + 讨论 15% + 市场 15% + 新鲜度 10%
手游：排名 40% + 口碑 25% + 讨论 5% + 市场 20% + 新鲜度 10%
Steam：排名 40% + 口碑 25% + 讨论 5% + 市场 15% + 新鲜度 15%
```

如果某个市场临时失败，其他数据源会继续运行，失败原因会进入报告。只有全部采集器都没有返回候选时，任务才会失败。

## 数据源边界

- Apple RSS 与 Lookup：公开接口，适合榜单与商店元数据；
- Steam 商店搜索结果：公开页面数据，接口或 HTML 结构变化时解析器可能需要更新；
- Hacker News Algolia：使用完整产品名精确匹配，只作为辅助讨论信号；
- 暂未直接采集 Google Play 竞品数据，因为 Google 官方开发者 API 主要面向自己账号下应用的发布和管理。后续可按需要增加合规的第三方数据源。
