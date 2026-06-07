# ComfyUI-FastImageSequence

A **fast PNG image-sequence saver** for [ComfyUI](https://github.com/comfyanonymous/ComfyUI).

The built-in **Save Image (Advanced)** node encodes each frame of a batch one at
a time. For long sequences (video / frame dumps) that's slow — almost all the
time goes into single-threaded zlib PNG compression, *not* metadata.

This node encodes the frames of a batch **in parallel**. PyAV releases the GIL
during compression, so encoding scales with your CPU cores. The output bytes are
**identical** to the built-in node — same pixels, same compression, same file
size — it's just much faster.

It also embeds the prompt/workflow metadata in **only the first frame** by
default, so the workflow is still recoverable from the sequence without bloating
every single frame.

## Node

**Save Image Sequence (Fast 16-bit)** (category: `image`)

| Input | Default | Notes |
|---|---|---|
| `images` | — | The image batch to save. |
| `filename_prefix` | `ComfyUI` | Supports the usual tokens (`%date:yyyy-MM-dd%`, etc.). |
| `bit_depth` | `16-bit` | `8-bit` or `16-bit` PNG. |
| `metadata` *(advanced)* | `first_frame` | `first_frame` / `all` / `none`. |
| `threads` *(advanced)* | `0` (auto) | Encoder threads. `0` = `min(8, CPU count)`. |

Drop it in wherever you'd use Save Image (Advanced). It handles the whole batch
itself, including the first-frame metadata, so wire the **entire** sequence into
this one node (don't split it across two save nodes — that would collide on the
sequential file numbering).

## Performance

Measured on a 16-core CPU, 457 frames at 1920×1088, 16-bit RGB (worst-case noisy
content; real frames are faster):

| threads | ms/frame | 457 frames |
|---|---:|---:|
| 1 (built-in, serial) | 372 | ~170 s |
| 4 | 101 | ~46 s |
| 8 (auto default) | 60 | ~27 s |
| 16 | 43 | ~20 s |
| 24 | 44 | ~20 s (no gain) |

Scaling flattens at your core count — there's no benefit to setting `threads`
higher than the number of CPU cores. `0` (auto = 8) already captures most of the
win; set it to your core count for the last few seconds.

For reference, metadata injection costs ~0.9 ms/frame (~0.2% of the total), so
the `metadata` option is about file size / cleanliness, not speed.

## Installation

Clone into your ComfyUI `custom_nodes` directory and restart:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/dreevelle/ComfyUI-FastImageSequence.git
```

`av`, `numpy` and `torch` ship with ComfyUI, so there are no extra dependencies
to install.

## Notes

- Output is byte-identical to Save Image (Advanced) for the same input — this
  node only changes *how fast* the frames are written, not the result.
- True 16-bit PNGs (IHDR bit depth 16). Note that Pillow downsamples 16-bit
  *color* PNGs to 8-bit when reading them, so a `PIL.Image.open(...).mode` check
  will report `RGB` even though the file on disk is genuinely 16-bit.
- Self-contained: it copies the small PNG encode/metadata helpers rather than
  importing from ComfyUI internals, so a core update won't break it.

## License

[MIT](LICENSE)
