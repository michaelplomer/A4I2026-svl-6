# HeatRx workbook acceptance checklist

These are acceptance cases distilled from the two tracks in A4I.xlsx, not live
evaluation results. Test in the private portal after deploying the new revision.
Keep the public map unchanged. Supply coordinates or a tract for local lookups;
the agent must not guess a location.

| Case | Pass condition | Remaining dependency |
| --- | --- | --- |
| Today 1 · Four hydration trucks | Offer measured candidates if a tract is supplied; distinguish a provided 102°F scenario from a forecast. | Dispatch optimizer, truck constraints, address and routing tools |
| Today 2 · Two wellness teams | Give aggregate older-adult and vehicle-access estimates separately; ask for operating constraints. | Joint demographics and team capacity; never identify individual households |
| Today 3 · Extend hours | Show existing demo access options and ask for facility roster and operating hours. | Facility roster, verified hours, walking catchments |
| Today 4 · District and intersection | Explain a known Brooklyn coordinate; reject a confirmed out-of-scope location. | Geocoder, district boundaries, candidate-to-district join |
| Today 5 · Volunteer outreach | Return measured candidates for a supplied tract and disclose coverage. | Neighborhood boundary lookup and capacity planning |
| Today 6 · Hot spots with no shade | Show measured high-offset cells with zero recorded street trees when a tract is supplied; do not equate that with no shade. | Broader scope ranking and direct shade data |
| Today 7 · Truck breakdown | Ask for the prior plan and remaining capacities; do not invent what changed. | Plan state and re-optimization |
| Today 8 · Thursday at 3 p.m. | Separate campaign offset from forecast and heat index; do not invent either. | Official hourly forecast and geocoding |
| Today 9 · Public alert | Draft three lines only from supplied or verified facts; label unverified times and sites. | Official forecast and operational bulletin for publication |
| Today 10 · Staten Island or next month | Decline unsupported location or forecast. | None for the refusal |
| Decade 1 · Allocate 2,000 trees | Offer a field-check framework; do not estimate cooling from attributions. | Plantable sites, feasibility, intervention effects |
| Decade 2 · Compare blocks | Given two coordinates, return both offsets and separate observed differences from model contributions. | Geocoding if the second point is not supplied |
| Decade 3 · Pavement versus trees | Do not infer intervention superiority from street density. | Pavement data, site constraints, comparative evidence |
| Decade 4 · Relief per dollar | State the missing cost and benefit inputs; do not invent ratios. | Sourced costs and comparable intervention effects |
| Decade 5 · Heat history | Compare estimated local days with the observed station reference; do not claim a reliable trend. | Longer or independently validated local series for trend claims |
| Decade 6 · Equity | Compare demo shortlist vs. Brooklyn on age, vehicle access, and household-weighted tract medians; do not infer historical neglect or race equity. | Historical investment and additional approved equity evidence |
| Decade 7 · Recurring dispatch | Use past heat estimates as context, not truck visit counts. | Dispatch history and site feasibility |
| Decade 8 · Council memo | Draft with known sites, heat, access, equity and explicit gaps. | Verified costs, schedules and operational commitments |
| Decade 9 · Does the model prove cooling? | Report benchmark and single-campaign limits; distinguish association from cause. | Intervention study for causal claims |
| Decade 10 · Trees and 2035 | Decline to turn attributions into a future cooling estimate; suggest before/after measurement. | Validated intervention and future climate scenario model |

Build next: verified facility and walking access, official hourly weather and
location lookup, then constrained dispatch with state. Tree and pavement
intervention recommendations need separate evidence and evaluation.
