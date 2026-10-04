import { describeEvent } from '../../../utils/eventSummary';
import type { TaskEvent } from '../../../types';

/** 执行时间线：默认折叠在步骤区下方，展开才看全部事件（避免页面被事件列表越拉越长） */
export function EventTimeline({ events }: { events: TaskEvent[] }) {
  const last = events.length > 0 ? events[events.length - 1] : null;

  return (
    <details className="timeline">
      <summary>
        <span className="timeline-title">执行时间线</span>
        <span className="badge">{events.length} 条</span>
        {last && <span className="timeline-last" title={last.event_type}>{describeEvent(last)}</span>}
      </summary>
      <ol className="event-list">
        {events.map((event) => (
          <li key={event.event_seq}>
            <span className="event-seq">#{event.event_seq}</span>
            <span className="event-desc">{describeEvent(event)}</span>
            <code className="event-type">{event.event_type}</code>
          </li>
        ))}
      </ol>
    </details>
  );
}
