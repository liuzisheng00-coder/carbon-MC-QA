import { fragmentCacheKey } from "./fragmentCache.mjs";

export const FRAGMENT_CONVERTER_VERSION = "@thatopen/fragments@3.4.7";

const DATABASE_NAME = "dm2c-fragment-cache";
const DATABASE_VERSION = 1;
const STORE_NAME = "fragments";

export class FragmentLoadError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "FragmentLoadError";
    this.source = "fragment-error";
  }
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => {
      reject(request.error ?? new Error("IndexedDB request failed"));
    };
  });
}

function openFragmentDatabase(indexedDB) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);

    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        database.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => {
      reject(request.error ?? new Error("Could not open the fragment cache"));
    };
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => {
      reject(transaction.error ?? new Error("Fragment cache transaction failed"));
    };
    transaction.onabort = () => {
      reject(transaction.error ?? new Error("Fragment cache transaction aborted"));
    };
  });
}

export function createIndexedDbFragmentCache(
  indexedDB = globalThis.indexedDB,
) {
  if (!indexedDB) {
    throw new FragmentLoadError(
      "Fragment cache is unavailable because IndexedDB is not supported",
    );
  }

  let databasePromise;
  const database = () => {
    databasePromise ??= openFragmentDatabase(indexedDB);
    return databasePromise;
  };

  return {
    async get(key) {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readonly");
      return requestResult(transaction.objectStore(STORE_NAME).get(key));
    },
    async set(key, fragmentBytes) {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readwrite");
      transaction.objectStore(STORE_NAME).put(fragmentBytes, key);
      await transactionDone(transaction);
    },
  };
}

function normalizeBytes(value, label) {
  if (value instanceof Uint8Array) {
    return value;
  }
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  throw new FragmentLoadError(`${label} did not contain binary data`);
}

function expectedIfcPath(projectId) {
  if (typeof projectId !== "string" || projectId.length === 0) {
    throw new FragmentLoadError("A non-empty projectId is required");
  }
  return `/api/projects/${encodeURIComponent(projectId)}/ifc-file`;
}

function validateIfcFileUrl(projectId, ifcFileUrl) {
  if (typeof ifcFileUrl !== "string" || ifcFileUrl.length === 0) {
    throw new FragmentLoadError("An IFC file URL is required");
  }

  const expectedPath = expectedIfcPath(projectId);
  let parsed;
  try {
    parsed = new URL(ifcFileUrl, "http://fragment-loader.invalid");
  } catch (error) {
    throw new FragmentLoadError("The IFC file URL is invalid", {
      cause: error,
    });
  }

  if (
    parsed.pathname !== expectedPath ||
    parsed.search.length > 0 ||
    parsed.hash.length > 0
  ) {
    throw new FragmentLoadError(
      `The IFC file URL must end with ${expectedPath}`,
    );
  }

  return ifcFileUrl;
}

function progress(onProgress, state, detail = {}) {
  onProgress?.({ state, ...detail });
}

function workerError(message, cause) {
  return new FragmentLoadError(
    `Fragment conversion failed: ${message || "unknown Worker error"}`,
    cause ? { cause } : undefined,
  );
}

function convertInWorker(ifcBytes, onProgress) {
  return new Promise((resolve, reject) => {
    if (typeof Worker !== "function") {
      reject(workerError("module Workers are unavailable"));
      return;
    }

    const worker = new Worker(
      new URL("./fragmentWorker.mjs", import.meta.url),
      { type: "module" },
    );
    let settled = false;

    const finish = (callback, value) => {
      if (settled) {
        return;
      }
      settled = true;
      worker.terminate();
      callback(value);
    };

    worker.onmessage = ({ data }) => {
      if (data?.type === "progress") {
        progress(onProgress, "fragment-converting", {
          progress: data.progress,
          message: data.message,
        });
        return;
      }
      if (data?.type === "result") {
        try {
          finish(
            resolve,
            normalizeBytes(data.fragmentBytes, "The converter result"),
          );
        } catch (error) {
          finish(reject, error);
        }
        return;
      }
      if (data?.type === "error") {
        finish(reject, workerError(data.message));
        return;
      }
      finish(reject, workerError("the Worker returned an unknown message"));
    };
    worker.onerror = (event) => {
      event.preventDefault?.();
      finish(reject, workerError(event.message, event.error));
    };
    worker.onmessageerror = (event) => {
      finish(reject, workerError("the Worker response could not be decoded", event));
    };

    const transferableBytes = ifcBytes.buffer.slice(
      ifcBytes.byteOffset,
      ifcBytes.byteOffset + ifcBytes.byteLength,
    );
    worker.postMessage(
      { type: "convert", ifcBytes: transferableBytes },
      [transferableBytes],
    );
  });
}

function asFragmentLoadError(error) {
  if (error instanceof FragmentLoadError) {
    return error;
  }
  const detail =
    error instanceof Error && error.message ? `: ${error.message}` : "";
  return new FragmentLoadError(`Fragment loading failed${detail}`, {
    cause: error,
  });
}

export async function loadProjectFragment({
  projectId,
  ifcFileUrl,
  cache,
  onProgress,
}) {
  try {
    const requestUrl = validateIfcFileUrl(projectId, ifcFileUrl);
    const fragmentCache = cache ?? createIndexedDbFragmentCache();

    progress(onProgress, "fragment-fetch");
    const response = await fetch(requestUrl);
    if (!response.ok) {
      throw new FragmentLoadError(
        `IFC file request failed with HTTP ${response.status}`,
      );
    }

    const ifcBytes = new Uint8Array(await response.arrayBuffer());
    const key = await fragmentCacheKey(
      ifcBytes,
      FRAGMENT_CONVERTER_VERSION,
    );

    progress(onProgress, "fragment-cache-lookup");
    const cachedBytes = await fragmentCache.get(key);
    if (cachedBytes !== undefined && cachedBytes !== null) {
      progress(onProgress, "fragment-ready");
      return {
        source: "fragment-cache",
        fragmentBytes: normalizeBytes(cachedBytes, "The fragment cache"),
      };
    }

    progress(onProgress, "fragment-converting");
    const fragmentBytes = await convertInWorker(ifcBytes, onProgress);

    progress(onProgress, "fragment-cache-write");
    await fragmentCache.set(key, fragmentBytes);
    progress(onProgress, "fragment-ready");

    return {
      source: "fragment-converted",
      fragmentBytes,
    };
  } catch (error) {
    throw asFragmentLoadError(error);
  }
}
