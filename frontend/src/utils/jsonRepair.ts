export function formatEventPayload(payload: Record<string, unknown>): string { return JSON.stringify(payload, null, 2); }
