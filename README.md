# OPDs/OSCs 中文研究简报自动化

本仓库包含一个 GitHub Actions 自动化，用于在每个中国工作日和周六早上 8 点前生成一份中文研究简报，主题为近期可拉伸有机光电探测器（OPDs）和有机太阳能电池（OSCs）的进展。

## 运行时间

- 定时任务：每天 UTC 23:50 触发，即中国标准时间 07:50。
- 脚本会在运行时按 `Asia/Shanghai` 判断是否应生成简报：默认周一至周六运行，周日跳过。
- 如果自动化创建/更新时中国时间已经晚于 08:00，`push` 触发器会调用 `--force-if-after-8` 立即补跑一次。
- 可以通过 `workflow_dispatch` 手动触发；默认强制运行。

> 中国法定节假日和调休可通过仓库变量覆盖：`CHINA_HOLIDAYS` 与 `CHINA_EXTRA_WORKDAYS`，格式均为英文逗号分隔的 `YYYY-MM-DD` 日期列表。

## 简报内容

脚本会优先检索近 14 天内的论文、预印本、期刊在线发表记录和相关学术元数据；若近期结果少于阈值，会扩展到近两年，并在简报中注明实际时间范围。生成的中文简报要求包括：

1. 近期可拉伸 OPDs 或 OSCs 的重要进展：核心材料体系、器件结构、关键性能指标、拉伸测试结果、创新点和潜在局限。
2. 参考文件清单：题名、作者、期刊/平台、发布日期或在线日期、DOI/链接。
3. 高分子专业视角文献简报：聚焦高分子材料、弹性体、聚合物添加剂、交联网络、界面层、封装层、共混策略或形貌调控材料如何提升光电性能和机械性能。
4. 3-5 个值得跟进的研究假设或实验设计建议。

脚本提示词要求明确区分“已由文献证明”和“基于文献的推断”，并避免编造文摘/元数据未提供的指标。

## 数据源与生成模型

当前脚本使用无需 API key 的公共学术元数据源：

- OpenAlex Works API
- arXiv API

如配置 `OPENAI_API_KEY`，脚本会调用 OpenAI 模型生成完整中文研究简报；否则会生成结构化来源摘要，便于检查检索结果。

## 必需/可选配置

### 生成完整简报

在 GitHub 仓库 Secrets 中配置：

- `OPENAI_API_KEY`：OpenAI API key。

可选仓库 Variables：

- `OPENAI_MODEL`：默认 `gpt-4o-mini`。
- `OPENALEX_MAILTO`：OpenAlex 推荐提供的联系邮箱。
- `CHINA_HOLIDAYS`：跳过的中国假期日期列表，例如 `2026-10-01,2026-10-02`。
- `CHINA_EXTRA_WORKDAYS`：需要补跑的调休工作日列表，例如 `2026-09-20`。

### 邮件投递

若需要把简报通过邮件发送，请配置以下 Secrets：

- `BRIEFING_EMAIL_TO`：收件人，多个地址用英文逗号分隔。
- `SMTP_HOST`：SMTP 服务器。
- `SMTP_PORT`：SMTP 端口，默认 `587`。
- `SMTP_USERNAME`：SMTP 用户名。
- `SMTP_PASSWORD`：SMTP 密码。
- `BRIEFING_EMAIL_FROM`：可选，发件人地址。

无邮件配置时，简报仍会作为 GitHub Actions artifact 上传。

## 本地测试

```bash
python scripts/opds_oscs_briefing.py --force --dry-run
```

生成文件位于 `briefs/opds-oscs-briefing-YYYY-MM-DD.md`。`briefs/*.md` 已被 `.gitignore` 忽略，避免把每日简报提交到仓库。
