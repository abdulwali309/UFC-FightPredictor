export type UpcomingPrediction = {
  fight_key: string;
  event_id: string | null;
  event_name: string | null;
  scheduled_date: string | null;
  card_order: number | null;
  fighter_1: string;
  fighter_2: string;
  fighter_1_id: string | null;
  fighter_2_id: string | null;
  weight_class: string | null;
  model_version: string | null;
  prob_f1: number | null;
  prob_f2: number | null;
};

export type CompletedPrediction = {
  fight_id: string;
  event_id: string;
  card_order: number | null;
  event_name: string | null;
  event_date: string | null;
  red_fighter: string | null;
  blue_fighter: string | null;
  weight_class: string | null;
  winner_corner: "red" | "blue" | "draw" | null;
  prob_red: number | null;
  prob_blue: number | null;
  predicted_corner: "red" | "blue" | null;
  correct: boolean | null;
};

export type QuickMatchup = {
  weight_class: string | null;
  fighter_1_id: string | null;
  fighter_1: string;
  fighter_2_id: string | null;
  fighter_2: string;
  fighter_1_last_fight: string | null;
  fighter_2_last_fight: string | null;
  pair_freshness_date: string | null;
};

type ApiListResponse<T> = {
  count: number;
  rows: T[];
  model_version?: string | null;
};

export type PredictRequest = {
  fighter_1: string;
  fighter_2: string;
  weight_class?: string;
};

export type PredictResponse = {
  model_version: string;
  fighter_1: string;
  fighter_2: string;
  weight_class: string | null;
  prob_f1: number;
  prob_f2: number;
};

const FALLBACK_API_BASE = "https://ufcml-api-548699782535.us-east1.run.app";

export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE_URL || FALLBACK_API_BASE;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`API request failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function fetchUpcoming(limit = 16): Promise<ApiListResponse<UpcomingPrediction>> {
  return getJson<ApiListResponse<UpcomingPrediction>>(`/upcoming/predictions?limit=${limit}`);
}

export async function fetchCompleted(limit = 40): Promise<ApiListResponse<CompletedPrediction>> {
  return getJson<ApiListResponse<CompletedPrediction>>(`/completed/predictions?limit=${limit}`);
}

export async function fetchQuickMatchups(limit = 8): Promise<ApiListResponse<QuickMatchup>> {
  return getJson<ApiListResponse<QuickMatchup>>(`/matchups/quick?limit=${limit}`);
}

export async function postPredict(payload: PredictRequest): Promise<PredictResponse> {
  const res = await fetch(`${apiBase()}/predict`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    const msg = body?.detail || `Prediction failed: ${res.status}`;
    throw new Error(msg);
  }

  return (await res.json()) as PredictResponse;
}

export async function searchFighters(q: string, limit = 8): Promise<string[]> {
  const res = await getJson<ApiListResponse<{ full_name: string }>>(
    `/fighters/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  );
  return res.rows.map((r) => r.full_name).filter(Boolean);
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) {
    return "--";
  }
  return `${(v * 100).toFixed(1)}%`;
}
