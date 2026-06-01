import yfinance as yf
import pandas as pd

# Download BBCA and IHSG
data = yf.download(["BBCA.JK", "^JKSE"], period="120d")
print("Data columns:", data.columns)

# Extract BBCA and IHSG
bbca = data["Close"]["BBCA.JK"].dropna()
ihsg = data["Close"]["^JKSE"].dropna()

print("Original BBCA length:", len(bbca))
print("Original IHSG length:", len(ihsg))

# Align BBCA to IHSG dates
bbca_aligned = data["Volume"]["BBCA.JK"].reindex(ihsg.index)
print("Aligned BBCA length:", len(bbca_aligned))
print("Aligned BBCA tail:")
print(bbca_aligned.tail(15))

# Calculate stats
vol = bbca_aligned
recent_vol  = vol.tail(5).sum()
avg_vol_20d = vol.tail(20).mean()
zero_days = (vol.tail(20) == 0).sum()
ratio = (recent_vol / avg_vol_20d) if avg_vol_20d > 0 else 0

print(f"Recent volume (last 5): {recent_vol}")
print(f"Avg volume (20d): {avg_vol_20d}")
print(f"Zero days count: {zero_days}")
print(f"Ratio (recent / avg_20d): {ratio}")
