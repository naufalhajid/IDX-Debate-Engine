export type Rating = 'STRONG_BUY' | 'BUY' | 'HOLD' | 'AVOID';
export type ModelRating = Rating | 'SELL';
export type ExecutionStatus =
  | 'EXECUTABLE_BUY'
  | 'WAITLIST'
  | 'NO_TRADE'
  | 'AVOID'
  | 'INSUFFICIENT_DATA';

export interface StockResult {
  ticker: string;
  sector: string;
  conviction_score: number;
  /** Legacy BUY/HOLD/AVOID projection retained for compatibility only. */
  rating: Rating;
  legacy_rating: Rating;
  model_rating: ModelRating | null;
  execution_status: ExecutionStatus;
  actionable: boolean;
  target_price: number | null;
  stop_loss: number | null;
  entry_low: number | null;
  entry_high: number | null;
  risk_reward: number | null;
  debate_rounds: DebateRound[];
  scout_metrics: ScoutMetrics;
  devil_advocate_triggered: boolean;
  verdict_summary: string;
  verdict_reasoning?: string;
  last_debated_at: string;
}

export interface DebateRound {
  round: number;
  bull_argument: string;
  bear_argument: string;
  score_delta: number;
}

export interface ScoutMetrics {
  technical: Record<string, number | string>;
  fundamental: Record<string, number | string>;
  sentiment: Record<string, number | string>;
}

export type DebateEvent =
  | { type: 'progress'; ticker: string; phase: string; pct: number }
  | { type: 'scout'; ticker: string; metrics: ScoutMetrics }
  | { type: 'round'; ticker: string; data: DebateRound }
  | { type: 'devil_advocate'; ticker: string; question?: string }
  | { type: 'verdict'; ticker: string; result: StockResult; stage?: 'interim' | 'final' }
  | { type: 'done'; ticker: string }
  | { type: 'error'; ticker: string; message: string };
