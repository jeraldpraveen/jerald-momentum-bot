import { useCurrentCycle } from "./hooks/useCurrentCycle";
import { CycleDashboard } from "./features/cycle/CycleDashboard";

export function App() {
  const { cycle, loading, error, noActiveCycle, setCycle } = useCurrentCycle();

  return (
    <div className="app">
      <header className="app-header">
        <h1>Momentum Workbench</h1>
        <span className="muted">Weekly rotation · decision &amp; journaling</span>
      </header>

      <main>
        {loading && <p className="muted">Loading…</p>}
        {error && <p className="error-box">Backend unreachable: {error}</p>}
        {!loading && !error && (
          <CycleDashboard
            cycle={cycle}
            noActiveCycle={noActiveCycle}
            onCycleChange={setCycle}
          />
        )}
      </main>
    </div>
  );
}
