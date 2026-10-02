const SELECTED_COLOR = "#ffb000";
const XRAY_OPACITY = 0.16;

export function deriveFragmentRenderPolicy({ xray, selectedGlobalId }) {
  return {
    background: {
      opacity: xray ? XRAY_OPACITY : 1,
      transparent: Boolean(xray),
      selectable: true,
    },
    selected: selectedGlobalId
      ? {
          globalId: selectedGlobalId,
          color: SELECTED_COLOR,
          opacity: 1,
          transparent: false,
          selectable: true,
        }
      : null,
  };
}

export async function originalGlobalIdForPick({ fragments, pick }) {
  const modelId = pick?.fragments?.modelId;
  if (
    !fragments ||
    !pick ||
    typeof modelId !== "string" ||
    modelId.length === 0 ||
    !Number.isInteger(pick.localId)
  ) {
    return null;
  }

  const globalIds = await fragments.modelIdMapToGuids({
    [modelId]: new Set([pick.localId]),
  });
  const globalId = globalIds[0];
  return typeof globalId === "string" && globalId.length > 0
    ? globalId
    : null;
}
