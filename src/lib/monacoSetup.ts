/**
 * Monaco local bootstrap — must be imported exactly once, before any
 * component from ``@monaco-editor/react`` is rendered.
 *
 * Why this file exists
 * --------------------
 * By default, ``@monaco-editor/react`` lazy-loads the Monaco runtime
 * from a jsDelivr CDN (``https://cdn.jsdelivr.net/.../vs/loader.js``).
 * Our Electron app ships a strict CSP in ``index.html``:
 *
 *     script-src 'self' 'sha256-…'
 *
 * So the CDN fetch is blocked and the editor never mounts — that is the
 * error you saw:
 *
 *     Refused to load the script 'https://cdn.jsdelivr.net/...loader.js'
 *     because it violates the following Content Security Policy ...
 *
 * Fix: bundle ``monaco-editor`` locally (already ``npm install``-ed) and
 * hand it to the React wrapper via ``loader.config({ monaco })``. After
 * that, Monaco lives entirely inside our own bundle — no network I/O,
 * no CSP violations, and offline-first (important for a desktop GIS
 * workstation).
 *
 * Workers
 * -------
 * Monaco's language services (TS/JSON/CSS/HTML + editor core) run in
 * Web Workers. Vite handles worker bundling natively via the
 * ``?worker`` suffix. We wire them up through ``MonacoEnvironment`` so
 * the editor can spawn the correct worker per language.
 */

import * as monaco from 'monaco-editor'
import { loader } from '@monaco-editor/react'

// Vite-native worker imports. Each of these produces a constructor for
// a dedicated Worker instance bundled from the corresponding monaco
// worker entry.
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'
import CssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker'
import HtmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker'
import TsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker'

// Hint TypeScript about the ambient ``MonacoEnvironment`` global.
declare global {
  interface Window {
    MonacoEnvironment?: {
      getWorker(_: unknown, label: string): Worker
    }
  }
}

self.MonacoEnvironment = {
  getWorker(_workerId: unknown, label: string): Worker {
    switch (label) {
      case 'json':
        return new JsonWorker()
      case 'css':
      case 'scss':
      case 'less':
        return new CssWorker()
      case 'html':
      case 'handlebars':
      case 'razor':
        return new HtmlWorker()
      case 'typescript':
      case 'javascript':
        return new TsWorker()
      default:
        return new EditorWorker()
    }
  },
}

// Point ``@monaco-editor/react`` at the locally bundled monaco module
// instead of the jsDelivr CDN. Must be called before the first <Editor/>
// renders; we guarantee that by importing this file from ``main.tsx``
// at app bootstrap.
loader.config({ monaco })

// Kick off initialization eagerly so the first editor open is instant.
// (``loader.init()`` resolves when monaco is ready; we don't need to
// await it, we only need the side effect.)
void loader.init()
