import { useEffect, useRef, useState, type FormEvent } from "react";
import { Loader2, RotateCcw, Send, Sparkles, Wrench } from "lucide-react";
import { sendChatMessages, type ChatMessage } from "@/lib/api";
import { AiAnalysisBlock } from "@/components/AiAnalysisBlock";
import { cn } from "@/lib/utils";

interface ChatCopilotProps {
  activeDate?: string;
}

const SUGGESTED_PROMPTS = [
  "¿Cuáles son los mejores picks recomendables de hoy?",
  "Armame una combinada cuota 4 con PTS y AST",
  "Puntos proyectados de KCP contra Philadelphia 76ers sin Embiid",
  "¿Qué mercados me están dando mejor Win Rate y ROI?",
];

export function ChatCopilot({ activeDate }: ChatCopilotProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function handleSend(textOverride?: string) {
    const text = (textOverride ?? input).trim();
    if (!text || loading) return;

    const nextHistory: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages(nextHistory);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const resp = await sendChatMessages(
        nextHistory.map((m) => ({ role: m.role, content: m.content })),
        activeDate
      );
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: resp.reply,
          tools_used: resp.tools_used,
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error consultando al copiloto IA.");
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void handleSend();
  }

  return (
    <div className="flex h-[calc(100dvh-13.5rem)] flex-col overflow-hidden rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] sm:h-[calc(100vh-8.5rem)]">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border-hairline)] px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-amber-500/15 text-amber-500">
            <Sparkles className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-xs font-bold text-[var(--text-primary)] sm:text-sm">
              Copiloto IA · NBA Stats
            </h2>
            <p className="text-[10px] text-[var(--text-muted)] sm:text-[11px]">
              Conectado en vivo al Simulador ML, Props Board, Combinadas, Lesiones y tu Win Rate.
            </p>
          </div>
        </div>

        {messages.length > 0 ? (
          <button
            type="button"
            onClick={() => {
              setMessages([]);
              setError(null);
            }}
            className="inline-flex h-7 items-center gap-1 rounded border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-2 text-[11px] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
          >
            <RotateCcw className="h-3 w-3" />
            Nueva charla
          </button>
        ) : null}
      </div>

      {/* Messages area */}
      <div className="flex-1 space-y-3 overflow-y-auto p-3.5 sm:p-4">
        {messages.length === 0 ? (
          <div className="mx-auto flex max-w-xl flex-col items-center justify-center py-6 text-center sm:py-10">
            <div className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-amber-500/15 text-amber-500">
              <Sparkles className="h-5 w-5" />
            </div>
            <h3 className="mt-3 text-sm font-bold text-[var(--text-primary)]">
              Preguntame lo que quieras sobre la jornada NBA
            </h3>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Puedo recomendar picks de hoy, armar combinadas por cuota, simular escenarios What-If con bajas o analizar tu Win Rate real.
            </p>

            <div className="mt-5 grid w-full grid-cols-1 gap-2 sm:grid-cols-2">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => void handleSend(prompt)}
                  className="rounded-md border border-[var(--border-hairline)] bg-[var(--bg-subtle)] px-3 py-2.5 text-left text-xs font-medium text-[var(--text-primary)] transition-colors hover:border-amber-500 hover:bg-[var(--bg-hover)] cursor-pointer"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg, idx) => (
            <div
              key={idx}
              className={cn(
                "flex w-full",
                msg.role === "user" ? "justify-end" : "justify-start"
              )}
            >
              <div
                className={cn(
                  "max-w-[92%] rounded-xl px-3.5 py-2.5 text-xs sm:max-w-[82%]",
                  msg.role === "user"
                    ? "bg-amber-500 font-medium text-slate-950"
                    : "border border-[var(--border-hairline)] bg-[var(--bg-canvas)] text-[var(--text-primary)]"
                )}
              >
                {msg.role === "user" ? (
                  <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                ) : (
                  <div className="space-y-2">
                    <AiAnalysisBlock text={msg.content} compact />
                    {msg.tools_used && msg.tools_used.length > 0 ? (
                      <div className="flex flex-wrap items-center gap-1 border-t border-[var(--border-hairline)] pt-1.5 text-[10px] text-[var(--text-muted)]">
                        <Wrench className="h-2.5 w-2.5" />
                        <span>Datos en vivo:</span>
                        {msg.tools_used.map((tool) => (
                          <span
                            key={tool}
                            className="rounded bg-[var(--bg-subtle)] px-1.5 py-px font-medium text-[var(--text-secondary)]"
                          >
                            {tool}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
          ))
        )}

        {loading ? (
          <div className="flex justify-start">
            <div className="inline-flex items-center gap-2 rounded-xl border border-[var(--border-hairline)] bg-[var(--bg-canvas)] px-3.5 py-2.5 text-xs text-[var(--text-muted)]">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-500" />
              Consultando datos en vivo de la plataforma...
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-500">
            {error}
          </div>
        ) : null}

        <div ref={bottomRef} />
      </div>

      {/* Input form */}
      <form
        onSubmit={onSubmit}
        className="flex items-center gap-2 border-t border-[var(--border-hairline)] bg-[var(--bg-canvas)] px-3 py-2.5"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={loading}
          placeholder="Ej: Puntos de KCP contra PHI sin Embiid, o mejores picks de hoy..."
          className="h-9 min-w-0 flex-1 rounded-md border border-[var(--border-hairline)] bg-[var(--bg-panel)] px-3 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-amber-500 focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={loading || input.trim().length < 2}
          aria-label="Enviar consulta"
          className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-amber-500 px-3.5 text-xs font-bold text-slate-950 hover:opacity-90 disabled:opacity-40 cursor-pointer"
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}
          <span className="hidden sm:inline">Enviar</span>
        </button>
      </form>
    </div>
  );
}
