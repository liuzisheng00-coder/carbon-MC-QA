import {
  createIndexedDbFragmentCache,
  fragmentCacheKey,
} from "./fragmentCache.mjs";

export const FRAGMENT_CONVERTER_VERSION = "@thatopen/fragments@3.4.7";

export class FragmentLoadError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "FragmentLoadError";
    this.source = "fragment-error";
  }
}

const inFlightProjectLoads = new Map();

function normalizeBytes(value, label) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  throw new FragmentLoadError(`${label} did not contain binary data`);
}

function validateIfcFileUrl(projectId, ifcFileUrl) {
  if (typeof projectId !== "string" || projectId.length === 0) {
    throw new FragmentLoadError("A non-empty projectId is required");
  }
  if (typeof ifcFileUrl !== "string" || ifcFileUrl.length === 0) {
    throw new FragmentLoadError("An IFC file URL is required");
  }

  const expectedPath = `/api/projects/${encodeURIComponent(projectId)}/ifc-file`;
  let url;
  try {
    url = new URL(ifcFileUrl, "http://fragment-loader.invalid");
  } catch (error) {
    throw new FragmentLoadError("The IFC file URL is invalid", { cause: error });
  }
  if (url.pathname !== expectedPath || url.search || url.hash) {
    throw new FragmentLoadError(
      `The IFC file URL must end with /ifc-file (expected ${expectedPath})`,
    );
  }
  return ifcFileUrl;
}

function defaultWorkerFactory() {
  return new Worker(new URL("./fragmentWorker.mjs", import.meta.url), {
    type: "module",
  });
}

function convertInWorker(ifcBytes, workerFactory, onProgress) {
  return new Promise((resolve, reject) => {
    let worker;
    try {
      worker = workerFactory();
    } catch (error) {
      reject(new FragmentLoadError(`Fragment conversion failed: ${error.message}`, {
        cause: error,
      }));
      return;
    }

    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      worker.terminate?.();
      callback(value);
    };
    const onMessage = ({ data }) => {
      if (data?.type === "progress") {
        onProgress?.({ state: "fragment-converting", ...data });
        return;
      }
      if (data?.type === "converted") {
        try {
          finish(resolve, normalizeBytes(data.fragmentBytes, "The converter result"));
        } catch (error) {
          finish(reject, error);
        }
        return;
      }
      if (data?.type === "error") {
        finish(
          reject,
          new FragmentLoadError(
            `Fragment conversion failed: ${data.error ?? "unknown Worker error"}`,
          ),
        );
        return;
      }
      finish(
        reject,
        new FragmentLoadError("Fragment conversion failed: unknown Worker response"),
      );
    };
    const onError = (event) => {
      event.preventDefault?.();
      finish(
        reject,
        new FragmentLoadError(
          `Fragment conversion failed: ${event.message ?? "Worker error"}`,
          { cause: event.error },
        ),
      );
    };
    const onMessageError = () => finish(
      reject,
      new FragmentLoadError("Fragment conversion failed: Worker response could not be decoded"),
    );

    if (worker.addEventListener) {
      worker.addEventListener("message", onMessage);
      worker.addEventListener("error", onError);
      worker.addEventListener("messageerror", onMessageError);
    } else {
      worker.onmessage = onMessage;
      worker.onerror = onError;
      worker.onmessageerror = onMessageError;
    }

    const transferable = ifcBytes.buffer.slice(
      ifcBytes.byteOffset,
      ifcBytes.byteOffset + ifcBytes.byteLength,
    );
    worker.postMessage({ type: "convert", ifcBytes: transferable }, [transferable]);
  });
}

function asFragmentError(error) {
  if (error instanceof FragmentLoadError) return error;
  const detail = error instanceof Error ? error.message : String(error);
  return new FragmentLoadError(`Fragment loading failed: ${detail}`, { cause: error });
}

export async function loadProjectFragment({
  projectId,
  ifcFileUrl,
  cache,
  fetchImpl = globalThis.fetch,
  workerFactory = defaultWorkerFactory,
  onProgress,
}) {
  try {
    const requestUrl = validateIfcFileUrl(projectId, ifcFileUrl);
    const fragmentCache = cache ?? createIndexedDbFragmentCache();
    onProgress?.({ state: "fragment-fetch" });

    // Deliberately omit RequestInit: the cross-origin API uses no browser cookies.
    const response = await fetchImpl(requestUrl);
    if (!response.ok) {
      throw new FragmentLoadError(
        `IFC file request failed with HTTP ${response.status}`,
      );
    }

    const ifcBytes = new Uint8Array(await response.arrayBuffer());
    const cacheKey = await fragmentCacheKey(ifcBytes, FRAGMENT_CONVERTER_VERSION);
    onProgress?.({ state: "fragment-cache-lookup" });
    const cached = await fragmentCache.get(cacheKey);
    if (cached !== undefined && cached !== null) {
      return {
        source: "fragment-cache",
        fragmentBytes: normalizeBytes(cached, "The fragment cache"),
        cacheKey,
      };
    }

    onProgress?.({ state: "fragment-converting" });
    const fragmentBytes = await convertInWorker(ifcBytes, workerFactory, onProgress);
    await fragmentCache.set(cacheKey, fragmentBytes);
    return { source: "fragment-worker", fragmentBytes, cacheKey };
  } catch (error) {
    throw asFragmentError(error);
  }
}

function inFlightKey({ projectId, ifcFileUrl }) {
  return `${projectId}\u0000${ifcFileUrl}`;
}

// React.StrictMode intentionally replays effects in development. Keep one
// conversion alive for the exact same project IFC so its probe and live setup
// share download, cache lookup, and Worker work.
export function loadSharedProjectFragment(options) {
  const key = inFlightKey(options);
  const existing = inFlightProjectLoads.get(key);
  if (existing) return existing;

  const pending = loadProjectFragment(options);
  inFlightProjectLoads.set(key, pending);
  void pending.then(
    () => {
      if (inFlightProjectLoads.get(key) === pending) {
        inFlightProjectLoads.delete(key);
      }
    },
    () => {
      if (inFlightProjectLoads.get(key) === pending) {
        inFlightProjectLoads.delete(key);
      }
    },
  );
  return pending;
}
