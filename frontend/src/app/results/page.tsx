import { fetchCompleted, pct } from "@/lib/api";

function statusClass(correct: boolean | null): string {
  if (correct === true) {
    return "status-pill correct";
  }
  if (correct === false) {
    return "status-pill incorrect";
  }
  return "status-pill unknown";
}

function statusLabel(correct: boolean | null): string {
  if (correct === true) {
    return "Correct";
  }
  if (correct === false) {
    return "Incorrect";
  }
  return "--";
}

type EventGroup = {
  key: string;
  eventName: string;
  eventDate: string;
  fights: Awaited<ReturnType<typeof fetchCompleted>>["rows"];
};

function groupByEvent(rows: Awaited<ReturnType<typeof fetchCompleted>>["rows"]): EventGroup[] {
  const map = new Map<string, EventGroup>();
  for (const row of rows) {
    const eventName = row.event_name || "Event";
    const eventDate = row.event_date || "--";
    const key = `${row.event_id}|${eventDate}`;
    if (!map.has(key)) {
      map.set(key, {
        key,
        eventName,
        eventDate,
        fights: [],
      });
    }
    map.get(key)!.fights.push(row);
  }

  for (const g of map.values()) {
    g.fights.sort((a, b) => {
      const ao = a.card_order ?? Number.MAX_SAFE_INTEGER;
      const bo = b.card_order ?? Number.MAX_SAFE_INTEGER;
      if (ao !== bo) {
        return ao - bo;
      }
      return a.fight_id.localeCompare(b.fight_id);
    });
  }

  return [...map.values()];
}

export default async function ResultsPage() {
  let message = "";
  let rows: Awaited<ReturnType<typeof fetchCompleted>>["rows"] = [];

  try {
    const data = await fetchCompleted(80);
    rows = data.rows;
  } catch (err) {
    message = err instanceof Error ? err.message : "Failed to load completed predictions.";
  }

  const groups = groupByEvent(rows);

  return (
    <section className="stack">
      <div className="panel">
        <h2>Completed Fights</h2>
        <p className="helper-text">Expand an event to review each fight, predicted edge, and final outcome.</p>
      </div>

      {message ? <div className="error-box">{message}</div> : null}

      {!message && rows.length === 0 ? <div className="warning-box">No completed prediction rows found.</div> : null}

      {!message &&
        groups.map((group, groupIdx) => (
          <details key={group.key} className="panel event-details" open={groupIdx === 0}>
            <summary className="event-summary">
              <span>{group.eventName}</span>
              <span className="helper-text">{group.eventDate}</span>
            </summary>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Card Pos</th>
                    <th>Fight</th>
                    <th>Weight Class</th>
                    <th>Pred (Red/Blue)</th>
                    <th>Actual Winner</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {group.fights.map((row, idx) => (
                    <tr key={row.fight_id}>
                      <td>#{row.card_order ?? idx + 1}</td>
                      <td>{(row.red_fighter || "--") + " vs " + (row.blue_fighter || "--")}</td>
                      <td>{row.weight_class || "--"}</td>
                      <td>
                        {pct(row.prob_red)} / {pct(row.prob_blue)}
                        <br />
                        <span className="helper-text">pick={row.predicted_corner || "--"}</span>
                      </td>
                      <td>{row.winner_corner || "--"}</td>
                      <td>
                        <span className={statusClass(row.correct)}>{statusLabel(row.correct)}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ))}
    </section>
  );
}
