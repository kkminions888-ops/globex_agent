# 2026-09-18 · Agent 动态澄清工具

状态：动态澄清完整链路通过；首次商品漏调工具的失败另列。关联代码：`be13ff48`。

## 需求与本次变更

买家要求澄清内容由 Agent 决定，前端只渲染。本次将最初的固定问卷升级为 `show_shopping_form(title, questions, context="", description="")`：Agent 根据当前请求决定题目、顺序、类型、选项、单位、说明与必填性。支持文本、数字、单选和多选，最多 12 题，不自动猜测答案或追加固定问题。

- 服务端校验问题定义，并在 SQLite 保存题目、答案与版本；校验买家/会话归属，提交使用 CAS 与幂等标识。
- 以 A2UI v0.9 自定义 ShoppingForm 组件通过 AG-UI 扩展事件传递，前端通用渲染；不是完整通用 A2UI 渲染器。
- 提交后将结构化答案作为同一会话的新买家消息交给 Agent，稳定 run/message ID 防止重复继续。
- 旧固定创建接口返回 410，旧记录兼容读取；权威表单状态从数据库恢复，不能被旧 AG-UI 快照覆盖。
- 修复聊天气泡直接展示 JSON：仅展示层转换为可读题目/答案摘要，原始消息和单位保留。没有自动保存长期偏好或授权订单。

源码入口：[Agent 工具](../../../app/application/tools/shopping_form_tool.py)、[表单存储](../../../app/infrastructure/shopping_forms.py)、[接口](../../../app/presentation/shopping_forms.py)、[前端渲染](../../../frontend/src/components/ShoppingForm.tsx)。

## 验证与失败样本

真实模型生成耳机选购的八个问题，填写、提交、重复继续、刷新、无浏览器缓存及后端重启恢复通过；提交前刷新恢复已保存定义，不承诺恢复尚未提交的输入草稿。测试使用隔离买家和数据库。

商品精确查询的首次请求只返回“准备查询”，未调用工具、没有商品卡；追问后商品卡出现。**复核成功没有覆盖首次失败，首轮工具调用可靠性仍待改进。**

原始过程及 7 张截图见[浏览器联调报告](../../../eval/verification/clarification-browser-20260918/REPORT.md)；服务端与前端回归见[表单测试](../../../tests/test_shopping_forms.py)、[渲染测试](../../../frontend/tests/shoppingForm.test.tsx)。最终全量结果见[当日总览](README.md#共享验证与发布证据)。

## 数据与回滚边界

表单存于 `DATA_DIR/shopping_forms.db`。买家填写的航司、尺寸等仍是待核验要求，不是已验证政策。停用入口或回退前后端时保留数据库；澄清提交不等同于交易或记忆写入审批。

[返回当日目录](README.md)
