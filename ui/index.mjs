import { createElement as h, useEffect, useState } from "react";
import { useAppApi } from "@kirocrew/app-sdk";

const apiPath = "/apps/kiroween-night-watch/api";
const factionMetrics = [
  ["cowboy", "Cowboys"],
  ["wizard", "Wizards"],
  ["draw", "Draws"],
];
const seatMetrics = [["first", "First seat"], ["second", "Second seat"]];
const percent = (wins, count) => (100 * wins / count).toFixed(1) + "%";
const number = new Intl.NumberFormat();
const date = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const timestamp = (value) => date.format(new Date(value));
const sha = (run) => run?.commit?.slice(0, 8) || "—";

function difference(before, after, count) {
  const change = Number((100 * (after - before) / count).toFixed(1));
  return (change > 0 ? "+" : "") + change.toFixed(1) + " pp";
}

function RunStatus({ report }) {
  const label = { steady: "Within threshold", alert: "Balance alert", failed: "Check failed" }[report.state];
  return h("span", { className: "run-status " + report.state },
    h("span", { className: "status-dot", "aria-hidden": true }), label);
}

function Revision({ label, revision }) {
  return h("div", { className: "revision" },
    h("span", null, label),
    h("strong", { title: revision?.ref }, revision?.ref || "Unresolved"),
    h("code", { title: revision?.commit }, sha(revision)));
}

function OutcomeBar({ label, revision, count }) {
  return h("div", { className: "outcome-row" },
    h("span", { className: "outcome-label" }, label),
    h("div", { className: "outcome-track", "aria-hidden": true },
      factionMetrics.map(([key]) => h("span", {
        key,
        className: "portion " + key,
        style: { width: 100 * revision.counts[key] / count + "%" },
      }))));
}

function ComparisonTable({ metrics, report, caption }) {
  const { baseline, candidate, count } = report;
  return h("div", { className: "table-scroll" },
    h("table", { "aria-label": caption },
      h("thead", null, h("tr", null,
        h("th", { scope: "col" }, "Outcome"),
        h("th", { scope: "col" }, "Baseline"),
        h("th", { scope: "col" }, "Candidate"),
        h("th", { scope: "col" }, "Change"))),
      h("tbody", null, metrics.map(([key, name]) =>
        h("tr", { key },
          h("th", { scope: "row" },
            metrics === factionMetrics &&
              h("span", { className: "swatch " + key, "aria-hidden": true }),
            name),
          h("td", { title: number.format(baseline.counts[key]) + " matches" }, percent(baseline.counts[key], count)),
          h("td", { title: number.format(candidate.counts[key]) + " matches" }, percent(candidate.counts[key], count)),
          h("td", null, difference(baseline.counts[key], candidate.counts[key], count)))))));
}

function Report({ report }) {
  const ready = Boolean(report.baseline?.counts && report.candidate?.counts);
  const rulesMessage = report.rules?.state === "passed"
    ? (report.rules.tests ? number.format(report.rules.tests) + " rule tests passed" : "Rule tests passed")
    : report.rules?.state === "failed" ? "Rule tests failed"
      : report.rules?.state === "skipped" ? "Rule tests skipped" : "Rule tests unavailable";
  const changes = report.changes || [];
  return h("article", { className: "report", "aria-label": "Selected balance report" },
    h("div", { className: "run-info" },
      h(Revision, { label: "Baseline", revision: report.baseline }),
      h(Revision, { label: "Candidate", revision: report.candidate }),
      h("div", { className: "revision run-seeds" },
        h("span", null, "Identical seeds"),
        h("strong", null, number.format(report.count) + " per revision"),
        h("code", null, number.format(report.firstSeed) + "–" + number.format(report.firstSeed + report.count - 1)))),
    h("div", { className: "comparison-layout" },
      h("section", { className: "panel outcome-panel" },
        h("header", { className: "panel-heading" },
          h("h2", null, "Match outcomes"),
          h("span", { className: "quiet" }, "CPU vs. CPU")),
        ready
          ? h("div", null,
            h("div", { className: "outcome-bars" },
              h(OutcomeBar, { label: "Baseline", revision: report.baseline, count: report.count }),
              h(OutcomeBar, { label: "Candidate", revision: report.candidate, count: report.count })),
            h(ComparisonTable, { metrics: factionMetrics, report, caption: "Faction win share and draws" }))
          : h("p", { className: "panel-message" }, "The run stopped before both simulations completed.")),
      h("section", { className: "panel findings-panel", "aria-label": "Review findings" },
        h(RunStatus, { report }),
        h("p", { className: "finding" }, report.message),
        h("p", { className: "rule-status" }, rulesMessage),
        h("dl", { className: "run-metadata" },
          h("div", null, h("dt", null, "Alert threshold"), h("dd", null, report.threshold + " pp")),
          h("div", null, h("dt", null, "Trigger"), h("dd", null, report.source === "scheduled" ? "Scheduled" : "Manual")),
          h("div", null, h("dt", null, "Run time"), h("dd", null, h("time", { dateTime: report.at }, timestamp(report.at))))),
        report.issueUrl && h("a", { className: "action secondary", href: report.issueUrl, target: "_blank", rel: "noopener noreferrer" },
          "Open GitHub issue draft", h("span", { "aria-hidden": true }, "↗")))),
    report.detail && h("details", { className: "diagnostic panel" },
      h("summary", null, "Check output"), h("pre", null, report.detail)),
    ready && h("section", { className: "panel seat-panel" },
      h("header", { className: "panel-heading" }, h("h2", null, "First-seat advantage")),
      h(ComparisonTable, { metrics: seatMetrics, report, caption: "Win share by starting seat" })),
    changes.length > 0 && h("section", { className: "panel changes-panel" },
      h("header", { className: "panel-heading" }, h("h2", null, "Changed card specs")),
      h("ul", null, changes.map(({ card, field, old, new: next }) =>
        h("li", { key: card + field },
          h("strong", null, card),
          h("span", null, field),
          h("code", null, String(old), " → ", String(next)))))));
}

function History({ reports, current, select }) {
  if (reports.length < 2) return null;
  return h("section", { className: "panel history-panel" },
    h("header", { className: "panel-heading" }, h("h2", null, "Run history")),
    h("div", { className: "table-scroll" },
      h("table", { "aria-label": "Run history" },
        h("thead", null, h("tr", null,
          h("th", { scope: "col" }, "Run"),
          h("th", { scope: "col" }, "Candidate"),
          h("th", { scope: "col" }, "Trigger"),
          h("th", { scope: "col" }, "Result"))),
        h("tbody", null, reports.map((report) =>
          h("tr", { className: report.id === current ? "selected" : undefined, key: report.id },
            h("th", { scope: "row" }, h("button", { type: "button", className: "history-select",
              onClick: () => select(report.id), "aria-pressed": report.id === current },
            h("time", { dateTime: report.at }, timestamp(report.at)))),
            h("td", null, h("code", { title: report.candidate?.commit }, sha(report.candidate))),
            h("td", null, report.source === "scheduled" ? "Scheduled" : "Manual"),
            h("td", null, h(RunStatus, { report }))))))));
}

function Settings({ config, save, busy }) {
  const fields = [
    { key: "repo", label: "Duel checkout", className: "wide", placeholder: "/path/to/cowboys-vs-wizards" },
    { key: "baseline", label: "Baseline Git ref" },
    { key: "candidate", label: "Candidate Git ref" },
    { key: "count", label: "Matches per revision", min: 1, max: 10000 },
    { key: "firstSeed", label: "First seed", min: 0, max: 4294967295 },
    { key: "threshold", label: "Alert threshold (pp)", min: 1, max: 100, step: "0.1" },
  ];
  function submit(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    save(Object.fromEntries(fields.map(({ key, min }) =>
      [key, min === undefined ? String(data.get(key)).trim() : Number(data.get(key))])));
  }
  return h("section", { className: "settings panel", id: "night-watch-settings" },
    h("header", { className: "panel-heading" }, h("h2", null, "Comparison settings")),
    h("form", { onSubmit: submit },
      h("div", { className: "settings-fields" }, fields.map(({ key, label, min, max, step, className, placeholder }) =>
        h("label", { key, className },
          h("span", null, label),
          h("input", {
            name: key, defaultValue: config[key], placeholder, required: true,
            type: min === undefined ? "text" : "number", min, max, step,
          })))),
      h("p", { className: "settings-note quiet" }, "Night Watch runs the engine from commits in this local checkout."),
      h("button", { type: "submit", className: "action primary", disabled: Boolean(busy) },
        busy === "save" ? "Saving…" : "Save settings")));
}

export default function NightWatch() {
  const api = useAppApi();
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState("load");
  const [settings, setSettings] = useState(false);
  const [current, select] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!document.getElementById("night-watch-styles")) {
      const style = document.createElement("link");
      style.id = "night-watch-styles";
      style.rel = "stylesheet";
      style.href = new URL("./panel.css", import.meta.url).href;
      document.head.append(style);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    api.get(apiPath + "/state").then((data) => {
      if (!mounted) return;
      setState(data);
      setSettings(!data.config.repo);
      setBusy("");
    }).catch((failure) => {
      if (!mounted) return;
      setError(failure.message);
      setBusy("");
    });
    return () => { mounted = false; };
  }, [api, reload]);

  async function save(config) {
    setBusy("save");
    setError("");
    setNotice("");
    try {
      setState(await api.post(apiPath + "/config", config));
      setSettings(false);
      setNotice("Settings saved.");
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy("");
    }
  }

  async function run() {
    setBusy("run");
    setError("");
    setNotice("Checking both revisions on the same seeds…");
    try {
      const { report } = await api.post(apiPath + "/run", {});
      setState((data) => ({ ...data, reports: [report, ...data.reports].slice(0, 20) }));
      select(report.id);
      setNotice(report.state === "failed" ? "Run saved with check errors." : "Balance report saved.");
    } catch (failure) {
      setError(failure.message);
      setNotice("");
    } finally {
      setBusy("");
    }
  }

  const reports = state?.reports || [];
  const report = reports.find((item) => item.id === current) || reports[0];
  return h("main", { className: "night-watch" },
    h("div", { className: "watch-page" },
      h("header", { className: "watch-header" },
        h("div", null, h("h1", null, "Night Watch"), h("p", null, "Cowboys vs. Wizards balance reports")),
        state && h("div", { className: "watch-actions" },
          h("button", { type: "button", className: "action secondary", "aria-expanded": settings,
            "aria-controls": "night-watch-settings", onClick: () => setSettings((open) => !open) }, "Settings"),
          h("button", { type: "button", className: "action primary", disabled: Boolean(busy) || !state.config.repo,
            onClick: run }, busy === "run" ? "Checking…" : "Run balance check"))),
      h("div", { className: "feedback", role: "status" }, busy === "load" ? "Loading reports…" : notice),
      error && h("p", { role: "alert", className: "error-message" }, error),
      !state && error && h("button", { type: "button", className: "action secondary", onClick: () => {
        setError("");
        setBusy("load");
        setReload((value) => value + 1);
      } }, "Try again"),
      settings && state && h(Settings, { key: JSON.stringify(state.config), config: state.config, save, busy }),
      report && h(Report, { report }),
      state && !report && state.config.repo && h("section", { className: "panel empty" },
        h("h2", null, "No reports yet"),
        h("p", null, "Run a balance check to compare the saved revisions.")),
      h(History, { reports, current: report?.id, select })));
}
