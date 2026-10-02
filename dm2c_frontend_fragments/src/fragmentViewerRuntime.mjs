import * as THREE from "three";

export const FRAGMENT_VIEWER_STATUS = Object.freeze({
  converting: "Converting IFC",
  cache: "Fragment cache",
  ready: "Fragment ready",
  error: "Fragment error",
});

export function isXrayEnabled(status) {
  return status === FRAGMENT_VIEWER_STATUS.ready;
}

function loaderProgressStatus(state) {
  if (
    state === "fragment-cache-lookup" ||
    state === "fragment-cache-write" ||
    state === "fragment-ready"
  ) {
    return FRAGMENT_VIEWER_STATUS.cache;
  }
  return FRAGMENT_VIEWER_STATUS.converting;
}

export async function applyFragmentRenderPolicy(
  { fragments, model },
  policy,
) {
  await model.resetColor(undefined);
  await model.resetOpacity(undefined);
  await fragments.resetHighlight();

  if (policy.background.transparent) {
    await model.setOpacity(undefined, policy.background.opacity);
  }

  if (policy.selected) {
    const selection = await fragments.guidsToModelIdMap([
      policy.selected.globalId,
    ]);
    const selectedColor = new THREE.Color(policy.selected.color);

    for (const [modelId, localIdSet] of Object.entries(selection)) {
      if (localIdSet.size === 0) {
        continue;
      }
      const selectedModel = fragments.list.get(modelId);
      if (!selectedModel) {
        continue;
      }
      const localIds = [...localIdSet];

      await selectedModel.setColor(localIds, selectedColor);
      await selectedModel.setOpacity(localIds, policy.selected.opacity);
    }
  }

  await fragments.core.update(true);
}

export async function loadFragmentRuntime({
  loadBytes,
  loadModel,
  prepareModel,
  onStatus,
}) {
  onStatus(FRAGMENT_VIEWER_STATUS.converting);
  const loaded = await loadBytes({
    onProgress(update) {
      onStatus(loaderProgressStatus(update.state));
    },
  });

  if (loaded.source === "fragment-cache") {
    onStatus(FRAGMENT_VIEWER_STATUS.cache);
  }

  const model = await loadModel(loaded.fragmentBytes);
  await prepareModel(model, loaded);
  onStatus(FRAGMENT_VIEWER_STATUS.ready);

  return { loaded, model };
}
