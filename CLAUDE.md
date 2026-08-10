# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file ComfyUI custom node package. All code lives in `__init__.py`; there
is no build step, test suite, or linter config. The whole extension is one node,
`SaveImageSequenceFast`, which saves an image batch as a PNG sequence with the
frames encoded in parallel.

The node is registered through ComfyUI's **V3 schema API**, not the legacy
`NODE_CLASS_MAPPINGS` dict: `comfy_entrypoint()` returns a `ComfyExtension` whose
`get_node_list()` yields `IO.ComfyNode` subclasses. Inputs are declared in
`define_schema()` and arrive as named kwargs to `execute()`; hidden inputs
(`prompt`, `extra_pnginfo`) are read off `cls.hidden`, not from the signature.

## Local environment

- ComfyUI checkout: `/home/dreevelle/comfy/ComfyUI`
- Python venv (has `av`, `torch`, `numpy`): `/home/dreevelle/comfy-env/bin/python`
- The package is published, and `ComfyUI/custom_nodes/comfyui-fast-image-sequence`
  (lowercase, the registry id) is a **ComfyUI-Manager install** that tracks
  registry releases. This working tree is not wired into ComfyUI.

To test a change before releasing it, symlink this tree in under its CamelCase
name so it doesn't collide with the Manager install:

```bash
ln -sfn /home/dreevelle/Projects/ComfyUI-FastImageSequence \
        /home/dreevelle/comfy/ComfyUI/custom_nodes/ComfyUI-FastImageSequence
```

**Remove that symlink before doing anything in ComfyUI-Manager for this
package.** Manager's remove/replace path does `shutil.rmtree` on the *resolved*
path, so it deletes the symlink target's contents instead of unlinking — on
2026-08-09 that wiped a sibling package's working tree, `.git` included, and it
had to be restored by re-cloning. Commit and push before any Manager operation,
and never leave a symlinked dev copy in place while installing or updating the
published version.

Run ComfyUI:

```bash
/home/dreevelle/comfy-env/bin/python /home/dreevelle/comfy/ComfyUI/main.py
```

## Verifying changes

`__init__.py` imports `folder_paths`, `comfy.cli_args`, and `comfy_api.latest` at
module scope, so it cannot be imported standalone — ComfyUI must be on
`sys.path`. To exercise the pure-encoding helpers (`_encode_png`,
`inject_png_metadata`) without launching the server:

```bash
cd /home/dreevelle/comfy/ComfyUI && PYTHONPATH=/home/dreevelle/comfy/ComfyUI \
/home/dreevelle/comfy-env/bin/python -c "
import importlib.util, torch
spec = importlib.util.spec_from_file_location('fis', '/home/dreevelle/Projects/ComfyUI-FastImageSequence/__init__.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(len(m._encode_png(torch.rand(8, 8, 3), '16-bit')))
"
```

The project's core invariant is **byte-identical output** to the built-in
`Save Image (Advanced)` node. Any change to the encode path should be checked by
encoding the same tensor through both `_encode_png` here and `_encode_image` in
`comfy_extras/nodes_images.py` and comparing the bytes.

## Architecture

Three layers in `__init__.py`, in order:

1. **`_encode_png`** — tensor → PNG bytes, entirely in memory. Quantizes to
   uint8/uint16 via `_FORMAT_SPECS`, then encodes through a raw
   `av.CodecContext` rather than a container: PyAV's `image2` muxer needs a real
   file path and won't write to `BytesIO`, and for a single-frame PNG the codec
   output *is* the file. Note the little-endian frame format / big-endian stream
   format pairing for 16-bit — the `reformat()` call handles the swap.
2. **`inject_png_metadata`** — splices `tEXt` chunks in after IHDR by parsing the
   IHDR length from bytes 8:12. Operates on finished PNG bytes, so it's
   independent of the encoder.
3. **`SaveImageSequenceFast.execute`** — filename/counter allocation via
   `folder_paths.get_save_image_path`, then a `ThreadPoolExecutor` over
   `encode_and_write`. Parallelism works because PyAV releases the GIL during
   zlib compression. `pool.map` is used specifically because it preserves input
   order, keeping the returned UI results frame-ordered.

Because the node allocates one `counter` for the whole batch and derives each
filename as `counter + batch_number`, a single invocation owns a contiguous
numbering range. Splitting one sequence across two save nodes collides.

### Relationship to ComfyUI core

Layers 1 and 2 are **deliberately copied** from
`comfy_extras/nodes_images.py` rather than imported, so a core update can't
silently break this node. That means upstream changes don't propagate: when
touching the encode path, diff against that file.

The copy is also a subset. Upstream keys `_FORMAT_SPECS` by
`(file_format, bit_depth, num_channels)` and supports EXR, colorspace
conversion, and 1-channel grayscale (including 2D `HxW` tensors); here the key is
`(bit_depth, has_alpha)` and only 3- and 4-channel PNG exist. A grayscale input
raises `KeyError` — if that needs supporting, port the `num_channels`-keyed spec
table and the `ndim == 2` unsqueeze from upstream.

## Conventions

- Version is duplicated in `__version__` (`__init__.py`) and `[project].version`
  (`pyproject.toml`) — bump both.
- `dependencies` stays empty: `av`, `numpy`, and `torch` ship with ComfyUI.
- Respect `args.disable_metadata` (ComfyUI's global CLI flag) alongside the
  node's own `metadata` option in any metadata-related change.

## Releasing

Published on the Comfy Registry as `comfyui-fast-image-sequence` under publisher
`dreevelle`, which is what makes it installable from ComfyUI-Manager.

**Changing `[project].version` in `pyproject.toml` on `main` publishes a
release.** `.github/workflows/publish_action.yml` watches that file's path and
runs `Comfy-Org/publish-node-action`, authenticating with the
`REGISTRY_ACCESS_TOKEN` repo secret. Don't touch the version field unless a
release is intended; republishing an existing version fails with
`400 The node version already exists`.

`.comfyignore` keeps dev-only files (`CLAUDE.md`, `.github/`) out of the
published archive. `comfy node validate` runs the registry's checks locally and
`comfy node pack` produces the exact archive that would be uploaded — inspect it
before releasing if the file list may have changed.

`assets/icon.png` is the registry icon, pointed at by `[tool.comfy] Icon` as a
raw GitHub URL on `main`. The registry stores the *URL*, not the bytes, and the
Manager hotlinks it, so **replacing this file on `main` restyles every card with
no republish**. Only changing the URL needs a version bump, because `icon` is
node-level metadata written as a side effect of publishing a version.

The background is **transparent on purpose**. The frontend's `PackBanner` draws
the icon twice into a 7:3 box — once as a backdrop, `bg-cover` plus `blur(10px)`
at `opacity-30`, and once `object-contain` on top. An opaque icon therefore shows
as a hard rectangle: `bg-cover` crops to the centre and zooms ~2.3x, so it samples
mostly glow and lifts the surround to ~47, while the contained copy still shows
the icon's own near-black corners at ~17. Deriving alpha from luminance removes
the edge completely, and it is the right derivation because the artwork is
additive glow on near-black. The cost, accepted deliberately, is that the pale
mark washes out on ComfyUI's light theme.

Regenerate from the brand original, capped at the registry's 400x400 square
maximum. Downscale in linear light; the naive sRGB-space resize dulls the ring
stroke and blurs the bar separations inside the D:

```bash
magick ~/Projects/alora/dev/assets/brand/dreevelle_pfp.png \
  -colorspace RGB -filter Lanczos -resize 400x400 -colorspace sRGB \
  \( +clone -grayscale Rec601Luma -level 7.45%,100% \) \
  -alpha off -compose CopyOpacity -composite \
  -background none -alpha Background -strip assets/icon.png
oxipng -o max --strip safe assets/icon.png
```

Do not set `[tool.comfy] Banner`. `PackBanner` resolves `banner_url || icon`, so
a banner *replaces* the mark rather than sitting behind it.

Check what's live:

```bash
curl -s https://api.comfy.org/nodes/comfyui-fast-image-sequence/versions
```
