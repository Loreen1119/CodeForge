import { useEffect } from 'react';
import type { Dispatch } from 'react';
import { TERMINAL_EVENT_TYPES } from '../../constants';
import type { AppAction } from '../../state/appReducer';
import type { TaskEvent } from '../../types';

/**
 * 任务事件流（SSE）订阅。
 * - onopen → connected；onerror → error
 * - 终态事件（TASK_READY / TASK_FAILED）→ 主动 close → disconnected
 * - attempt 变化（重新分析）→ 重建连接，重放的事件由 reducer 按 event_seq 去重
 *
 * 断线后有两条完全不同的路径，必须分开处理：
 *
 * 1. 网络层中断（连接已建立后被切断）→ 浏览器自动重连，readyState 保持 CONNECTING。
 *    这种情况不要干预，否则会重复建连接。
 *
 * 2. HTTP 层失败（状态码非 200，或 Content-Type 不是 text/event-stream）
 *    → 规范规定直接 fail：readyState 变 CLOSED 且**浏览器不会重试**。
 *    开发环境实测会踩到：后端进程被杀后，vite 代理返回 `500 text/plain`
 *    （vite/dist/node/chunks/dep-BK3b2jBa.js 的 `res.writeHead(500, {"Content-Type":"text/plain"}).end()`），
 *    正好落在这条路径上。不手动重建就会永远停在红点，后端起来了也不恢复。
 */
/** 断线后重连次数上限，超过就放弃，改由低频同步兜底。8 次 × 3s ≈ 24s，够后端重启 + 人工反应 */
const MAX_RECONNECT_ATTEMPTS = 8;
/** 手动重建连接的间隔（仅用于浏览器已判定永久失败的场景） */
const RETRY_INTERVAL_MS = 3000;

const READY_STATE_CONNECTING = 0;
const READY_STATE_CLOSED = 2;

export function useTaskStream(taskId: number | null, attempt: number, dispatch: Dispatch<AppAction>) {
  useEffect(() => {
    if (taskId === null) return;

    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    let disposed = false;

    function connect() {
      if (disposed) return;

      const es = new EventSource(`/api/tasks/${taskId}/stream`);
      source = es;

      es.onopen = () => {
        failures = 0;
        dispatch({ type: 'CONNECTION', payload: 'connected' });
      };

      es.onerror = () => {
        if (disposed) return;

        failures += 1;

        if (es.readyState === READY_STATE_CLOSED) {
          // 路径 2：已被判定为永久失败，浏览器不会重试，必须自己重建
          if (failures >= MAX_RECONNECT_ATTEMPTS) {
            dispatch({
              type: 'STREAM_ABORTED',
              payload: `事件流已中断（重连 ${failures} 次失败），已切换为自动同步`,
            });
            return;
          }
          dispatch({ type: 'CONNECTION', payload: 'error' });
          retryTimer = setTimeout(connect, RETRY_INTERVAL_MS);
          return;
        }

        // 路径 1：浏览器正在自动重连（CONNECTING），只更新状态，不插手
        if (failures >= MAX_RECONNECT_ATTEMPTS) {
          es.close();
          dispatch({
            type: 'STREAM_ABORTED',
            payload: `事件流已中断（重连 ${failures} 次失败），已切换为自动同步`,
          });
          return;
        }
        dispatch({ type: 'CONNECTION', payload: 'error' });
      };

      es.onmessage = (event: MessageEvent<string>) => {
        let raw: Record<string, unknown>;
        try {
          raw = JSON.parse(event.data) as Record<string, unknown>;
        } catch {
          return;
        }

        const seq = Number(event.lastEventId);
        const eventType = String(raw.type ?? 'EVENT');
        dispatch({
          type: 'EVENT',
          payload: {
            event_seq: Number.isFinite(seq) ? seq : Date.now(),
            event_type: eventType,
            agent_name: typeof raw.agent_name === 'string' ? raw.agent_name : null,
            message: typeof raw.message === 'string' ? raw.message : null,
            payload: raw,
            created_at: new Date().toISOString(),
          } satisfies TaskEvent,
        });

        if (TERMINAL_EVENT_TYPES.includes(eventType)) {
          es.close();
          dispatch({ type: 'CONNECTION', payload: 'disconnected' });
        }
      };
    }

    connect();

    return () => {
      disposed = true;
      if (retryTimer !== null) clearTimeout(retryTimer);
      source?.close();
    };
  }, [taskId, attempt, dispatch]);
}
