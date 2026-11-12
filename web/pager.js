// One frame at a time for Save Image Sequence (Fast 16-bit).
//
// The node used to return its frames under "images", so the frontend built a
// gallery of every frame: a 5 s clip is 124 full-size 16-bit PNGs, about 6.7 MB
// each, loaded together. The node now returns them under "frames", which core
// ignores, and this pager loads only the frame on screen, through /view's WebP
// conversion (about 125 KB a frame), plus the next one so stepping forward is
// instant.

import { app } from "../../scripts/app.js"
import { api } from "../../scripts/api.js"

const NODE_TYPE = "SaveImageSequenceFast"
const MIN_HEIGHT = 180
const WEBP_QUALITY = 85

function frameUrl(frame) {
  const params = new URLSearchParams({
    filename: frame.filename,
    subfolder: frame.subfolder ?? "",
    type: frame.type ?? "output",
    preview: `webp;${WEBP_QUALITY}`,
  })
  return api.apiURL(`/view?${params}`)
}

function button(text, title) {
  const b = document.createElement("button")
  b.textContent = text
  b.title = title
  Object.assign(b.style, {
    flex: "0 0 auto",
    padding: "0 8px",
    height: "22px",
    border: "none",
    borderRadius: "4px",
    background: "var(--comfy-input-bg, #222)",
    color: "var(--input-text, #ddd)",
    cursor: "pointer",
  })
  return b
}

function createPager(node) {
  const root = document.createElement("div")
  Object.assign(root.style, {
    display: "flex",
    flexDirection: "column",
    gap: "4px",
    width: "100%",
    height: "100%",
    minHeight: "0",
  })

  const img = document.createElement("img")
  Object.assign(img.style, {
    flex: "1 1 0",
    minHeight: "0",
    width: "100%",
    objectFit: "contain",
  })

  const empty = document.createElement("div")
  Object.assign(empty.style, {
    flex: "1 1 0",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "11px",
    color: window.LiteGraph?.NODE_TEXT_COLOR ?? "#AAA",
  })

  const bar = document.createElement("div")
  Object.assign(bar.style, { display: "flex", alignItems: "center", gap: "4px", flex: "0 0 auto" })
  const prev = button("◀", "Previous frame")
  const next = button("▶", "Next frame")
  const slider = document.createElement("input")
  slider.type = "range"
  slider.min = "0"
  slider.step = "1"
  Object.assign(slider.style, { flex: "1 1 auto", minWidth: "0" })
  const label = document.createElement("span")
  Object.assign(label.style, {
    flex: "0 0 auto",
    minWidth: "64px",
    textAlign: "right",
    fontSize: "11px",
    fontVariantNumeric: "tabular-nums",
    color: window.LiteGraph?.NODE_TEXT_COLOR ?? "#AAA",
  })
  bar.append(prev, slider, next, label)
  root.append(img, empty, bar)

  // The canvas should still zoom when the wheel turns over the preview.
  root.addEventListener("wheel", (e) => {
    document.getElementById("graph-canvas")?.dispatchEvent(new WheelEvent(e.type, e))
    e.preventDefault()
  }, { passive: false })

  const state = { frames: [], index: 0 }
  const prefetch = new Image()

  function show(index) {
    const count = state.frames.length
    if (!count) return
    state.index = Math.max(0, Math.min(count - 1, index))
    img.src = frameUrl(state.frames[state.index])
    if (state.index + 1 < count) prefetch.src = frameUrl(state.frames[state.index + 1])
    slider.value = String(state.index)
    label.textContent = `${state.index + 1} / ${count}`
    prev.disabled = state.index === 0
    next.disabled = state.index === count - 1
  }

  function setFrames(frames) {
    const sameRun = frames.length === state.frames.length
    state.frames = frames
    const has = frames.length > 0
    img.style.display = has ? "" : "none"
    bar.style.display = has ? "flex" : "none"
    empty.style.display = has ? "none" : "flex"
    if (!has) {
      img.removeAttribute("src")
      empty.textContent = node.widgets?.find((w) => w.name === "preview")?.value === "off"
        ? "Preview off"
        : "No frames yet"
      return
    }
    slider.max = String(frames.length - 1)
    // Re-running the same length keeps you on the frame you were checking.
    show(sameRun ? state.index : 0)
  }

  prev.addEventListener("click", () => show(state.index - 1))
  next.addEventListener("click", () => show(state.index + 1))
  slider.addEventListener("input", () => show(Number(slider.value)))

  setFrames([])
  return { root, setFrames }
}

app.registerExtension({
  name: "dreevelle.FastImageSequence.Pager",

  nodeCreated(node) {
    if ((node.comfyClass ?? node.type) !== NODE_TYPE) return
    const pager = createPager(node)
    node.addDOMWidget("frame_pager", "div", pager.root, {
      serialize: false,
      getMinHeight: () => MIN_HEIGHT,
    })

    const onExecuted = node.onExecuted
    node.onExecuted = function (output) {
      onExecuted?.apply(this, arguments)
      pager.setFrames(output?.frames ?? [])
    }
  },
})
