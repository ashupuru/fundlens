import { useEffect, useState } from "react";
import { getAiQuestions, postAiAsk, type AiAnswer, type AiToolCall } from "../api/client";

const EXAMPLES = [
  "Can I retire at 52 instead of 58?",
  "What happens to my retirement plan if I stop investing for two years?",
  "Which of my funds has the worst risk-adjusted return?",
  "Am I on track for my goals?",
];

function ToolChain({ calls }: { calls: AiToolCall[] }) {
  const [open, setOpen] = useState(false);
  if (calls.length === 0) return null;

  return (
    <div style={{ marginTop: 12 }}>
      <button className="link-button" onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Show"} the {calls.length} calculation
        {calls.length === 1 ? "" : "s"} behind this
      </button>
      {open && (
        <div style={{ marginTop: 8 }}>
          {calls.map((call, i) => (
            <div
              key={i}
              style={{
                marginBottom: 8,
                paddingLeft: 12,
                borderLeft: "2px solid var(--border)",
              }}
            >
              <code>
                [{i}] {call.tool}(
                {Object.entries(call.arguments ?? {})
                  .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                  .join(", ")}
                )
              </code>
              {call.error ? (
                <p style={{ color: "var(--negative)", margin: "4px 0" }}>{call.error}</p>
              ) : (
                <pre
                  style={{
                    margin: "4px 0",
                    fontSize: "0.85em",
                    overflowX: "auto",
                    maxHeight: 220,
                  }}
                >
                  {JSON.stringify(call.result, null, 1)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Answer({ answer }: { answer: AiAnswer }) {
  if (answer.status === "unavailable") {
    return (
      <div className="card">
        <p className="muted">
          Couldn't answer{answer.error_message ? `: ${answer.error_message}` : "."}
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3 style={{ marginBottom: 4 }}>{answer.question}</h3>
      <p className="muted" style={{ marginBottom: 12 }}>
        {new Date(answer.created_at).toLocaleString()}
      </p>

      {answer.answer_summary &&
        (answer.summary_unverified_figures.length > 0 ? (
          <div
            style={{
              marginBottom: 12,
              padding: "10px 12px",
              borderLeft: "3px solid var(--warning, #b8860b)",
              background: "var(--card-alt-bg, transparent)",
            }}
          >
            <p style={{ margin: 0 }}>{answer.answer_summary}</p>
            <p className="muted" style={{ margin: "6px 0 0" }}>
              Summary only — these figures in it couldn't be traced to a calculation:{" "}
              {answer.summary_unverified_figures.join(", ")}. The findings below are the
              verified ones.
            </p>
          </div>
        ) : (
          <p style={{ marginBottom: 12 }}>{answer.answer_summary}</p>
        ))}

      {answer.statements.map((s, i) => (
        <p key={i} style={{ marginBottom: 8 }}>
          {s.text}
        </p>
      ))}

      {answer.status === "all_rejected" && (
        <p className="muted">
          None of the figures in the answer could be traced back to your portfolio data, so
          nothing is shown. Try rephrasing the question.
        </p>
      )}

      <ToolChain calls={answer.tool_calls} />
    </div>
  );
}

export default function Ask() {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<AiAnswer[]>([]);

  useEffect(() => {
    getAiQuestions()
      .then(setHistory)
      .catch(() => setHistory([]));
  }, []);

  async function handleAsk() {
    const trimmed = question.trim();
    if (!trimmed) return;
    setAsking(true);
    setError(null);
    try {
      const answer = await postAiAsk(trimmed);
      setHistory((prev) => [answer, ...prev]);
      setQuestion("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setAsking(false);
    }
  }

  return (
    <div>
      <h1>Ask</h1>
      <p className="muted" style={{ marginBottom: 20 }}>
        Ask a question about your portfolio or your plan. Every figure in the answer is computed
        by this app, not by the AI — the AI only decides which calculations to run. Anything it
        can't trace back to a real calculation is dropped rather than shown.
      </p>

      <div className="card">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Can I retire at 52 instead of 58?"
          rows={3}
          style={{ width: "100%", marginBottom: 8 }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void handleAsk();
          }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <button onClick={() => void handleAsk()} disabled={asking || !question.trim()}>
            {asking ? "Working…" : "Ask"}
          </button>
          <span className="muted">
            {asking ? "Running calculations…" : "⌘/Ctrl + Enter to submit"}
          </span>
        </div>
        {error && <p style={{ color: "var(--negative)", marginTop: 8 }}>{error}</p>}
      </div>

      {history.length === 0 && !asking && (
        <div className="card">
          <h3>Try asking</h3>
          {EXAMPLES.map((example) => (
            <p key={example} style={{ marginBottom: 6 }}>
              <button className="link-button" onClick={() => setQuestion(example)}>
                {example}
              </button>
            </p>
          ))}
        </div>
      )}

      {history.map((answer) => (
        <Answer key={answer.id} answer={answer} />
      ))}
    </div>
  );
}
