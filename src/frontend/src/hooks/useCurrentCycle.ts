import { useCallback, useEffect, useState } from "react";
import { api, ApiRequestError } from "../api/client";
import type { Cycle } from "../api/types";

interface State {
  cycle: Cycle | null;
  loading: boolean;
  error: string | null;
  noActiveCycle: boolean;
}

export function useCurrentCycle() {
  const [state, setState] = useState<State>({
    cycle: null,
    loading: true,
    error: null,
    noActiveCycle: false,
  });

  const refresh = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const cycle = await api.getCurrentCycle();
      setState({ cycle, loading: false, error: null, noActiveCycle: false });
    } catch (e) {
      if (e instanceof ApiRequestError && e.status === 404) {
        setState({ cycle: null, loading: false, error: null, noActiveCycle: true });
      } else {
        setState({
          cycle: null,
          loading: false,
          error: e instanceof Error ? e.message : String(e),
          noActiveCycle: false,
        });
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Setting a cycle (create/run/advance) always means one is now active.
  const setCycle = (cycle: Cycle) =>
    setState((s) => ({ ...s, cycle, noActiveCycle: false, error: null }));

  return { ...state, refresh, setCycle };
}
