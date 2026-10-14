import RetrievalEvaluation from "./components/RetrievalEvaluation";
import { useEffect } from "react";
import Icon from "./components/Icon";
import "./retrievalEvaluationPage.css";

async function readEvaluation(path: string): Promise<Record<string, unknown>> {
  const base = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
  const url = new URL(`${base}/commerce${path}`, window.location.origin);
  url.searchParams.set("buyer_id", import.meta.env.VITE_BUYER_ID || "pao-coder");
  let token = import.meta.env.VITE_API_TOKEN;
  try { token ||= window.localStorage.getItem("globex.access-token") ?? undefined; } catch { /* 本机存储不可用时沿用构建配置。 */ }
  const response = await fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const payload: unknown = await response.json().catch(() => { throw new Error("评测服务返回格式无效，请检查只读服务是否启动。"); });
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? payload.detail : null;
    throw new Error(typeof detail === "string" ? detail : "暂时无法读取检索评测，请刷新重试。");
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("评测服务返回格式无效。");
  return payload as Record<string, unknown>;
}

export default function RetrievalEvaluationPage() {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = "Globex · 商品检索评测";
    return () => { document.title = previousTitle; };
  }, []);
  return <main className="evaluation-page">
    <header className="evaluation-page-header">
      <a className="evaluation-page-brand" href="/" aria-label="返回 Globex 选购"><Icon name="globe" /><span>Globex<span>检索评测工作台</span></span></a>
      <a className="evaluation-page-back" href="/"><Icon name="arrow" />返回选购</a>
    </header>
    <RetrievalEvaluation request={readEvaluation} />
  </main>;
}
