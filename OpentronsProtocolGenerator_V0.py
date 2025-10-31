import tkinter as tk
from tkinter import filedialog, messagebox
import csv
import datetime
import math
import pandas as pd
import numpy as np
import re



def calc_aspirate_height(stock_data, current_stock,transfer_volume_ul, ID_CM = 1.83):
    # ID_CM = 1.83
    row_number = stock_data.index.get_loc(stock_data[stock_data['stock name'] == f'{current_stock}'].index[0])
    transfer_volume = transfer_volume_ul/1000
    solvent_height = transfer_volume / ((ID_CM * 0.5) * (ID_CM * 0.5) * math.pi)
    pre_transfer_solvent_height_cm = (stock_data[r'volume(ul)'].iloc[row_number]/1000) / ((ID_CM * 0.5) * (ID_CM * 0.5) * math.pi)
    post_transfer_solvent_height_cm = ((stock_data[r'volume(ul)'].iloc[row_number]/1000) - transfer_volume) / ((ID_CM * 0.5) * (ID_CM * 0.5) * math.pi)

    suggested_aspirate_height = (post_transfer_solvent_height_cm * 10) - 5
    if suggested_aspirate_height <= 1:
        aspirate_height = 1
    else:
        aspirate_height = float(int(suggested_aspirate_height))

    stock_data.loc[row_number, 'volume(ul)'] = stock_data.loc[row_number, 'volume(ul)'] - (transfer_volume*1000)

    return aspirate_height, stock_data


def parse_csv(file_path):
    try:
        with open(file_path, mode='r') as csvfile:
            reader = csv.DictReader(csvfile)
            data = [row for row in reader]
        return data
    except Exception as e:
        messagebox.showerror("Error", f"Failed to parse CSV {file_path}: {e}")
        return None


# Max liquid the P300 should ever hold at once (µL)
MAX_P300_HOLD_UL = 200

def chunk_volumes(total_ul: float, max_ul: float = MAX_P300_HOLD_UL):
    """Split a total volume into chunks <= max_ul (both in µL)."""
    total = float(total_ul)
    if total <= 0:
        return []
    n_full = int(total // max_ul)
    remainder = total - (n_full * max_ul)
    chunks = [float(max_ul)] * n_full
    if remainder > 0:
        chunks.append(float(remainder))
    return chunks

def generate_protocol(stock_data, labware_data, operation_data, save_path):
    # Initialize that no tip has been used
    current_tip = None
    # Initialize the protocol script
    content = """from opentrons import protocol_api

metadata = {
    'apiLevel': '2.15',
    'protocolName': 'Automatic Protocol',
    'author': 'Generated'
}

def run(protocol: protocol_api.ProtocolContext):
    # Load labware
"""
    # labware_data = sort_with_heaters(labware_data)
    # Load labware based on labware_data
    labware_dict = {}
    module_locations = {}
    for labware_row in labware_data.iterrows():
        labware = labware_row[1]
        labware_variable = f"labware_{labware['location']}"
        if 'tuberack' in labware['labware_title']:
            content += f"    {labware_variable} = protocol.load_labware('{labware['labware_title']}', {labware['location']})\n"
        if 'tiprack' in labware['labware_title']:
            parts = labware['labware_title'].split("_")
            volume = next((p for p in parts if "ul" in p.lower()), None)
            labware_variable = f'tiprack_{volume}'
            content += f"    {labware_variable} = protocol.load_labware('{labware['labware_title']}', {labware['location']})\n"
        if 'heaterShakerModuleV1' in labware['labware_title']:
            content += f"    {labware_variable} = protocol.load_module('{labware['labware_title']}', '{labware['location']}')\n"
            module_locations[labware['location']] = {labware_variable}
        if 'plate' in labware['labware_title']:
            # if labware['location'] in module_locations:
            content += f"    {labware_variable} = protocol.load_labware('{labware['labware_title']}', {labware['location']})\n"



        labware_dict[labware['location']] = labware_variable

    # Example: Load tiprack and pipette
    content += """
    pipette = protocol.load_instrument('p300_single', 'left', tip_racks=[tiprack_200ul])
"""


    #csv_dir = r'C:\Users\mcfee\PycharmProjects\OpenTrons\misc\Gen4-2 Transfers.csv'
    #operation_data = pd.read_csv(csv_dir)
    #stock_data = pd.read_csv(r'C:\Users\mcfee\PycharmProjects\OpenTrons\misc\Gen4 Stocks.csv')
    operation_data.sort_values(['stock well location 1','volume 1'])
    current_pipette_res = None

    # operation_data.sort_values()
    for operation_row in operation_data.iterrows():
        operation = operation_row[1]
        stock_loc = operation[f'stock labware location 1']
        stock_well = operation['stock well location 1']

        recv_loc = operation['receiving labware location']
        recv_well = operation['receiving well location']

        transfer_volume = float(operation['volume 1'])

        # Tip policy: one tip per source well
        if current_tip is None:
            current_tip = stock_well
            content += "    pipette.pick_up_tip()\n"
        elif current_tip != stock_well:
            current_tip = stock_well
            content += "    pipette.drop_tip()\n"
            content += "    pipette.pick_up_tip()\n"

        # Split into <=200 µL chunks and update height/stock per chunk
        for vol_chunk in chunk_volumes(transfer_volume, MAX_P300_HOLD_UL):
            aspirate_height, stock_data = calc_aspirate_height(stock_data, stock_well, vol_chunk)
            content += (
                f"    pipette.aspirate({vol_chunk}, "
                f"{labware_dict[stock_loc]}['{stock_well}'].bottom(z={aspirate_height}))\n"
            )
            content += (
                f"    pipette.dispense({vol_chunk}, "
                f"{labware_dict[recv_loc]}['{recv_well}'].top(z=-3), push_out=2)\n"
                f"    pipette.touch_tip({labware_dict[recv_loc]}['{recv_well}'], radius=0.8, v_offset=-1, speed=60)\n"
            )
    print(content)
    content += "    pipette.drop_tip()\n"

    # Write the protocol content to the specified file
    with open(save_path, 'w') as f:
        f.write(content)




def select_file(title):
    file_path = filedialog.askopenfilename(title=title, filetypes=[("CSV files", "*.csv")])
    return file_path

def main():
    root = tk.Tk()
    root.withdraw()  # Hide the main window

    stock_csv = select_file("Select Stock Solutions CSV")
    if not stock_csv:
        return

    labware_csv = select_file("Select Labware CSV")
    if not labware_csv:
        return

    operations_csv = select_file("Select Operations CSV")
    if not operations_csv:
        return

    destination = filedialog.asksaveasfilename(defaultextension=".py", filetypes=[("Python Files", "*.py")],title="Save Protocol As")
    if not destination:
        return

    stock_data = pd.read_csv(stock_csv)
    labware_data = pd.read_csv(labware_csv)
    operations_data = pd.read_csv(operations_csv)


    if stock_data is not None and labware_data is not None and operations_data is not None:
        generate_protocol(stock_data, labware_data, operations_data, destination)
        messagebox.showinfo("Success", "Protocol successfully generated.")
    else:
        messagebox.showerror("Error", "Failed to parse all input files.")

    root.destroy()


if __name__ == "__main__":
    main()