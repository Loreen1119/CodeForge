import { useEffect, useRef } from 'react';
import { CONNECTION_LABELS } from '../../constants';
import type { ConnectionStatus, LogEntry } from '../../types';

interface AgentLogPanelProps {
  logs: LogEntry[];
  connectionStatus: ConnectionStatus;
  collapsed: boolean;
  /** 事件流放弃重连等需要用户介入的提示，优先于连接状态文案 */
  notice?: string | null;
}

function statusBadge(status?: string) {
  if (!status) return null;
  return <span className={`log-badge ${status}`}>{status.toUpperCase()}</span>;
}

export function AgentLogPanel({ logs, connectionStatus, collapsed, notice }: AgentLogPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const isActive = connectionStatus === 'connecting' || connectionStatus === 'connected';

  useEffect(() => {
    if (collapsed) return;
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    bottomRef.current?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth' });
  }, [logs.length, collapsed]);

  return (
    <div className="log-panel">
      <div className={`log-panel-header${notice ? ' alert' : ''}`} aria-live="polite" aria-atomic="true">
        <span className={`status-dot ${notice || connectionStatus === 'error' ? 'error' : isActive ? 'active' : ''}`} aria-hidden="true" />
        <h3>{notice ?? CONNECTION_LABELS[connectionStatus]}</h3>
      </div>

      <div className="log-container" role="log" aria-label="Agent 执行日志">
        {logs.map((log) => (
          <div key={log.id} className={`log-entry ${log.type}`}>
            <div className="log-meta">
              <span className="log-ts">{log.timestamp}</span>
              <span className="log-phase">{log.phase}</span>
              {statusBadge(log.status)}
            </div>
            <div className="log-msg">{log.message}</div>
            {log.detail && <div className="log-detail">{log.detail}</div>}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
