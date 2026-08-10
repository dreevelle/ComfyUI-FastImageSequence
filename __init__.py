"""ComfyUI-FastImageSequence.

A fast PNG image-sequence saver for ComfyUI.

It mirrors the PNG path of the built-in ``Save Image (Advanced)`` node but
encodes the frames of a batch concurrently with a thread pool. PyAV releases the
GIL during zlib compression, so this scales ~linearly with CPU cores and gives a
large speedup when dumping long frame sequences (e.g. video) — with output bytes
identical to the built-in node. Prompt/workflow metadata is embedded only in the
first frame by default, so a sequence stays recoverable into ComfyUI without
bloating every frame.

Self-contained on purpose (helpers copied, nothing imported from
``comfy_extras``) so a ComfyUI core update can't silently break it.
"""

import json
import os
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import av
import numpy as np
import torch
from typing_extensions import override

import folder_paths
from comfy.cli_args import args
from comfy_api.latest import ComfyExtension, IO

__version__ = "1.0.1"


# ---------------------------------------------------------------------------
# Format specifications (PNG subset, copied from comfy_extras/nodes_images.py)
# Maps (bit_depth, has_alpha) -> numpy dtype, scale, and PyAV pixel formats.
# ---------------------------------------------------------------------------
_FORMAT_SPECS = {
    ("8-bit", False):  {"scale": 255.0,   "dtype": np.uint8,  "frame_fmt": "rgb24",   "stream_fmt": "rgb24"},
    ("8-bit", True):   {"scale": 255.0,   "dtype": np.uint8,  "frame_fmt": "rgba",    "stream_fmt": "rgba"},
    ("16-bit", False): {"scale": 65535.0, "dtype": np.uint16, "frame_fmt": "rgb48le", "stream_fmt": "rgb48be"},
    ("16-bit", True):  {"scale": 65535.0, "dtype": np.uint16, "frame_fmt": "rgba64le", "stream_fmt": "rgba64be"},
}


# ---------------------------------------------------------------------------
# Metadata injection (copied from comfy_extras/nodes_images.py)
# ---------------------------------------------------------------------------
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """Build a single PNG chunk: length | type | data | CRC32(type+data)."""
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _png_text_chunk(keyword: str, text: str) -> bytes:
    """tEXt chunk: latin-1 keyword + NUL + latin-1 text."""
    payload = keyword.encode("latin-1") + b"\x00" + text.encode("latin-1", errors="replace")
    return _png_chunk(b"tEXt", payload)


def inject_png_metadata(png_bytes: bytes, prompt: dict | None, extra_pnginfo: dict | None) -> bytes:
    """Insert ComfyUI prompt/workflow as tEXt chunks right after IHDR."""
    if not png_bytes.startswith(_PNG_SIGNATURE):
        return png_bytes

    chunks: list[bytes] = []
    if prompt is not None:
        chunks.append(_png_text_chunk("prompt", json.dumps(prompt)))
    if extra_pnginfo:
        for key, value in extra_pnginfo.items():
            chunks.append(_png_text_chunk(key, json.dumps(value)))
    if not chunks:
        return png_bytes

    # IHDR is always the first chunk; insert ours immediately after it.
    ihdr_length = struct.unpack(">I", png_bytes[8:12])[0]
    ihdr_end = 8 + 8 + ihdr_length + 4  # signature + (len+type) + data + crc
    return png_bytes[:ihdr_end] + b"".join(chunks) + png_bytes[ihdr_end:]


# ---------------------------------------------------------------------------
# Encoding (PNG branch of comfy_extras/nodes_images.py:_encode_image)
# ---------------------------------------------------------------------------
def _encode_png(img_tensor: torch.Tensor, bit_depth: str) -> bytes:
    """Encode a single HxWxC tensor to PNG bytes in memory.

    PNG is delivered sRGB-encoded; pixels are quantized to the integer range for
    the chosen bit depth (8 or 16 bit). Output is byte-identical to the built-in
    Save Image (Advanced) node for the same input.
    """
    height, width, num_channels = img_tensor.shape
    has_alpha = num_channels == 4

    spec = _FORMAT_SPECS[(bit_depth, has_alpha)]

    scaled = (img_tensor * spec["scale"]).clamp(0, spec["scale"])
    img_np = scaled.to(torch.int32).cpu().numpy().astype(spec["dtype"])

    # Encode directly via CodecContext. PyAV's `image2` muxer does NOT write to
    # BytesIO (it expects a real file path), so we bypass the container entirely.
    # For single-frame PNG the raw codec output IS the file.
    codec = av.CodecContext.create("png", "w")
    codec.width = width
    codec.height = height
    codec.pix_fmt = spec["stream_fmt"]
    codec.time_base = Fraction(1, 1)

    frame = av.VideoFrame.from_ndarray(img_np, format=spec["frame_fmt"])
    if spec["frame_fmt"] != spec["stream_fmt"]:
        frame = frame.reformat(format=spec["stream_fmt"])
    frame.pts = 0
    frame.time_base = codec.time_base

    packets = list(codec.encode(frame)) + list(codec.encode(None))  # flush with None
    return b"".join(bytes(p) for p in packets)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------
class SaveImageSequenceFast(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="SaveImageSequenceFast",
            search_aliases=["save", "save image", "save sequence", "16 bit png", "frames", "export"],
            display_name="Save Image Sequence (Fast 16-bit)",
            description=(
                "Saves an image batch as a PNG sequence using parallel encoding "
                "(much faster than Save Image (Advanced) for long sequences). "
                "Metadata is embedded only in the first frame by default."
            ),
            category="image",
            inputs=[
                IO.Image.Input("images", tooltip="The images to save."),
                IO.String.Input(
                    "filename_prefix",
                    default="ComfyUI",
                    tooltip=(
                        "The prefix for the file to save. May include formatting tokens "
                        "such as %date:yyyy-MM-dd% or %Empty Latent Image.width%."
                    ),
                ),
                IO.Combo.Input(
                    "bit_depth",
                    options=["8-bit", "16-bit"],
                    default="16-bit",
                    tooltip="PNG bit depth per channel.",
                ),
                IO.Combo.Input(
                    "metadata",
                    options=["first_frame", "all", "none"],
                    default="first_frame",
                    advanced=True,
                    tooltip=(
                        "Where to embed the prompt/workflow:\n"
                        "  'first_frame' — only the first image (recover the workflow "
                        "from the sequence without bloating every frame).\n"
                        "  'all' — every image (built-in behavior).\n"
                        "  'none' — no metadata."
                    ),
                ),
                IO.Int.Input(
                    "threads",
                    default=0,
                    min=0,
                    max=64,
                    advanced=True,
                    tooltip="Encoder threads. 0 = auto (min(8, CPU count)).",
                ),
            ],
            hidden=[IO.Hidden.prompt, IO.Hidden.extra_pnginfo],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, images, filename_prefix: str, bit_depth: str,
                metadata: str = "first_frame", threads: int = 0) -> IO.NodeOutput:
        output_dir = folder_paths.get_output_directory()
        full_output_folder, filename, counter, subfolder, filename_prefix = (
            folder_paths.get_save_image_path(
                filename_prefix, output_dir, images[0].shape[1], images[0].shape[0]
            )
        )

        prompt = cls.hidden.prompt
        extra_pnginfo = cls.hidden.extra_pnginfo
        write_metadata = metadata != "none" and not args.disable_metadata

        def metadata_for(batch_number: int) -> bool:
            if not write_metadata:
                return False
            if metadata == "all":
                return True
            return batch_number == 0  # "first_frame"

        def encode_and_write(batch_number: int) -> dict:
            image = images[batch_number]
            encoded = _encode_png(image, bit_depth)
            if metadata_for(batch_number):
                encoded = inject_png_metadata(encoded, prompt, extra_pnginfo)

            name = filename.replace("%batch_num%", str(batch_number))
            file = f"{name}_{counter + batch_number:05}.png"
            with open(os.path.join(full_output_folder, file), "wb") as f:
                f.write(encoded)
            return {"filename": file, "subfolder": subfolder, "type": "output"}

        num_images = len(images)
        if num_images == 1:
            results = [encode_and_write(0)]
        else:
            num_workers = threads if threads > 0 else min(8, os.cpu_count() or 1)
            with ThreadPoolExecutor(max_workers=num_workers) as pool:
                # map preserves input order, so results stay frame-ordered.
                results = list(pool.map(encode_and_write, range(num_images)))

        return IO.NodeOutput(ui={"images": results})


class FastImageSequenceExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[IO.ComfyNode]]:
        return [SaveImageSequenceFast]


async def comfy_entrypoint() -> FastImageSequenceExtension:
    return FastImageSequenceExtension()
