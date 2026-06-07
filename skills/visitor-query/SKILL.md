---
name: visitor-query
description: 查询访客数据库的统计信息（来访车辆数、高峰时段、某访客来访记录等）
version: "1.0"
---

## 访客查询技能

当用户询问访客统计、来访记录等查询类问题时使用此技能。

### 使用方法

使用 terminal 工具执行以下脚本查询数据：

```
python3 /home/rimuru/.hermes/profiles/voiceagent/skills/visitor-query/scripts/query_visitors.py <参数>
```

### 可用查询命令

| 用户问题示例 | 对应命令 |
|---|---|
| "本周来了多少车" | `--stats --period week` |
| "今天多少访客" | `--stats --period day` |
| "本月统计" | `--stats --period month` |
| "什么时间段来访最多" | `--peak-hours` |
| "张师傅来了几次" | `--visitor 张` |
| "沪A12345的记录" | `--visitor 沪A12345` |
| "来访最多的访客" | `--top 5` |
| "最近的来访记录" | `--recent 10` |

### 回复风格

- 用简洁的自然语言回复，不要直接输出 JSON
- 如果数据为空，说明"暂无记录"
- 回复示例："本周共有 12 辆不同车辆来访，总计 18 次。"
