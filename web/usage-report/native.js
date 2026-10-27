import { mountReport } from "./report.js";

// Integration seam owned by this project. This is NOT a Huawei JSAPI.
// The approved product/plugin adapter must provide the authenticated device scope.
const root = document.querySelector("#report");
let mounted = false;
function attach() {
  if (mounted || !window.jomooReportHost) return;
  mounted = true;
  mountReport(root, window.jomooReportHost);
}
root.innerHTML =
  '<div class="loading"><h1>使用报告</h1><p>等待设备卡片连接报告服务</p></div>';
window.addEventListener("jomoo-report-ready", attach, { once: true });
attach();
