# HeatRx: three-minute pitch video script

All three tracks: **Today**, **This budget cycle**, **This decade**. Five voices: team lead
(on camera), data lane, agent lane, story lane, front-end lane. Spoken words: 405, about
2:42 at 150 words a minute, which leaves time for typing, clicks and pauses on screen.

The designed, shareable version of this script is `docs/pitch-video-script.html` (published as
an artifact); both are generated from the same segment list, so edit them together.

---

### 0:00 – 0:12 · ON CAMERA · Team lead

*Team lead on camera.*

> Heat kills more Americans than any other weather, and it doesn't hit evenly. On one July afternoon in Brooklyn, NOAA's volunteer sensors read temperatures ten degrees apart: same hour, same borough.

### 0:12 – 0:34 · ON CAMERA · Team lead

*Team lead on camera.*

> So we built HeatRx, one planning assistant for all three clocks a city runs on. Today: where trucks go before a heat wave. This budget cycle: where permanent cooling centers belong. This decade: where trees buy relief. Our users are city staff, so it's an internal portal on Cloud Run, built around a map.

### 0:34 – 0:58 · SCREEN + VO · Data lane

*Portal map, default heat layer. Start zoomed out on Brooklyn. Pick Thursday in the forecast strip and let the colours update.*

> Every square is a block about a hundred metres across. Fifteen thousand Heat Watch readings became a thousand measured blocks, and our BigQuery ML model predicts the rest: how much hotter each block runs than the city average. Add Thursday's National Weather Service high, and every block gets its own estimate. Grey means nothing was measured nearby, so we don't guess.

### 0:58 – 1:24 · SCREEN + VO · Agent lane · ⚡ Today

*Assistant tab. Type prompt 1 and cut the wait in editing. Click marker 1 and ask why. Then type prompt 2.*

Prompt 1: `Two wellness teams, six hours. Which blocks in Crown Heights North are hottest, with the most residents over 65 and households without a car?`

Prompt 2: `What about Staten Island?`

> Today. A wellness team needs the hottest blocks with the most seniors and car-free households. The agent, built with Google's ADK on Gemini, answers through Google's managed BigQuery MCP server and tools we wrote, and puts the shortlist on the map. Ask why a block is hot, and it runs ML.EXPLAIN_PREDICT live. Ask about Staten Island, and it says it has no data.

### 1:24 – 1:39 · SCREEN + VO · Story lane · 🏛️ Budget cycle

*Heat history view. Show the city chart, then scroll to the neighbourhood table and highlight Rugby–Remsen Village.*

> This budget cycle. A cooling center is permanent, so it belongs where heat keeps coming back. Brooklyn averages fifteen days a year above ninety degrees. Rugby–Remsen Village gets about twenty. That turns a heat map into a budget argument.

### 1:39 – 1:59 · SCREEN + VO · Front-end lane · 🌳 Decade

*Decade view. Show the 1,850 headline, then the planting-priority list on the map.*

> This decade. The hottest quarter of blocks holds eighteen hundred and fifty dead trees and stumps: planting sites ready now. But the model credits ten more street trees with only a quarter of a degree, and that's an association. So we say where to plant, then measure before and after.

### 1:59 – 2:16 · SCREEN + VO · Story lane

*Equity audit view. Highlight the Today list column, then the Aged 65+ row.*

> We kept race and demographics out of the model, then audited its output. Today's top twenty-five blocks: seventy-four percent car-free households against fifty-five across Brooklyn, and poverty thirty-one percent against twenty. But slightly fewer seniors, so our ranking needs more weight on age.

### 2:16 – 2:30 · SCREEN + VO · Data lane

*Model card in the map legend.*

> We tested on whole one-kilometre areas, held out five ways. Typical error: point eight two degrees, against one point oh four for every block is average. Trees and EPA data alone barely help.

### 2:30 – 2:42 · ON CAMERA · Team lead

*Team lead on camera.*

> Limits: one afternoon of data, association not cause, and much of Brooklyn unmeasured. Next: truck routing, verified cooling-center hours, and a second drive. HeatRx: the right help, on the right block.

---

## Production notes

- Record each segment separately and cut them together.
- Use the deployed portal, not localhost, so the address bar shows Cloud Run.
- Agent segment: rehearse both prompts and record several takes. Gemini's wording varies, so
  the voice-over describes what the agent does, never its exact reply. Keep a screenshot of a
  good run as a fallback.
- Caption the numbers as they're spoken. They come from the portal's Equity, History and Decade
  views and `sql/model/README.md` (17 November 2026); re-read them if the grid or model is rebuilt.
