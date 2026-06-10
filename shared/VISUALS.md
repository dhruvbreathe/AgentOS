# VISUALS.md — Visual-First Discord Output (operator hard rule, 2026-06-09)

**Dhruv's directive: data-heavy replies must ship as visuals, not text walls.** This is a hard rule for every agent, same tier as the em-dash ban.

## The two rules

1. **3+ numbers in one reply → render a chart or KPI infographic and attach it.** Metrics, trends, comparisons, funnels: matplotlib via the relay venv (`./.venv/bin/python`, matplotlib installed). Dark Vayu palette below.
2. **Structured reply (sections, digest, report) → wrap it in a Discord Components V2 container.** Accent bar + text displays + separators + media gallery reads 10x better than markdown soup.

Plain text stays fine for: one-liners, acks, conversational back-and-forth, anything without numbers or sections.

## Components V2 webhook recipe

POST to your webhook with `?with_components=true`, payload `flags: 32768`, NO `content` field (everything goes in components):

```json
{
  "username": "<me>",
  "flags": 32768,
  "components": [
    {"type": 17, "accent_color": 5231045, "components": [
      {"type": 10, "content": "## 📊 Title line"},
      {"type": 14, "divider": true, "spacing": 1},
      {"type": 10, "content": "**Bold** body markdown works here"},
      {"type": 12, "items": [{"media": {"url": "attachment://chart.png"}}]},
      {"type": 14, "divider": true, "spacing": 1},
      {"type": 10, "content": "-# footer subtext + signature emoji"}
    ]}
  ]
}
```

Component types: 17 container, 10 text display, 14 separator, 12 media gallery. Attach the chart in the same multipart request (`-F "file1=@chart.png"`) and reference it as `attachment://<filename>`.

Working example: `shared/scripts/cv2_post_example.sh`. Chart template: `shared/scripts/render_infographic_template.py`.

## Chart palette (keep it consistent across agents)

- Background `#0E1420`, panel `#161E2E`
- Teal `#4FD1C5` (primary metric), amber `#F6AD55` (secondary), blue `#63B3ED` (tertiary), grey `#8C9BB3` (labels), white `#E6EDF7` (titles)
- KPI tiles top, trend chart bottom, title block top-left with date + source line
- 160 dpi, `figsize` ~(11, 6), always `matplotlib.use("Agg")`

## Discipline

- Render fresh from live data; never reuse a stale chart image for a new period.
- Em-dash scan still applies to every `content` string BEFORE the curl fires.
- Chart + container is not a substitute for the one-line takeaway. Lead the container with the verdict, the chart is evidence.
- Keep payload under 10 MB and under 40 components (Discord caps).
- Cron-posted digests use this format too. A daily digest without a chart is now a defect.
