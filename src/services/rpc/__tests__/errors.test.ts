import { RpcError, RPC_ERROR_CODES } from '../errors';

describe('RpcError', () => {
  it('has correct JSON-RPC error codes', () => {
    expect(RPC_ERROR_CODES.PARSE_ERROR).toBe(-32700);
    expect(RPC_ERROR_CODES.INVALID_REQUEST).toBe(-32600);
    expect(RPC_ERROR_CODES.METHOD_NOT_FOUND).toBe(-32601);
    expect(RPC_ERROR_CODES.INVALID_PARAMS).toBe(-32602);
    expect(RPC_ERROR_CODES.INTERNAL_ERROR).toBe(-32603);
    expect(RPC_ERROR_CODES.TIMEOUT).toBe(-32000);
    expect(RPC_ERROR_CODES.USER_CANCELLED).toBe(-32001);
    expect(RPC_ERROR_CODES.PERMISSION_DENIED).toBe(-32002);
    expect(RPC_ERROR_CODES.SANDBOX_ERROR).toBe(-32003);
    expect(RPC_ERROR_CODES.LLM_ERROR).toBe(-32004);
  });

  it('factory: methodNotFound carries method in data', () => {
    const err = RpcError.methodNotFound('rpc.x.y');
    expect(err.code).toBe(-32601);
    expect(err.message).toContain('rpc.x.y');
    expect((err.data as { method: string }).method).toBe('rpc.x.y');
  });

  it('factory: invalidParams produces -32602', () => {
    const err = RpcError.invalidParams('bad', { field: 'x' });
    expect(err.code).toBe(-32602);
    expect(err.data).toEqual({ field: 'x' });
  });

  it('factory: notImplemented produces -32603 stage-1-stub marker', () => {
    const err = RpcError.notImplemented('rpc.ui.map.add_layer');
    expect(err.code).toBe(-32603);
    expect((err.data as { stage: string }).stage).toBe('stage-1-stub');
  });

  it('toResponse produces valid JSON-RPC error response', () => {
    const err = RpcError.invalidParams('bad');
    const resp = err.toResponse('req-1');
    expect(resp).toMatchObject({
      jsonrpc: '2.0',
      id: 'req-1',
      error: { code: -32602, message: 'bad' },
    });
  });
});
