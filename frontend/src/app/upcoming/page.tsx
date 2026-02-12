import { fetchUpcoming, pct } from "@/lib/api";

type EventGroup = {
  key: string;
  name: string;
  date: string;
  fights: Awaited<ReturnType<typeof fetchUpcoming>>["rows"];
};

function groupByEvent(rows: Awaited<ReturnType<typeof fetchUpcoming>>["rows"]): EventGroup[] {
  const map = new Map<string, EventGroup>();
  for (const row of rows) {
    const eventName = row.event_name || "Event TBD";
    const date = row.scheduled_date || "Date TBD";
    const key = `${row.event_id || eventName}|${date}`;
    if (!map.has(key)) {
      map.set(key, { key, name: eventName, date, fights: [] });
    }
    map.get(key)!.fights.push(row);
  }
  for (const group of map.values()) {
    group.fights.sort((a, b) => {
      const ao = a.card_order ?? Number.MAX_SAFE_INTEGER;
      const bo = b.card_order ?? Number.MAX_SAFE_INTEGER;
      if (ao !== bo) {
        return ao - bo;
      }
      return a.fight_key.localeCompare(b.fight_key);
    });
  }
  return [...map.values()];
}

export default async function UpcomingPage() {
  let message = "";
  let rows: Awaited<ReturnType<typeof fetchUpcoming>>["rows"] = [];

  try {
    const data = await fetchUpcoming(80);
    rows = data.rows;
  } catch (err) {
    message = err instanceof Error ? err.message : "Failed to load upcoming predictions.";
  }

  const groups = groupByEvent(rows);

  return (
    <section className="stack">
      <div className="panel">
        <h2>Upcoming Fight Predictions</h2>
        <p className="helper-text">Projected win probabilities for scheduled UFC matchups.</p>
      </div>

      {message ? <div className="error-box">{message}</div> : null}

      {!message && rows.length === 0 ? <div className="warning-box">No upcoming predictions found.</div> : null}

      {!message &&
        groups.map((group) => (
          <section key={group.key} className="panel event-block">
            <header className="event-header">
              <h3>{group.name}</h3>
              <p className="helper-text">{group.date}</p>
            </header>

            <div className="fight-list">
              {group.fights.map((fight, idx) => {
                const displayOrder = fight.card_order ?? idx + 1;
                const hasPrediction = fight.prob_f1 !== null && fight.prob_f2 !== null;
                return (
                  <article key={fight.fight_key} className="fight-card">
                    <div className="fight-meta">
                      <span className="meta-chip">Fight #{displayOrder}</span>
                      <span className="weight-pill">{fight.weight_class || "Weight class TBD"}</span>
                    </div>

                    <div className="fighters">
                      <p className="fighter-name left">{fight.fighter_1}</p>
                      <span className="vs-badge">VS</span>
                      <p className="fighter-name right">{fight.fighter_2}</p>
                    </div>

                    {hasPrediction ? (
                      <div className="prob-grid">
                        <p className="prob-item">
                          {fight.fighter_1}: <strong>{pct(fight.prob_f1)}</strong>
                        </p>
                        <p className="prob-item">
                          {fight.fighter_2}: <strong>{pct(fight.prob_f2)}</strong>
                        </p>
                      </div>
                    ) : (
                      <p className="helper-text">Prediction coming soon</p>
                    )}
                  </article>
                );
              })}
            </div>
          </section>
        ))}
    </section>
  );
}
