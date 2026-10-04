import { Fragment } from 'react';

/** 手写 diff 着色（D8）：+ 绿 / - 红 / @@ 灰，长行横向滚动不撑破布局 */
function lineClass(line: string): string {
  if (line.startsWith('@@')) return 'diff-hunk';
  if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ') || line.startsWith('index ')) return 'diff-meta';
  if (line.startsWith('+')) return 'diff-add';
  if (line.startsWith('-')) return 'diff-del';
  return '';
}

export function DiffView({ diff }: { diff: string }) {
  const lines = diff.split('\n');
  return (
    <pre className="diff-view">
      {lines.map((line, index) => (
        <Fragment key={index}>
          <div className={`diff-line ${lineClass(line)}`}>{line || ' '}</div>
        </Fragment>
      ))}
    </pre>
  );
}
