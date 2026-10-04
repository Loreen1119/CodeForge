import { describe, expect, it } from 'vitest';
import { formatEventPayload } from './jsonRepair';
describe('event payload formatting', () => { it('formats durable events for inspection', () => { expect(formatEventPayload({type:'TASK_READY'})).toContain('TASK_READY'); }); });
