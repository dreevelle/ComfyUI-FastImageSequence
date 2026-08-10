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
- The installed copy at `/home/dreevelle/comfy/ComfyUI/custom_nodes/ComfyUI-FastImageSequence`
  is an **independent clone of the same GitHub remote**, not a symlink to this
  working tree. Changes made here are not live in ComfyUI until they're pushed
  and pulled there (or copied over).

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
raw GitHub URL on `main`. The registry stores the *URL*, not the bytes, so the
asset has to be on `main` before the version bump that references it — and
because `icon` is node-level metadata written as a side effect of publishing a
version, a changed icon only reaches the registry on the next bump. It is the
shared `dreevelle` mark, regenerated from the brand original at the registry's
400×400 square maximum:

```bash
magick ~/Projects/alora/dev/assets/brand/dreevelle_pfp.png \
  -colorspace RGB -filter Lanczos -resize 400x400 -colorspace sRGB -strip \
  assets/icon.png
oxipng -o max --strip safe assets/icon.png
```

Downscale in linear light as above; the naive sRGB-space resize dulls the glow.

Check what's live:

```bash
curl -s https://api.comfy.org/nodes/comfyui-fast-image-sequence/versions
```
