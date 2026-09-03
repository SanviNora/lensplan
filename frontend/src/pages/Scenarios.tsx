import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Scenario } from "../api";

function money(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

export default function Scenarios() {
  const [rows, setRows] = useState<Scenario[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .scenarios()
      .then(setRows)
      .catch((err: Error) => setError(err.message));
  }, []);

  return (
    <>
      <div className="card">
        <h2>Scenarios</h2>
        <p className="muted">
          Each scenario is a lens manufacturing network. Open one to solve the mixed-integer
          plan, compare it with simulated annealing, and read which line hour is worth buying.
        </p>
        {error && <p className="error">{error}</p>}
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Periods</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.name}</strong>
                  <div className="muted">{row.description}</div>
                </td>
                <td>{row.periods}</td>
                <td>
                  <Link to={`/scenarios/${row.id}`}>Open</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export { money };
