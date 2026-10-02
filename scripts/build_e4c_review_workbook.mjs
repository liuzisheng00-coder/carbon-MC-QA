import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const [casesPath, truthPath, auditPath, outputPath] = process.argv.slice(2);
if (!casesPath || !truthPath || !auditPath || !outputPath) {
  throw new Error(
    "Usage: node build_e4c_review_workbook.mjs <cases.jsonl> <truth.jsonl> <audit.json> <output.xlsx>",
  );
}

const previewDir = process.env.E4C_PREVIEW_DIR;
const previewSheet = process.env.E4C_PREVIEW_SHEET;
const previewSheetNames = ["Instructions", "Cases", "Coverage", "Codebook"];
if (previewDir && !previewSheet) {
  throw new Error("E4C_PREVIEW_SHEET is required when E4C_PREVIEW_DIR is set");
}
if (previewDir && !previewSheetNames.includes(previewSheet)) {
  throw new Error(
    `E4C_PREVIEW_SHEET must be one of: ${previewSheetNames.join(", ")}`,
  );
}
if (previewDir && process.env.SKIA_CANVAS_THREADS !== "1") {
  throw new Error(
    "SKIA_CANVAS_THREADS=1 is required when E4C_PREVIEW_DIR is set",
  );
}


async function readJsonl(filePath) {
  const text = await fs.readFile(filePath, "utf8");
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}


function compactJson(value) {
  return JSON.stringify(value);
}


function operationLabel(program) {
  return program.steps.map((step) => step.op).join(" > ");
}


function sourceLabel(program) {
  const step = program.steps.find((item) => item.op === "CarbonAtoms");
  const source = step?.source ?? "";
  return Array.isArray(source) ? source.join(" + ") : String(source);
}


function groupLabel(program) {
  const step = program.steps.find((item) => item.op === "GroupBy");
  const keys = step?.keys ?? [];
  return Array.isArray(keys) ? keys.join(" + ") : String(keys);
}


function candidateOrAtomCount(truth) {
  const summary = truth.summary ?? {};
  if (Array.isArray(summary.candidate_targets)) return summary.candidate_targets.length;
  if (Array.isArray(summary.unresolved_targets)) return summary.unresolved_targets.length;
  if (typeof summary.atom_count === "number") return summary.atom_count;
  if (typeof summary.traced_atom_count === "number") return summary.traced_atom_count;
  return 0;
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


function hasNonEmptyValue(value) {
  if (Array.isArray(value)) return value.some(hasNonEmptyValue);
  return value !== undefined && value !== null && String(value).trim() !== "";
}


function evidenceLabel(value) {
  if (Array.isArray(value)) return value.filter(hasNonEmptyValue).join(" | ");
  return hasNonEmptyValue(value) ? String(value) : "";
}


const cases = await readJsonl(casesPath);
const truths = await readJsonl(truthPath);
const audit = JSON.parse(await fs.readFile(auditPath, "utf8"));
const truthById = new Map(truths.map((truth) => [truth.case_id, truth]));
const hasLanguageMetadata = cases.every(
  (row) => row.language_tier && row.grounding_mode && row.rewrite_rationale,
);
const truthBasisPresence = truths.map((truth) => Object.hasOwn(truth, "truth_basis"));
if (truthBasisPresence.some(Boolean) && !truthBasisPresence.every(Boolean)) {
  throw new Error("truth_basis must be present on either every truth row or none");
}
const hasTruthBasis = truthBasisPresence.every(Boolean);
const revisionMetadataFields = [
  "supersedes_case_id",
  "review_revision_kind",
  "perspective_evidence",
];
const hasRevisionMetadata = cases.some((row) =>
  revisionMetadataFields.some((field) => hasNonEmptyValue(row[field])),
);
if (cases.length !== 48 || truths.length !== 48) {
  throw new Error(`Expected 48 cases and truths, found ${cases.length} and ${truths.length}`);
}

const workbook = Workbook.create();
const instructions = workbook.worksheets.add("Instructions");
const caseSheet = workbook.worksheets.add("Cases");
const coverage = workbook.worksheets.add("Coverage");
const codebook = workbook.worksheets.add("Codebook");

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


function applyTitle(sheet, range, title) {
  sheet.getRange(range).merge();
  sheet.getRange(range).values = [[title]];
  sheet.getRange(range).format = {
    fill: palette.ink,
    font: { bold: true, color: palette.white, size: 18 },
    verticalAlignment: "center",
  };
}


function applyHeader(range) {
  range.format = {
    fill: palette.panel,
    font: { bold: true, color: palette.ink },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "doubleBottom", style: "thin", color: palette.border },
  };
}


instructions.showGridLines = false;
applyTitle(instructions, "A1:H2", "E4c CarbonQL held-out review");
instructions.getRange("A4:H4").merge();
instructions.getRange("A4:H4").values = [[
  "Human gate: review every case before any held-out question is sent to an LLM.",
]];
instructions.getRange("A4:H4").format = {
  fill: "#EAF3FF",
  font: { bold: true, color: palette.blue },
  wrapText: true,
};
instructions.getRange("A6:B14").values = [
  ["Step", "Required action"],
  [1, "Read the question, selection context, expected status, and gold program."],
  [2, "Choose PASS only when the wording and gold semantics are fully aligned."],
  [3, "Choose REVISE when wording or the gold program must change; fill the revision columns."],
  [4, "Choose REJECT when the case cannot be retained in the benchmark."],
  [5, "Do not change expected numeric truth manually; revised programs must be re-frozen."],
  ["Frozen graph SHA-256", audit.graph_sha256],
  ["Cases SHA-256", audit.cases_sha256],
  ["Truth SHA-256", audit.truth_sha256],
];
applyHeader(instructions.getRange("A6:B6"));
instructions.getRange("A7:A14").format = { font: { bold: true, color: palette.muted } };
instructions.getRange("B7:B14").format.wrapText = true;
instructions.getRange("A6:B14").format.borders = {
  preset: "outside",
  style: "thin",
  color: palette.border,
};
instructions.getRange("A1:H14").format.font = { name: "Aptos", color: palette.ink };
instructions.getRange("A1:H2").format.font = {
  name: "Aptos Display",
  bold: true,
  color: palette.white,
  size: 18,
};
instructions.getRange("A1:H2").format.rowHeight = 30;
instructions.getRange("A4:H4").format.rowHeight = 34;
instructions.getRange("A6:A14").format.columnWidthPx = 170;
instructions.getRange("B6:B14").format.columnWidthPx = 620;


caseSheet.showGridLines = false;
const headers = [
  "Case ID",
  "Question",
  "Selected IDs",
  "Combination",
  "Category",
  "Expected compiler status",
  "Expected query status",
  "Gold group keys",
  "Gold sources",
  "Gold operations",
  "Gold holes",
  "Gold program JSON",
  hasTruthBasis ? "Truth summary JSON" : "Independent summary JSON",
  "Candidate / atom count",
  "Reviewer decision",
  "Revised question",
  "Revised program JSON",
  "Reviewer comment",
];
if (hasLanguageMetadata) {
  headers.splice(
    5,
    0,
    "Language tier",
    "Grounding mode",
    "Rewrite rationale",
  );
}
if (hasRevisionMetadata) {
  headers.splice(
    headers.indexOf("Expected compiler status"),
    0,
    "Supersedes Case ID",
    "Review revision kind",
    "Perspective evidence",
  );
}
const caseLastColumn = columnName(headers.length);
const caseLastRow = cases.length + 1;
const columnForHeader = (header) => {
  const index = headers.indexOf(header);
  if (index === -1) throw new Error(`Missing Cases header: ${header}`);
  return columnName(index + 1);
};
const reviewerColumn = columnName(headers.indexOf("Reviewer decision") + 1);
const reviewerRange = `'Cases'!$${reviewerColumn}$2:$${reviewerColumn}$${caseLastRow}`;
const rows = cases.map((item) => {
  const truth = truthById.get(item.case_id);
  if (!truth) throw new Error(`Missing truth row for ${item.case_id}`);
  const truthSummary = hasTruthBasis
    ? { truth_basis: truth.truth_basis, ...truth.summary }
    : truth.summary;
  const rowValues = {
    "Case ID": item.case_id,
    "Question": item.question,
    "Selected IDs": item.selected_component_ids.join(" | "),
    "Combination": item.view_combination,
    "Category": item.category,
    "Expected compiler status": item.expected_compiler_status,
    "Expected query status": truth.status,
    "Gold group keys": groupLabel(item.gold_program),
    "Gold sources": sourceLabel(item.gold_program),
    "Gold operations": operationLabel(item.gold_program),
    "Gold holes": compactJson(item.gold_program.holes ?? []),
    "Gold program JSON": compactJson(item.gold_program),
    [hasTruthBasis ? "Truth summary JSON" : "Independent summary JSON"]: compactJson(truthSummary),
    "Candidate / atom count": candidateOrAtomCount(truth),
    "Reviewer decision": "",
    "Revised question": "",
    "Revised program JSON": "",
    "Reviewer comment": "",
  };
  if (hasLanguageMetadata) {
    rowValues["Language tier"] = item.language_tier;
    rowValues["Grounding mode"] = item.grounding_mode;
    rowValues["Rewrite rationale"] = item.rewrite_rationale;
  }
  if (hasRevisionMetadata) {
    rowValues["Supersedes Case ID"] = item.supersedes_case_id ?? "";
    rowValues["Review revision kind"] = item.review_revision_kind ?? "";
    rowValues["Perspective evidence"] = evidenceLabel(item.perspective_evidence);
  }
  return headers.map((header) => {
    if (!Object.hasOwn(rowValues, header)) {
      throw new Error(`Missing Cases row value for header: ${header}`);
    }
    return rowValues[header];
  });
});
caseSheet.getRange(`A1:${caseLastColumn}${caseLastRow}`).values = [headers, ...rows];
applyHeader(caseSheet.getRange(`A1:${caseLastColumn}1`));
caseSheet.getRange(`A2:${caseLastColumn}${caseLastRow}`).format = {
  font: { name: "Aptos", size: 9, color: palette.ink },
  verticalAlignment: "top",
  wrapText: true,
  borders: {
    insideHorizontal: { style: "thin", color: "#E8E8ED" },
  },
};
caseSheet.freezePanes.freezeRows(1);
caseSheet.freezePanes.freezeColumns(2);
if (hasRevisionMetadata) {
  const revisionKindColumn = columnForHeader("Review revision kind");
  caseSheet.getRange(`${revisionKindColumn}2:${revisionKindColumn}${caseLastRow}`).conditionalFormats.add("notContainsBlanks", {
    format: { fill: "#F2F7FD" },
  });
}
caseSheet.getRange(`${reviewerColumn}2:${reviewerColumn}${caseLastRow}`).dataValidation = {
  rule: { type: "list", values: ["PASS", "REVISE", "REJECT"] },
};
caseSheet.getRange(`${reviewerColumn}2:${reviewerColumn}${caseLastRow}`).conditionalFormats.add("containsText", {
  text: "PASS",
  format: { fill: "#EAF7ED", font: { color: palette.green, bold: true } },
});
caseSheet.getRange(`${reviewerColumn}2:${reviewerColumn}${caseLastRow}`).conditionalFormats.add("containsText", {
  text: "REVISE",
  format: { fill: "#FFF4D6", font: { color: palette.amber, bold: true } },
});
caseSheet.getRange(`${reviewerColumn}2:${reviewerColumn}${caseLastRow}`).conditionalFormats.add("containsText", {
  text: "REJECT",
  format: { fill: "#FDECEC", font: { color: palette.red, bold: true } },
});
const caseTable = caseSheet.tables.add(`A1:${caseLastColumn}${caseLastRow}`, true, "E4cHeldoutCases");
caseTable.showFilterButton = true;
caseTable.showBandedColumns = false;
const caseColumnWidths = {
  "Case ID": 185,
  "Question": 430,
  "Selected IDs": 300,
  "Combination": 150,
  "Category": 150,
  "Language tier": 170,
  "Grounding mode": 170,
  "Rewrite rationale": 360,
  "Supersedes Case ID": 185,
  "Review revision kind": 220,
  "Perspective evidence": 360,
  "Expected compiler status": 150,
  "Expected query status": 150,
  "Gold group keys": 190,
  "Gold sources": 190,
  "Gold operations": 190,
  "Gold holes": 190,
  "Gold program JSON": 420,
  "Truth summary JSON": 420,
  "Independent summary JSON": 420,
  "Candidate / atom count": 150,
  "Reviewer decision": 150,
  "Revised question": 360,
  "Revised program JSON": 360,
  "Reviewer comment": 360,
};
for (const header of headers) {
  const column = columnForHeader(header);
  caseSheet.getRange(`${column}1:${column}${caseLastRow}`).format.columnWidthPx = caseColumnWidths[header];
}
caseSheet.getRange(`A1:${caseLastColumn}1`).format.rowHeightPx = 44;
caseSheet.getRange(`A2:${caseLastColumn}${caseLastRow}`).format.rowHeightPx = 72;


coverage.showGridLines = false;
applyTitle(coverage, "A1:F2", "Coverage and review gate");
const machineAuditRows = [
  ["Machine audit", "Value"],
  ["Cases", audit.case_count],
  ["Oracle mismatches", audit.oracle_mismatch_count],
  ["Development overlap", audit.development_overlap_count],
  ["Normalized duplicates", audit.normalized_duplicate_count],
  ["Compiler mismatches", audit.compiler_status_mismatch_count],
  ["View-signature mismatches", audit.view_signature_mismatch_count],
  ["Executable truth cases", audit.query_status_counts.executable],
  ["Non-executable truth cases", audit.case_count - audit.query_status_counts.executable],
];
if (hasTruthBasis) {
  machineAuditRows.push(
    ["Direct-graph truth basis", audit.truth_basis_counts.direct_graph],
    ["Gold-hole consistency truth basis", audit.truth_basis_counts.gold_hole_consistency],
  );
}
coverage.getRange(`A4:B${3 + machineAuditRows.length}`).values = machineAuditRows;
applyHeader(coverage.getRange("A4:B4"));
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
coverage.getRange("E9").formulas = [[`=IF(E5=${cases.length},"READY","NOT READY")`]];
applyHeader(coverage.getRange("D4:E4"));
coverage.getRange("D5:D9").format.font = { bold: true, color: palette.muted };
coverage.getRange("E9").conditionalFormats.add("expression", {
  formula: '=E9="READY"',
  format: { fill: "#EAF7ED", font: { color: palette.green, bold: true } },
});
coverage.getRange("E9").conditionalFormats.add("expression", {
  formula: '=E9="NOT READY"',
  format: { fill: "#FDECEC", font: { color: palette.red, bold: true } },
});
coverage.getRange("A15:B20").values = [
  ["View combination", "Cases"],
  ...Object.entries(audit.view_combination_counts),
  ["Total", audit.case_count],
];
applyHeader(coverage.getRange("A15:B15"));
coverage.getRange("D15:E19").values = [
  ["Expected query status", "Cases"],
  ...Object.entries(audit.query_status_counts),
  ["Total", audit.case_count],
];
applyHeader(coverage.getRange("D15:E15"));
coverage.getRange("A4:B20").format.borders = {
  preset: "outside",
  style: "thin",
  color: palette.border,
};
coverage.getRange("D4:E19").format.borders = {
  preset: "outside",
  style: "thin",
  color: palette.border,
};
coverage.getRange("A1:F20").format.font = { name: "Aptos", color: palette.ink };
coverage.getRange("A1:F2").format.font = {
  name: "Aptos Display",
  bold: true,
  color: palette.white,
  size: 18,
};
coverage.getRange("A1:F20").format.columnWidthPx = 175;
coverage.getRange("A1:A20").format.columnWidthPx = 240;
coverage.getRange("D1:D20").format.columnWidthPx = 240;

if (hasLanguageMetadata) {
  const countMetadata = (field) => Object.entries(
    cases.reduce((counts, row) => {
      counts[row[field]] = (counts[row[field]] ?? 0) + 1;
      return counts;
    }, {}),
  ).sort(([left], [right]) => left.localeCompare(right));
  const tierCoverageRows = [
    ["Language tier", "Cases"],
    ...countMetadata("language_tier"),
    ["Total", cases.length],
  ];
  const groundingCoverageRows = [
    ["Grounding mode", "Cases"],
    ...countMetadata("grounding_mode"),
    ["Total", cases.length],
  ];
  const tierLastRow = 21 + tierCoverageRows.length;
  const groundingLastRow = 21 + groundingCoverageRows.length;
  coverage.getRange(`A22:B${tierLastRow}`).values = tierCoverageRows;
  coverage.getRange(`D22:E${groundingLastRow}`).values = groundingCoverageRows;
  applyHeader(coverage.getRange("A22:B22"));
  applyHeader(coverage.getRange("D22:E22"));
  coverage.getRange(`A22:B${tierLastRow}`).format.borders = {
    preset: "outside",
    style: "thin",
    color: palette.border,
  };
  coverage.getRange(`D22:E${groundingLastRow}`).format.borders = {
    preset: "outside",
    style: "thin",
    color: palette.border,
  };
  coverage.getRange(`A22:E${Math.max(tierLastRow, groundingLastRow)}`).format.font = {
    name: "Aptos",
    color: palette.ink,
  };
}


codebook.showGridLines = false;
applyTitle(codebook, "A1:D2", "Review codebook");
const codebookRows = [
  ["Field / code", "Meaning", "Allowed values", "Reviewer action"],
  ["PASS", "Question and gold semantics are fully aligned.", "Reviewer decision", "No revision fields required."],
  ["REVISE", "The case is usable after a specific wording or program change.", "Reviewer decision", "Fill revised question/program and comment."],
  ["REJECT", "The case should not enter the held-out benchmark.", "Reviewer decision", "Explain the reason in reviewer comment."],
  ["valid", "The gold program is fully typed and executable at compiler level.", "Compiler status", "Check independently from query status."],
  ["partial", "The program contains one explicit typed hole and must not execute.", "Compiler status", "Confirm the question explicitly asks for clarification."],
  ["executable", "Independent graph traversal returns carbon atoms/results.", "Query status", "Check dimensions, source, and operation."],
  ["unresolved_target", "The supplied selection does not resolve to a graph object.", "Query status", "Confirm no numeric result is expected."],
  ["clarification_required", "A typed hole or multiple natural targets prevents execution.", "Query status", "Confirm candidates/hole are appropriate."],
  ["product+material", "Product grouping combined with material grouping.", "Combination", "Do not replace this with a fixed perspective label in the question."],
  ["product+process", "Product grouping combined with process grouping.", "Combination", "Check both requested dimensions are explicit."],
  ["material+process", "Material grouping combined with process grouping.", "Combination", "Check both carbon sources are explicit unless source is a hole."],
  ["product+material+process", "All three grouping families in one program.", "Combination", "Check attribution join is present when executable."],
  ["Aggregate", "Sum distinct calculated atomic emission IDs by requested keys.", "Operation", "Check every explicit key appears in GroupBy."],
  ["Rank", "Order grouped totals and retain top_k rows.", "Operation", "Check top_k matches question wording."],
  ["Compare", "Compare the first two ranked rows.", "Operation", "Rank must occur before Compare."],
  ["Trace", "Return atomic records and evidence identifiers.", "Operation", "Confirm the evidence request is explicit."],
  ["Gold program", "Human-authored CarbonQL truth; never sent in synthesis prompts.", "JSON", "Revise only with a written reason."],
  [
    hasTruthBasis ? "Truth summary" : "Independent summary",
    hasTruthBasis
      ? "Expected result with an explicit truth-basis classification."
      : "Direct-graph expected result, separate from the executor under test.",
    "JSON",
    "Do not hand-edit numeric truth.",
  ],
];
if (hasLanguageMetadata) {
  codebookRows.push(
    ["L1_explicit", "Uses formal CarbonQL terminology.", "Language tier", "Check that formal labels remain semantically aligned."],
    ["L2_natural", "Uses natural building-language wording without formal schema terms.", "Language tier", "Check that the natural wording still covers the gold semantics."],
    ["L3_deictic", "Uses selection or deictic context such as selected items.", "Language tier", "Confirm the selected grounding and gold selector agree."],
    ["project", "Question applies to the complete project.", "Grounding mode", "Confirm project-wide grounding."],
    ["selected", "Question is grounded in the supplied selected items.", "Grounding mode", "Confirm selected IDs and clicked grounding are appropriate."],
    ["unresolved_selection", "The supplied selection intentionally cannot resolve to a graph object.", "Grounding mode", "Confirm that no numeric result is expected."],
    ["natural_target", "Question names a target that must be resolved from natural language.", "Grounding mode", "Confirm candidate resolution or clarification behavior."],
  );
}
if (hasRevisionMetadata) {
  codebookRows.push(
    ["implicit_view_resolution", "Human review clarified an implied carbon perspective so the gold source is explicit.", "Review revision kind", "Confirm the evidence phrase resolves the selected source."],
    ["implicit_grouping_resolution", "Human review clarified an implied grouping so the gold keys are explicit.", "Review revision kind", "Confirm the evidence phrase resolves the requested grouping."],
    ["automatic_join_correction", "Human review removed an unsupported attribution join while preserving the approved question.", "Review revision kind", "Confirm no unrequested attribution join remains."],
  );
}
if (hasTruthBasis) {
  codebookRows.push(
    ["direct_graph", "Expected result obtained from direct graph traversal for a Gold program without typed holes.", "Truth basis", "Check numeric, status, rows, and atom identifiers against the graph."],
    ["gold_hole_consistency", "Expected clarification result derived from the typed hole declared by the Gold program; it is not independent direct-graph truth.", "Truth basis", "Check status and hole summary against the Gold program."],
  );
}
const codebookLastRow = 3 + codebookRows.length;
codebook.getRange(`A4:D${codebookLastRow}`).values = codebookRows;
applyHeader(codebook.getRange("A4:D4"));
codebook.getRange(`A5:D${codebookLastRow}`).format = {
  font: { name: "Aptos", size: 10, color: palette.ink },
  verticalAlignment: "top",
  wrapText: true,
  borders: { insideHorizontal: { style: "thin", color: "#E8E8ED" } },
};
codebook.freezePanes.freezeRows(4);
codebook.getRange(`A1:D${codebookLastRow}`).format.columnWidthPx = 230;
codebook.getRange(`B1:B${codebookLastRow}`).format.columnWidthPx = 430;
codebook.getRange(`C1:C${codebookLastRow}`).format.columnWidthPx = 190;
codebook.getRange(`D1:D${codebookLastRow}`).format.columnWidthPx = 390;
codebook.getRange(`A5:D${codebookLastRow}`).format.rowHeightPx = 52;


const tableCheck = await workbook.inspect({
  kind: "table",
  range: `Cases!A1:${caseLastColumn}8`,
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: headers.length,
  maxChars: 5000,
});
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "E4c workbook formula error scan",
  maxChars: 2000,
});

if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  const preview = await workbook.render({
    sheetName: previewSheet,
    autoCrop: "all",
    scale: previewSheet === "Cases" ? 0.7 : 1,
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
process.stdout.write(
  JSON.stringify({
    output: outputPath,
    caseCount: cases.length,
    tableCheck: tableCheck.ndjson,
    formulaErrors: formulaErrors.ndjson,
  }),
);
