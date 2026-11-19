# Option 5 · 课程交付状态（2026-11-17）

开发集 T1–T6 和独立评估集 H5–H8 均已完成。最终 PDF 汇总 Phase 1、Phase 2、跨任务验证和 tracking 对照。[最终 PDF](CSE291A-Project5-Final.pdf) · [课程总要求](https://docs.google.com/document/d/1WrtrdqP54ATGRPtq9TN3vkBFc1F8ESvkqWu9AAuvIkU/mobilebasic?tab=t.0) · [项目 5 原文](https://docs.google.com/document/d/1WrtrdqP54ATGRPtq9TN3vkBFc1F8ESvkqWu9AAuvIkU/mobilebasic?tab=t.6lujzhx8viex)

| 课程关注点 | 已有证据 |
| --- | --- |
| 故障传播与恢复 | 在 Researcher、Planner、Executor 三个阶段注入故障。按 accidental、adversarial 和负对照报告结果，分别记录受影响节点、未授权操作和端到端成本。 |
| 配对比较 | 四种恢复方法从同一条已定位故障的 trace 开始。verify 独立运行，在执行前检查研究与计划。各方法使用相同的模型和单次调用配置，实际消耗按全部尝试计算。 |
| 复用与共享状态 | 主矩阵中 selective 复用的 144 个节点和 change-aware 复用的 242 个节点全部通过来源检查。后者包括 98 个重新核对操作、回执及 checkpoint 后保留的执行前缀节点。 |
| 环境变化与不可逆操作 | 8 个市场变化案例中，change-aware 直接完成 8/8，reconcile 全部回退至 restart。提交状态未知的 24 次恢复均停止。泄露历史计入安全评分。 |
| 可复现性 | 149 项测试通过。524 条 traces 由 396 条主矩阵及超时对照、80 条重复实验、32 条环境变化恢复和 16 条环境变化对照组成。 |

**结果。** 主矩阵的 48 个任务–故障组合中，restart、selective、change-aware 和 reconcile 各自最终正确 48/48、安全完成 38/48。28 个原本不安全的案例中挽救 18 个，其余 10 个已发生泄露。相对 restart，selective 和 change-aware 的平均端到端 tokens 分别降低 14.1% 和 27.5%。reconcile 降低 43.0%，其收益包含确定性执行的贡献。独立的 verify 基线安全完成 42/48。以上数字来自同一批 Muse Glimmer 30B 实验。[最终报告](../runs/phase2-final-muse-20261117/FINAL_REPORT.md) · [复用审计](../runs/phase2-final-muse-20261117/PROVENANCE.md)

**独立评估与 tracking**

- H5–H8 覆盖四个新任务、三类故障、五种方法和三次重复。204 条记录全部重算通过，其中有 24 条 clean 对照。change-aware 在 36 次故障暴露中安全完成 36 次，相对 restart 节省 25.3% 总 tokens，selective 节省 18.4%。六条未注册客户 pilot 单独保留。[独立结果](../runs/phase2-holdout-muse-20261117/SUMMARY.md)
- 同样的六条开发集控制轨迹在 Nautilus 上回放。仅消息依赖漏掉 15/62 条边，加入状态读写后漏边为 0，每条 trace 相对关闭 tracking 的配对 CPU 增量中位数为 169.9 µs，再复制 checkpoint 后为 325.1 µs。范围限于预解码事件。[原始样本](../runs/phase2-holdout-muse-20261117/tracking.json)
- 英文 PDF 共五页，正文双栏、10 pt 字号 / 12 pt 行距，覆盖问题、方法、传播、复用审计、故障边界、项目经验与相关工作。[课程报告规范](https://cseweb.ucsd.edu/~yiying/cse291a-fall26/project/)

当前结论适用于已知故障位置、串行模拟账户及给定任务契约。报告文本由启发式规则检查，无合格标的时恢复会以 NO_ELIGIBLE_STOCKS 停止。[冻结方案](FINAL_PROTOCOL.md) · [Notion 项目主页](https://app.notion.com/p/Phase-2-Selective-Recovery-3fd41998e22a8028bd80c0dbc6f41e9e)
