import fs from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [casesPath, truthPath, auditPath, outputPath] = process.argv.slice(2);
if (!casesPath || !truthPath || !auditPath || !outputPath) {
  throw new Error(
    "Usage: node build_e4d_review_workbook.mjs <cases.jsonl> <truth.jsonl> <audit.json> <output.xlsx>",
  );
}

const previewDir = process.env.E4D_PREVIEW_DIR;
const previewSheet = process.env.E4D_PREVIEW_SHEET;
const sheetNames = ["Instructions", "Cases", "Coverage", "Codebook"];
if (previewDir && !previewSheet) {
  throw new Error("E4D_PREVIEW_SHEET is required when E4D_PREVIEW_DIR is set");
}
if (previewDir && !sheetNames.includes(previewSheet)) {
  throw new Error(`E4D_PREVIEW_SHEET must be one of: ${sheetNames.join(", ")}`);
}
if (previewDir && process.env.SKIA_CANVAS_THREADS !== "1") {
  throw new Error("SKIA_CANVAS_THREADS=1 is required when E4D_PREVIEW_DIR is set");
}

const palette = {
  ink: "#1D1D1F",
  muted: "#6E6E73",
  border: "#D2D2D7",
  panel: "#F5F5F7",
  blue: "#0071E3",
  green: "#1E7D34",
  amber: "#9A6700",
  red: "#B42318",
  white: "#FFFFFF",
};

function readJsonl(contents) {
  return contents.toString("utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

function compactJson(value) {
  return JSON.stringify(value ?? {});
}

function columnName(columnNumber) {
  let value = columnNumber;
  let name = "";
  while (value > 0) {
    value -= 1;
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26);
  }
  return name;
}

function applyTitle(sheet, range, title) {
  sheet.getRange(range).merge();
  sheet.getRange(range).values = [[title]];
  sheet.getRange(range).format = {
    fill: palette.ink,
    font: { name: "Aptos Display", bold: true, color: palette.white, size: 18 },
    verticalAlignment: "center",
  };
}

function applyHeader(range) {
  range.format = {
    fill: palette.panel,
    font: { name: "Aptos", bold: true, color: palette.ink, size: 10 },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "doubleBottom", style: "thin", color: palette.border },
  };
}

function candidateMap(caseRow) {
  const candidates = caseRow.candidate_set?.candidates;
  if (!Array.isArray(candidates) || candidates.length !== 3) {
    throw new Error(`Case ${caseRow.case_id} must provide exactly three candidates`);
  }
  const byId = new Map(candidates.map((candidate) => [candidate.candidate_id, candidate]));
  for (const id of ["material", "process", "unified"]) {
    if (!byId.has(id)) throw new Error(`Case ${caseRow.case_id} is missing ${id} candidate`);
  }
  return byId;
}

function executionMap(truthRow) {
  const executions = truthRow.executions;
  if (!Array.isArray(executions) || executions.length !== 3) {
    throw new Error(`Truth ${truthRow.case_id} must provide exactly three executions`);
  }
  const byId = new Map(executions.map((execution) => [execution.candidate_id, execution]));
  for (const id of ["material", "process", "unified"]) {
    if (!byId.has(id)) throw new Error(`Truth ${truthRow.case_id} is missing ${id} execution`);
  }
  return byId;
}

function truthSummary(truthRow, execution) {
  return compactJson({
    status: execution.status,
    summary: execution.summary,
    atom_count: execution.atom_ids?.length ?? 0,
    result_row_count: execution.rows?.length ?? 0,
    trace_row_count: execution.trace_rows?.length ?? 0,
    decision_signature: truthRow.decision_signatures?.[execution.candidate_id] ?? null,
  });
}

function classCoverage(caseRow, execution) {
  if (caseRow.grounding_mode !== "natural_class") return "not applicable";
  const groundedIds = caseRow.grounded_component_ids;
  if (!Array.isArray(groundedIds)) {
    throw new Error(`Natural-class case ${caseRow.case_id} must provide grounded_component_ids`);
  }
  const groundedComponents = new Set(groundedIds.map((globalId) => {
    if (typeof globalId !== "string" || !globalId || /[:\s]/.test(globalId)) {
      throw new Error(`Grounded component ID for ${caseRow.case_id} must be a non-empty GlobalId`);
    }
    return `component:${globalId}`;
  }));
  const probeComponents = new Set();
  for (const row of execution.decision_probe_rows ?? []) {
    const component = row?.component;
    if (component === undefined || component === null || component === "") continue;
    if (typeof component !== "string" || !/^component:[^:\s]+$/.test(component)) {
      throw new Error(`Probe component for ${caseRow.case_id}:${execution.candidate_id} must use component:<GlobalId>`);
    }
    if (!groundedComponents.has(component)) {
      throw new Error(`Probe component ${component} is outside grounded set for case ${caseRow.case_id}:${execution.candidate_id}`);
    }
    probeComponents.add(component);
  }

  const coverage = execution.summary?.coverage;
  if (!coverage || typeof coverage !== "object") {
    throw new Error(`Natural-class truth ${caseRow.case_id}:${execution.candidate_id} is missing coverage`);
  }
  const coverageSet = (field) => {
    const values = coverage[field];
    if (!Array.isArray(values) || values.some((value) => typeof value !== "string" || !/^component:[^:\s]+$/.test(value))) {
      throw new Error(`Coverage ${field} for ${caseRow.case_id}:${execution.candidate_id} must contain component IDs`);
    }
    const unique = new Set(values);
    if (unique.size !== values.length) {
      throw new Error(`Coverage ${field} for ${caseRow.case_id}:${execution.candidate_id} contains duplicates`);
    }
    return unique;
  };
  const requested = coverageSet("requested_target_ids");
  const covered = coverageSet("covered_target_ids");
  const missing = coverageSet("missing_target_ids");
  if (
    requested.size !== groundedComponents.size
    || [...requested].some((component) => !groundedComponents.has(component))
    || [...groundedComponents].some((component) => !requested.has(component))
  ) {
    throw new Error(`Coverage requested targets differ from grounded set for case ${caseRow.case_id}:${execution.candidate_id}`);
  }
  if (
    [...covered].some((component) => !requested.has(component))
    || [...missing].some((component) => !requested.has(component))
    || [...covered].some((component) => missing.has(component))
    || covered.size + missing.size !== requested.size
  ) {
    throw new Error(`Coverage partition is invalid for case ${caseRow.case_id}:${execution.candidate_id}`);
  }
  if (
    coverage.requested_target_count !== requested.size
    || coverage.covered_target_count !== covered.size
    || coverage.missing_target_count !== missing.size
  ) {
    throw new Error(`Coverage counts disagree with IDs for case ${caseRow.case_id}:${execution.candidate_id}`);
  }
  const complete = missing.size === 0;
  if (coverage.complete !== complete) {
    throw new Error(`Coverage completeness disagrees with missing targets for case ${caseRow.case_id}:${execution.candidate_id}`);
  }
  const expectedStatus = complete ? "executable" : "incomplete_path";
  if (execution.status !== expectedStatus) {
    throw new Error(`Execution status disagrees with coverage for case ${caseRow.case_id}:${execution.candidate_id}`);
  }
  return `${covered.size}/${requested.size} (${execution.status})`;
}

function classGroundingValues(caseRow, executionMapById) {
  if (caseRow.grounding_mode !== "natural_class") {
    return ["", "", "", "", "", "not applicable", "not applicable", "not applicable", "not applicable"];
  }
  const metadata = caseRow.grounding_metadata ?? {};
  const selector = metadata.reference_selector ?? {};
  return [
    caseRow.expected_type_term ?? "",
    caseRow.expected_ifc_type ?? "",
    selector.expected_cardinality ?? "",
    caseRow.grounded_component_ids?.length ?? 0,
    (caseRow.grounded_component_ids ?? []).join(" | "),
    metadata.reason ?? "",
    classCoverage(caseRow, executionMapById.get("material")),
    classCoverage(caseRow, executionMapById.get("process")),
    classCoverage(caseRow, executionMapById.get("unified")),
  ];
}

function indexedByUniqueCaseId(rows, label) {
  const indexed = new Map();
  for (const row of rows) {
    if (!row.case_id || indexed.has(row.case_id)) {
      throw new Error(`${label} must have unique non-empty case IDs`);
    }
    indexed.set(row.case_id, row);
  }
  return indexed;
}

function verifyAuditHash(audit, field, inputLabel, bytes) {
  const expected = audit[field];
  if (typeof expected !== "string" || !expected) {
    throw new Error(`Audit is missing ${field}`);
  }
  if (!/^[a-f0-9]{64}$/.test(expected)) {
    throw new Error(`Audit ${field} must be a 64-character SHA-256 hex digest`);
  }
  const actual = createHash("sha256").update(bytes).digest("hex");
  if (actual !== expected) {
    throw new Error(`Audit ${field} mismatch for supplied ${inputLabel}: expected ${expected}, computed ${actual}`);
  }
}

const [casesBytes, truthBytes, auditBytes] = await Promise.all([
  fs.readFile(casesPath),
  fs.readFile(truthPath),
  fs.readFile(auditPath),
]);
const cases = readJsonl(casesBytes);
const truths = readJsonl(truthBytes);
const audit = JSON.parse(auditBytes.toString("utf8"));
verifyAuditHash(audit, "cases_sha256", "cases JSONL", casesBytes);
verifyAuditHash(audit, "truth_sha256", "truth JSONL", truthBytes);
const caseCount = cases.length;
if (![16, 24].includes(caseCount) || truths.length !== caseCount || audit.question_row_count !== caseCount) {
  throw new Error("E4d review workbook requires matching 16-row legacy or 24-row V4 cases, truths, and audited rows");
}
const caseById = indexedByUniqueCaseId(cases, "Cases");
const truthById = indexedByUniqueCaseId(truths, "Truth rows");
if (caseById.size !== truthById.size || cases.some((caseRow) => !truthById.has(caseRow.case_id))) {
  throw new Error("Cases and truth rows must have matching unique case IDs");
}

const workbook = Workbook.create();
const instructions = workbook.worksheets.add("Instructions");
const caseSheet = workbook.worksheets.add("Cases");
const coverage = workbook.worksheets.add("Coverage");
const codebook = workbook.worksheets.add("Codebook");

instructions.showGridLines = false;
applyTitle(instructions, "A1:H2", "E4d CarbonQL candidate-set review");
instructions.getRange("A4:H4").merge();
instructions.getRange("A4:H4").values = [[
  `Human gate: all ${caseCount} reviewer decisions must be PASS before E4d LLM scoring or any READY/paper_ready=true claim.`,
]];
instructions.getRange("A4:H4").format = {
  fill: "#EAF3FF",
  font: { name: "Aptos", bold: true, color: palette.blue },
  wrapText: true,
};
instructions.getRange("A6:B14").values = [
  ["Step", "Required action"],
  [1, "Read the question, grounding, operation, selected or grounded class context, and all three candidate programs."],
  [2, "Compare material, process, and unified truth summaries, including coverage status and missing targets."],
  [3, "Choose PASS only when wording, candidates, and the gold decision class are appropriate."],
  [4, "Choose REVISE when a case is usable after a specific wording change; record the replacement question and reason."],
  [5, "Choose REJECT when the case should not remain in the benchmark."],
  ["Cases SHA-256", audit.cases_sha256],
  ["Candidate truth SHA-256", audit.truth_sha256],
  ["Graph SHA-256", audit.graph_sha256],
];
applyHeader(instructions.getRange("A6:B6"));
instructions.getRange("A7:A14").format = { font: { name: "Aptos", bold: true, color: palette.muted } };
instructions.getRange("B7:B14").format.wrapText = true;
instructions.getRange("A6:B14").format.borders = { preset: "outside", style: "thin", color: palette.border };
instructions.getRange("A1:H14").format.font = { name: "Aptos", color: palette.ink };
instructions.getRange("A1:H2").format.rowHeightPx = 34;
instructions.getRange("A4:H4").format.rowHeightPx = 34;
instructions.getRange("A6:A14").format.columnWidthPx = 180;
instructions.getRange("B6:B14").format.columnWidthPx = 670;
instructions.getRange("A1:H2").format.font = {
  name: "Aptos Display", bold: true, color: palette.white, size: 18,
};

caseSheet.showGridLines = false;
const headers = [
  "Case ID",
  "Question",
  "Grounding mode",
  "Operation",
  "Selected IDs",
  "Expected type term",
  "Expected IFC type",
  "Grounding cardinality",
  "Grounded count",
  "Grounded IDs",
  "Grounding reason",
  "Material coverage",
  "Process coverage",
  "Unified coverage",
  "Candidate material SHA-256",
  "Candidate material program JSON",
  "Candidate process SHA-256",
  "Candidate process program JSON",
  "Candidate unified SHA-256",
  "Candidate unified program JSON",
  "Material truth summary JSON",
  "Process truth summary JSON",
  "Unified truth summary JSON",
  "Gold decision class",
  "Answer policy",
  "Reviewer decision",
  "Revised question",
  "Reviewer comment",
];
const caseLastColumn = columnName(headers.length);
const caseLastRow = cases.length + 1;
const columnForHeader = (header) => {
  const index = headers.indexOf(header);
  if (index === -1) throw new Error(`Missing fixed E4d Cases header: ${header}`);
  return columnName(index + 1);
};
const reviewerColumn = columnForHeader("Reviewer decision");
const reviewerRange = `Cases!$${reviewerColumn}$2:$${reviewerColumn}$${caseLastRow}`;
const rows = cases.map((caseRow) => {
  const truthRow = truthById.get(caseRow.case_id);
  const candidates = candidateMap(caseRow);
  const executions = executionMap(truthRow);
  for (const id of ["material", "process", "unified"]) {
    if (candidates.get(id).program_sha256 !== executions.get(id).program_sha256) {
      throw new Error(`Candidate/truth hash mismatch for ${caseRow.case_id}:${id}`);
    }
  }
  return [
    caseRow.case_id,
    caseRow.question,
    caseRow.grounding_mode,
    caseRow.operation,
    (caseRow.selected_component_ids ?? []).join(" | "),
    ...classGroundingValues(caseRow, executions),
    candidates.get("material").program_sha256,
    compactJson(candidates.get("material").program),
    candidates.get("process").program_sha256,
    compactJson(candidates.get("process").program),
    candidates.get("unified").program_sha256,
    compactJson(candidates.get("unified").program),
    truthSummary(truthRow, executions.get("material")),
    truthSummary(truthRow, executions.get("process")),
    truthSummary(truthRow, executions.get("unified")),
    truthRow.decision_class ?? "",
    truthRow.answer_policy ?? "",
    "",
    "",
    "",
  ];
});
caseSheet.getRange(`A1:${caseLastColumn}${caseLastRow}`).values = [headers, ...rows];
applyHeader(caseSheet.getRange(`A1:${caseLastColumn}1`));
caseSheet.getRange(`A2:${caseLastColumn}${caseLastRow}`).format = {
  font: { name: "Aptos", size: 9, color: palette.ink },
  verticalAlignment: "top",
  wrapText: true,
  borders: { insideHorizontal: { style: "thin", color: "#E8E8ED" } },
};
caseSheet.freezePanes.freezeRows(1);
caseSheet.freezePanes.freezeColumns(2);
caseSheet.getRange(`${reviewerColumn}2:${reviewerColumn}${caseLastRow}`).dataValidation = {
  rule: { type: "list", values: ["PASS", "REVISE", "REJECT"] },
};
caseSheet.getRange(`${reviewerColumn}2:${reviewerColumn}${caseLastRow}`).conditionalFormats.add("containsText", {
  text: "PASS", format: { fill: "#EAF7ED", font: { color: palette.green, bold: true } },
});
caseSheet.getRange(`${reviewerColumn}2:${reviewerColumn}${caseLastRow}`).conditionalFormats.add("containsText", {
  text: "REVISE", format: { fill: "#FFF4D6", font: { color: palette.amber, bold: true } },
});
caseSheet.getRange(`${reviewerColumn}2:${reviewerColumn}${caseLastRow}`).conditionalFormats.add("containsText", {
  text: "REJECT", format: { fill: "#FDECEC", font: { color: palette.red, bold: true } },
});
const caseTable = caseSheet.tables.add(`A1:${caseLastColumn}${caseLastRow}`, true, "E4dCandidateCases");
caseTable.showFilterButton = true;
caseTable.showBandedColumns = false;
const widths = {
  "Case ID": 190, "Question": 390, "Grounding mode": 130, "Operation": 110, "Selected IDs": 260,
  "Expected type term": 120, "Expected IFC type": 120, "Grounding cardinality": 135,
  "Grounded count": 105, "Grounded IDs": 620, "Grounding reason": 180,
  "Material coverage": 125, "Process coverage": 125, "Unified coverage": 125,
  "Candidate material SHA-256": 270, "Candidate material program JSON": 370,
  "Candidate process SHA-256": 270, "Candidate process program JSON": 370,
  "Candidate unified SHA-256": 270, "Candidate unified program JSON": 370,
  "Material truth summary JSON": 360, "Process truth summary JSON": 360, "Unified truth summary JSON": 360,
  "Gold decision class": 170, "Answer policy": 190, "Reviewer decision": 150,
  "Revised question": 360, "Reviewer comment": 360,
};
for (const header of headers) {
  caseSheet.getRange(`${columnForHeader(header)}1:${columnForHeader(header)}${caseLastRow}`).format.columnWidthPx = widths[header];
}
caseSheet.getRange(`A1:${caseLastColumn}1`).format.rowHeightPx = 66;
caseSheet.getRange(`A2:${caseLastColumn}${caseLastRow}`).format.rowHeightPx = 118;

coverage.showGridLines = false;
applyTitle(coverage, "A1:I2", "Coverage and review gate");
const machineAuditRows = [
  ["Machine audit", "Value"],
  ["Question rows", audit.question_row_count],
  ["Grounding-operation units", audit.decision_unit_count],
  ["Candidates", audit.candidate_count],
  ["Invalid candidates", audit.invalid_candidate_count],
  ["Oracle mismatches", audit.oracle_mismatch_count],
  ["Hash mismatches", audit.hash_mismatch_count],
  ["Source-cue leakage", audit.source_cue_leakage_count],
  ["Rank-cardinality mismatches", audit.rank_cardinality_mismatch_count ?? 0],
  ["Paper ready", String(audit.paper_ready)],
];
const machineAuditLastRow = 3 + machineAuditRows.length;
coverage.getRange(`A4:B${machineAuditLastRow}`).values = machineAuditRows;
coverage.getRange("D4:E9").values = [
  ["Human review", "Live count"],
  ["PASS", null],
  ["REVISE", null],
  ["REJECT", null],
  ["Blank", null],
  ["Gate", null],
];
coverage.getRange("E5").formulas = [[`=COUNTIF(${reviewerRange},"PASS")`]];
coverage.getRange("E6").formulas = [[`=COUNTIF(${reviewerRange},"REVISE")`]];
coverage.getRange("E7").formulas = [[`=COUNTIF(${reviewerRange},"REJECT")`]];
coverage.getRange("E8").formulas = [[`=COUNTBLANK(${reviewerRange})`]];
coverage.getRange("E9").formulas = [[`=IF(E5=${caseCount},"READY","NOT READY")`]];
applyHeader(coverage.getRange("A4:B4"));
applyHeader(coverage.getRange("D4:E4"));
coverage.getRange("D5:D9").format = { font: { name: "Aptos", bold: true, color: palette.muted } };
coverage.getRange("E9").conditionalFormats.add("expression", {
  formula: '=E9="READY"', format: { fill: "#EAF7ED", font: { color: palette.green, bold: true } },
});
coverage.getRange("E9").conditionalFormats.add("expression", {
  formula: '=E9="NOT READY"', format: { fill: "#FDECEC", font: { color: palette.red, bold: true } },
});
const policyRows = [
  ["Gold decision / policy", "Units"],
  ...Object.entries(audit.decision_class_unit_counts ?? {}),
  ["Flip-rate numerator", audit.flip_rate_numerator],
  ["Flip-rate denominator", audit.flip_rate_denominator],
  ["Flip rate", audit.flip_rate],
];
const policyStartRow = machineAuditLastRow + 3;
const policyLastRow = policyStartRow + policyRows.length - 1;
coverage.getRange(`A${policyStartRow}:B${policyLastRow}`).values = policyRows;
applyHeader(coverage.getRange(`A${policyStartRow}:B${policyStartRow}`));
coverage.getRange(`B${policyLastRow}`).format.numberFormat = "0.0%";
coverage.getRange(`A4:B${policyLastRow}`).format.borders = { preset: "outside", style: "thin", color: palette.border };
coverage.getRange("D4:E9").format.borders = { preset: "outside", style: "thin", color: palette.border };
const classCoverageRows = [
  ["Natural-class complete coverage", "Covered", "Grounded", "Missing", "Rate", "Status"],
  ...["material", "process", "unified"].map((candidateId) => {
    const view = audit.class_view_coverage?.[candidateId];
    return [
      candidateId,
      view?.covered_count ?? "not applicable",
      view?.grounded_count ?? "",
      view?.missing_count ?? "",
      null,
      view?.status ?? "",
    ];
  }),
];
coverage.getRange("D15:I18").values = classCoverageRows;
applyHeader(coverage.getRange("D15:I15"));
for (let row = 16; row <= 18; row += 1) {
  coverage.getRange(`H${row}`).formulas = [[`=IFERROR(E${row}/F${row},"")`]];
}
coverage.getRange("H16:H18").format.numberFormat = "0.0%";
coverage.getRange("D15:I18").format.borders = { preset: "outside", style: "thin", color: palette.border };
coverage.getRange(`A1:I${Math.max(policyLastRow, 18)}`).format.font = { name: "Aptos", color: palette.ink };
coverage.getRange("A1:I2").format.rowHeightPx = 34;
coverage.getRange(`A1:A${policyLastRow}`).format.columnWidthPx = 245;
coverage.getRange(`B1:B${policyLastRow}`).format.columnWidthPx = 150;
coverage.getRange("D1:D18").format.columnWidthPx = 220;
coverage.getRange("E1:H18").format.columnWidthPx = 110;
coverage.getRange("I1:I18").format.columnWidthPx = 150;
coverage.getRange("A1:I2").format.font = {
  name: "Aptos Display", bold: true, color: palette.white, size: 18,
};

codebook.showGridLines = false;
applyTitle(codebook, "A1:D2", "E4d review codebook");
const codebookRows = [
  ["Field / code", "Meaning", "Allowed values", "Reviewer action"],
  ["material", "Candidate limited to material emission atoms.", "Candidate ID", "Compare its program and truth summary."],
  ["process", "Candidate limited to production-energy emission atoms.", "Candidate ID", "Compare its program and truth summary."],
  ["unified", "Candidate combines material and process atom sources.", "Candidate ID", "Compare its program and truth summary."],
  ["natural_class", "Question names a complete natural class that resolves to a grounded set.", "Grounding mode", "Inspect the grounded class context, not Selected IDs."],
  ["set cardinality", "All matching class members are retained by the selector.", "Grounding cardinality", "Confirm the value is set for a natural-class row."],
  ["grounded count", "Number of committed class members in Grounded IDs.", "Grounding metadata", "Confirm the count and IDs agree."],
  ["view coverage", "Members complete across every requested source / grounded class members; process and unified 4/162 indicate missing coverage, not zero emissions.", "Coverage", "Compare counts, rates, missing members, and execution status."],
  ["view_sensitive", "Candidate views produce different decision signatures.", "Gold decision class", "Confirm the question is intentionally underspecified."],
   ["decomposition_required", "Aggregate output needs source-separated presentation.", "Answer policy", "Confirm all three source summaries are visible."],
   ["source_dependent_trace", "Trace output is source-conditioned.", "Answer policy", "Confirm trace-sensitive evidence remains reviewable."],
   ["strict_winner", "Compare policy selects one strict winner.", "Answer policy", "Confirm the winning candidate is unambiguous."],
   ["ranked_tie_classes", "Rank policy preserves ordered tie classes.", "Answer policy", "Confirm tied ranks remain visible at the cutoff."],
  ["PASS", "Case wording, candidates, and gold class are accepted.", "Reviewer decision", "No revision field is required."],
  ["REVISE", "Case is retained only with a specific wording revision.", "Reviewer decision", "Record revised question and reviewer comment."],
  ["REJECT", "Case is unsuitable for the benchmark.", "Reviewer decision", "Record the reason in reviewer comment."],
];
const codebookLastRow = 3 + codebookRows.length;
codebook.getRange(`A4:D${codebookLastRow}`).values = codebookRows;
applyHeader(codebook.getRange("A4:D4"));
codebook.getRange(`A5:D${codebookLastRow}`).format = {
  font: { name: "Aptos", size: 10, color: palette.ink },
  verticalAlignment: "top", wrapText: true,
  borders: { insideHorizontal: { style: "thin", color: "#E8E8ED" } },
};
codebook.freezePanes.freezeRows(4);
codebook.getRange(`A1:A${codebookLastRow}`).format.columnWidthPx = 210;
codebook.getRange(`B1:B${codebookLastRow}`).format.columnWidthPx = 400;
codebook.getRange(`C1:C${codebookLastRow}`).format.columnWidthPx = 190;
codebook.getRange(`D1:D${codebookLastRow}`).format.columnWidthPx = 350;
codebook.getRange(`A5:D${codebookLastRow}`).format.rowHeightPx = 52;

const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "E4d workbook formula error scan",
  maxChars: 2000,
});
if (/#[A-Z0-9/?!]+/.test(formulaErrors.ndjson)) {
  throw new Error(`Formula error scan failed: ${formulaErrors.ndjson}`);
}

if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  const preview = await workbook.render({
    sheetName: previewSheet,
    autoCrop: "all",
    scale: previewSheet === "Cases" ? 0.25 : 1,
    format: "png",
  });
  await fs.writeFile(
    path.join(previewDir, `${previewSheet}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
process.stdout.write(JSON.stringify({
  sheets: sheetNames,
  reviewerRange,
  formulaErrorScan: formulaErrors.ndjson,
  outputPath,
}) + "\n");
