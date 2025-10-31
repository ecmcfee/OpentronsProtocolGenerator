import tkinter as tk
from tkinter import filedialog, messagebox
import math
import pandas as pd
import re

MAX_P300_HOLD_UL = 200   # hard cap for p300 holds/dispenses
MAX_P1000_HOLD_UL = 900  # hard cap for p1000 holds/dispenses
DEFAULT_MIX_REPS = 5
DEFAULT_MIX_Z_MM = 2.0   # mix near bottom

def chunk_volumes(total_ul, max_ul):
    vols = []
    remaining = float(total_ul)
    while remaining > 0:
        v = min(max_ul, remaining)
        vols.append(round(v, 2))
        remaining -= v
    return vols

def _find_stock_row(stocks_df: pd.DataFrame, stock_labware_slot, stock_well):
    """Return index of stock row by (slot + well). Fallback: 'stock name' equals well (legacy)."""
    m = stocks_df[
        (stocks_df['labware location'].astype(str) == str(stock_labware_slot))
        & (stocks_df['well location'].astype(str).str.strip() == str(stock_well).strip())
    ]
    if len(m) >= 1:
        return m.index[0]
    m2 = stocks_df[stocks_df['stock name'].astype(str).str.strip() == str(stock_well).strip()]
    if len(m2) >= 1:
        return m2.index[0]
    return None  # allow graceful fallback

def lookup_id(operation, labware_data):
    """Return inner diameter (cm) for the *dispensing* vessel's labware."""
    dispensing_location = int(operation['stock labware location 1'])
    lw = labware_data.copy()
    lw['location'] = lw['location'].astype(int)
    labware_name = str(lw.loc[lw['location'] == dispensing_location, 'labware_title'].iloc[0])

    if labware_name == 'ecmcustom_15_tuberack_14780ul':
        ID = 1.83
    elif labware_name == 'avantorhplcvial_40_wellplate_1500ul':
        ID = 1.0
    elif labware_name == 'ecmcustom_40_wellplate_881.3ul':
        ID = 0.6
    else:
        ID = 3.0
    return ID

def _calc_height_and_update(stocks_df: pd.DataFrame, idx, transfer_ul, default_z=10.0, ID_CM=1.83):
    """Compute a safe aspirate height (mm) and update stock volume if row exists; else return default_z."""
    if idx is None:
        return float(default_z)
    transfer_ml = float(transfer_ul) / 1000.0
    radius_cm = ID_CM * 0.5
    area = math.pi * radius_cm * radius_cm
    pre_vol_ul = float(stocks_df.loc[idx, 'volume(ul)'])
    pre_h_cm = (pre_vol_ul / 1000.0) / area
    dh_cm = transfer_ml / area
    post_h_cm = max(0.0, pre_h_cm - dh_cm)
    z_mm = max(1.0, round(post_h_cm * 10.0 - 5.0, 1))  # 5 mm below predicted surface; clamp to >=1 mm
    stocks_df.loc[idx, 'volume(ul)'] = max(0.0, pre_vol_ul - float(transfer_ul))
    return z_mm

def upsert_destination_stock(stock_df: pd.DataFrame,
                             dst_slot: int,
                             dst_well: str,
                             add_volume_ul: float) -> pd.DataFrame:
    """
    Ensure (dst_slot, dst_well) exists in stock_df and add 'add_volume_ul' to its volume.
    This lets any receiving well/vial become a 'stock solution' for later steps.
    """
    dst_slot = int(dst_slot)
    dst_well = str(dst_well).strip()

    mask = (
        stock_df['labware location'].astype(int).eq(dst_slot) &
        stock_df['well location'].astype(str).str.strip().eq(dst_well)
    )

    if not mask.any():
        # First time this vessel gets liquid → create a row
        new_row = {
            'stock name': f'{dst_slot}:{dst_well}',   # deterministic name; change if you prefer
            'volume(ul)': float(add_volume_ul),
            'labware location': dst_slot,
            'well location': dst_well,
        }
        stock_df.loc[len(stock_df)] = new_row
    else:
        # Already exists → increment volume
        stock_df.loc[mask, 'volume(ul)'] = (
            stock_df.loc[mask, 'volume(ul)'].astype(float) + float(add_volume_ul)
        )

    return stock_df

# ---------------- Priority handling ----------------

_PRIORITY_SYNONYMS = [
    'priority', 'priority rank', 'priority_rank', 'priorities',
    'run first', 'run_first', 'runfirst'
]

def _coerce_priority_series(df: pd.DataFrame) -> pd.Series:
    """
    Build a numeric priority score (lower = earlier). Missing/unknown -> large number (no priority).
    Supports:
      - numeric values directly
      - strings: 'high' < 'med/medium' < 'low'
      - boolean-ish flags in 'run first' columns (true -> 0, false/NA -> large)
    """
    # Find a matching column (case-insensitive)
    name_map = {c.lower(): c for c in df.columns}
    colname = None
    for k in _PRIORITY_SYNONYMS:
        if k in name_map:
            colname = name_map[k]
            break

    # Default: no priority column at all
    if colname is None:
        return pd.Series([float('inf')] * len(df), index=df.index, dtype='float64')

    s = df[colname]

    # Try boolean-ish "run first" interpretation first for "run first" style names
    if colname.lower() in ['run first', 'run_first', 'runfirst']:
        def truthy(v):
            if pd.isna(v): return False
            if isinstance(v, (int, float)): return v != 0
            v = str(v).strip().lower()
            return v in ('1', 'true', 't', 'yes', 'y', 'on')
        return s.map(lambda v: 0 if truthy(v) else float('inf'))

    # Else a true priority column: coerce
    def to_score(v):
        if pd.isna(v): return float('inf')
        # numeric?
        try:
            return float(v)
        except Exception:
            pass
        # string mapping
        vstr = str(v).strip().lower()
        if vstr in ('high', 'hi', 'h', 'urgent', 'top'):
            return 0.0
        if vstr in ('med', 'medium', 'mid', 'm', 'normal'):
            return 1.0
        if vstr in ('low', 'lo', 'l'):
            return 2.0
        # unknown token -> put at end
        return float('inf')

    return s.map(to_score, na_action='ignore').astype('float64')

def _apply_priority_sort(ops_df: pd.DataFrame) -> pd.DataFrame:
    """Return operations sorted by priority (ascending). Ties keep original CSV order."""
    ops = ops_df.copy()
    ops['__row__'] = range(len(ops))  # stable tie-breaker
    ops['__priority__'] = _coerce_priority_series(ops)
    ops = ops.sort_values(['__priority__', '__row__'], kind='stable')
    return ops.drop(columns=['__priority__', '__row__'])

# ---------------- Mix handling ----------------

_MIX_FLAG_SYNONYMS = ['mix', 'mix?', 'mix after', 'mix_after', 'do mix', 'do_mix']
_MIX_REPS_SYNONYMS = ['mix reps', 'mix_reps', 'mix_n', 'mix times', 'mix_times']
_MIX_VOL_SYNONYMS  = ['mix volume', 'mix_volume', 'mix vol', 'mix_vol']
_MIX_EACH_CHUNK_SYNONYMS = ['mix each chunk', 'mix_each_chunk', 'mix per chunk', 'mix_per_chunk']

def _col_lookup_case_insensitive(df: pd.DataFrame, candidates):
    name_map = {c.lower(): c for c in df.columns}
    for k in candidates:
        if k.lower() in name_map:
            return name_map[k.lower()]
    return None

def _truthy(v) -> bool:
    if pd.isna(v): return False
    if isinstance(v, (int, float)): return v != 0
    s = str(v).strip().lower()
    return s in ('1', 'true', 't', 'yes', 'y', 'on')

def _extract_mix_params(op_row: pd.Series, pip_max_ul: float, total_vol_ul: float):
    # Determine if mix is requested
    mix_flag_col = _col_lookup_case_insensitive(op_row.to_frame().T, _MIX_FLAG_SYNONYMS)
    do_mix = _truthy(op_row[mix_flag_col]) if mix_flag_col else False

    # reps
    reps_col = _col_lookup_case_insensitive(op_row.to_frame().T, _MIX_REPS_SYNONYMS)
    reps = DEFAULT_MIX_REPS
    if reps_col is not None and not pd.isna(op_row[reps_col]):
        try:
            reps = int(op_row[reps_col])
        except Exception:
            pass

    # volume
    vol_col = _col_lookup_case_insensitive(op_row.to_frame().T, _MIX_VOL_SYNONYMS)
    if vol_col is not None and not pd.isna(op_row[vol_col]):
        try:
            mix_vol = float(op_row[vol_col])
        except Exception:
            mix_vol = min(0.8 * pip_max_ul, total_vol_ul)
    else:
        mix_vol = min(0.8 * pip_max_ul, total_vol_ul)
    mix_vol = max(1.0, min(mix_vol, pip_max_ul))

    # each-chunk?
    each_col = _col_lookup_case_insensitive(op_row.to_frame().T, _MIX_EACH_CHUNK_SYNONYMS)
    mix_each_chunk = _truthy(op_row[each_col]) if each_col else False

    return do_mix, reps, mix_vol, mix_each_chunk

# ---------------------------------------------------

def generate_protocol(stock_data: pd.DataFrame, labware_data: pd.DataFrame, operation_data: pd.DataFrame, save_path: str):
    # Build labware section
    content = [
        "from opentrons import protocol_api",
        "",
        "metadata = {",
        "    'apiLevel': '2.15',",
        "    'protocolName': 'Automatic Protocol',",
        "    'author': 'Generated'",
        "}",
        "",
        "def run(protocol: protocol_api.ProtocolContext):",
        "    # Load labware",
    ]

    labware_map = {}
    tiprack_200_vars = []
    tiprack_1000_vars = []
    tiprack_any = []

    for _, lw in labware_data.iterrows():
        title = str(lw['labware_title']).strip()
        loc = int(lw['location'])
        if 'tiprack' in title.lower():
            m = re.search(r'(\d{2,4})\s*ul', title, re.IGNORECASE)
            size = (m.group(1) + 'ul') if m else 'tips'
            var = f"tiprack_{size}"
            content.append(f"    {var} = protocol.load_labware('{title}', {loc})")
            tiprack_any.append(var)
            if '200' in size:
                tiprack_200_vars.append(var)
            if '1000' in size:
                tiprack_1000_vars.append(var)
        else:
            var = f"labware_{loc}"
            content.append(f"    {var} = protocol.load_labware('{title}', {loc})")
        labware_map[loc] = var

    # Load instruments
    if not tiprack_200_vars and not tiprack_any:
        raise RuntimeError("No tipracks found in labware CSV. Please include at least one tiprack.")

    # P300 (left) prefers 200 µL tipracks; else fallback to any tiprack
    p300_tipracks = tiprack_200_vars if tiprack_200_vars else tiprack_any[:1]
    content.append("")
    content.append(f"    p300 = protocol.load_instrument('p300_single_gen2', 'left', tip_racks=[{', '.join(p300_tipracks)}])")

    # P1000 (right) only if 1000 µL tipracks are present
    p1000_loaded = len(tiprack_1000_vars) > 0
    if p1000_loaded:
        content.append(f"    p1000 = protocol.load_instrument('p1000_single_gen2', 'right', tip_racks=[{', '.join(tiprack_1000_vars)}])")
    content.append("")

    # Normalize and sort operations with PRIORITY
    ops = operation_data.copy()
    ops['stock labware location 1'] = ops['stock labware location 1'].astype(int)
    ops['receiving labware location'] = ops['receiving labware location'].astype(int)

    # ---- priority sort (optional) ----
    ops = _apply_priority_sort(ops).reset_index(drop=True)

    # One-tip-per-source-well policy, tracked per pipette (overridden by mix logic)
    current_source = {'p300': None, 'p1000': None}
    picked = {'p300': False, 'p1000': False}

    def select_pipette(total_vol_ul: float) -> str:
        """Choose 'p1000' for >200 µL if available; else 'p300'."""
        if p1000_loaded and float(total_vol_ul) > MAX_P300_HOLD_UL:
            return 'p1000'
        return 'p300'

    def _next_source_for_pipette(ops_df: pd.DataFrame, start_row_idx: int, pip_name: str):
        """
        Find the next operation (after start_row_idx) that uses 'pip_name',
        and return its (slot, well) *source*. If none, return None.
        """
        for k in range(start_row_idx + 1, len(ops_df)):
            nxt = ops_df.iloc[k]
            pn = select_pipette(float(nxt['volume 1']))
            if pn != pip_name:
                continue
            return (int(nxt['stock labware location 1']),
                    str(nxt['stock well location 1']).strip())
        return None

    for row_idx, op in ops.iterrows():
        src_slot = int(op['stock labware location 1'])
        src_well = str(op['stock well location 1']).strip()
        dst_slot = int(op['receiving labware location'])
        dst_well = str(op['receiving well location']).strip()
        total_vol = float(op['volume 1'])

        pip_name = select_pipette(total_vol)
        max_hold = MAX_P1000_HOLD_UL if pip_name == 'p1000' else MAX_P300_HOLD_UL
        pip_var = 'p1000' if pip_name == 'p1000' else 'p300'

        # Mix parameters for this op (decided at op level)
        do_mix, mix_reps, mix_vol, mix_each_chunk = _extract_mix_params(op, max_hold, total_vol)

        src_key = (src_slot, src_well)
        if current_source[pip_name] != src_key:
            if picked[pip_name]:
                content.append(f"    {pip_var}.drop_tip()")
                picked[pip_name] = False
            content.append(f"    {pip_var}.pick_up_tip()")
            picked[pip_name] = True
            current_source[pip_name] = src_key

        chunks = chunk_volumes(total_vol, max_hold)
        for i, chunk in enumerate(chunks):
            idx = _find_stock_row(stock_data, src_slot, src_well)
            id_cm = lookup_id(op, labware_data)
            z = _calc_height_and_update(stock_data, idx, chunk, ID_CM=float(id_cm))
            if idx is None:
                content.append(f"    # WARNING: No stock specified for slot {src_slot} well {src_well}; using default aspirate height.")
            content.append(f"    {pip_var}.aspirate({chunk}, {labware_map[src_slot]}['{src_well}'].bottom(z={z}))")
            content.append(f"    {pip_var}.dispense({chunk}, {labware_map[dst_slot]}['{dst_well}'].top(z=-3))")

            # Determine if we should mix now (per-chunk or only after the final chunk)
            mix_now = do_mix and (mix_each_chunk or i == len(chunks) - 1)

            if mix_now:
                # Touch BEFORE mixing on the destination vessel
                content.append(f"    {pip_var}.mix({int(mix_reps)}, {round(float(mix_vol),2)}, {labware_map[dst_slot]}['{dst_well}'].bottom(z={DEFAULT_MIX_Z_MM}))")
                content.append(f"    {pip_var}.touch_tip({labware_map[dst_slot]}['{dst_well}'], radius=0.8, v_offset=-1, speed=60)")

                # --- NEW: conditional tip keep/drop after mix ---
                keep_tip = False

                if mix_each_chunk and i < len(chunks) - 1:
                    # Next aspiration is the next chunk of THIS op (from src), which is NOT the just-mixed dest.
                    keep_tip = False
                else:
                    # Final chunk (or only mixing at end). Look ahead to the next op that uses this pipette.
                    next_src = _next_source_for_pipette(ops, row_idx, pip_name)
                    keep_tip = (next_src is not None and next_src == (dst_slot, dst_well))

                if keep_tip:
                    # Keep the tip because the very next aspiration by this pipette is from the just-mixed solution.
                    # Update current_source so the next op doesn't force a tip change.
                    current_source[pip_name] = (dst_slot, dst_well)
                    # Do NOT drop the tip here.
                else:
                    # Drop now; a different solution will be aspirated next time this pipette is used.
                    content.append(f"    {pip_var}.drop_tip()")
                    picked[pip_name] = False
                    current_source[pip_name] = None

                # If we dropped the tip due to mix_each_chunk and there are more chunks, pick up for the next chunk.
                if mix_each_chunk and i < len(chunks) - 1:
                    content.append(f"    {pip_var}.pick_up_tip()")
                    picked[pip_name] = True
                    current_source[pip_name] = src_key
            else:
                # No mix yet → still touch tip after dispense
                content.append(f"    {pip_var}.touch_tip({labware_map[dst_slot]}['{dst_well}'], radius=0.8, v_offset=-1, speed=60)")

            # Track destination volume so it becomes a valid 'stock' for later steps
            stock_data = upsert_destination_stock(stock_data, dst_slot, dst_well, chunk)

    # Drop any remaining picked tips (only if not already dropped during mixing logic)
    if picked['p300']:
        content.append("    p300.drop_tip()")
    if p1000_loaded and picked['p1000']:
        content.append("    p1000.drop_tip()")

    content_string = ''
    for i in range(len(content)):
        content_string += content[i] + '\n'

    with open(save_path, 'w', encoding='utf-8') as f:
        f.write(content_string)

def main():
    root = tk.Tk()
    root.withdraw()

    try:
        stock_csv = filedialog.askopenfilename(title="Select stock solution CSV", filetypes=[("CSV files", "*.csv")])
        labware_csv = filedialog.askopenfilename(title="Select labware information CSV", filetypes=[("CSV files", "*.csv")])
        operations_csv = filedialog.askopenfilename(title="Select transfers CSV", filetypes=[("CSV files", "*.csv")])
        if not stock_csv or not labware_csv or not operations_csv:
            messagebox.showerror("Error", "All three CSV files must be selected."); return

        destination = filedialog.asksaveasfilename(defaultextension=".py", filetypes=[("Python files", "*.py")], title="Save protocol as")
        if not destination:
            messagebox.showerror("Error", "Please choose a destination filename."); return

        stock_data = pd.read_csv(stock_csv)
        labware_data = pd.read_csv(labware_csv)
        operations_data = pd.read_csv(operations_csv)

        # Basic header validation
        required_stock_cols = {'stock name','volume(ul)','labware location','well location'}
        if not required_stock_cols.issubset(set(stock_data.columns)):
            messagebox.showerror("Error", f"Stock CSV is missing required columns: {required_stock_cols}"); return

        required_labware_cols = {'labware_title','location'}
        if not required_labware_cols.issubset(set(labware_data.columns)):
            messagebox.showerror("Error", f"Labware CSV is missing required columns: {required_labware_cols}"); return

        required_ops_cols = {'receiving labware location','receiving well location','stock labware location 1','stock well location 1','volume 1'}
        if not required_ops_cols.issubset(set(operations_data.columns)):
            messagebox.showerror("Error", f"Transfers CSV is missing required columns: {required_ops_cols}"); return

        # Generate protocol
        generate_protocol(stock_data.copy(), labware_data.copy(), operations_data.copy(), destination)
        messagebox.showinfo("Success", "Protocol successfully generated.")

    except Exception as e:
        messagebox.showerror("Error", f"Failed: {e}")
    finally:
        root.destroy()

if __name__ == "__main__":
    main()
