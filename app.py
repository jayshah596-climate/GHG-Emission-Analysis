import io
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="GHG Emissions Calculator", page_icon="🌍", layout="wide")

st.title("🌍 Enterprise GHG Emissions Platform")
st.caption(
    "Track Scope 1/2/3 emissions with transparent calculations, filters, trends, targets, and reporting insights."
)

# -----------------------------
# Constants and helper mappings
# -----------------------------
CAR_EQUIVALENT_TCO2E_PER_YEAR = 4.6
DEFAULT_UNCERTAINTY = {
    "Scope 1": 0.08,
    "Scope 2": 0.10,
    "Scope 3": 0.18,
}

REGION_MULTIPLIER = {
    "Global Average": 1.00,
    "India": 1.10,
    "EU": 0.70,
    "US": 0.85,
    "APAC (avg)": 1.05,
}

UNIT_CONVERSIONS = {
    # liters is base for liquid fuels
    "liters": {"liters": 1.0, "gallons": 3.78541},
    "gallons": {"liters": 1 / 3.78541, "gallons": 1.0},
    # distance (base: km)
    "km": {"km": 1.0, "miles": 1.60934},
    "miles": {"km": 1 / 1.60934, "miles": 1.0},
    # energy (base: kWh)
    "kWh": {"kWh": 1.0, "MWh": 0.001},
    "MWh": {"kWh": 1000.0, "MWh": 1.0},
    # mass (base: kg)
    "kg": {"kg": 1.0, "tonnes": 0.001},
    "tonnes": {"kg": 1000.0, "tonnes": 1.0},
}

SCOPE3_ALL_CATEGORIES = [
    "Purchased goods and services",
    "Capital goods",
    "Fuel- and energy-related activities",
    "Upstream transportation and distribution",
    "Waste generated in operations",
    "Business travel",
    "Employee commuting",
    "Upstream leased assets",
    "Downstream transportation and distribution",
    "Processing of sold products",
    "Use of sold products",
    "End-of-life treatment of sold products",
    "Downstream leased assets",
    "Franchises",
    "Investments",
]


def convert_to_factor_unit(quantity: float, from_unit: str, factor_unit: str) -> tuple[float, str]:
    from_u = (from_unit or "").strip()
    factor_u = (factor_unit or "").strip()

    if from_u == factor_u or quantity == 0:
        return quantity, "No conversion"

    if from_u in UNIT_CONVERSIONS and factor_u in UNIT_CONVERSIONS[from_u]:
        converted = quantity * UNIT_CONVERSIONS[from_u][factor_u]
        return converted, f"{quantity:.4f} {from_u} × {UNIT_CONVERSIONS[from_u][factor_u]:.6f} → {converted:.4f} {factor_u}"

    return quantity, f"No conversion rule found ({from_u} → {factor_u}); treated as same unit"


def expected_scope3_from_factors(df: pd.DataFrame) -> list[str]:
    categories = sorted(df[df["scope"] == "Scope 3"]["category"].dropna().unique().tolist())
    return sorted(set(SCOPE3_ALL_CATEGORIES + categories))


# -----------------------------
# Load data and initialize state
# -----------------------------
try:
    emission_factors = pd.read_csv("emission_factors.csv")
except FileNotFoundError:
    st.error("emission_factors.csv not found. Add it next to app.py.")
    st.stop()

required_factor_columns = {"scope", "activity", "unit", "emission_factor"}
if not required_factor_columns.issubset(emission_factors.columns):
    st.error(f"Emission factors file must include columns: {required_factor_columns}")
    st.stop()

emission_factors["category"] = emission_factors.get("category", pd.Series(dtype="object")).fillna("-")

if "emissions_log" not in st.session_state:
    st.session_state.emissions_log = []
if "target_reduction_pct" not in st.session_state:
    st.session_state.target_reduction_pct = 20.0
if "baseline_emissions" not in st.session_state:
    st.session_state.baseline_emissions = None

# -----------------------------
# Sidebar: input + controls
# -----------------------------
st.sidebar.header("➕ Add Emission Entry")
entry_date = st.sidebar.date_input("Activity date", value=date.today(), help="Used in trend charts and period comparisons")
selected_scope = st.sidebar.selectbox("Scope", emission_factors["scope"].unique())

scope_df = emission_factors[emission_factors["scope"] == selected_scope]
selected_category = "-"
if selected_scope == "Scope 3":
    selected_category = st.sidebar.selectbox(
        "Scope 3 Category",
        sorted(scope_df["category"].dropna().unique()),
        help="Maps entries to the full GHG Protocol Scope 3 category structure",
    )
    scope_df = scope_df[scope_df["category"] == selected_category]

selected_activity = st.sidebar.selectbox("Activity Type", sorted(scope_df["activity"].unique()))
factor_row = scope_df[scope_df["activity"] == selected_activity].iloc[0]
factor_unit = factor_row["unit"]
base_factor = float(factor_row["emission_factor"])

region = st.sidebar.selectbox(
    "Region (location-based adjustment)",
    list(REGION_MULTIPLIER.keys()),
    help="Applies a region multiplier to represent location-based factors (especially electricity).",
)
region_multiplier = REGION_MULTIPLIER[region]

use_custom_factor = st.sidebar.toggle(
    "Use custom emission factor",
    value=False,
    help="Enable for specialized activities where your internal EF differs from defaults.",
)
if use_custom_factor:
    emission_factor = st.sidebar.number_input(
        f"Custom emission factor (tCO₂e/{factor_unit})",
        min_value=0.0,
        value=base_factor,
        format="%.8f",
    )
else:
    emission_factor = base_factor * region_multiplier

possible_input_units = [factor_unit]
for source_unit, targets in UNIT_CONVERSIONS.items():
    if factor_unit in targets and source_unit not in possible_input_units:
        possible_input_units.append(source_unit)
input_unit = st.sidebar.selectbox("Input unit", possible_input_units, help="Auto-converted into factor unit when needed")
quantity = st.sidebar.number_input("Quantity", min_value=0.0, format="%.4f")
uncertainty_pct = st.sidebar.slider(
    "Uncertainty (+/- %)",
    min_value=0,
    max_value=50,
    value=int(DEFAULT_UNCERTAINTY.get(selected_scope, 0.15) * 100),
    help="Shows confidence range for estimates",
)

if st.sidebar.button("Add Entry", type="primary", use_container_width=True):
    if quantity <= 0:
        st.sidebar.warning("Quantity must be greater than 0.")
    else:
        converted_qty, conversion_note = convert_to_factor_unit(quantity, input_unit, factor_unit)
        emissions = converted_qty * emission_factor
        uncertainty = uncertainty_pct / 100
        lower = emissions * (1 - uncertainty)
        upper = emissions * (1 + uncertainty)

        formula = (
            f"{converted_qty:.4f} {factor_unit} × {emission_factor:.8f} tCO₂e/{factor_unit} = {emissions:.4f} tCO₂e"
        )

        st.session_state.emissions_log.append(
            {
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Date": pd.to_datetime(entry_date),
                "Scope": selected_scope,
                "Category": selected_category,
                "Activity": selected_activity,
                "Region": region,
                "Quantity": quantity,
                "Input Unit": input_unit,
                "Converted Quantity": converted_qty,
                "Factor Unit": factor_unit,
                "Emission Factor": emission_factor,
                "Base Emission Factor": base_factor,
                "Custom Factor Used": use_custom_factor,
                "Region Multiplier": region_multiplier,
                "Emissions (tCO₂e)": emissions,
                "Uncertainty (%)": uncertainty_pct,
                "Lower (tCO₂e)": lower,
                "Upper (tCO₂e)": upper,
                "Conversion Note": conversion_note,
                "Formula": formula,
            }
        )

        st.sidebar.success("Entry added.")

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Goal Tracking")
st.session_state.target_reduction_pct = st.sidebar.slider(
    "Reduction target (%)",
    min_value=0.0,
    max_value=100.0,
    value=float(st.session_state.target_reduction_pct),
    step=1.0,
)

if st.sidebar.button("Set current emissions as baseline"):
    if st.session_state.emissions_log:
        st.session_state.baseline_emissions = pd.DataFrame(st.session_state.emissions_log)["Emissions (tCO₂e)"].sum()
        st.sidebar.success("Baseline saved.")
    else:
        st.sidebar.warning("Add data first to set a baseline.")

st.sidebar.markdown("---")
st.sidebar.subheader("📥 Bulk Entry")
template_df = pd.DataFrame(
    {
        "Date": [date.today().isoformat()],
        "Scope": ["Scope 1"],
        "Category": ["-"],
        "Activity": ["Diesel"],
        "Region": ["Global Average"],
        "Quantity": [100],
        "Input Unit": ["liters"],
        "Custom Emission Factor": [""],
        "Uncertainty (%)": [10],
    }
)
st.sidebar.download_button(
    "Download CSV template",
    data=template_df.to_csv(index=False).encode("utf-8"),
    file_name="ghg_bulk_template.csv",
    mime="text/csv",
)

bulk_file = st.sidebar.file_uploader("Upload bulk CSV", type=["csv"])
if bulk_file:
    bulk_df = pd.read_csv(bulk_file)
    required_cols = {"Date", "Scope", "Activity", "Quantity"}
    if not required_cols.issubset(bulk_df.columns):
        st.sidebar.error(f"CSV missing required columns: {required_cols}")
    else:
        warnings = []
        for idx, row in bulk_df.iterrows():
            try:
                scope = row["Scope"]
                activity = row["Activity"]
                category = row.get("Category", "-")
                quantity_value = float(row["Quantity"])
                input_u = row.get("Input Unit", None)
                region_name = row.get("Region", "Global Average")
                unc = float(row.get("Uncertainty (%)", DEFAULT_UNCERTAINTY.get(scope, 0.15) * 100))

                match = emission_factors[
                    (emission_factors["scope"] == scope)
                    & (emission_factors["activity"] == activity)
                    & (emission_factors["category"].fillna("-") == category)
                ]
                if match.empty:
                    match = emission_factors[
                        (emission_factors["scope"] == scope)
                        & (emission_factors["activity"] == activity)
                    ]

                if match.empty:
                    warnings.append(f"Row {idx + 2}: no factor found for {scope} / {activity}")
                    continue

                factor_row_bulk = match.iloc[0]
                factor_u = factor_row_bulk["unit"]
                base_ef = float(factor_row_bulk["emission_factor"])
                custom_ef = row.get("Custom Emission Factor", "")
                if pd.notna(custom_ef) and str(custom_ef).strip() != "":
                    ef = float(custom_ef)
                    custom_used = True
                else:
                    ef = base_ef * REGION_MULTIPLIER.get(region_name, 1.0)
                    custom_used = False

                if input_u is None or pd.isna(input_u):
                    input_u = factor_u

                converted_qty, conversion_note = convert_to_factor_unit(quantity_value, str(input_u), factor_u)
                emissions = converted_qty * ef
                lower = emissions * (1 - unc / 100)
                upper = emissions * (1 + unc / 100)

                st.session_state.emissions_log.append(
                    {
                        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "Date": pd.to_datetime(row["Date"]),
                        "Scope": scope,
                        "Category": category,
                        "Activity": activity,
                        "Region": region_name,
                        "Quantity": quantity_value,
                        "Input Unit": input_u,
                        "Converted Quantity": converted_qty,
                        "Factor Unit": factor_u,
                        "Emission Factor": ef,
                        "Base Emission Factor": base_ef,
                        "Custom Factor Used": custom_used,
                        "Region Multiplier": REGION_MULTIPLIER.get(region_name, 1.0),
                        "Emissions (tCO₂e)": emissions,
                        "Uncertainty (%)": unc,
                        "Lower (tCO₂e)": lower,
                        "Upper (tCO₂e)": upper,
                        "Conversion Note": conversion_note,
                        "Formula": f"{converted_qty:.4f} {factor_u} × {ef:.8f} tCO₂e/{factor_u} = {emissions:.4f} tCO₂e",
                    }
                )

                if quantity_value > bulk_df["Quantity"].median() * 5:
                    warnings.append(f"Row {idx + 2}: unusually high quantity ({quantity_value})")

            except Exception as exc:  # noqa: PERF203
                warnings.append(f"Row {idx + 2}: failed to process ({exc})")

        if warnings:
            st.sidebar.warning("; ".join(warnings[:5]))
        st.sidebar.success("Bulk file processed.")

# -----------------------------
# Main dashboard
# -----------------------------
if not st.session_state.emissions_log:
    st.info("No entries yet. Add an entry from the sidebar to start analytics.")
    st.stop()

log_df = pd.DataFrame(st.session_state.emissions_log)
log_df["Date"] = pd.to_datetime(log_df["Date"]).dt.date

# Filters
st.subheader("🔎 Interactive Filters")
f1, f2, f3, f4 = st.columns(4)
with f1:
    min_d = min(log_df["Date"])
    max_d = max(log_df["Date"])
    filter_dates = st.date_input("Date range", value=(min_d, max_d), min_value=min_d, max_value=max_d)
with f2:
    filter_scopes = st.multiselect("Scope", options=sorted(log_df["Scope"].unique()), default=sorted(log_df["Scope"].unique()))
with f3:
    filter_activities = st.multiselect(
        "Activity",
        options=sorted(log_df["Activity"].unique()),
        default=sorted(log_df["Activity"].unique()),
    )
with f4:
    filter_categories = st.multiselect(
        "Category",
        options=sorted(log_df["Category"].unique()),
        default=sorted(log_df["Category"].unique()),
    )

if isinstance(filter_dates, tuple) and len(filter_dates) == 2:
    d_start, d_end = filter_dates
else:
    d_start, d_end = min_d, max_d

filtered = log_df[
    (log_df["Date"] >= d_start)
    & (log_df["Date"] <= d_end)
    & (log_df["Scope"].isin(filter_scopes))
    & (log_df["Activity"].isin(filter_activities))
    & (log_df["Category"].isin(filter_categories))
]

if filtered.empty:
    st.warning("No data after filters. Adjust filters to continue.")
    st.stop()

# KPI cards
total_emissions = filtered["Emissions (tCO₂e)"].sum()
cars_equivalent = total_emissions / CAR_EQUIVALENT_TCO2E_PER_YEAR

k1, k2, k3, k4 = st.columns(4)
k1.metric("Filtered Emissions", f"{total_emissions:,.2f} tCO₂e")
k2.metric("Equivalent Cars / Year", f"{cars_equivalent:,.1f}")
k3.metric("Entries", f"{len(filtered)}")
k4.metric("Avg Uncertainty", f"±{filtered['Uncertainty (%)'].mean():.1f}%")

# Goal progress
st.subheader("🎯 Reduction Goal Progress")
if st.session_state.baseline_emissions and st.session_state.baseline_emissions > 0:
    target = st.session_state.baseline_emissions * (1 - st.session_state.target_reduction_pct / 100)
    progress = max(0.0, min(1.0, (st.session_state.baseline_emissions - total_emissions) / (st.session_state.baseline_emissions - target))) if st.session_state.baseline_emissions != target else 1.0
    st.progress(progress)
    st.write(
        f"Baseline: **{st.session_state.baseline_emissions:,.2f}** tCO₂e | "
        f"Target ({st.session_state.target_reduction_pct:.0f}% cut): **{target:,.2f}** tCO₂e | "
        f"Current filtered: **{total_emissions:,.2f}** tCO₂e"
    )
else:
    st.info("Set a baseline from sidebar to track progress against a reduction target.")

# Visualizations
st.subheader("📈 Time-Series Tracking")
trend_mode = st.radio("Trend granularity", ["Monthly", "Quarterly"], horizontal=True)
trend_df = filtered.copy()
trend_df["Date"] = pd.to_datetime(trend_df["Date"])
if trend_mode == "Monthly":
    trend_df["Period"] = trend_df["Date"].dt.to_period("M").astype(str)
else:
    trend_df["Period"] = trend_df["Date"].dt.to_period("Q").astype(str)

trend_agg = trend_df.groupby(["Period", "Scope"], as_index=False)["Emissions (tCO₂e)"].sum()
fig_trend = px.line(trend_agg, x="Period", y="Emissions (tCO₂e)", color="Scope", markers=True)
st.plotly_chart(fig_trend, use_container_width=True)

c1, c2 = st.columns(2)
with c1:
    st.subheader("🥧 Pie by Scope")
    scope_pie = filtered.groupby("Scope", as_index=False)["Emissions (tCO₂e)"].sum()
    st.plotly_chart(px.pie(scope_pie, names="Scope", values="Emissions (tCO₂e)", hole=0.45), use_container_width=True)

with c2:
    st.subheader("🥧 Pie by Activity")
    activity_pie = filtered.groupby("Activity", as_index=False)["Emissions (tCO₂e)"].sum().sort_values("Emissions (tCO₂e)", ascending=False)
    st.plotly_chart(px.pie(activity_pie.head(10), names="Activity", values="Emissions (tCO₂e)", hole=0.35), use_container_width=True)

# Uncertainty / hotspot / scenario
st.subheader("🧠 Insights & Scenario Modeling")
ins1, ins2 = st.columns(2)
with ins1:
    hotspot = filtered.groupby(["Scope", "Activity"], as_index=False)["Emissions (tCO₂e)"].sum().sort_values("Emissions (tCO₂e)", ascending=False)
    st.markdown("**Top Emission Hotspots**")
    st.dataframe(hotspot.head(8), use_container_width=True)

with ins2:
    scenario_reduction = st.slider("What-if reduction on selected activity emissions (%)", 0, 100, 15)
    scenario_total = total_emissions * (1 - scenario_reduction / 100)
    fig_scenario = go.Figure()
    fig_scenario.add_bar(name="Current", x=["Total"], y=[total_emissions])
    fig_scenario.add_bar(name="Scenario", x=["Total"], y=[scenario_total])
    fig_scenario.update_layout(barmode="group", yaxis_title="tCO₂e")
    st.plotly_chart(fig_scenario, use_container_width=True)

# Scope 3 completeness and progress indicator
st.subheader("📦 Scope 3 Completeness")
scope3_expected = expected_scope3_from_factors(emission_factors)
scope3_logged = sorted(filtered[filtered["Scope"] == "Scope 3"]["Category"].dropna().unique().tolist())
completion = len(set(scope3_logged)) / max(1, len(set(scope3_expected)))
st.progress(completion)
st.write(f"Covered **{len(set(scope3_logged))} / {len(set(scope3_expected))}** Scope 3 categories in filtered data.")

# Reporting section
st.subheader("📝 Reporting & Compliance")
r1, r2 = st.columns(2)
with r1:
    check_fields = {
        "Has Scope 1 data": (filtered["Scope"] == "Scope 1").any(),
        "Has Scope 2 data": (filtered["Scope"] == "Scope 2").any(),
        "Has Scope 3 data": (filtered["Scope"] == "Scope 3").any(),
        "Has uncertainty metadata": filtered["Uncertainty (%)"].notna().all(),
        "Has formula transparency": filtered["Formula"].notna().all(),
        "Has date coverage": filtered["Date"].notna().all(),
    }
    st.markdown("**GHG Protocol readiness checks (basic):**")
    for label, ok in check_fields.items():
        st.write(f"{'✅' if ok else '❌'} {label}")

with r2:
    filtered_dt = filtered.copy()
    filtered_dt["Date"] = pd.to_datetime(filtered_dt["Date"])
    latest_period = filtered_dt["Date"].dt.to_period("M").max()
    prev_period = latest_period - 1

    current_val = filtered_dt[filtered_dt["Date"].dt.to_period("M") == latest_period]["Emissions (tCO₂e)"].sum()
    prev_val = filtered_dt[filtered_dt["Date"].dt.to_period("M") == prev_period]["Emissions (tCO₂e)"].sum()
    delta = current_val - prev_val
    delta_pct = (delta / prev_val * 100) if prev_val else 0.0

    st.markdown("**Period-over-period (monthly):**")
    st.write(f"Current ({latest_period}): **{current_val:,.2f} tCO₂e**")
    st.write(f"Previous ({prev_period}): **{prev_val:,.2f} tCO₂e**")
    st.write(f"Change: **{delta:+,.2f} tCO₂e ({delta_pct:+.1f}%)**")

executive_summary = f"""
Executive Summary
- Total filtered emissions: {total_emissions:,.2f} tCO₂e.
- Scope contribution: {filtered.groupby('Scope')['Emissions (tCO₂e)'].sum().to_dict()}.
- Top hotspot: {hotspot.iloc[0]['Activity'] if not hotspot.empty else 'N/A'}.
- Estimated equivalency: {cars_equivalent:,.1f} passenger vehicles driven for one year.
- Average uncertainty: ±{filtered['Uncertainty (%)'].mean():.1f}%.
Recommendations
1. Prioritize top hotspot categories and activities.
2. Replace high-intensity electricity/fuels with low-carbon alternatives.
3. Improve data quality for entries with higher uncertainty.
"""

st.text_area("Executive summary (auto-generated)", executive_summary, height=220)

report_csv = filtered.sort_values("Date").to_csv(index=False).encode("utf-8")
st.download_button("Download detailed report (CSV)", data=report_csv, file_name="ghg_report.csv", mime="text/csv")
st.download_button(
    "Download executive summary (TXT)",
    data=io.BytesIO(executive_summary.encode("utf-8")),
    file_name="executive_summary.txt",
    mime="text/plain",
)

# Transparency table
st.subheader("🔬 Calculation Transparency Log")
transparency_cols = [
    "Date",
    "Scope",
    "Category",
    "Activity",
    "Region",
    "Quantity",
    "Input Unit",
    "Converted Quantity",
    "Factor Unit",
    "Emission Factor",
    "Uncertainty (%)",
    "Lower (tCO₂e)",
    "Upper (tCO₂e)",
    "Formula",
    "Conversion Note",
]
st.dataframe(filtered[transparency_cols].sort_values("Date", ascending=False), use_container_width=True)
