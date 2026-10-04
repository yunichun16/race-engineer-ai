from dataclasses import replace

import torch
from test_mae import batch

from race_engineer.models.cnn import ConvMAE
from race_engineer.models.mae import MAEConfig, build_model, masked_loss, random_patch_mask

CFG = MAEConfig(model_type="cnn", d_model=32, dropout=0.0, embed_dim=16)


def test_shapes_match_the_transformer() -> None:
    for channels in (CFG.signal_channels, ("speed", "offset_m")):
        cfg = replace(CFG, signal_channels=channels)
        model = build_model(cfg).eval()
        assert isinstance(model, ConvMAE)
        x = batch()
        x["signal"] = x["signal"][:, cfg.channel_index]
        mean, logvar, embedding = model(**x, mask=random_patch_mask(8, cfg.n_patches, 0.5))
        assert mean.shape == logvar.shape == (8, cfg.n_signal, cfg.n_points)
        assert embedding.shape == (8, cfg.embed_dim)


def test_hidden_patches_cannot_leak_into_the_prediction() -> None:
    model = ConvMAE(CFG).eval()
    x = batch()
    mask = torch.zeros(8, CFG.n_patches, dtype=torch.bool)
    mask[:, [0, 4, 5, 9]] = True  # both edges and a run of two
    altered = {**x, "signal": x["signal"].clone()}
    for p in (0, 4, 5, 9):
        altered["signal"][:, :, p * CFG.patch : (p + 1) * CFG.patch] += 100.0 * (p + 1)
    with torch.no_grad():
        a, b = model(**x, mask=mask), model(**altered, mask=mask)
    assert all(torch.allclose(p, q) for p, q in zip(a, b, strict=True))


def test_without_context_the_context_inputs_are_ignored() -> None:
    model = ConvMAE(replace(CFG, use_context=False)).eval()
    x = batch()
    other = {
        **x,
        "context_channels": x["context_channels"] * 3,
        "context_num": x["context_num"] + 1,
    }
    mask = random_patch_mask(8, CFG.n_patches, 0.5, generator=torch.Generator().manual_seed(0))
    with torch.no_grad():
        a, b = model(**x, mask=mask), model(**other, mask=mask)
    assert all(torch.allclose(p, q) for p, q in zip(a, b, strict=True))


def test_default_size_is_comparable_to_the_transformer() -> None:
    n = sum(p.numel() for p in build_model(MAEConfig(model_type="cnn")).parameters())
    assert 300_000 < n < 1_000_000


def test_it_learns_a_predictable_signal() -> None:
    torch.manual_seed(0)
    model = ConvMAE(CFG)
    x = batch(64)
    t = torch.linspace(0, 3.14, CFG.n_points)
    x["signal"] = torch.sin(t[None, None] + torch.rand(64, CFG.n_signal, 1))
    opt = torch.optim.AdamW(model.parameters(), lr=3e-3)
    losses = []
    for _ in range(100):
        loss = masked_loss(model, x, random_patch_mask(64, CFG.n_patches, 0.5))
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(loss.item())
    assert losses[-1] < losses[0] - 1.0
