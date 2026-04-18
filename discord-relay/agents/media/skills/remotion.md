<!-- Source: https://github.com/vibetokens/skills/tree/main/skills/remotion-video -->

---
name: remotion-video
description: Create programmatic videos and animations using Remotion. Use when asked to build a video, animation, motion graphic, or visual composition with React. Scaffolds projects, writes compositions, and renders to MP4 or WebM.
effort: max
---

# Remotion Video Skill

You create videos programmatically using Remotion — a React-based video framework. Videos are React components. Animations are pure math on `frame`. Nothing is drag-and-drop.

## Core Mental Model

```
frame = current frame number (starts at 0)
fps = frames per second (usually 30)
durationInFrames = total length
progress = frame / durationInFrames  // 0 → 1
```

Every visual state is a function of `frame`. There is no timeline scrubber — you write the math.

---

## Rules

See the individual rule files for detailed patterns:

- [Core Animation Patterns](rules/animations.md)
- [Text & Typography](rules/text.md)
- [Layouts & Composition](rules/layout.md)
- [Color & Brand](rules/brand.md)
- [Rendering & Export](rules/render.md)

---

## Scaffold a new Remotion project

```bash
npx create-video@latest
# Choose: TypeScript, blank template
cd my-video
npm install
npm run dev
```

---

## Minimal composition

```tsx
import { AbsoluteFill, useCurrentFrame, interpolate } from "remotion";

export const MyComp = () => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 30], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ backgroundColor: "#080A0F", opacity }}>
      <div style={{ color: "#00FFB2", fontSize: 64, fontWeight: 900 }}>
        Hello.
      </div>
    </AbsoluteFill>
  );
};
```

Register in `Root.tsx`:

```tsx
import { Composition } from "remotion";
import { MyComp } from "./MyComp";

export const RemotionRoot = () => (
  <Composition
    id="MyComp"
    component={MyComp}
    durationInFrames={90}
    fps={30}
    width={1920}
    height={1080}
  />
);
```

---

## The two animation primitives

### interpolate — linear value mapping

```tsx
const x = interpolate(frame, [0, 30, 60], [0, 100, 0], {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
});
```

### spring — physics-based easing

```tsx
import { spring, useVideoConfig } from "remotion";

const { fps } = useVideoConfig();
const scale = spring({ fps, frame, config: { damping: 12, stiffness: 100 } });
```

Use `spring` for entrances. Use `interpolate` for controlled timing. Never use CSS transitions or `setTimeout`.

---

## Render to file

```bash
npx remotion render MyComp out/video.mp4
npx remotion render MyComp out/video.webm
npx remotion still MyComp --frame=0 out/frame.png  # single frame
```

---

## Common mistakes to avoid

- Never use `Math.random()` — use `random(seed)` from remotion for deterministic values
- Never use `Date.now()` — derive everything from `frame`
- Never animate with CSS transitions — use `interpolate` or `spring`
- Never use `useEffect` for animation state — derive from frame directly
- Always clamp `interpolate` with `extrapolateLeft: "clamp"` and `extrapolateRight: "clamp"` unless you explicitly want extrapolation
