"""Filter EPA SLD v3 to the five NYC counties, rebuild GEOID10, null out -99999 sentinels."""
import csv, sys
src, dst = sys.argv[1], sys.argv[2]
NYC = {"005", "047", "061", "081", "085"}
KEEP = ["Ac_Land", "Ac_Water", "TotPop", "CountHU", "TotEmp",
        "D1A", "D1B", "D1C", "D1D", "D2A_EPHHM", "D2B_E8MIXA", "D2A_JPHH",
        "D3A", "D3AAO", "D3AMM", "D3APO", "D3B", "D3BAO", "D3BMM4", "D3BPO4",
        "D4A", "D4C", "D4D", "D4E",
        "D2A_Ranked", "D2B_Ranked", "D3B_Ranked", "D4A_Ranked", "NatWalkInd"]
n = sent = 0
with open(src, newline="") as f, open(dst, "w", newline="") as g:
    r = csv.DictReader(f)
    w = csv.writer(g)
    w.writerow(["bg_geo_id", "county_fips"] + [k.lower() for k in KEEP])
    for row in r:
        st, co = int(float(row["STATEFP"])), int(float(row["COUNTYFP"]))
        if st != 36 or f"{co:03d}" not in NYC:
            continue
        geoid = f"{st:02d}{co:03d}{int(float(row['TRACTCE'])):06d}{int(float(row['BLKGRPCE']))}"
        vals = []
        for k in KEEP:
            v = row[k].strip()
            if v in ("", "-99999", "-99999.0"):
                sent += v != ""
                v = ""
            vals.append(v)
        w.writerow([geoid, f"{co:03d}"] + vals)
        n += 1
print(f"rows={n} sentinels_nulled={sent}")
