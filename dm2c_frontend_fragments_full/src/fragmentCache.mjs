const DATABASE_NAME = "dm2c-full-fragment-cache";
const DATABASE_VERSION = 1;
const STORE_NAME = "fragments";

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(
      transaction.error ?? new Error("Fragment cache transaction failed"),
    );
    transaction.onabort = () => reject(
      transaction.error ?? new Error("Fragment cache transaction aborted"),
    );
  });
}

function openDatabase(indexedDB) {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(
      request.error ?? new Error("Could not open the fragment cache"),
    );
  });
}

export function createIndexedDbFragmentCache(indexedDB = globalThis.indexedDB) {
  if (!indexedDB) {
    throw new Error("Fragment cache is unavailable because IndexedDB is not supported");
  }
  let databasePromise;
  const database = () => {
    databasePromise ??= openDatabase(indexedDB);
    return databasePromise;
  };

  return {
    async get(key) {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readonly");
      return requestResult(transaction.objectStore(STORE_NAME).get(key));
    },
    async set(key, value) {
      const db = await database();
      const transaction = db.transaction(STORE_NAME, "readwrite");
      transaction.objectStore(STORE_NAME).put(value, key);
      await transactionDone(transaction);
    },
  };
}

function toHex(arrayBuffer) {
  return Array.from(
    new Uint8Array(arrayBuffer),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function fragmentCacheKey(ifcBytes, converterVersion) {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", ifcBytes);
  return `${converterVersion}:${toHex(digest)}`;
}
