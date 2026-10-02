/**
 * Extract the only supported existing-project handoff parameter. Keeping this
 * pure makes the URL contract independently testable without a browser.
 */
export function getExistingProjectId(search = "") {
  return new URLSearchParams(search).get("projectId")?.trim() || "";
}
