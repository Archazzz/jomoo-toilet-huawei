import { mountReport } from "./report.js";
const root = document.querySelector("#report");
const selector = document.querySelector("#device");
const scenario = document.querySelector("#scenario");
let cleanup,
  generation = 0;
async function json(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw Error("report unavailable");
  return response.json();
}
async function showReport() {
  const ticket = ++generation;
  cleanup?.();
  const device = selector.value;
  const host = {
    getContext: () => json(`/api/context?device=${encodeURIComponent(device)}`),
    async readReport(query) {
      if (scenario.value === "error") throw Error("demo error");
      if (scenario.value === "slow")
        await new Promise((resolve) => setTimeout(resolve, 2500));
      return json(
        `/api/report?${new URLSearchParams({ ...query, scenario: scenario.value })}`,
      );
    },
    back: showControl,
  };
  const dispose = mountReport(root, host);
  if (ticket === generation) cleanup = dispose;
}
function showControl() {
  ++generation;
  cleanup?.();
  root.innerHTML =
    '<div class="topbar">设备卡片原型</div><div class="control-preview"><svg viewBox="0 0 120 120" fill="none" stroke="currentColor" stroke-width="3" aria-hidden="true"><rect x="27" y="12" width="66" height="42" rx="18"/><path d="M27 53h66v18c0 20-12 26-27 27v14H42V96c-10-5-15-15-15-25Z"/><ellipse cx="60" cy="62" rx="33" ry="10"/></svg><h1>九牧马桶</h1><p>这是报告入口的位置预览。实际控制仍使用智慧生活中的原卡片。</p></div><button class="report-entry"><div>使用报告<span>冲水、落座与清洗记录</span></div>›</button>';
  root.querySelector(".report-entry").onclick = showReport;
}
selector.onchange = showReport;
scenario.onchange = showReport;
showReport();
