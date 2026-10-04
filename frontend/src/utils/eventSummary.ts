import { EVENT_LABELS } from '../constants';
import type { TaskEvent } from '../types';

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/**
 * 事件的一句话描述。
 *
 * SSE 帧里只有 payload（后端 _frame 只序列化 payload_json），**不含 message**；
 * 而 /events 回放接口带 message。所以实时事件要由前端按事件类型合成描述，
 * 回放时优先用后端 message，保证两条路径展示一致（都是中文、都有信息量）。
 */
export function describeEvent(event: TaskEvent): string {
  if (event.message) return event.message;

  const payload = event.payload;
  switch (event.event_type) {
    case 'TASK_CREATED':
      return '代码任务已创建';
    case 'TASK_STARTED':
      return '代码任务已启动';
    case 'REQUIREMENT_ANALYZED': {
      const objective = asRecord(payload.analysis)?.objective;
      return typeof objective === 'string' ? `需求已解析：${objective}` : '需求已解析';
    }
    case 'CODE_SEARCHED':
      return `代码检索完成，命中 ${asArray(payload.results).length} 个位置`;
    case 'PLAN_DRAFTED':
      return `方案已生成（${asArray(asRecord(payload.plan)?.proposed_changes).length} 处待确认）`;
    case 'PLAN_REVIEWED': {
      const review = asRecord(payload.review);
      if (!review) return '自动审查完成';
      return review.approved
        ? '自动审查通过'
        : `自动审查发现 ${asArray(review.findings).length} 项问题`;
    }
    case 'TASK_READY':
      return '方案等待人工确认';
    case 'NEEDS_CONTEXT':
      return `上下文不足，待补充 ${asArray(payload.missing_context).length} 项`;
    case 'TASK_FAILED':
      return typeof payload.error === 'string' ? `任务失败：${payload.error}` : '任务失败';
    case 'PLAN_APPROVED':
      return '方案已通过人工确认';
    case 'PLAN_REJECTED':
      return '方案已驳回';
    case 'PLAN_REANALYSIS_REQUESTED':
      return '已收到人工反馈，开始重新分析';
    case 'PATCH_VALIDATED':
      return `补丁 #${String(payload.patch_id)} 校验完成`;
    case 'PATCH_APPROVED':
      return `补丁 #${String(payload.patch_id)} 已通过人工确认`;
    case 'PATCH_REJECTED':
      return `补丁 #${String(payload.patch_id)} 已驳回`;
    case 'PATCH_APPLIED':
      return `补丁 #${String(payload.patch_id)} 已在隔离 Worktree 应用`;
    case 'TEST_RUN_COMPLETED':
      return `白名单测试执行完成（${String(payload.status)}）`;
    default:
      return EVENT_LABELS[event.event_type] ?? event.event_type;
  }
}
