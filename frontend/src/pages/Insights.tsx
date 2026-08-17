import { useEffect, useState } from "react";
import { getLatestAiAnalysis, postAiAnalyze } from "../api/client";
import type { components } from "../api/schema";

type AiAnalysis = components["schemas"]["AiAnalysisResponse"];
type GroundedStatement = components["schemas"]["GroundedStatementResponse"];

type LoadState =
  | { status: "loading" }
  | { status: "empty" }
  | { status: "ready"; analysis: AiAnalysis };

function groupByTopic(statements: GroundedStatement[]): Record<string, GroundedStatement[]> {
  const groups: Record<string, GroundedStatement[]> = {};
  for (const statement of statements) {
    (groups[statement.topic] ??= []).push(statement);
  }
  return groups;
}

export default function Insights() {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);

  useEffect(() => {
    getLatestAiAnalysis()
      .then((analysis) => setState(analysis ? { status: "ready", analysis } : { status: "empty" }))
      .catch(() => setState({ status: "empty" }));
  }, []);

  async function handleAnalyze() {
    setAnalyzing(true);
    setAnalyzeError(null);
    try {
      const analysis = await postAiAnalyze();
      setState({ status: "ready", analysis });
    } catch (err) {
      setAnalyzeError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setAnalyzing(false);
    }
  }

  const analysis = state.status === "ready" ? state.analysis : null;
  const groupedStatements = analysis ? groupByTopic(analysis.accepted) : {};

  return (
    <div>
      <h1>Insights</h1>
      <p className="muted" style={{ marginBottom: 20 }}>
        An AI-generated read of your portfolio — concentration, fee drag, and other signals.
        Every numeric claim is checked against your actual portfolio data before it's shown;
        anything that doesn't check out is dropped, not guessed at.
      </p>

      <div className="card">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <button onClick={() => void handleAnalyze()} disabled={analyzing}>
            {analyzing ? "Analyzing…" : "Analyze now"}
          </button>
          {analysis && (
            <span className="muted">
              Last run: {new Date(analysis.created_at).toLocaleString()}
            </span>
          )}
        </div>
        {analyzeError && (
          <p style={{ color: "var(--negative)", marginTop: 8 }}>{analyzeError}</p>
        )}
      </div>

      {state.status === "loading" && (
        <div className="card">
          <p className="muted">Loading…</p>
        </div>
      )}

      {state.status === "empty" && (
        <div className="card">
          <p className="muted">No analysis yet — click "Analyze now" to generate one.</p>
        </div>
      )}

      {analysis && analysis.status === "unavailable" && (
        <div className="card">
          <p className="muted">
            AI analysis unavailable{analysis.error_message ? `: ${analysis.error_message}` : "."}
          </p>
        </div>
      )}

      {analysis && analysis.status === "all_rejected" && (
        <div className="card">
          <p className="muted">
            The AI's response didn't check out against your portfolio data, so nothing is shown
            for this run. Try "Analyze now" again.
          </p>
        </div>
      )}

      {analysis && analysis.accepted.length > 0 && (
        <div className="card">
          <h3>Portfolio Health</h3>
          {Object.entries(groupedStatements).map(([topic, statements]) => (
            <div key={topic} style={{ marginBottom: 16 }}>
              <h4 style={{ textTransform: "capitalize", marginBottom: 6 }}>
                {topic.replace("_", " ")}
              </h4>
              {statements.map((s, i) => (
                <p key={i} style={{ marginBottom: 6 }}>
                  {s.text}
                </p>
              ))}
            </div>
          ))}
        </div>
      )}

      {analysis && analysis.news_context.length > 0 && (
        <div className="card">
          <h3>From the web</h3>
          <p className="muted" style={{ marginBottom: 12 }}>
            External context — not verified against your portfolio data, sourced from the web.
          </p>
          {analysis.news_context.map((item, i) => (
            <div key={i} style={{ marginBottom: 12, paddingLeft: 12, borderLeft: "2px solid var(--border)" }}>
              <strong>{item.fund_name}</strong>
              <p style={{ margin: "4px 0" }}>{item.text}</p>
              {item.source_url && (
                <a href={item.source_url} target="_blank" rel="noreferrer" className="muted">
                  Source
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
