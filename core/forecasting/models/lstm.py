"""LSTM sequence forecaster — experimental (optional dep: torch)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.forecasting.models import ModelBase

_WINDOW: int = 60
_HIDDEN: int = 64
_LAYERS: int = 2


class LSTMForecaster(ModelBase):
    """Sequence LSTM for return prediction.

    Experimental: is_experimental=True means it cannot enter production ensemble
    unless it beats XGBoost after Benjamini-Hochberg correction.

    Requires: torch >= 2.3.0 (optional dependency).
    """

    name = "lstm"
    is_experimental = True

    def __init__(self, window: int = _WINDOW, hidden: int = _HIDDEN) -> None:
        self._window = window
        self._hidden = hidden
        self._model = None
        self._scaler = None

    def fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        try:
            import torch  # noqa: PLC0415
            import torch.nn as nn  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("torch is required for LSTMForecaster") from e

        X_num = X.select_dtypes(include=[np.number]).fillna(0)
        if len(X_num) < self._window + 10:
            self._model = None
            return

        from sklearn.preprocessing import StandardScaler  # noqa: PLC0415

        self._scaler = StandardScaler()
        X_scaled = self._scaler.fit_transform(X_num.values)

        seqs, targets = [], []
        for i in range(len(X_scaled) - self._window):
            seqs.append(X_scaled[i : i + self._window])
            targets.append(float(y.iloc[i + self._window]))

        X_t = torch.tensor(np.array(seqs), dtype=torch.float32)
        y_t = torch.tensor(np.array(targets), dtype=torch.float32).unsqueeze(1)

        class _LSTMNet(nn.Module):
            def __init__(self, n_features: int, hidden: int, layers: int) -> None:
                super().__init__()
                self.lstm = nn.LSTM(n_features, hidden, layers, batch_first=True, dropout=0.2)
                self.fc = nn.Linear(hidden, 1)

            def forward(self, x: "torch.Tensor") -> "torch.Tensor":
                out, _ = self.lstm(x)
                return self.fc(out[:, -1, :])

        n_features = X_t.shape[2]
        net = _LSTMNet(n_features, self._hidden, _LAYERS)
        optimizer = torch.optim.Adam(net.parameters(), lr=1e-3)
        loss_fn = nn.MSELoss()

        # Chronological 80/20 train/val split for early stopping.
        split_idx = max(int(len(X_t) * 0.8), 1)
        X_train, y_train = X_t[:split_idx], y_t[:split_idx]
        X_val, y_val = X_t[split_idx:], y_t[split_idx:]
        use_val = len(X_val) >= 5

        best_val_loss = float("inf")
        patience_count = 0
        _PATIENCE = 5
        _MAX_EPOCHS = 100

        for _ in range(_MAX_EPOCHS):
            net.train()
            optimizer.zero_grad()
            loss = loss_fn(net(X_train), y_train)
            loss.backward()
            optimizer.step()

            if use_val:
                net.eval()
                with torch.no_grad():
                    val_loss = float(loss_fn(net(X_val), y_val))
                if val_loss < best_val_loss - 1e-4:
                    best_val_loss = val_loss
                    patience_count = 0
                else:
                    patience_count += 1
                    if patience_count >= _PATIENCE:
                        break

        net.eval()
        self._model = net

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self._model is None or self._scaler is None:
            return np.zeros(len(X))
        try:
            import torch  # noqa: PLC0415

            X_num = X.select_dtypes(include=[np.number]).fillna(0)
            X_scaled = self._scaler.transform(X_num.values)

            if len(X_scaled) < self._window:
                return np.zeros(len(X))

            seq = torch.tensor(X_scaled[-self._window :][np.newaxis, :, :], dtype=torch.float32)
            with torch.no_grad():
                pred = float(self._model(seq).item())
            return np.full(len(X), pred)
        except Exception:
            return np.zeros(len(X))
