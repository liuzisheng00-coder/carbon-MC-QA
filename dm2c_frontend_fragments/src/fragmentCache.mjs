const CACHE_KEY_PREFIX = "dm2c-fragment-cache:v1";

function toHex(bytes) {
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function fragmentCacheKey(ifcBytes, converterVersion) {
  const digest = await crypto.subtle.digest("SHA-256", ifcBytes);
  return `${CACHE_KEY_PREFIX}:${converterVersion}:${toHex(digest)}`;
}
