export const meta = {
  name: "phase5-deploy",
  description:
    "P5 确定性部署流水线：三道确认门之后编排生产核实、DQC 布防（L2/L3 并行 dqc-deployer）、回刷（backfiller）、工单流转与 delivery_summary 证据",
  whenToUse:
    "Run by the /start flow at P5 after the three AskUserQuestion gates. args carry ticket, projectRoot, runMode, plan (deploy_plan.json items) and the confirmation tokens.",
  phases: [
    { title: "Verify", detail: "read-only production check of new columns / ETL online state" },
    { title: "Parallel", detail: "L2/L3: dqc-deployer + backfiller in parallel; L1: backfiller alone" },
    { title: "Transit", detail: "工单状态逐级推进到 待验收 (no stage jumps)" },
    { title: "Evidence", detail: "delivery_summary.md + deploy_log.json + advance" },
  ],
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
if (bare || Object.keys(parsed).length === 0) {
  log("phase5-deploy started with no plan -- refusing to guess");
  return {
    started: false,
    reason: "no-plan",
    next: "先生成 deploy_plan.json 并完成三道 AskUserQuestion 确认（发布/ALTER/回刷），再调用本 workflow。",
  };
}

const ticket = String(parsed.ticket || "").trim();
const projectRoot = String(parsed.projectRoot || parsed.project_root || "").trim();
const runMode = parsed.runMode === "autopilot" ? "autopilot" : "interactive";
const plan = parsed.plan || {};
const items = Array.isArray(plan.items) ? plan.items : [];
const confirms = parsed.confirmations || {};

if (!ticket || !projectRoot || items.length === 0) {
  throw new Error("phase5-deploy.js requires ticket, projectRoot and a non-empty plan");
}

const gates = {
  publish: String(confirms.publish || "").includes("[已确认发布]"),
  alter: String(confirms.alter || "").includes("[已确认发布]"),
  backfill: String(confirms.backfill || "").includes("[已确认发布]"),
};
if (runMode === "autopilot") {
  log(`phase5-deploy autopilot: 三道不可逆门仍需人工确认 token（publish=${gates.publish}, alter=${gates.alter}, backfill=${gates.backfill}）`);
} else {
  log(`phase5-deploy interactive: 确认 token 齐备（${JSON.stringify(gates)}）`);
}
if (!gates.publish || (items.some((i) => i.action === "alter") && !gates.alter) || (items.some((i) => i.action === "backfill") && !gates.backfill)) {
  return {
    started: false,
    reason: "confirmation-missing",
    gates,
    next: "不可逆门未全部确认：autopilot 也不旁路。请逐项 AskUserQuestion 并取回含 [已确认发布] 的确认记录。",
  };
}

log(`phase5-deploy: ticket ${ticket}, mode ${runMode}, ${items.length} item(s)`);

const jobs = [];
for (const item of items) {
  if (item.action === "dqc_publish") {
    jobs.push(() =>
      agent(
        `在项目 ${projectRoot} 对生产表 ${item.table} 部署 DQC 规则（主键唯一/非空/行数波动/口径校验），` +
          `返回 {table, rules, deployed, evidence}。未部署成功必须 deployed=false。`,
        {
          label: `dqc:${item.table}`,
          phase: "Parallel",
          agentType: "data-development-plugin:dqc-deployer",
          schema: {
            type: "object",
            required: ["table", "rules", "deployed"],
            properties: {
              table: { type: "string" },
              rules: { type: "array", items: { type: "string" } },
              deployed: { type: "boolean" },
              evidence: { type: "string" },
            },
          },
        }
      )
    );
  }
  if (item.action === "backfill") {
    jobs.push(() =>
      agent(
        `在项目 ${projectRoot} 对表 ${item.table} 回刷 ${item.range_start || "?"}~${item.range_end || "?"}（三阶段顺序，禁止 SUPPLEMENT），` +
          `返回 {task_name, range_start, range_end, run_id, status}。run_id 为空即失败。`,
        {
          label: `backfill:${item.table}`,
          phase: "Parallel",
          agentType: "data-development-plugin:backfiller",
          schema: {
            type: "object",
            required: ["task_name", "range_start", "range_end", "run_id", "status"],
            properties: {
              task_name: { type: "string" },
              range_start: { type: "string" },
              range_end: { type: "string" },
              run_id: { type: "string", minLength: 1 },
              status: { type: "string", enum: ["SUCCESS", "FAILED"] },
            },
          },
        }
      )
    );
  }
}

const parallel_evidence = (await parallel(jobs)).filter(Boolean);
const dqc_results = parallel_evidence.filter((r) => r && typeof r.table !== "undefined");
const backfill_results = parallel_evidence.filter((r) => r && typeof r.task_name !== "undefined");

return {
  started: true,
  ticket,
  runMode,
  gates,
  dqc_results,
  backfill_results,
  next:
    "发布/DDL 已通过引擎 deploy 命令执行。若本 workflow 内 dqc/backfill 有 FAILED，禁止 advance：" +
    "修复后重跑。全部成功后：把 dqc_results 原样写入 <req_dir>/dqc_results.json" +
    "（P5 出口会对 deploy_plan 里每个 dqc_publish 项反查 deployed=true；回刷由引擎写 " +
    "runtime/backfill_runs.json 账本，出口按 table|start|end 反查成功记录），" +
    "再写 delivery_summary.md（>=10 行）与 deploy_log.json，工单逐级流转到待验收，" +
    "最后调用引擎 advance --phase 6。",
};
