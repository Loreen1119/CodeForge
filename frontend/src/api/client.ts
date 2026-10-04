/**
 * 统一 fetch 封装：把 HTTP 错误与网络异常归一为 ApiError，
 * 让上层组件只需要 try/catch + 一句文案就能做错误反馈。
 */

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function extractDetail(body: unknown): string | null {
  if (typeof body === 'string') return body.trim() || null;
  if (!body || typeof body !== 'object') return null;

  const record = body as Record<string, unknown>;
  const detail = record.detail ?? record.message ?? record.error;

  // FastAPI 校验错误：detail 是 [{ loc, msg, type }]
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (item && typeof item === 'object' && typeof (item as Record<string, unknown>).msg === 'string') {
          return String((item as Record<string, unknown>).msg);
        }
        return String(item);
      })
      .filter(Boolean);
    return messages.length ? messages.join('；') : null;
  }

  if (typeof detail === 'string') return detail;

  if (detail && typeof detail === 'object') {
    const inner = detail as Record<string, unknown>;
    if (typeof inner.message === 'string') {
      const findings = Array.isArray(inner.findings) ? inner.findings.map(String) : [];
      return findings.length ? `${inner.message}：${findings.join('；')}` : inner.message;
    }
    return JSON.stringify(detail);
  }

  return null;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, '无法连接后端服务，请确认后端已启动');
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw new ApiError(response.status, extractDetail(body) ?? `请求失败（HTTP ${response.status}）`);
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? (JSON.parse(text) as T) : (undefined as T));
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return '发生未知错误';
}
