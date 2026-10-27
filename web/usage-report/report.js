const $ = (root, selector) => root.querySelector(selector);
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const formatNumber = (value) =>
  value === null || value === undefined ? "—" : value;
const shiftDate = (day, delta) =>
  new Date(Date.parse(`${day}T00:00:00Z`) + delta * 86400000)
    .toISOString()
    .slice(0, 10);
const localDay = (ts) =>
  new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(ts * 1000));
const timeLabel = (ts) =>
  new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(ts * 1000));
const modes = {
  hip: "臀洗",
  bidet: "妇洗",
  assist: "助便",
  dry: "暖风",
  nozzle_clean: "喷嘴清洁",
};

export function mountReport(root, host) {
  let metric = "flush";
  let context,
    period = 7,
    anchor,
    selected,
    response,
    sequence = 0,
    disposed = false;
  const dispose = () => {
    disposed = true;
    sequence++;
    root.replaceChildren();
  };
  const loading = () => {
    root.innerHTML =
      '<div class="loading" role="status"><span class="spinner"></span>正在读取使用报告</div>';
  };
  function failure(message) {
    if (disposed) return;
    root.innerHTML = `<div class="topbar"><button class="back" aria-label="返回设备">‹</button>使用报告</div><section class="section empty" role="alert"><strong>暂时无法读取报告</strong><p>${escapeHTML(message)}</p><br><button class="primary" id="retry">重新加载</button></section>`;
    $(root, ".back").onclick = () => host.back?.();
    $(root, "#retry").onclick = () => (context ? load() : initialize());
  }
  async function initialize() {
    loading();
    try {
      context = await host.getContext();
      if (disposed) return;
      if (!context.device || !/^\d{4}-\d{2}-\d{2}$/.test(context.today))
        throw Error("设备上下文无效");
      anchor = context.today;
      await load();
    } catch {
      failure("请检查网关连接，稍后重试。");
    }
  }
  async function load() {
    if (disposed) return;
    let timeout;
    const ticket = ++sequence;
    loading();
    try {
      const data = await Promise.race([
        host.readReport({
          device: context.device,
          start: shiftDate(anchor, 1 - period),
          days: period,
        }),
        new Promise((_, reject) => {
          timeout = setTimeout(() => reject(Error("timeout")), 15000);
        }),
      ]);
      if (disposed || ticket !== sequence) return;
      if (
        data.schema_version !== 1 ||
        data.device !== context.device ||
        data.days !== period ||
        data.start !== shiftDate(anchor, 1 - period) ||
        !Array.isArray(data.daily) ||
        data.daily.length !== period
      )
        throw Error("报告范围不匹配");
      const metrics = [
        "flush_large",
        "flush_small",
        "foam",
        "seated",
        "seated_hip",
      ];
      for (const row of [data.totals, ...data.daily]) {
        if (
          metrics.some(
            (key) =>
              row[key] !== null &&
              (!Number.isInteger(row[key]) || row[key] < 0),
          )
        )
          throw Error("invalid count");
      }
      if (!Array.isArray(data.sessions)) throw Error("invalid sessions");
      for (const session of data.sessions) {
        if (
          ![
            session.start,
            session.last_seen,
            session.duration_seconds_estimate,
            session.end ?? 0,
          ].every(Number.isFinite) ||
          !Array.isArray(session.modes)
        )
          throw Error("invalid session");
      }
      if (
        data.daily.some(
          (day, index) => day.date !== shiftDate(data.start, index),
        ) ||
        !Number.isInteger(data.collection.retention_days) ||
        !Number.isInteger(data.collection.observed_days) ||
        (data.collection.last_observed_at !== null &&
          !Number.isFinite(data.collection.last_observed_at))
      ) {
        throw Error("invalid report metadata");
      }
      response = data;
      selected = anchor;
      render();
    } catch {
      if (ticket === sequence)
        failure("报告服务暂不可用，已有记录仍保留在网关中。");
    } finally {
      clearTimeout(timeout);
    }
  }
  function render() {
    const r = response,
      t = r.totals;
    const flushes =
      t.flush_large === null ? null : t.flush_large + t.flush_small;
    const latest = r.collection.last_observed_at;
    root.innerHTML = `
      <header class="topbar"><button class="back" aria-label="返回设备">‹</button><span class="room">${escapeHTML(context.name)}</span><button id="refresh" class="icon-button" aria-label="刷新报告">↻</button></header>
      <div class="heading"><div><p class="eyebrow">日常点滴 · 清晰记录</p><h1>使用报告</h1></div>${context.demo ? '<span class="demo-pill">演示数据</span>' : ""}</div>
      <nav class="segments" aria-label="报告时间范围">${[
        [1, "日"],
        [7, "周"],
        [30, "月"],
      ]
        .map(
          ([days, label]) =>
            `<button data-period="${days}" aria-pressed="${period === days}">${label}</button>`,
        )
        .join("")}</nav>
      <div class="date-nav"><button id="previous" aria-label="上一时段">‹</button><div class="date-center"><label class="date-label" for="anchor">${period === 1 ? "查看日期" : `截至日期 · 最近 ${period} 天`}</label><input id="anchor" type="date" value="${anchor}" max="${context.today}" aria-label="报告截至日期"></div><button id="next" aria-label="下一时段" ${anchor >= context.today ? "disabled" : ""}>›</button></div>
      <p class="status ${r.collection.stale ? "warning" : ""}"><i></i>${r.collection.stale ? "采集暂未更新" : "周期采样记录"}${latest ? ` · 最近采样 ${escapeHTML(localDay(latest))} ${timeLabel(latest)}` : " · 还没有采样数据"}</p>
      <div class="report-grid">
        <section class="section summary"><div class="kicker"><span>已记录冲水</span><span>${period === 1 ? "当日" : `近 ${period} 天`}</span></div><div class="hero-number">${formatNumber(flushes)}<span>次</span></div><p class="breakdown">大冲 ${formatNumber(t.flush_large)} 次 <span aria-hidden="true"> · </span> 小冲 ${formatNumber(t.flush_small)} 次</p><dl class="metric-grid"><div><dt>已记录落座</dt><dd>${formatNumber(t.seated)}<small>次</small></dd><p>按观察到的落座起点</p></div><div><dt>落座并使用臀洗</dt><dd>${formatNumber(t.seated_hip)}<small>次</small></dd><p>每次落座最多记一次</p></div></dl></section>
        <section class="section"><div class="section-head"><h2>使用趋势</h2><small>次</small></div><nav class="metric-tabs" aria-label="趋势指标"><button data-metric="flush" aria-pressed="${metric === "flush"}">冲水</button><button data-metric="seated" aria-pressed="${metric === "seated"}">落座</button></nav><div id="legend" class="legend"></div><div id="chart"></div></section>
      </div>
      <section class="section"><div class="section-head"><h2>使用明细</h2><small id="detail-date"></small></div><p class="subheading">选择图中的日期，查看当天的落座记录</p><div id="session-list" class="session-list"></div></section>
      <section class="section"><div class="section-head"><h2>记录说明</h2><small>${r.collection.observed_days} / ${period} 天有采样</small></div><p class="explain">统计可能漏掉两次采样之间的短动作。数据按设备记录，不区分使用者。</p><details class="explain"><summary>这些数字是怎么来的？</summary><p>冲水按真实运行状态从关闭变为开启计数，点击按钮不直接计数。落座并使用臀洗是同一次落座内观察到臀洗，不能等同于排便次数。</p><p>次数按首次观察到的日期归属，跨午夜的落座时长按日拆分。时长为采样估算；重启、断连或长时间未读取会标记记录不完整，不填补未知时段。</p><p>没有采样的日期显示“—”，有采样但未观察到动作时才显示“0”。滚动保留 ${r.collection.retention_days} 天记录，时间为北京时间。</p></details></section>
      <p class="footer">记录日常使用，不生成健康评分</p>`;
    $(root, ".back").onclick = () => host.back?.();
    $(root, "#refresh").onclick = load;
    root.querySelectorAll("[data-period]").forEach((button) => {
      button.onclick = () => {
        period = Number(button.dataset.period);
        load();
      };
    });
    $(root, "#previous").onclick = () => {
      anchor = shiftDate(anchor, -period);
      load();
    };
    $(root, "#next").onclick = () => {
      anchor = shiftDate(anchor, period);
      if (anchor > context.today) anchor = context.today;
      load();
    };
    $(root, "#anchor").onchange = (event) => {
      if (event.target.value && event.target.value <= context.today) {
        anchor = event.target.value;
        load();
      }
    };
    root.querySelectorAll("[data-metric]").forEach((button) => {
      button.onclick = () => {
        metric = button.dataset.metric;
        root
          .querySelectorAll("[data-metric]")
          .forEach((b) =>
            b.setAttribute("aria-pressed", String(b.dataset.metric === metric)),
          );
        drawChart();
      };
    });
    drawChart();
    details();
  }
  function drawChart() {
    const rows = response.daily;
    const pair = (d) =>
      metric === "flush"
        ? [d.flush_large || 0, d.flush_small || 0]
        : [(d.seated || 0) - (d.seated_hip || 0), d.seated_hip || 0];
    const rawMax = Math.max(
      4,
      ...rows.map((d) => pair(d).reduce((a, b) => a + b, 0)),
    );
    const max = Math.ceil(rawMax / 2) * 2;
    $(root, "#legend").innerHTML =
      metric === "flush"
        ? '<span><i class="dot"></i>大冲</span><span><i class="dot light"></i>小冲</span>'
        : '<span><i class="dot"></i>未记录臀洗</span><span><i class="dot light"></i>使用臀洗</span>';
    $(root, "#chart").innerHTML =
      `<div class="chart-scroll"><div class="chart-wrap ${period > 7 ? "month-chart" : ""}"><div class="chart-grid" aria-hidden="true"><div>${max}</div><div>${Math.round(max / 2)}</div><div>0</div></div><div class="bars">${rows
        .map((d, index) => {
          const [a, b] = pair(d),
            total = a + b,
            height = (total / max) * 166;
          const label = d.observed
            ? metric === "flush"
              ? `大冲 ${a} 次，小冲 ${b} 次`
              : `落座 ${total} 次，其中使用臀洗 ${b} 次`
            : "没有采样";
          return `<button class="bar-day ${d.observed ? "" : "missing"} ${d.date === selected ? "selected" : ""}" data-day="${d.date}" aria-label="${d.date}，${label}" aria-pressed="${d.date === selected}"><span class="bar-stack" style="height:${height}px"><span class="bar-large" style="height:${total ? (a / total) * 100 : 0}%"></span><span class="bar-small" style="height:${total ? (b / total) * 100 : 0}%"></span></span><span class="bar-date">${period < 10 || index % 5 === 0 || index === rows.length - 1 ? d.date.slice(8) : ""}</span></button>`;
        })
        .join(
          "",
        )}</div></div></div><div class="chart-caption"><strong id="selected-count"></strong><span>斜纹表示未采集</span></div>`;
    root.querySelectorAll("[data-day]").forEach((button) => {
      button.onclick = () => {
        selected = button.dataset.day;
        drawChart();
        details();
      };
    });
    if (period > 7) {
      const scroller = $(root, ".chart-scroll");
      const selectedBar = $(root, ".bar-day.selected");
      scroller.scrollLeft = Math.max(
        0,
        selectedBar.offsetLeft - scroller.clientWidth / 2,
      );
    }
    const d = rows.find((d) => d.date === selected);
    $(root, "#selected-count").textContent =
      `${selected.slice(5).replace("-", "月")}日 · ${d.observed ? `${pair(d).reduce((a, b) => a + b, 0)} 次${metric === "flush" ? "冲水" : "落座"}` : "未采集"}`;
  }
  function details() {
    $(root, "#detail-date").textContent = selected;
    const day = response.daily.find((d) => d.date === selected);
    // Include sessions overlapping the selected day; their start may be yesterday.
    const dayStart = Date.parse(`${selected}T00:00:00+08:00`) / 1000;
    const sessions = response.sessions.filter(
      (s) => s.start < dayStart + 86400 && (s.end ?? s.last_seen) >= dayStart,
    );
    const note = day.interrupted
      ? '<p class="notice">这一天有采集中断，部分记录可能不完整。</p>'
      : "";
    $(root, "#session-list").innerHTML =
      note +
      (sessions.length
        ? sessions
            .map((s) => {
              const duration = s.duration_seconds_estimate;
              const label =
                duration < 60
                  ? "不足 1 分钟"
                  : `${Math.round(duration / 60)} 分钟`;
              const datePrefix =
                localDay(s.start) !== selected
                  ? `${localDay(s.start).slice(5)} `
                  : "";
              return `<article class="session"><time>${timeLabel(s.start)}</time><div class="detail">${s.known_start ? "落座记录" : "起点未采到"}<p class="tags">${escapeHTML(s.modes.map((m) => modes[m] || m).join(" · ") || "未观察到清洗")} ${datePrefix ? "· 跨日" : ""}</p><p class="tags">${datePrefix ? escapeHTML(datePrefix) : ""}${timeLabel(s.start)} — ${s.ongoing ? "最近 " + timeLabel(s.last_seen) : timeLabel(s.end)}</p></div><div class="duration">约 ${label}<small>${s.ongoing ? "结束未确认" : s.complete ? "采样估算" : "记录不完整"}</small></div></article>`;
            })
            .join("")
        : `<div class="empty"><strong>${day.observed ? "未记录到落座" : "这一天还没有记录"}</strong><p>${day.observed ? "已有采样，但没有观察到落座会话。" : "采集开启后，记录会显示在这里。"}</p></div>`);
  }
  void initialize();
  return dispose;
}
