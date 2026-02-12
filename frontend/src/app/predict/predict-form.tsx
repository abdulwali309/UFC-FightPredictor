"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchQuickMatchups, pct, postPredict, searchFighters, type PredictResponse } from "@/lib/api";

const weightClassHints = [
  "Strawweight",
  "Flyweight",
  "Bantamweight",
  "Featherweight",
  "Lightweight",
  "Welterweight",
  "Middleweight",
  "Light Heavyweight",
  "Heavyweight",
];

const fallbackQuickMatchups = [
  { fighter1: "Islam Makhachev", fighter2: "Kamaru Usman", weightClass: "Welterweight" },
  { fighter1: "Ilia Topuria", fighter2: "Paddy Pimblett", weightClass: "Lightweight" },
  { fighter1: "Sean O'Malley", fighter2: "Song Yadong", weightClass: "Bantamweight" },
  { fighter1: "Alexander Volkanovski", fighter2: "Diego Lopes", weightClass: "Featherweight" },
];

export default function PredictForm() {
  const [fighter1, setFighter1] = useState("");
  const [fighter2, setFighter2] = useState("");
  const [weightClass, setWeightClass] = useState("");
  const [suggestions1, setSuggestions1] = useState<string[]>([]);
  const [suggestions2, setSuggestions2] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PredictResponse | null>(null);
  const [quickMatchups, setQuickMatchups] = useState(fallbackQuickMatchups);

  const canSubmit = useMemo(
    () => fighter1.trim().length > 1 && fighter2.trim().length > 1 && !loading,
    [fighter1, fighter2, loading],
  );

  function normalizeName(name: string): string {
    return name.trim().replace(/\s+/g, " ").toLowerCase();
  }

  async function resolveExistingFighterName(input: string): Promise<string | null> {
    const q = input.trim();
    if (!q) {
      return null;
    }
    const names = await searchFighters(q, 20);
    const target = normalizeName(q);
    const exact = names.find((n) => normalizeName(n) === target);
    return exact ?? null;
  }

  function applyQuickMatchup(match: { fighter1: string; fighter2: string; weightClass: string }) {
    setFighter1(match.fighter1);
    setFighter2(match.fighter2);
    setWeightClass(match.weightClass);
    setError("");
    setResult(null);
  }

  useEffect(() => {
    let cancelled = false;
    async function loadQuickMatchups() {
      try {
        const data = await fetchQuickMatchups(8);
        const mapped = data.rows
          .map((row) => ({
            fighter1: row.fighter_1,
            fighter2: row.fighter_2,
            weightClass: row.weight_class || "",
          }))
          .filter((row) => row.fighter1 && row.fighter2);
        if (!cancelled && mapped.length > 0) {
          setQuickMatchups(mapped);
        }
      } catch {
        // Keep fallback quick matchups if API endpoint is unavailable.
      }
    }
    void loadQuickMatchups();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onFighterInput(name: string, target: "f1" | "f2") {
    try {
      if (name.trim().length < 2) {
        if (target === "f1") {
          setSuggestions1([]);
        } else {
          setSuggestions2([]);
        }
        return;
      }
      const names = await searchFighters(name.trim(), 6);
      if (target === "f1") {
        setSuggestions1(names);
      } else {
        setSuggestions2(names);
      }
    } catch {
      if (target === "f1") {
        setSuggestions1([]);
      } else {
        setSuggestions2([]);
      }
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) {
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);

    try {
      const [resolved1, resolved2] = await Promise.all([
        resolveExistingFighterName(fighter1),
        resolveExistingFighterName(fighter2),
      ]);
      if (!resolved1 || !resolved2) {
        setError("Enter valid UFC fighter names from the database.");
        return;
      }
      if (normalizeName(resolved1) === normalizeName(resolved2)) {
        setError("Select two different fighters.");
        return;
      }

      const res = await postPredict({
        fighter_1: resolved1,
        fighter_2: resolved2,
        weight_class: weightClass.trim() || undefined,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Prediction request failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="panel">
      <h2>Custom Matchup Prediction</h2>
      <p className="helper-text">Enter two fighters from UFC records. Weight class is optional and can be inferred.</p>

      <div className="quick-matchups">
        <p className="quick-matchups-label">Quick Matchups (auto-updated)</p>
        <div className="quick-matchup-grid">
          {quickMatchups.map((match) => (
            <button
              key={`${match.fighter1}-${match.fighter2}`}
              type="button"
              className="quick-chip"
              onClick={() => applyQuickMatchup(match)}
            >
              {match.fighter1} vs {match.fighter2}
            </button>
          ))}
        </div>
      </div>

      <form className="predict-form" onSubmit={onSubmit}>
        <div className="predict-grid">
          <label>
            Fighter 1
            <input
              list="fighter-1-list"
              value={fighter1}
              onChange={(e) => {
                const v = e.target.value;
                setFighter1(v);
                void onFighterInput(v, "f1");
              }}
              placeholder="Islam Makhachev"
              required
            />
            <datalist id="fighter-1-list">
              {suggestions1.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>

          <label>
            Fighter 2
            <input
              list="fighter-2-list"
              value={fighter2}
              onChange={(e) => {
                const v = e.target.value;
                setFighter2(v);
                void onFighterInput(v, "f2");
              }}
              placeholder="Kamaru Usman"
              required
            />
            <datalist id="fighter-2-list">
              {suggestions2.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
        </div>

        <label>
          Weight Class (optional)
          <input
            list="weight-class-list"
            value={weightClass}
            onChange={(e) => setWeightClass(e.target.value)}
            placeholder="Welterweight"
          />
          <datalist id="weight-class-list">
            {weightClassHints.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </label>

        <button type="submit" disabled={!canSubmit}>
          {loading ? "Running Prediction..." : "Run Prediction"}
        </button>
      </form>

      {error ? <div className="error-box">{error}</div> : null}

      {result ? (
        <div className="prediction-output">
          <p className="prediction-title">Prediction Result</p>
          <div className="prediction-row">
            <span>{result.fighter_1}</span>
            <strong>{pct(result.prob_f1)}</strong>
          </div>
          <div className="prediction-row">
            <span>{result.fighter_2}</span>
            <strong>{pct(result.prob_f2)}</strong>
          </div>
          <p className="helper-text mono">Weight Class: {result.weight_class || "Inferred"}</p>
        </div>
      ) : null}
    </div>
  );
}
