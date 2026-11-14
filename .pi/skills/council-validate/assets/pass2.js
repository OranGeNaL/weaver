// Council pass 2: one curated cross-exam per advisor, resumed from its pass-1 run.
// args: { packet: string, targets: [{ key, runId, label }] }
// Launch with: subagent({ workflow: "<skill dir>/assets/pass2.js", args: {...}, async: true, cwd: "<repo>" })

const schema = {
  type: "object",
  required: ["responses", "recommendationChanged", "outOfScopeFindings"],
  properties: {
    responses: {
      type: "array",
      items: {
        type: "object",
        required: ["claimId", "disposition", "reason", "sources"],
        properties: {
          claimId: { type: "string" },
          disposition: { enum: ["accept", "reject", "refine", "owner-decision"] },
          reason: { type: "string" },
          sources: { type: "array", items: { type: "string" } }
        }
      }
    },
    recommendationChanged: {
      type: "object",
      required: ["changed", "reason"],
      properties: { changed: { type: "boolean" }, reason: { type: "string" } }
    },
    outOfScopeFindings: { type: "array", items: { type: "string" } }
  }
};

const targets = args.targets || [];
if (targets.length < 2) {
  throw new Error("council pass 2 needs at least two resumed advisors");
}
if (targets.length > 4) {
  throw new Error("council pass 2 allows at most four advisors");
}

const results = await runs.all(
  targets.map((item) => ({
    key: item.key,
    resume: item.runId,
    phase: "Council pass 2",
    label: item.label,
    output: false,
    outputSchema: schema,
    task: args.packet
  }))
);

return {
  pass: 2,
  advisors: results.map((result, index) => ({
    key: result.key,
    agent: result.agent,
    resumedFrom: targets[index].runId,
    runId: result.runId,
    ok: result.ok,
    report: result.structuredOutput ?? result.output
  }))
};
