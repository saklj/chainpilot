import { API_BASE, ApiError } from "@/lib/api";
import { ChatEventSchema, type ChatEvent } from "@/lib/schemas";

function emitFrame(frame: string, onEvent: (event: ChatEvent) => void) {
  const payload = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (payload) onEvent(ChatEventSchema.parse(JSON.parse(payload)));
}

export async function streamChat(
  question: string,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    const detail =
      typeof payload === "object" && payload !== null && "detail" in payload
        ? payload.detail
        : payload;
    throw new ApiError(response.status, detail);
  }
  if (!response.body) throw new Error("浏览器未提供可读取的响应流");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    frames.forEach((frame) => emitFrame(frame, onEvent));
    if (done) break;
  }

  if (buffer.trim()) emitFrame(buffer, onEvent);
}
