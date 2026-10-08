import { memo, useState } from "react";
import type { ExecutionActivity } from "../types";
import Icon from "./Icon";
import "./executionTrace.css";

const stateLabels: Record<ExecutionActivity["status"], string> = {
  preparing: "准备中", running: "进行中", waiting: "等待确认", completed: "已完成",
  approved: "已批准", error: "未完成", cancelled: "已取消", interrupted: "已中断",
};

function duration(item: ExecutionActivity): string | null {
  if (item.startedAt === null || item.finishedAt === null || item.finishedAt < item.startedAt) return null;
  const seconds = (item.finishedAt - item.startedAt) / 1000;
  return seconds < 1 ? "< 1 秒" : seconds < 60 ? `${seconds.toFixed(1)} 秒` : `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
}

const ActivityRow = memo(function ActivityRow({ item }: { item: ExecutionActivity }) {
  const [open, setOpen] = useState(item.kind === "thinking" && item.status === "running");
  const thinking = item.kind === "thinking";
  const live = item.status === "running" || item.status === "preparing";
  const elapsed = duration(item);
  return <li className={`execution-row execution-${item.kind}`} data-status={item.status}>
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>
        <span className={`execution-icon${live ? " is-live" : ""}`}>
          <Icon name={thinking ? "spark" : item.status === "completed" ? "check" : item.status === "error" ? "info" : "clock"} />
        </span>
        <span className="execution-title">{thinking ? "Think · 思考过程" : item.label}</span>
        <span className="execution-preview">{thinking ? item.content?.replace(/\s+/g, " ").slice(0, 80) : item.resultSummary || item.inputSummary}</span>
        <span className="execution-status">{stateLabels[item.status]}</span>
        {elapsed && <span className="execution-duration">{elapsed}</span>}
        <span className="execution-chevron" aria-hidden="true">⌄</span>
      </summary>
      <div className="execution-detail">
        {thinking ? <>
          <p className="execution-source">模型返回的思考内容</p>
          <div className="execution-thinking-text">{item.content || (live ? "正在等待模型返回思考内容…" : "本段没有可显示的思考内容。")}</div>
        </> : <>
          {item.inputSummary && <div className="execution-detail-group"><span>处理条件</span><p>{item.inputSummary}</p></div>}
          {item.resultSummary && <div className="execution-detail-group"><span>处理结果</span><p>{item.resultSummary}</p></div>}
          {!item.inputSummary && !item.resultSummary && <p>已记录执行状态，此步骤没有额外的可展示详情。</p>}
        </>}
        {item.truncated && <p className="execution-source">内容较长，此处展示已保存的片段。</p>}
      </div>
    </details>
  </li>;
});

export default function ExecutionTrace({ activities, runId, running = false, compact = false }: {
  activities: ExecutionActivity[]; runId?: string; running?: boolean; compact?: boolean;
}) {
  const visible = activities.filter(item => !runId || item.runId === runId);
  if (!visible.length) return compact ? null : <div className="execution-empty">
    <Icon name="spark" /><strong>{running ? "正在接收执行过程" : "执行过程会保存在这里"}</strong>
    <p>工具调用和模型返回的 Think 会逐步出现。旧版会话可能没有这些记录。</p>
  </div>;
  // 分组保留轮次的接收顺序，同一轮按服务端序号排序；同名并行调用不合并。
  const groups = new Map<string, ExecutionActivity[]>();
  for (const item of visible) groups.set(item.runId, [...(groups.get(item.runId) ?? []), item]);
  return <section className={`execution-trace${compact ? " is-compact" : ""}`} aria-label="思考与工具执行过程">
    {[...groups].map(([id, items], index) => <div className="execution-run" key={id}>
      {!compact && <div className="execution-run-heading"><span>选购过程 {index + 1}</span><small>{items.length} 个步骤</small></div>}
      <ol>{items.sort((a, b) => a.sequence - b.sequence).map(item => <ActivityRow key={item.id} item={item} />)}</ol>
    </div>)}
  </section>;
}
