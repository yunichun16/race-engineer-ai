"""A convolutional masked autoencoder: the baseline the Telemetry Transformer has to beat.

Same inputs, masking and outputs as TelemetryMAE (MAEConfig.model_type = "cnn" picks it), so
training, scoring and the ablation runner treat the two alike. If a plain 1D CNN does as well,
attention isn't what makes the model work.

A small U-Net. The encoder sees the signal with hidden patches zeroed (so they can't leak), the
always-visible context channels and a mask channel (1 = hidden); the context token is added at
every position. It halves the 80 points three times down to 10 positions, one per 40 m patch
like the Transformer's tokens. The embedding is the mean over those 10, and that summary is
also broadcast back into the decoder so a long hidden stretch can be filled from both ends of
the corner. A thin full-resolution skip keeps 5 m detail without paying for wide convolutions
at 80 points (convolutions are the slow part on the Mac GPU).
"""

from __future__ import annotations

import torch
from torch import nn

from race_engineer.models.mae import MAEConfig, MaskedAutoencoder, point_mask
from race_engineer.models.tensors import N_COMPOUNDS, N_NUMERIC


class ResBlock(nn.Module):
    def __init__(self, channels: int, dropout: float, kernel: int = 3) -> None:
        super().__init__()
        # GroupNorm(1, C) normalises each sample on its own: no batch statistics.
        self.net = nn.Sequential(
            nn.GroupNorm(1, channels),
            nn.GELU(),
            nn.Conv1d(channels, channels, kernel, padding=kernel // 2),
            nn.GroupNorm(1, channels),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Conv1d(channels, channels, kernel, padding=kernel // 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.net(x)


def down(channels_in: int, channels_out: int) -> nn.Conv1d:
    return nn.Conv1d(channels_in, channels_out, 4, stride=2, padding=1)


class Up(nn.Module):
    """Nearest-neighbour doubling, then a convolution. ConvTranspose1d and nn.Upsample do the
    same job but run several times slower on the Mac GPU (MPS)."""

    def __init__(self, channels_in: int, channels_out: int) -> None:
        super().__init__()
        self.conv = nn.Conv1d(channels_in, channels_out, 3, padding=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, c, n = x.shape
        return self.conv(x[..., None].expand(b, c, n, 2).reshape(b, c, 2 * n))


class ConvMAE(MaskedAutoencoder):
    def __init__(self, cfg: MAEConfig) -> None:
        super().__init__(cfg)
        if cfg.n_points % 8:
            raise ValueError("the CNN halves the segment three times: n_points must divide by 8")
        c, thin = cfg.d_model, max(8, cfg.d_model // 4)
        self.stem = nn.Conv1d(cfg.n_signal + cfg.n_context + 1, thin, 5, padding=2)  # 80 points
        self.session = nn.Embedding(2, c)
        self.compound = nn.Embedding(N_COMPOUNDS, c)
        self.numeric = nn.Sequential(nn.Linear(N_NUMERIC, c), nn.GELU(), nn.Linear(c, c))
        self.down0 = down(thin, c)
        self.enc1 = ResBlock(c, cfg.dropout)  # 40 points (10 m)
        self.down1 = down(c, c)
        self.enc2 = ResBlock(c, cfg.dropout)  # 20 points
        self.down2 = down(c, c)
        self.mid = ResBlock(c, cfg.dropout)  # 10 points: one per 40 m patch
        self.summary = nn.Linear(c, c)
        self.up2 = Up(c, c)
        self.dec2 = ResBlock(c, cfg.dropout)
        self.up1 = Up(c, c)
        self.dec1 = ResBlock(c, cfg.dropout)
        self.up0 = Up(c, thin)
        self.dec0 = ResBlock(thin, cfg.dropout)  # back at 80 points
        self.out_norm = nn.GroupNorm(1, thin)
        self.head = nn.Conv1d(thin, 2 * cfg.n_signal, 1)  # mean and log-variance per point
        self.embed_norm = nn.LayerNorm(c)
        self.embed_head = nn.Linear(c, cfg.embed_dim)

    def forward(
        self,
        signal: torch.Tensor,  # (B, n_signal, 80)
        context_channels: torch.Tensor,  # (B, n_context, 80)
        context_cat: torch.Tensor,  # (B, 2)
        context_num: torch.Tensor,  # (B, N_NUMERIC)
        mask: torch.Tensor,  # (B, n_patches) bool, True = hidden
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        cfg = self.cfg
        b = signal.shape[0]
        if not cfg.use_context:
            context_channels = torch.zeros_like(context_channels)
        hidden = point_mask(mask, cfg.patch).to(signal.dtype)
        x = torch.cat([signal * (1 - hidden), context_channels, hidden], dim=1)
        e0 = self.stem(x)
        h = self.down0(e0)
        if cfg.use_context:
            context = (
                self.session(context_cat[:, 0])
                + self.compound(context_cat[:, 1])
                + self.numeric(context_num)
            )
            h = h + context[..., None]
        e1 = self.enc1(h)
        e2 = self.enc2(self.down1(e1))
        m = self.mid(self.down2(e2))
        pooled = m.mean(dim=2)
        m = m + self.summary(pooled)[..., None]
        d2 = self.dec2(self.up2(m) + e2)
        d1 = self.dec1(self.up1(d2) + e1)
        d0 = self.dec0(self.up0(d1) + e0)
        out = self.head(torch.nn.functional.gelu(self.out_norm(d0)))
        out = out.reshape(b, 2, cfg.n_signal, cfg.n_points)
        mean, logvar = out[:, 0], out[:, 1].clamp(-6.0, 6.0)
        return mean, logvar, self.embed_head(self.embed_norm(pooled))
