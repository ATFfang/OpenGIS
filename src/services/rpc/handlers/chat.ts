/**
 * rpc.ui.chat.* handlers.
 *
 * show_text / show_table remain lightweight stubs; normal assistant text
 * arrives through MessagePart events. show_image is implemented: the backend
 * save_plot tool writes
 *   PNG 落到 workspace/assets/plots/ 后调用本 handler，参数仅传路径，
 *   前端读文件 → Blob URL → 注入 chatStore 渲染。
 */

import type { RpcHandler } from '../registry';
import { notImplemented, parseParams } from './_util';
import {
  PlanUpdateSchema,
  ShowImageSchema,
  ShowTableSchema,
  ShowTextSchema,
  SubagentUpdateSchema,
} from './schemas';
import { useChatStore } from '@/stores/chatStore';
import { pathToImageUrl } from './_image_url';
import type { MessagePart, PlanData, SubagentData } from '@/types/chat';

type ScreenshotData = {
  requestId: string
  savePath: string
  prompt: string
}

export const chatHandlers: Record<string, RpcHandler> = {
  'rpc.ui.chat.show_text': (params) => {
    parseParams(ShowTextSchema, params, 'rpc.ui.chat.show_text');
    notImplemented('rpc.ui.chat.show_text');
  },

  'rpc.ui.chat.show_image': async (params) => {
    const parsed = parseParams(ShowImageSchema, params, 'rpc.ui.chat.show_image');

    const url = await pathToImageUrl(parsed.path);
    const store = useChatStore.getState();
    const conv = store.activeConversation();

    // A plot can arrive while the agent is still inside execute_code. Close
    // any transient spinner so the image is visible as a first-class result.
    if (conv) {
      for (let i = conv.messages.length - 1; i >= 0; i--) {
        const msg = conv.messages[i];
        if ((msg.say === 'progress' || msg.say === 'thinking') && msg.partial) {
          store._updateMessage(msg.ts, { partial: false });
          break;
        }
        if (msg.say === 'image' || msg.say === 'text' || msg.say === 'code_result') {
          break;
        }
      }
    }

    // Inject as a `say='image'` message. ImageRow renders the image and
    // a persistent "Pin to map" button.
    store._addMessage({
      ts: Date.now(),
      type: 'say',
      say: 'image',
      text: parsed.caption ?? '',
      images: [url],
      // Stash the absolute path so the Pin button can pass it back to
      // the map handler without re-reading the blob URL.
      files: [parsed.path],
      runId: parsed.run_id,
      parts: [imageMessagePart({
        caption: parsed.caption ?? '',
        images: [url],
        files: [parsed.path],
        runId: parsed.run_id,
      })],
    });

    return { ok: true, path: parsed.path };
  },

  'rpc.ui.chat.show_table': (params) => {
    parseParams(ShowTableSchema, params, 'rpc.ui.chat.show_table');
    notImplemented('rpc.ui.chat.show_table');
  },

  /**
   * Plan / TODO checklist. The backend `update_plan` tool sends the FULL
   * plan on every call; we upsert a single `say='plan'` message keyed by
   * `plan_id` so repeated updates animate the same card in place rather
   * than spamming the chat with one card per update.
   */
  'rpc.ui.chat.plan_update': (params) => {
    const parsed = parseParams(PlanUpdateSchema, params, 'rpc.ui.chat.plan_update');

    const planData: PlanData = {
      planId: parsed.plan_id,
      title: parsed.title,
      steps: parsed.steps,
      runId: parsed.run_id,
      workflow: Boolean(parsed.workflow),
      updatedAt: Date.now(),
    };

    const store = useChatStore.getState();
    const conv = store.activeConversation();

    // Upsert: find an existing plan message with the same planId in the
    // active conversation and update it in place; otherwise append a new
    // one. Searching newest-first keeps it O(updates) in practice.
    let existingTs: number | null = null;
    if (conv) {
      for (let i = conv.messages.length - 1; i >= 0; i--) {
        const m = conv.messages[i];
        if (m.say === 'plan' && m.planData?.planId === parsed.plan_id) {
          existingTs = m.ts;
          break;
        }
      }
    }

    if (existingTs != null) {
      store._updateMessage(existingTs, { planData, parts: [planMessagePart(planData)] });
    } else {
      store._addMessage({
        ts: Date.now(),
        type: 'say',
        say: 'plan',
        planData,
        runId: parsed.run_id,
        parts: [planMessagePart(planData)],
      });
    }

    // Track workflow compact mode only while a workflow is actively running.
    // Completed / failed / skipped-only plans should not suppress later normal
    // agent messages in the same conversation.
    if (parsed.workflow) {
      const active = parsed.steps.some((step) => step.status === 'in_progress')
      useChatStore.setState({ workflowPlanActive: active })
    }

    return { ok: true, plan_id: parsed.plan_id, steps: parsed.steps.length };
  },

  /**
   * Sub-agent running indicator. The backend run_subagent / run_subagents
   * tools push a content-free status card (task titles + state) while an
   * isolated child agent churns — mirroring opencode's collapsed sub-agent
   * affordance. Upserted by `subagent_id` so the running → done transition
   * (and per-task progress in a parallel fan-out) animates the same card.
   */
  'rpc.ui.chat.subagent_update': (params) => {
    const parsed = parseParams(SubagentUpdateSchema, params, 'rpc.ui.chat.subagent_update');

    const store = useChatStore.getState();
    const conv = store.activeConversation();

    let existingTs: number | null = null;
    let startedAt: number | undefined;
    if (conv) {
      for (let i = conv.messages.length - 1; i >= 0; i--) {
        const m = conv.messages[i];
        if (m.say === 'subagent' && m.subagentData?.subagentId === parsed.subagent_id) {
          existingTs = m.ts;
          startedAt = m.subagentData?.startedAt;
          break;
        }
      }
    }

    const now = Date.now();
    const subagentData: SubagentData = {
      subagentId: parsed.subagent_id,
      status: parsed.status,
      parallel: parsed.parallel ?? parsed.tasks.length > 1,
      tasks: parsed.tasks.map((task) =>
        parsed.status !== 'running' && task.status === 'running'
          ? { ...task, status: parsed.status === 'done' ? 'done' : parsed.status }
          : task
      ),
      okCount: parsed.ok_count,
      total: parsed.total ?? parsed.tasks.length,
      runId: parsed.run_id,
      startedAt: startedAt ?? now,
      updatedAt: now,
    };

    if (existingTs != null) {
      store._updateMessage(existingTs, { subagentData, parts: [subagentMessagePart(subagentData)] });
    } else {
      store._addMessage({
        ts: now,
        type: 'say',
        say: 'subagent',
        subagentData,
        runId: parsed.run_id,
        parts: [subagentMessagePart(subagentData)],
      });
    }

    return { ok: true, subagent_id: parsed.subagent_id };
  },

  /**
   * Interactive map screenshot request. The backend tool sends this
   * to show a capture card in the chat. The user adjusts the map and
   * clicks "Capture" — the frontend saves the image and writes a
   * result marker so the backend tool can unblock.
   */
  'rpc.ui.chat.interactive_snapshot': (params) => {
    const raw = (params ?? {}) as Record<string, unknown>;
    const requestId = raw.request_id as string;
    const savePath = raw.save_path as string;
    const prompt = raw.prompt as string;

    if (!requestId || !savePath) {
      return { ok: false, error: 'Missing request_id or save_path' };
    }

    const store = useChatStore.getState();
    const screenshotData = { requestId, savePath, prompt: prompt || '' };
    store._addMessage({
      ts: Date.now(),
      type: 'say',
      say: 'screenshot',
      screenshotData,
      parts: [screenshotMessagePart(screenshotData)],
    });

    return { ok: true, request_id: requestId };
  },
};

function planMessagePart(planData: PlanData): MessagePart {
  return {
    id: `plan:${planData.planId}`,
    type: 'plan',
    status: planData.steps.some((step) => step.status === 'in_progress')
      ? 'running'
      : planData.steps.some((step) => step.status === 'failed')
        ? 'failed'
        : 'completed',
    runId: planData.runId,
    data: { planData },
    createdAt: planData.updatedAt,
  }
}

function subagentMessagePart(subagentData: SubagentData): MessagePart {
  return {
    id: `subagent:${subagentData.subagentId}`,
    type: 'progress',
    status: subagentData.status === 'running'
      ? 'running'
      : subagentData.status === 'failed'
        ? 'failed'
        : subagentData.status === 'cancelled'
          ? 'cancelled'
          : 'completed',
    runId: subagentData.runId,
    data: { kind: 'subagent', subagentData },
    createdAt: subagentData.updatedAt,
  }
}

function imageMessagePart({
  caption,
  images,
  files,
  runId,
}: {
  caption: string
  images: string[]
  files: string[]
  runId?: string
}): MessagePart {
  return {
    id: `image:${runId ?? 'no-run'}:${files[0] ?? images[0] ?? Date.now()}`,
    type: 'artifact',
    status: 'completed',
    text: caption,
    runId,
    data: { kind: 'image', images, files },
    createdAt: Date.now(),
  }
}

function screenshotMessagePart(screenshotData: ScreenshotData): MessagePart {
  return {
    id: `screenshot:${screenshotData.requestId}`,
    type: 'approval',
    status: 'pending',
    data: { kind: 'screenshot', screenshotData },
    createdAt: Date.now(),
  }
}
