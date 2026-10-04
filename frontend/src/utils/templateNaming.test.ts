import { describe, expect, it } from 'vitest';
import { distillTemplateBaseName, nextAvailableTemplateName } from './templateNaming';

describe('template naming', () => {
  it('keeps the base name when it is free', () => {
    expect(nextAvailableTemplateName('校验入参 · 沉淀', [])).toBe('校验入参 · 沉淀');
    expect(nextAvailableTemplateName('校验入参 · 沉淀', ['别的模板'])).toBe('校验入参 · 沉淀');
  });

  it('walks past every taken suffix instead of failing on a duplicate', () => {
    const base = '校验入参 · 沉淀';
    expect(nextAvailableTemplateName(base, [base])).toBe(`${base} · 2`);
    expect(nextAvailableTemplateName(base, [base, `${base} · 2`])).toBe(`${base} · 3`);
    expect(nextAvailableTemplateName(base, [`${base} · 2`])).toBe(base);
  });

  it('falls back to a usable name for a blank base', () => {
    expect(nextAvailableTemplateName('   ', [])).toBe('未命名模板');
    expect(nextAvailableTemplateName('', ['未命名模板'])).toBe('未命名模板 · 2');
  });

  it('derives the distillation base name from the issue title', () => {
    expect(distillTemplateBaseName('normalize_email 未处理全角 ＠')).toBe('normalize_email 未处理全角 ＠ · 沉淀');
    expect(distillTemplateBaseName('   ')).toBe('未命名模板 · 沉淀');
    expect(distillTemplateBaseName('x'.repeat(80))).toBe(`${'x'.repeat(40)} · 沉淀`);
  });
});
