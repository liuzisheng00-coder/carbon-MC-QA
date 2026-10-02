import * as THREE from "three";

import { deriveFragmentRenderPolicy } from "./fragmentViewerState.mjs";

export const FRAGMENT_VIEWER_STATUS = Object.freeze({
  converting: "Converting IFC",
  cache: "Fragment cache",
  ready: "Fragment ready",
  error: "Fragment error",
});

export function isXrayEnabled(status) {
  return status === FRAGMENT_VIEWER_STATUS.ready;
}

export class FragmentRuntimeCancelledError extends Error {
  constructor() {
    super("Fragment viewer setup was cancelled");
    this.name = "FragmentRuntimeCancelledError";
  }
}

function assertRuntimeActive(isActive) {
  if (isActive && !isActive()) {
    throw new FragmentRuntimeCancelledError();
  }
}

function statusForProgress(state) {
  return state === "fragment-cache-lookup"
    ? FRAGMENT_VIEWER_STATUS.cache
    : FRAGMENT_VIEWER_STATUS.converting;
}

export async function loadFragmentRuntime({
  loadBytes,
  loadModel,
  prepareModel,
  onStatus,
  isActive,
}) {
  assertRuntimeActive(isActive);
  onStatus?.(FRAGMENT_VIEWER_STATUS.converting);
  const loaded = await loadBytes({
    onProgress(update) {
      onStatus?.(statusForProgress(update.state));
    },
  });
  assertRuntimeActive(isActive);
  if (loaded.source === "fragment-cache") {
    onStatus?.(FRAGMENT_VIEWER_STATUS.cache);
  }
  assertRuntimeActive(isActive);
  const model = await loadModel(loaded.fragmentBytes);
  assertRuntimeActive(isActive);
  await prepareModel(model, loaded);
  assertRuntimeActive(isActive);
  onStatus?.(FRAGMENT_VIEWER_STATUS.ready);
  return { loaded, model };
}

export async function applyFragmentRenderPolicy({
  model,
  fragments,
  xray,
  selectedGlobalId,
  selectedGlobalIds,
  candidateGlobalIds,
  resolveLocalIds,
}) {
  const policy = deriveFragmentRenderPolicy({
    xray,
    selectedGlobalId,
    selectedGlobalIds,
    candidateGlobalIds,
  });

  // ThatOpen style overrides are explicit; clear both before each new policy.
  await model.resetColor(undefined);
  await model.resetOpacity(undefined);
  await fragments?.resetHighlight?.();

  if (policy.background.transparent) {
    await model.setOpacity(undefined, policy.background.opacity);
  }

  const applyItem = async (item) => {
    if (resolveLocalIds) {
      const localIds = [...await resolveLocalIds(item.globalId)];
      if (localIds.length > 0) {
        await model.setColor(localIds, item.color);
        await model.setOpacity(localIds, item.opacity);
      }
    } else if (fragments?.guidsToModelIdMap) {
      const selection = await fragments.guidsToModelIdMap([item.globalId]);
      for (const [modelId, ids] of Object.entries(selection)) {
        const selectedModel = fragments.list.get(modelId);
        if (!selectedModel || ids.size === 0) continue;
        const localIds = [...ids];
        await selectedModel.setColor(
          localIds,
          new THREE.Color(item.color),
        );
        await selectedModel.setOpacity(localIds, item.opacity);
      }
    }
  };

  for (const candidate of policy.candidates) {
    await applyItem(candidate);
  }
  for (const selected of policy.selectedItems) {
    await applyItem(selected);
  }

  await fragments?.core?.update?.(true);
  return policy;
}

function findResult(results, globalId) {
  return (Array.isArray(results) ? results : []).find((result) => (
    result?.id === globalId ||
    result?.globalId === globalId ||
    result?.global_id === globalId
  ));
}

function itemAttributeValue(item, key) {
  const entry = item?.[key];
  if (!entry) return null;
  if (typeof entry === "object" && "value" in entry) return entry.value;
  return entry;
}

async function resolveFragmentItemLabel(fragments, modelId, localId) {
  const model = fragments?.list?.get?.(modelId);
  if (!model || !Number.isInteger(localId)) return null;
  try {
    const items = await model.getItemsData([localId], {
      attributesDefault: true,
      attributes: ["Name"],
    });
    const item = items?.[0];
    const name = itemAttributeValue(item, "Name") ?? itemAttributeValue(item, "name");
    return typeof name === "string" && name.trim() ? name.trim() : null;
  } catch {
    return null;
  }
}

export async function dispatchFragmentPick({
  pick,
  fragments,
  results,
  onSelectResult,
}) {
  const modelId = pick?.fragments?.modelId ?? pick?.modelId;
  const localId = pick?.localId;
  if (!modelId || !Number.isInteger(localId)) return null;

  const globalIds = await fragments.modelIdMapToGuids({
    [modelId]: new Set([localId]),
  });
  const globalId = globalIds?.[0];
  if (typeof globalId !== "string" || globalId.length === 0) return null;

  const result = findResult(results, globalId);
  const fragmentLabel = await resolveFragmentItemLabel(fragments, modelId, localId);
  const label = result?.name ?? result?.label ?? fragmentLabel ?? globalId;
  const metadata = {
    label,
    type: result?.type ?? result?.ifcType ?? "IFC component",
  };
  onSelectResult?.(globalId, metadata);
  return globalId;
}
