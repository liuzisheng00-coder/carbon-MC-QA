const XRAY_OPACITY = 0.16;
const SELECTED_COLOR = "#ffb000";
const CANDIDATE_COLOR = "#ef9f27";

export function normalizeFragmentGlobalIds(values) {
  const source = values instanceof Set
    ? [...values]
    : Array.isArray(values)
      ? values
      : values
        ? [values]
        : [];
  return [...new Set(
    source.filter((value) => typeof value === "string" && value.length > 0),
  )];
}

export function deriveFragmentRenderPolicy({
  xray,
  selectedGlobalId,
  selectedGlobalIds,
  candidateGlobalIds,
}) {
  const selectedIds = normalizeFragmentGlobalIds(
    selectedGlobalIds ?? selectedGlobalId,
  );
  const selectedSet = new Set(selectedIds);
  const candidateIds = normalizeFragmentGlobalIds(candidateGlobalIds)
    .filter((globalId) => !selectedSet.has(globalId));
  const selectedItems = selectedIds.map((globalId) => ({
    globalId,
    color: SELECTED_COLOR,
    opacity: 1,
    transparent: false,
    selectable: true,
  }));

  return {
    background: {
      opacity: xray ? XRAY_OPACITY : 1,
      transparent: Boolean(xray),
      selectable: true,
    },
    selectedItems,
    selected: selectedItems.at(-1) ?? null,
    candidates: candidateIds.map((globalId) => ({
      globalId,
      color: CANDIDATE_COLOR,
      opacity: 0.72,
      transparent: true,
      selectable: true,
    })),
  };
}
