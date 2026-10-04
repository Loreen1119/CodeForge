/**
 * 模板命名：把一次任务沉淀成模板时的取名规则。
 *
 * 模板名在库里是 UNIQUE，而「同一个 Issue 反复沉淀」是很自然的操作，
 * 撞名直接 400 会让人以为是 bug。这里在提交前就把名字让开，
 * 把"重名"变成可预期的 `xxx · 2`、`xxx · 3`。
 */
export function nextAvailableTemplateName(base: string, existing: readonly string[]): string {
  const trimmed = base.trim() || '未命名模板';
  if (!existing.includes(trimmed)) return trimmed;

  let index = 2;
  while (existing.includes(`${trimmed} · ${index}`)) index += 1;
  return `${trimmed} · ${index}`;
}

/** 沉淀模板的默认名：以 Issue 标题为骨架，便于之后在列表里一眼认出同类任务。 */
export function distillTemplateBaseName(issueTitle: string): string {
  const trimmed = issueTitle.trim();
  return trimmed ? `${trimmed.slice(0, 40)} · 沉淀` : '未命名模板 · 沉淀';
}
