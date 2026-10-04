import { describe, expect, it } from 'vitest';
import { getLatestPatchId } from './selectors';
import type { TaskEvent } from '../types';

function ev(seq: number, event_type: string, payload: Record<string, unknown>): TaskEvent {
  return { event_seq: seq, event_type, payload, created_at: 'now' };
}

describe('getLatestPatchId', () => {
  it('从 PATCH_GENERATED 取补丁 id（P0-3 自动生成路径）', () => {
    const id = getLatestPatchId([ev(1, 'PATCH_GENERATION_STARTED', {}), ev(2, 'PATCH_GENERATED', { patch_id: 7 })]);
    expect(id).toBe(7);
  });

  it('从 PATCH_VALIDATED 取补丁 id（人工提交路径仍然可用）', () => {
    expect(getLatestPatchId([ev(1, 'PATCH_VALIDATED', { patch_id: 3 })])).toBe(3);
  });

  it('两类事件混用时取序号更大的那一个', () => {
    const id = getLatestPatchId([ev(1, 'PATCH_GENERATED', { patch_id: 7 }), ev(2, 'PATCH_VALIDATED', { patch_id: 9 })]);
    expect(id).toBe(9);
  });

  it('没有补丁事件时返回 null', () => {
    expect(getLatestPatchId([ev(1, 'TASK_CREATED', {})])).toBeNull();
  });
});
