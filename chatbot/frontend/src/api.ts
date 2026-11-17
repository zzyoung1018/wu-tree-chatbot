import type { Language } from "./types";
import { translate } from "./i18n";
const uiLang = (): Language =>
  localStorage.getItem("tree-chat-lang") === "en" ? "en" : "zh";
const t = (text: string) => translate(uiLang(), text);
const apiBase = `${import.meta.env.BASE_URL}api`;

export function imageUrl(path?: string): string | undefined {
  return path?.startsWith("/api/")
    ? `${import.meta.env.BASE_URL}${path.slice(1)}`
    : path;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await request(apiBase + path, {
    ...options,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : uiLang() === "en"
          ? `Request failed (${response.status})`
          : `请求未完成（${response.status}）`,
    );
  }
  return response.json();
}

export async function streamAnswer(
  cid: string,
  text: string,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal,
  lang: Language = "zh",
) {
  const response = await request(`${apiBase}/conversations/${cid}/messages`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, request_id: requestId(), lang }),
    signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string" ? data.detail : t("发送失败，请重试。"),
    );
  }
  if (!response.body) throw new Error(t("浏览器没有接收到回答。"));
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "",
    gotResult = false;
  while (true) {
    const { value, done } = await reader.read().catch(() => {
      throw new Error(
        t("连接已中断，已接受的回答会继续生成。恢复连接后可打开此对话查看。"),
      );
    });
    buffer += decoder.decode(value, { stream: !done });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const packet = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const event = packet
        .split("\n")
        .find((l) => l.startsWith("event: "))
        ?.slice(7);
      const data = packet
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      if (event && data) {
        onEvent(event, JSON.parse(data));
        if (event === "result") gotResult = true;
      }
    }
    if (done) break;
  }
  if (!gotResult)
    throw new Error(t("连接已断开。回答可能仍在生成，可刷新此对话查看。"));
}
export const cancelAnswer = (cid: string) =>
  api(`/conversations/${cid}/cancel`, { method: "POST", body: "{}" });
async function request(path: string, options: RequestInit): Promise<Response> {
  try {
    return await fetch(path, options);
  } catch {
    throw new Error(t("网络连接中断，请恢复连接后重试。"));
  }
}

function requestId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // getRandomValues also works on a phone using an HTTP LAN address.
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}
