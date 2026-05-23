import os
import json
from pathlib import Path
from datetime import datetime
import datetime as dt

def analyze_debates():
    debates_dir = Path("output/debates")
    if not debates_dir.exists():
        print(f"Directory {debates_dir} does not exist.")
        return

    json_files = list(debates_dir.glob("*.json"))
    
    total_files = len(json_files)
    if total_files == 0:
        print("No debate JSON files found.")
        return

    results = []
    errors = []

    for f in json_files:
        ticker = f.stem.replace("_debate", "").upper()
        try:
            mtime = f.stat().st_mtime
            file_date = datetime.fromtimestamp(mtime)
            
            content = f.read_text(encoding="utf-8")
            if not content.strip():
                continue
            data = json.loads(content)
            
            # Extract metadata/verdict
            verdict = data.get("verdict", {}) or {}
            rating = verdict.get("rating", "UNKNOWN")
            confidence = verdict.get("confidence", 0.0)
            conviction_score = data.get("conviction_score", 0.0)
            rounds = data.get("debate_rounds", 0)
            consensus = data.get("consensus_reached", False)
            
            # Check batch_timestamp in metadata
            metadata = data.get("metadata", {}) or {}
            batch_ts = metadata.get("batch_timestamp") or metadata.get("run_timestamp")
            
            debate_date = file_date
            if batch_ts:
                try:
                    debate_date = datetime.strptime(batch_ts, "%Y%m%d_%H%M%S")
                except ValueError:
                    try:
                        debate_date = datetime.strptime(batch_ts.split("_")[0], "%Y%m%d")
                    except Exception:
                        pass
            
            results.append({
                "ticker": ticker,
                "rating": rating,
                "confidence": confidence,
                "conviction_score": conviction_score,
                "rounds": rounds,
                "consensus": consensus,
                "date": debate_date,
                "file_date": file_date
            })
        except Exception as e:
            errors.append((ticker, str(e)))

    # Sort results by date descending (newest first)
    results.sort(key=lambda x: x["date"], reverse=True)

    # Compute statistics
    ratings_dist = {}
    total_conviction = 0
    total_confidence = 0
    consensus_count = 0
    
    for r in results:
        rating = r["rating"]
        ratings_dist[rating] = ratings_dist.get(rating, 0) + 1
        total_conviction += r["conviction_score"]
        total_confidence += r["confidence"]
        if r["consensus"]:
            consensus_count += 1

    avg_conviction = total_conviction / len(results) if results else 0
    avg_confidence = total_confidence / len(results) if results else 0
    consensus_rate = (consensus_count / len(results)) * 100 if results else 0

    # Time window analysis (based on May 23, 2026)
    today = datetime(2026, 5, 23, 19, 44, 48)
    one_month_ago = today - dt.timedelta(days=30)
    
    recent_count = sum(1 for r in results if r["date"] >= one_month_ago)
    stale_count = len(results) - recent_count

    report_lines = []
    report_lines.append(f"# Analysis of `output/debates` Folder")
    report_lines.append(f"\n## General Summary")
    report_lines.append(f"- **Total Tickers Debated:** {len(results)}")
    report_lines.append(f"- **Avg Conviction Score:** {avg_conviction:.3f}")
    report_lines.append(f"- **Avg Confidence:** {avg_confidence:.3f}")
    report_lines.append(f"- **Consensus Rate:** {consensus_rate:.2f}% ({consensus_count}/{len(results)})")
    report_lines.append(f"- **Unreadable Files / Errors:** {len(errors)}")

    report_lines.append(f"\n## Ratings Distribution")
    for rating, count in sorted(ratings_dist.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / len(results)) * 100
        report_lines.append(f"- **{rating}:** {count} ({percentage:.2f}%)")

    report_lines.append(f"\n## Freshness Analysis (Reference Date: {today.strftime('%Y-%m-%d')})")
    report_lines.append(f"- **Fresh (< 1 Month Old, >= {one_month_ago.strftime('%Y-%m-%d')}):** {recent_count} (Colored Green in UI)")
    report_lines.append(f"- **Stale (>= 1 Month Old, < {one_month_ago.strftime('%Y-%m-%d')}):** {stale_count} (Colored Red in UI)")

    if results:
        newest = results[0]
        oldest = results[-1]
        report_lines.append(f"- **Newest Debate:** {newest['ticker']} ({newest['date'].strftime('%Y-%m-%d %H:%M:%S')})")
        report_lines.append(f"- **Oldest Debate:** {oldest['ticker']} ({oldest['date'].strftime('%Y-%m-%d %H:%M:%S')})")

    report_lines.append(f"\n## Detailed Ticker Breakdown")
    report_lines.append("| Ticker | Rating | Conviction | Confidence | Rounds | Consensus | Debate Date | Status |")
    report_lines.append("|--------|--------|------------|------------|--------|-----------|-------------|--------|")
    for r in results:
        status_str = "🟢 Fresh" if r["date"] >= one_month_ago else "🔴 Stale"
        report_lines.append(f"| {r['ticker']} | {r['rating']} | {r['conviction_score']:.3f} | {r['confidence']:.3f} | {r['rounds']} | {r['consensus']} | {r['date'].strftime('%Y-%m-%d')} | {status_str} |")

    if errors:
        report_lines.append(f"\n## Errors")
        for ticker, err in errors:
            report_lines.append(f"- **{ticker}:** {err}")

    report_md = "\n".join(report_lines)

    # Output path for the artifact
    artifact_path = Path("C:/Users/naufa/.gemini/antigravity/brain/6de612df-ec1d-495a-85ed-77fb96995161/debates_analysis_report.md")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(report_md, encoding="utf-8")
    
    # Print a safe console message without Unicode errors
    print("Report written successfully to: C:/Users/naufa/.gemini/antigravity/brain/6de612df-ec1d-495a-85ed-77fb96995161/debates_analysis_report.md")

if __name__ == "__main__":
    analyze_debates()
