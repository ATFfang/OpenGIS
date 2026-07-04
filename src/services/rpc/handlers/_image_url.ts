/**
 * Convert an absolute local image path into a renderer-safe URL.
 *
 * In Electron with `webSecurity: true`, `file://` URLs in <img src> can
 * trip CORS / mixed-content blocks (esp. once the renderer is served from
 * `http://localhost:5173`). We instead read the file via the preload
 * `electronAPI.readFileAsBuffer` IPC and wrap the bytes in a Blob URL —
 * works the same in dev and packaged mode, and MapLibre's ImageSource
 * accepts it just fine.
 *
 * Cache: the same path always resolves to the same Blob URL within a
 * session, so re-rendering the same chat row or pinning the same image
 * to the map doesn't allocate more memory.
 */

const _urlCache = new Map<string, string>();

const EXT_MIME: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.svg': 'image/svg+xml',
  '.bmp': 'image/bmp',
};

function mimeFromPath(path: string): string {
  const dot = path.lastIndexOf('.');
  if (dot < 0) return 'application/octet-stream';
  const ext = path.slice(dot).toLowerCase();
  return EXT_MIME[ext] ?? 'application/octet-stream';
}

function normalizeLocalImagePath(path: string): string {
  let value = path.trim();
  if (/^file:\/\//i.test(value)) {
    try {
      value = new URL(value).pathname;
    } catch {
      value = value.replace(/^file:\/\//i, '');
    }
  }
  try {
    value = decodeURIComponent(value);
  } catch {
    // Keep the original path if it is not percent-encoded.
  }
  return value;
}

function toArrayBuffer(buffer: unknown): ArrayBuffer | null {
  if (buffer instanceof ArrayBuffer) return buffer;
  if (ArrayBuffer.isView(buffer)) {
    const view = buffer as ArrayBufferView;
    const bytes = new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
    return new Uint8Array(bytes).buffer;
  }
  if (
    buffer
    && typeof buffer === 'object'
    && Array.isArray((buffer as { data?: unknown }).data)
  ) {
    return new Uint8Array((buffer as { data: number[] }).data).buffer;
  }
  return null;
}

/**
 * Resolve a local absolute path to a URL usable as `<img src>` /
 * MapLibre ImageSource `url`.
 */
export async function pathToImageUrl(path: string): Promise<string> {
  const normalizedPath = normalizeLocalImagePath(path);
  const cached = _urlCache.get(normalizedPath);
  if (cached) return cached;

  const api = (globalThis as any).window?.electronAPI;
  if (api?.readFileAsBuffer) {
    const result = await api.readFileAsBuffer(normalizedPath);
    const ok = result?.ok ?? result?.success ?? false;
    const buf = ok ? toArrayBuffer(result.buffer) : null;
    if (ok && buf) {
      const blob = new Blob([buf], { type: mimeFromPath(normalizedPath) });
      const url = URL.createObjectURL(blob);
      _urlCache.set(normalizedPath, url);
      return url;
    }
    throw new Error(result?.error || `Unable to read image: ${normalizedPath}`);
  }

  // Last-ditch for non-Electron tests / browser-only demos. In the real
  // Electron renderer this branch is intentionally skipped because Chromium
  // blocks file:// images from the localhost app origin.
  const fallback =
    'file:///' + normalizedPath.replace(/\\/g, '/').replace(/^\/+/, '');
  _urlCache.set(normalizedPath, fallback);
  return fallback;
}

/** Drop a cached URL (and revoke it) — call when the image is no longer needed. */
export function releaseImageUrl(path: string): void {
  const normalizedPath = normalizeLocalImagePath(path);
  const url = _urlCache.get(normalizedPath);
  if (url && url.startsWith('blob:')) {
    URL.revokeObjectURL(url);
  }
  _urlCache.delete(normalizedPath);
}
