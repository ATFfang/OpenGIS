/**
 * ID 生成器 — 全部 TS 端生成，前缀约定见 `docs/api/INTERFACE.md` §0.3
 *
 * 前缀：
 *   - layer_   会话/工程级
 *   - asset_   工作区级
 *   - script_  永久
 *   - run_     永久
 *   - msg_     会话级
 */

import { v4 as uuidv4 } from 'uuid';

export const ID_PREFIXES = {
  layer: 'layer_',
  asset: 'asset_',
  script: 'script_',
  run: 'run_',
  msg: 'msg_',
} as const;

export type IdKind = keyof typeof ID_PREFIXES;

function gen(prefix: string): string {
  return `${prefix}${uuidv4()}`;
}

export const newLayerId = (): string => gen(ID_PREFIXES.layer);
export const newAssetId = (): string => gen(ID_PREFIXES.asset);
export const newScriptId = (): string => gen(ID_PREFIXES.script);
export const newRunId = (): string => gen(ID_PREFIXES.run);
export const newMsgId = (): string => gen(ID_PREFIXES.msg);

/** 泛化工厂，给 dispatcher / 测试用。 */
export function newId(kind: IdKind): string {
  return gen(ID_PREFIXES[kind]);
}

/** 从 id 字符串推断 kind，未知返回 null。 */
export function inferIdKind(id: string): IdKind | null {
  for (const [kind, prefix] of Object.entries(ID_PREFIXES) as Array<[IdKind, string]>) {
    if (id.startsWith(prefix)) return kind;
  }
  return null;
}

/** 校验 id 是否形如 `<prefix><uuid>`。 */
export function isValidId(id: string, kind: IdKind): boolean {
  const prefix = ID_PREFIXES[kind];
  if (!id.startsWith(prefix)) return false;
  const rest = id.slice(prefix.length);
  // RFC 4122 UUID v4-ish（宽松）：8-4-4-4-12 hex
  return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
    rest,
  );
}
