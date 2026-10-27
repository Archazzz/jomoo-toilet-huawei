/* Browser regression tests use synthetic data only; no gateway or Bluetooth access. */
const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs/promises");
const { chromium } = require("playwright");

async function main() {
  const port = Number(process.env.REPORT_TEST_PORT || 18766);
  const origin = `http://127.0.0.1:${port}`;
  const server = spawn(
    process.env.PYTHON_BIN || "python3",
    ["tools/preview_usage_report.py", "--port", String(port)],
    {
      env: { ...process.env, PYTHONPATH: "src" },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let output = "";
  server.stdout.on("data", (b) => {
    output += b;
  });
  server.stderr.on("data", (b) => {
    output += b;
  });
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 50; i++) {
      if (server.exitCode !== null) throw Error(output);
      try {
        if ((await fetch(`${origin}/api/context`)).ok) {
          ready = true;
          break;
        }
      } catch {}
      await new Promise((r) => setTimeout(r, 100));
    }
    assert(ready, "preview server did not start");
    browser = await chromium.launch({
      headless: true,
      ...(process.env.REPORT_CHROME ? { channel: "chrome" } : {}),
    });
    const page = await browser.newPage({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 1,
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    const requests = [];
    page.on("request", (r) => requests.push(r));
    await page.goto(origin);
    await page.locator(".hero-number").waitFor();
    assert.match(await page.locator(".hero-number").innerText(), /44/);
    assert.equal(await page.locator(".session").count(), 2);
    await page.getByRole("button", { name: "落座", exact: true }).click();
    assert.match(await page.locator("#selected-count").innerText(), /2 次落座/);
    await page.getByRole("button", { name: "月", exact: true }).click();
    await page.locator("[data-day]").last().waitFor();
    assert.equal(await page.locator("[data-day]").count(), 30);
    assert.equal(await page.locator(".missing").count(), 23);
    await page.getByRole("button", { name: "日", exact: true }).click();
    await page.locator(".hero-number").waitFor();
    assert.match(await page.locator(".hero-number").innerText(), /5/);
    await page.getByRole("button", { name: "上一时段" }).click();
    await page.waitForFunction(
      () =>
        document.querySelector("#detail-date")?.textContent === "2026-10-26",
    );
    assert.match(await page.locator(".hero-number").innerText(), /9/);
    await page.selectOption("#device", "guest");
    await page.waitForFunction(
      () => document.querySelector(".room")?.textContent === "客卫九牧马桶",
    );
    assert.match(await page.locator(".hero-number").innerText(), /23/);
    await page.selectOption("#scenario", "empty");
    await page.getByText("这一天还没有记录").waitFor();
    assert.match(await page.locator(".hero-number").innerText(), /—/);
    await page.selectOption("#scenario", "error");
    await page.getByRole("alert").waitFor();
    await page.selectOption("#scenario", "normal");
    await page.locator(".hero-number").waitFor();
    // A late response from another device must not overwrite the currently mounted card.
    await page.selectOption("#scenario", "slow");
    await page.getByRole("status").waitFor();
    await page.selectOption("#device", "main");
    await page.selectOption("#scenario", "normal");
    await page.waitForFunction(
      () => document.querySelector(".room")?.textContent === "主卫九牧马桶",
    );
    await page.waitForTimeout(2700);
    assert.equal(await page.locator(".room").innerText(), "主卫九牧马桶");
    // Foreign-device responses fail closed.
    await page.route("**/api/report?**", async (route) => {
      const response = await route.fetch();
      const json = await response.json();
      json.device = "foreign";
      await route.fulfill({ response, json });
    });
    await page.getByRole("button", { name: "刷新报告" }).click();
    await page.getByRole("alert").waitFor();
    await page.unroute("**/api/report?**");
    await page.getByRole("button", { name: "重新加载" }).click();
    await page.locator(".hero-number").waitFor();
    await page.getByRole("button", { name: "返回设备" }).click();
    await page
      .getByRole("button", { name: "使用报告 冲水、落座与清洗记录" })
      .click();
    await page.locator(".hero-number").waitFor();
    const screenshotDir = process.env.REPORT_SCREENSHOTS || "work/report-qa";
    await fs.mkdir(screenshotDir, { recursive: true });
    for (const [width, theme] of [
      [320, "light"],
      [390, "light"],
      [390, "dark"],
      [1000, "light"],
    ]) {
      await page.setViewportSize({ width, height: 844 });
      await page.emulateMedia({ colorScheme: theme });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        true,
        `overflow at ${width}`,
      );
      await page.screenshot({
        path: `${screenshotDir}/report-${width}-${theme}.png`,
        fullPage: true,
      });
    }
    assert.deepEqual(errors, [], "browser errors");
    assert(
      requests.every((r) => r.method() === "GET" && r.url().startsWith(origin)),
      "unexpected mutation/external request",
    );
    await page.goto(`${origin}/index.html`);
    await page.getByText("等待设备卡片连接报告服务").waitFor();
    assert.equal(
      await page.locator(".hero-number").count(),
      0,
      "native package must not show demo data",
    );
    console.log(
      "Report UI passed: navigation, metrics, rooms, missing/error/loading, stale-response isolation, read-only network, mobile/dark/desktop layouts.",
    );
  } finally {
    await browser?.close();
    server.kill("SIGTERM");
    await new Promise((resolve) =>
      server.exitCode !== null ? resolve() : server.once("exit", resolve),
    );
  }
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
