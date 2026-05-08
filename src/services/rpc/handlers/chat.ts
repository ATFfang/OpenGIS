/**
 * rpc.ui.chat.* handlers — 3 个
 *
 * Stage 1：show_text / show_table 仍是 stub（轻量内容直接走 stream_delta）。
 * Stage 3.12 (2026-04-28)：show_image 真实现。后端 save_plot skill 把
 *   PNG 落到 workspace/assets/plots/ 后调用本 handler，参数仅传路径，
 *   前端读文件 → Blob URL → 注入 chatStore 渲染。
 */

import type { RpcHandler } from '../registry';
import { notImplemented, parseParams } from './_util';
import { ShowImageSchema, ShowTableSchema, ShowTextSchema } from './schemas';
import { useChatStore } from '@/stores/chatStore';
import { pathToImageUrl } from './_image_url';

export const chatHandlers: Record<string, RpcHandler> = {
  'rpc.ui.chat.show_text': (params) => {
    parseParams(ShowTextSchema, params, 'rpc.ui.chat.show_text');
    notImplemented('rpc.ui.chat.show_text');
  },

  'rpc.ui.chat.show_image': async (params) => {
    const parsed = parseParams(ShowImageSchema, params, 'rpc.ui.chat.show_image');

    const url = await pathToImageUrl(parsed.path);

    // Inject as a `say='image'` message. ImageRow renders the image and
    // a "Pin to map" button which calls rpc.ui.map.add_image_overlay.
    useChatStore.getState()._addMessage({
      ts: Date.now(),
      type: 'say',
      say: 'image',
      text: parsed.caption ?? '',
      images: [url],
      // Stash the absolute path so the Pin button can pass it back to
      // the map handler without re-reading the blob URL.
      files: [parsed.path],
    });

    return { ok: true, path: parsed.path };
  },

  'rpc.ui.chat.show_table': (params) => {
    parseParams(ShowTableSchema, params, 'rpc.ui.chat.show_table');
    notImplemented('rpc.ui.chat.show_table');
  },
};
