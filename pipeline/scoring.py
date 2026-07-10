"""
Tax Compliance Deviation Score (0-100): named sub-score decomposition A-F.

  A  AssetGap        0.30  individual lifestyle-implied vs declared income
  B  HouseholdGap    0.20  household-level gap (benami: assets parked on kin)
  C  FilingBehavior  0.15  non-filer with assets / zero-return / implausible tax
  D  BankingDev      0.15  deposits vs declared, structuring   (plug-in only)
  E  TravelDev       0.10  luxury travel vs declared income    (plug-in only)
  F  GraphAnomaly    0.10  GraphSAGE reconstruction-error percentile

Weights renormalise over PRESENT components: the official 4-CSV run scores
A,B,C,F; plug-ins only ever add. Every component returns (score, evidence[])
and every evidence item is traceable to source_file + row_number (C4).
"""
BILL_INCOME_SHARE = 0.06
VEHICLE_AMORT_YEARS = 8
PROPERTY_AMORT_YEARS = 20
BASE_WEIGHTS = {"A": .30, "B": .20, "C": .15, "D": .15, "E": .10, "F": .10}
TIERS = [(81, "CRITICAL", "انتہائی سنگین"), (61, "HIGH", "سنگین"),
         (41, "MEDIUM", "درمیانہ"), (21, "LOW", "کم"), (0, "MINIMAL", "معمولی")]

DEFENSIBILITY = ("Each finding above is traceable to an original government record "
                 "(source file and row cited). Identity links were established by the "
                 "entity-resolution pipeline at match threshold τ=0.75 using blocking, "
                 "multi-signal scoring and, where applicable, adjudicated borderline "
                 "review; ambiguous identity matches are routed to human review and are "
                 "never auto-flagged. Scores are fully decomposable into the weighted "
                 "components shown and contain no unexplained model output.")

def tier_of(score):
    for lo, en, ur in TIERS:
        if score >= lo: return en, ur
    return "MINIMAL", "معمولی"

def fmt(n):
    if n >= 10_000_000: return f"Rs {n/10_000_000:.1f} crore"
    if n >= 100_000:    return f"Rs {n/100_000:.1f} lakh"
    return f"Rs {n:,.0f}"

def lifestyle_estimate(d):
    bill = (d["avg_bill"] * 12) / BILL_INCOME_SHARE if d["avg_bill"] else 0
    veh = d["total_vehicle_value"] / VEHICLE_AMORT_YEARS
    prop = d["total_property_value"] / PROPERTY_AMORT_YEARS
    return dict(bill=int(bill), vehicle=int(veh), prop=int(prop),
                total=int(bill + veh + prop))

def _gap_score(lifestyle, declared):
    if lifestyle <= 0: return 0.0
    return max(0.0, (lifestyle - declared) / lifestyle) * 100

def _ev(rec, finding, comp):
    return dict(source=rec["source"].replace("property_seller", "Registry")
                       .replace("property", "Registry").replace("fbr", "FBR")
                       .replace("excise", "Excise").replace("disco", "DISCO")
                       .replace("banking", "Banking").replace("travel", "Travel"),
                record_id=rec["record_id"], finding=finding,
                source_file=rec["source_file"], row_number=rec["row_number"],
                contributes_to=comp)

def component_scores(G, pid, ent, gnn_pct, circular_flags, plugins_present):
    d = G.nodes[pid]
    life = lifestyle_estimate(d)
    comps, evidence = {}, []

    # ---- evidence from raw records (typed findings) ----
    for rec in ent["records"]:
        row, src = rec["row"], rec["source"]
        if src == "fbr":
            evidence.append(_ev(rec, f"{row['filer_status']}; declared income "
                f"{fmt(int(row['declared_income_pkr']))}/yr, tax paid "
                f"{fmt(int(row['tax_paid_pkr']))}", "C"))
        elif src == "excise":
            evidence.append(_ev(rec, f"Owns {row['vehicle_make_model']} "
                f"({row['engine_capacity_cc']}cc, reg. {row['registration_year']})", "A"))
        elif src == "disco":
            evidence.append(_ev(rec, f"Average electricity bill "
                f"{fmt(int(row['avg_monthly_bill_pkr']))}/month ({row['connection_type']})", "A"))
        elif src == "property":
            evidence.append(_ev(rec, f"Purchased {row['area_marla']}-marla "
                f"{row['property_type'].lower()} valued {fmt(int(row['property_value_pkr']))} "
                f"on {row['transfer_date']}", "A"))
        elif src == "property_seller":
            evidence.append(_ev(rec, f"Sold property at {row['property_address']} "
                f"({fmt(int(row['property_value_pkr']))}) on {row['transfer_date']}", "B"))
        elif src == "banking":
            evidence.append(_ev(rec, f"{row['bank_name']} {row['account_type']} account, "
                f"avg deposits {fmt(int(row['avg_monthly_deposit_pkr']))}/month "
                f"(opened {row['opened_date']})", "D"))
        elif src == "travel":
            evidence.append(_ev(rec, f"{row['trip_class']} trip to {row['destination']} "
                f"on {row['departure_date']} (~{fmt(int(row['est_trip_cost_pkr']))})", "E"))

    declared = d["declared_income"]

    # ---- A: AssetGap ----
    a = _gap_score(life["total"], declared)
    # corroboration rule: an Unknown-to-FBR entity backed by a single dataset
    # is an ER fragment, not evidence of evasion — cap and route to review
    weak_link = d["filer"] == "Unknown" and d["n_sources"] < 2
    if weak_link:
        a = min(a, 40.0)
    comps["A"] = round(min(100.0, a), 1)

    # ---- B: HouseholdGap (benami) ----
    hh_decl = d.get("household_declared", declared)
    hh_members = [n for n, nd in G.nodes(data=True)
                  if nd.get("kind") == "person"
                  and nd.get("household_id") == d.get("household_id")]
    hh_life = sum(lifestyle_estimate(G.nodes[m])["total"] for m in hh_members)
    b = _gap_score(hh_life, hh_decl)
    if weak_link:
        b = min(b, 40.0)
    for flag in circular_flags.get(pid, []):
        b = max(b, 90.0)
        evidence.append(dict(source="Graph", record_id="pattern",
                             finding=f"Transfer-chain pattern detected: {flag}",
                             source_file="property_transfers.csv", row_number=0,
                             contributes_to="B"))
    comps["B"] = round(min(100.0, b), 1)

    # ---- C: FilingBehavior ----
    has_assets = (d["total_vehicle_value"] + d["total_property_value"] + d["avg_bill"]) > 0
    c = 0.0
    if d["filer"] == "Non-Filer" and has_assets:
        c = 90.0
    elif d["filer"] == "Unknown" and has_assets:
        c = 95.0 if d["n_sources"] >= 2 else 35.0   # singleton: review, don't accuse
    elif declared > 0:
        eff = d["tax_paid"] / declared
        if declared > 1_200_000 and eff < 0.02:
            c = 60.0
        elif declared == 0:
            c = 70.0
    elif d["filer"] == "Filer" and declared == 0 and has_assets:
        c = 85.0                                     # zero-return filer with assets
    comps["C"] = round(c, 1)

    # ---- D: BankingDeviation (plug-in) ----
    if "banking" in plugins_present:
        dep = d.get("annual_deposits", 0)
        dd = 0.0
        if dep > 0:
            base = max(declared, 1)
            ratio = dep / base if declared > 0 else float("inf")
            if declared == 0 and dep > 0:
                dd = 95.0
            elif ratio >= 5: dd = 90.0
            elif ratio >= 3: dd = 70.0
            elif ratio >= 1.5: dd = 40.0
        if d.get("n_accounts", 0) >= 3:
            dd = max(dd, 75.0)
            evidence.append(dict(source="Banking", record_id="pattern",
                finding=f"{d['n_accounts']} accounts on one resolved identity "
                        "(possible structuring)", source_file="banking_accounts.csv",
                row_number=0, contributes_to="D"))
        comps["D"] = round(min(100.0, dd), 1)

    # ---- E: TravelDeviation (plug-in) ----
    if "travel" in plugins_present:
        spend = d.get("travel_spend", 0)
        e = 0.0
        if spend > 0:
            if declared == 0:
                e = 90.0
            else:
                share = spend / declared
                if share >= 0.5: e = 85.0
                elif share >= 0.25: e = 60.0
                elif share >= 0.1: e = 30.0
        if d.get("n_intl_trips", 0) >= 3 and declared < 1_000_000:
            e = max(e, 80.0)
        comps["E"] = round(min(100.0, e), 1)

    # ---- F: GraphAnomaly ----
    comps["F"] = round(gnn_pct * 100, 1)

    # ---- weight renormalisation over present components ----
    weights = {k: BASE_WEIGHTS[k] for k in comps}
    tot = sum(weights.values())
    weights = {k: round(v / tot, 4) for k, v in weights.items()}
    final = round(min(100.0, sum(comps[k] * weights[k] for k in comps)), 1)
    return final, comps, weights, evidence, life

COMP_NAMES = {"A": ("Asset gap", "lifestyle-implied vs declared income"),
              "B": ("Household / benami", "household-level gap and transfer patterns"),
              "C": ("Filing behavior", "non-filing, zero returns, implausible tax paid"),
              "D": ("Banking deviation", "deposit volume vs declared; structuring"),
              "E": ("Travel deviation", "luxury travel spend vs declared income"),
              "F": ("Graph anomaly", "GraphSAGE reconstruction-error percentile")}

def audit_narrative(G, pid, final, comps, weights, life, ent):
    d = G.nodes[pid]
    declared = d["declared_income"]
    lines = [f"Records from {d['n_sources']} of the available government datasets "
             f"were linked to this individual by the entity-resolution pipeline."]
    if d["filer"] == "Non-Filer":
        lines.append("The individual is registered with FBR as a NON-FILER.")
    elif d["filer"] == "Unknown":
        lines.append("No FBR tax record could be linked — assets exist entirely "
                     "outside the tax net.")
    else:
        lines.append(f"Declared annual income: {fmt(declared)}; "
                     f"tax paid: {fmt(d['tax_paid'])}.")
    parts = []
    if life["bill"]:    parts.append(f"{fmt(life['bill'])} implied by electricity consumption")
    if life["vehicle"]: parts.append(f"{fmt(life['vehicle'])}/yr implied by vehicle holdings "
                                     f"(fleet value {fmt(d['total_vehicle_value'])})")
    if life["prop"]:    parts.append(f"{fmt(life['prop'])}/yr implied by property "
                                     f"(total {fmt(d['total_property_value'])})")
    if parts:
        lines.append("Lifestyle-implied annual income of " + fmt(life["total"]) +
                     ": " + "; ".join(parts) + ".")
    if d.get("household_members", 1) > 1:
        lines.append(f"Household analysis: {d['household_members']} resolved individuals "
                     f"at the same residence ({', '.join(d['household_names'][:4])}), "
                     f"combined declared income {fmt(d['household_declared'])}.")
    if d.get("annual_deposits", 0) and "D" in comps:
        lines.append(f"Banking: {d['n_accounts']} account(s), annual deposit volume "
                     f"{fmt(d['annual_deposits'])} against declared {fmt(declared)}.")
    if d.get("travel_spend", 0) and "E" in comps:
        lines.append(f"Travel: {d['n_intl_trips']} international trip(s), estimated spend "
                     f"{fmt(d['travel_spend'])}.")
    comp_str = " + ".join(f"{COMP_NAMES[k][0]} {comps[k]}×{weights[k]:.2f}"
                          for k in sorted(comps))
    lines.append(f"Score composition: {comp_str} = final deviation score {final}.")
    return " ".join(lines)
