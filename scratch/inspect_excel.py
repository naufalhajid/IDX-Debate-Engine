import pandas as pd

file_path = "output/IDX Fundamental Analysis 2026-04-24.xlsx"
xl = pd.ExcelFile(file_path)
print("Sheet names:", xl.sheet_names)

for sheet in xl.sheet_names:
    df = pd.read_excel(file_path, sheet_name=sheet, nrows=2)
    print(f"\nSheet '{sheet}' Columns:")
    print(list(df.columns))
