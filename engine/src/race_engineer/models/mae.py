"""Telemetry Transformer: a masked autoencoder over corner segments.

The 80-point segment is cut into 10 patches of 8 points (40 m each). Training hides about half
the patches' signal and asks the model to reconstruct it, predicting a mean *and* a variance at
every point (a heteroscedastic Gaussian), so it can say "this part of the corner is always
variable" versus "this should have been predictable".

Two outputs matter downstream:
- the [CLS] embedding, a compact description of how the corner was driven (driving style);
- the reconstruction likelihood, which is low when a lap does something the model can't explain
  from the rest of the corner and its context (a candidate mistake).

MAEConfig also carries the ablation switches (objective, context, signal channels, and a CNN in
place of the Transformer), so a checkpoint records exactly what was trained and scoring can
honour it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

import torch
from torch import nn

from race_engineer.models.tensors import N_COMPOUNDS, N_NUMERIC, SIGNAL

MODEL_TYPES = ("transformer", "cnn")
OBJECTIVES = ("nll", "mse")


@dataclass
class MAEConfig:
    n_points: int = 80
    patch: int = 8
    d_model: int = 128
    n_heads: int = 4
    n_layers: int = 4
    d_ff: int = 256
    dropout: float = 0.1
    embed_dim: int = 64
    n_context: int = 2
    # Ablation switches; the defaults are the original model.
    model_type: str = "transformer"  # or "cnn" (models/cnn.py), same inputs and outputs
    objective: str = "nll"  # "nll": Gaussian mean and variance; "mse": squared error only
    use_context: bool = True  # False: context token and context channels are zeroed
    signal_channels: tuple[str, ...] = SIGNAL  # which inputs are masked and reconstructed

    def __post_init__(self) -> None:
        self.signal_channels = tuple(self.signal_channels)  # TOML and JSON give lists
        if self.model_type not in MODEL_TYPES:
            raise ValueError(f"model_type {self.model_type!r}: choose from {MODEL_TYPES}")
        if self.objective not in OBJECTIVES:
            raise ValueError(f"objective {self.objective!r}: choose from {OBJECTIVES}")
        unknown = set(self.signal_channels) - set(SIGNAL)
        if not self.signal_channels or unknown or len(set(self.signal_channels)) < self.n_signal:
            raise ValueError(f"signal_channels must be distinct names from {SIGNAL}")
        if self.n_points % self.patch:
            raise ValueError("n_points must be a whole number of patches")

    @property
    def n_signal(self) -> int:
        return len(self.signal_channels)

    @property
    def n_patches(self) -> int:
        return self.n_points // self.patch

    @property
    def channel_index(self) -> list[int]:
        """Positions of the model's channels in SIGNAL (the order the dataset stores them)."""
        return [SIGNAL.index(c) for c in self.signal_channels]

    def to_dict(self) -> dict:
        return {**asdict(self), "n_signal": self.n_signal}

    @classmethod
    def from_dict(cls, values: dict) -> MAEConfig:
        """Rebuild a saved config. Fields added since it was saved take their defaults (the
        original model), and keys that are no longer fields (like n_signal) are ignored."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in values.items() if k in known})


class MaskedAutoencoder(nn.Module):
    """What training and scoring rely on: `cfg`, and forward(signal, context_channels,
    context_cat, context_num, mask) -> (mean, logvar, embedding), where `signal` holds only
    cfg.signal_channels and `mask` is (B, n_patches) with True = hidden."""

    def __init__(self, cfg: MAEConfig) -> None:
        super().__init__()
        self.cfg = cfg


class TelemetryMAE(MaskedAutoencoder):
    def __init__(self, cfg: MAEConfig) -> None:
        super().__init__(cfg)
        width = (cfg.n_signal + cfg.n_context) * cfg.patch
        self.patch_in = nn.Linear(width, cfg.d_model)
        self.mask_token = nn.Parameter(torch.zeros(cfg.d_model))
        self.cls = nn.Parameter(torch.zeros(1, 1, cfg.d_model))
        self.session = nn.Embedding(2, cfg.d_model)
        self.compound = nn.Embedding(N_COMPOUNDS, cfg.d_model)
        self.numeric = nn.Sequential(
            nn.Linear(N_NUMERIC, cfg.d_model), nn.GELU(), nn.Linear(cfg.d_model, cfg.d_model)
        )
        self.pos = nn.Parameter(
            torch.zeros(1, cfg.n_patches + 2, cfg.d_model)
        )  # [CLS], context, patches
        layer = nn.TransformerEncoderLayer(
            cfg.d_model,
            cfg.n_heads,
            cfg.d_ff,
            cfg.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, cfg.n_layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(cfg.d_model)
        self.head = nn.Linear(cfg.d_model, 2 * cfg.n_signal * cfg.patch)  # mean and log-variance
        self.embed_head = nn.Linear(cfg.d_model, cfg.embed_dim)
        nn.init.trunc_normal_(self.pos, std=0.02)
        nn.init.trunc_normal_(self.cls, std=0.02)
        nn.init.trunc_normal_(self.mask_token, std=0.02)

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
        visible = point_mask(~mask, cfg.patch).to(signal.dtype)
        x = torch.cat([signal * visible, context_channels], dim=1)  # hidden signal is zeroed
        patches = (
            x.reshape(b, -1, cfg.n_patches, cfg.patch)
            .permute(0, 2, 1, 3)
            .reshape(b, cfg.n_patches, -1)
        )
        tokens = self.patch_in(patches) + mask[..., None].to(signal.dtype) * self.mask_token
        if cfg.use_context:
            context = (
                self.session(context_cat[:, 0])
                + self.compound(context_cat[:, 1])
                + self.numeric(context_num)
            )
        else:
            context = tokens.new_zeros(b, cfg.d_model)
        seq = torch.cat([self.cls.expand(b, -1, -1), context[:, None], tokens], dim=1) + self.pos
        hidden = self.norm(self.encoder(seq))
        out = self.head(hidden[:, 2:]).reshape(b, cfg.n_patches, 2, cfg.n_signal, cfg.patch)
        out = out.permute(0, 2, 3, 1, 4).reshape(b, 2, cfg.n_signal, cfg.n_points)
        mean, logvar = out[:, 0], out[:, 1].clamp(-6.0, 6.0)
        return mean, logvar, self.embed_head(hidden[:, 0])


def build_model(cfg: MAEConfig) -> MaskedAutoencoder:
    """The masked autoencoder named by cfg.model_type."""
    if cfg.model_type == "cnn":
        from race_engineer.models.cnn import ConvMAE  # cnn.py builds on this module

        return ConvMAE(cfg)
    return TelemetryMAE(cfg)


def point_mask(mask: torch.Tensor, patch: int) -> torch.Tensor:
    """(B, n_patches) patch mask -> (B, 1, n_points), to broadcast over channels."""
    return mask.repeat_interleave(patch, dim=1)[:, None, :]


def model_inputs(cfg: MAEConfig, batch: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """The batch as this model sees it. Datasets keep every SIGNAL channel and each model picks
    its own here, at batch time, so one copy of the data serves every channel ablation."""
    if batch["signal"].shape[1] != len(SIGNAL):
        raise ValueError(f"batches hold all of {SIGNAL}; the model picks its channels")
    if cfg.signal_channels == SIGNAL:
        return batch
    return {**batch, "signal": batch["signal"][:, cfg.channel_index]}


def gaussian_nll(target: torch.Tensor, mean: torch.Tensor, logvar: torch.Tensor) -> torch.Tensor:
    """Pointwise negative log-likelihood (up to a constant): (B, n_signal, 80)."""
    return 0.5 * (logvar + (target - mean) ** 2 * torch.exp(-logvar))


def pointwise_loss(
    cfg: MAEConfig, target: torch.Tensor, mean: torch.Tensor, logvar: torch.Tensor
) -> torch.Tensor:
    """The training objective at every point; "mse" ignores the variance head entirely."""
    if cfg.objective == "mse":
        return (target - mean) ** 2
    return gaussian_nll(target, mean, logvar)


def random_patch_mask(
    batch: int,
    n_patches: int,
    ratio: float,
    generator: torch.Generator | None = None,
    device: torch.device | None = None,
) -> torch.Tensor:
    """Hide `ratio` of the patches in each row, chosen at random."""
    scores = torch.rand(batch, n_patches, generator=generator, device=device)
    n_hidden = max(1, round(ratio * n_patches))
    return scores.argsort(dim=1).argsort(dim=1) < n_hidden


def masked_metrics(
    model: MaskedAutoencoder, batch: dict[str, torch.Tensor], mask: torch.Tensor
) -> tuple[torch.Tensor, torch.Tensor]:
    """Training loss and plain squared error, both averaged over the hidden points only.

    The squared error is comparable between objectives; the loss is what gets optimised.
    """
    batch = model_inputs(model.cfg, batch)
    mean, logvar, _ = model(
        batch["signal"], batch["context_channels"], batch["context_cat"], batch["context_num"], mask
    )
    hidden = point_mask(mask, model.cfg.patch).expand_as(mean)
    target = batch["signal"]
    loss = pointwise_loss(model.cfg, target, mean, logvar)[hidden].mean()
    return loss, ((target - mean) ** 2)[hidden].mean()


def masked_loss(
    model: MaskedAutoencoder, batch: dict[str, torch.Tensor], mask: torch.Tensor
) -> torch.Tensor:
    return masked_metrics(model, batch, mask)[0]
