// Council pass 1: one independent, read-only report per advisor.
// args: { brief: string, roster: [{ key, agent, context?, label, role }] }
// Launch with: subagent({ workflow: "<skill dir>/assets/pass1.js", args: {...}, async: true, cwd: "<repo>" })

const schema = {
  type: "object",
  required: [
    "recommendation",
    "evidence",
    "assumptions",
    "risks",
    "confidence",
    "challengeClaims",
    "ownerDecisions",
    "changeMyMind"
  ],
  properties: {
    recommendation: { type: "string" },
    evidence: {
      type: "array",
      items: {
        type: "object",
        required: ["claim", "sources"],
        properties: {
          claim: { type: "string" },
          sources: { type: "array", items: { type: "string" } }
        }
      }
    },
    assumptions: {
      type: "array",
      items: {
        type: "object",
        required: ["assumption", "status"],
        properties: {
          assumption: { type: "string" },
          status: { enum: ["verified", "unverified"] }
        }
      }
    },
    risks: { type: "array", items: { type: "string" } },
    confidence: {
      type: "object",
      required: ["level", "reason"],
      properties: {
        level: { enum: ["high", "medium", "low"] },
        reason: { type: "string" }
      }
    },
    challengeClaims: { type: "array", items: { type: "string" }, maxItems: 3 },
    ownerDecisions: { type: "array", items: { type: "string" } },
    changeMyMind: { type: "array", items: { type: "string" } }
  }
};

const roster = args.roster || [];
if (roster.length < 2) {
  throw new Error("council pass 1 needs at least two advisors");
}
if (roster.length > 4) {
  throw new Error("council pass 1 allows at most four advisors");
}

const results = await runs.all(
  roster.map((item) => ({
    key: item.key,
    agent: item.agent,
    ...(item.context ? { context: item.context } : {}),
    phase: "Council pass 1",
    label: item.label,
    output: false,
    outputSchema: schema,
    task: args.brief + "\n\nROLE: " + item.role
  }))
);

return {
  pass: 1,
  advisors: results.map((result, index) => ({
    key: result.key,
    agent: result.agent,
    requestedContext: roster[index].context ?? "runtime-default-unknown",
    runId: result.runId,
    ok: result.ok,
    report: result.structuredOutput ?? result.output
  }))
};
