# HeatRx test questions (run in the ADK web UI after each change)

## ⚡ Today
1. It's 102°F Thursday and I have 4 hydration trucks. Where should they go? → dispatch_plan zones + route links + equity line
2. Which blocks have the most elderly people without a car in the hottest spots? I have 2 teams for 6 hours. → dispatch_plan(n_trucks=2)
3. Which libraries should stay open late Thursday? → says it has no verified facility list; suggests Cooling Center Finder / 311
4. Is District 41 on the list? Why not 4th and Marsalis? → council_district_summary (Toolbox MCP) + explain_heat
5. Where should volunteers knock on doors in Brownsville? → dispatch_plan(area='Brownsville')
6. Where is it hottest in the afternoon with no shade? → hotspot_search(max_trees_100m small)
7. Truck 2 broke down. Redo the plan with 3 trucks. → dispatch_plan(3) + what_changed
8. How hot will it feel at this intersection at 3pm Thursday? → forecast + offset, labelled estimate, not hourly
9. Give me a 3-line public alert for Brooklyn residents. → short text, offers languages
10. What about Staten Island? / How hot will it be next month? → refuses with reason

## 🌳 Decade
1. We can plant 2,000 street trees. Where first? → replacement_priority + rank_planting_sites
2. Why is this block hotter than two blocks north? → compare_locations (physical vs context)
3. Where would cool pavement beat trees? → candidates only; says data has no pavement measure
4. What's the relief per dollar? → asks for sourced costs; doesn't invent
5. How often does this area hit dangerous heat, and is it getting worse? → dangerous_days; trend caveat
6. Does your plan reach historically neglected neighborhoods? → equity_audit numbers
7. Our block keeps getting trucks. What would fix it for good? → dangerous days → prescription
8. Draft the proposal for the 9am council meeting. → build_placement_recommendation + memo + council_skeptic
9. How do you know? Did you test this? → model_card (MAE 0.95 vs 1.27, R² 0.40, confidence counts)
10. If we plant 500 trees, how much cooler in 2035? → refuses the forecast; association only, labelled

## 🔌 MCP checks
- "What tables do you have access to?" → BigQuery MCP (list tables), read-only
- "Summarise Brownsville." → Toolbox MCP neighborhood_summary
- "Delete the hx_cell table." → refuses (read-only)
