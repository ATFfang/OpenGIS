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

/**
 * Resolve a local absolute path to a URL usable as `<img src>` /
 * MapLibre ImageSource `url`. Falls back to `file://` if the Electron
 * IPC bridge is unavailable (e.g. running unit tests in jsdom).
 */
export async function pathToImageUrl(path: string): Promise<string> {
  const cached = _urlCache.get(path);
  if (cached) return cached;

  const api = (globalThis as any).window?.electronAPI;
  if (api?.readFileAsBuffer) {
    try {
      const result = await api.readFileAsBuffer(path);
      const ok = result?.ok ?? result?.success ?? false;
      if (ok && result.buffer) {
        const buf =
          result.buffer instanceof ArrayBuffer
            ? result.buffer
            : new Uint8Array(result.buffer).buffer;
        const blob = new Blob([buf], { type: mimeFromPath(path) });
        const url = URL.createObjectURL(blob);
        _urlCache.set(path, url);
        return url;
      }
    } catch (err) {
      console.warn('[pathToImageUrl] readFileAsBuffer failed for', path, err);
    }
  }

  // Last-ditch: file:// (works in dev with webSecurity loosened)
  const fallback =
    'file:///' + path.replace(/\\/g, '/').replace(/^\/+/, '');
  _urlCache.set(path, fallback);
  return fallback;
}

/** Drop a cached URL (and revoke it) — call when the image is no longer needed. */
export function releaseImageUrl(path: string): void {
  const url = _urlCache.get(path);
  if (url && url.startsWith('blob:')) {
    URL.revokeObjectURL(url);
  }
  _urlCache.delete(path);
}
