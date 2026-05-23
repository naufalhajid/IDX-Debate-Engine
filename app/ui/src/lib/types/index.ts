export type Rating = 'STRONG_BUY' | 'BUY' | 'HOLD' | 'AVOID';

export interface StockResult {
  ticker: string;
  sector: string;
  conviction_score: number;
  rating: Rating;
  actionable: boolean;
  target_price: number;
  stop_loss: number;
  entry_low: number;
  entry_high: number;
  risk_reward: number;
  debate_rounds: DebateRound[];
  scout_metrics: ScoutMetrics;
  devil_advocate_triggered: boolean;
  verdict_summary: string;
  verdict_reasoning?: string;
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
  | { type: 'verdict'; ticker: string; result: StockResult }
  | { type: 'done'; ticker: string }
  | { type: 'error'; ticker: string; message: string };
