export const meta = {
  name: "phase4-test",
  description:
    "P4 机器证据测试流水线：并行调度 data-tester agents，schema 强制每个用例返回非空 query_id；汇总 test_runs.json 证据并写 test_report.md（✅ 只标有 SUCCESS+query_id 的用例）",
  whenToUse:
    "Run by the /start flow at P4 (or by copilot for a single-case re-test). args carry ticket, projectRoot, cases (test plan rows). A bare invocation with no cases must not invent tests.",
  phases: [
    { title: "Dispatch", detail: "one data-tester per test case, parallel" },
    { title: "Validate", detail: "reject any result without a non-empty query_id" },
    { title: "Aggregate", detail: "build test_runs.json payload + report rows, return to caller" },
  ],
};

const caseSchema = {
  type: "object",
  required: ["test_id", "description", "query_id", "status"],
  properties: {
    test_id: { type: "string" },
    description: { type: "string" },
    query_id: { type: "string", minLength: 1, description: "真实执行返回的 query_id，禁止空串/占位" },
    status: { type: "string", enum: ["SUCCESS", "FAILED"] },
    sql_file: { type: "string" },
    duration_s: { type: "number" },
  },
};

let parsed = args;
let bare = false;
if (typeof parsed === "string") {
  try {
    parsed = JSON.parse(parsed);
  } catch {
    parsed = {};
    bare = true;
  }
}
parsed = parsed || {};
const cases = Array.isArray(parsed.cases) ? parsed.cases : [];
if (bare || Object.keys(parsed).length === 0 || cases.length === 0) {
  log("phase4-test started with no cases -- nothing to test; do not improvise");
  return {
    started: false,
    reason: "no-cases",
    next: "先从 data-testing skill 产出测试计划（含 test_id 列表），再带 cases 调用本 workflow。禁止凭空造测试。",
  };
}

const ticket = String(parsed.ticket || "").trim();
const projectRoot = String(parsed.projectRoot || parsed.project_root || "").trim();
if (!ticket || !projectRoot) {
  throw new Error("phase4-test.js requires ticket and projectRoot in args");
}

log(`phase4-test: ${cases.length} case(s), ticket ${ticket}`);

const dispatch = cases.map((tc) => () =>
  agent(
    `在项目 ${projectRoot}、工单 ${ticket} 的 P4 测试中执行用例 ${tc.test_id}（${tc.description || ""}）。` +
      `用只读/开发环境 SQL 真实执行并拿到 query_id（必须经项目引擎执行：` +
      `py -3 .data-dev/engine/ddp.py platform run-sql ...，让 query_id 进入 executions.json 账本）；` +
      `执行失败就返回 FAILED + 原因。` +
      `你只返回结构化结果，不写报告文件。`,
    {
      label: `test:${tc.test_id}`,
      phase: "Dispatch",
      agentType: "data-development-plugin:data-tester",
      schema: caseSchema,
    }
  )
);

const results = (await parallel(dispatch)).filter(Boolean);
log(`phase4-test: ${results.length}/${cases.length} tester(s) returned`);

const runs = results.map((r) => {
  const queryId = String((r && r.query_id) || "").trim();
  if (!queryId) {
    // schema violation -> downgrade to a visible FAILED record so the report
    // can never mark this case ✅ (the validator cross-checks test_runs.json)
    return {
      test_id: String((r && r.test_id) || "TC-UNKNOWN"),
      description: String((r && r.description) || ""),
      query_id: "",
      status: "FAILED",
      note: "schema 强制：无 query_id 视为未执行",
    };
  }
  return {
    test_id: String(r.test_id),
    description: String(r.description || ""),
    query_id: queryId,
    status: r.status === "FAILED" ? "FAILED" : "SUCCESS",
    sql_file: String(r.sql_file || ""),
    duration_s: Number(r.duration_s || 0),
  };
});

const success = runs.filter((r) => r.status === "SUCCESS").length;
const failed = runs.length - success;

return {
  started: true,
  ticket,
  runs,
  summary: { total: runs.length, success, failed },
  report_rows: runs.map((r) => ({
    test_id: r.test_id,
    description: r.description,
    marker: r.status === "SUCCESS" ? "✅ PASS" : "❌ FAIL",
    query_id: r.query_id || "（无）",
  })),
  next:
    "把本返回的 runs 原样写入 <req_dir>/test_runs.json；report_rows 中仅 SUCCESS 行写 ✅。" +
    "然后调用引擎 advance --phase 5（P4 出口会反查 test_runs.json）。",
};
