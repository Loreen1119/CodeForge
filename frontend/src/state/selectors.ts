import type { ChangePlanDTO, PlanReviewDTO, TaskEvent } from '../types';

/**
 * 事件是唯一事实源：所有派生视图（方案、审查结论、失败原因…）都从 events 投影，
 * reducer 里不再维护第二份状态。
 */

export interface PlanView {
  planId: number | null;
  plan: ChangePlanDTO | null;
  review: PlanReviewDTO | null;
}

export interface NeedsContext {
  findings: string[];
  missingContext: string[];
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number') return value;
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null;
}

export function getLatestEvent(events: TaskEvent[], eventType: string): TaskEvent | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    if (events[i].event_type === eventType) return events[i];
  }
  return null;
}

export function getPlanView(events: TaskEvent[]): PlanView {
  let planId: number | null = null;
  let plan: ChangePlanDTO | null = null;
  let review: PlanReviewDTO | null = null;

  for (const event of events) {
    const payload = event.payload ?? {};
    if (event.event_type === 'PLAN_DRAFTED') {
      planId = asNumber(payload.plan_id);
      plan = (asRecord(payload.plan) as unknown as ChangePlanDTO) ?? null;
      review = null; // 新方案产生后，旧审查结论作废
      continue;
    }
    if (event.event_type === 'PLAN_REVIEWED') {
      const reviewPlanId = asNumber(payload.plan_id);
      if (reviewPlanId !== null && planId !== null && reviewPlanId !== planId) continue;
      review = (asRecord(payload.review) as unknown as PlanReviewDTO) ?? null;
    }
  }

  return { planId, plan, review };
}

export function getNeedsContext(events: TaskEvent[]): NeedsContext | null {
  const event = getLatestEvent(events, 'NEEDS_CONTEXT');
  if (!event) return null;
  return {
    findings: asStringArray(event.payload?.findings),
    missingContext: asStringArray(event.payload?.missing_context),
  };
}

/**
 * 补丁 id 有两条来源，都带 `patch_id`：
 * - `PATCH_GENERATED`：方案批准后**后台自动生成**补丁（P0-3 之后的主路径）
 * - `PATCH_VALIDATED`：人工通过 `POST /patches/validate` 提交补丁（旧路径，保留）
 * 倒序遍历取最新的一条，避免两类事件混用时取到旧的那个。
 */
export function getLatestPatchId(events: TaskEvent[]): number | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const type = events[i].event_type;
    if (type !== 'PATCH_GENERATED' && type !== 'PATCH_VALIDATED') continue;
    const patchId = asNumber(events[i].payload?.patch_id);
    if (patchId !== null) return patchId;
  }
  return null;
}

export function getFailureMessage(events: TaskEvent[]): string | null {
  const event = getLatestEvent(events, 'TASK_FAILED');
  if (!event) return null;
  return asString(event.payload?.error) ?? asString(event.message) ?? '任务执行失败';
}
