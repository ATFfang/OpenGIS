import {
  newLayerId,
  newAssetId,
  newScriptId,
  newRunId,
  newMsgId,
  newId,
  inferIdKind,
  isValidId,
  ID_PREFIXES,
} from '../idGen';

describe('idGen', () => {
  it('produces ids with the correct prefix', () => {
    expect(newLayerId()).toMatch(/^layer_[0-9a-f-]+$/i);
    expect(newAssetId()).toMatch(/^asset_/);
    expect(newScriptId()).toMatch(/^script_/);
    expect(newRunId()).toMatch(/^run_/);
    expect(newMsgId()).toMatch(/^msg_/);
  });

  it('generates unique ids on successive calls', () => {
    const ids = new Set<string>();
    for (let i = 0; i < 100; i += 1) ids.add(newLayerId());
    expect(ids.size).toBe(100);
  });

  it('newId(kind) matches the per-kind factory prefix', () => {
    expect(newId('layer').startsWith(ID_PREFIXES.layer)).toBe(true);
    expect(newId('asset').startsWith(ID_PREFIXES.asset)).toBe(true);
  });

  it('inferIdKind recognises valid prefixes', () => {
    expect(inferIdKind(newLayerId())).toBe('layer');
    expect(inferIdKind(newScriptId())).toBe('script');
    expect(inferIdKind('random_xxx')).toBe(null);
  });

  it('isValidId checks prefix + uuid shape', () => {
    const id = newLayerId();
    expect(isValidId(id, 'layer')).toBe(true);
    expect(isValidId(id, 'asset')).toBe(false);
    expect(isValidId('layer_not-uuid', 'layer')).toBe(false);
  });
});
