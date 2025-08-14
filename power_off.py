from caen_hv_py.CAENHVController import CAENHVController
import json
import time
import argparse

DEVICE_IP_ADDRESS = "128.141.143.244"
DEVICE_USER = "admin"
DEVICE_PASSWORD = "admin"
RESISTANCES = [0.625, 0.525, 0.875, 0.55, 0.438, 0.56, 1.125] # Sum = 4.698

p = argparse.ArgumentParser(description="QC6 Voltage Ramp Up Script")
p.add_argument("--chamber", type=str, nargs="+", required=True, help="Chamber(s) to run the QC6 test on")
args = p.parse_args()

with open("mapping.json", "r") as f:
    mapping = json.load(f)

def voltage_divider(voltage: float,
                    resistances: list[float]) -> list[float]:
    voltages = []
    V_lim = 1000
    for R in resistances:
        if R <= 0:
            raise ValueError("Resistance must be greater than zero.")
        voltage_divided = voltage * (R / sum(resistances))
        if voltage_divided > V_lim:
            voltage_divided = V_lim
        voltages.append(voltage_divided)
    return voltages

with CAENHVController(DEVICE_IP_ADDRESS, DEVICE_USER, DEVICE_PASSWORD) as hv_wrapper:
    for chamber in args.chamber:
        for s, c in mapping[chamber]:
            print(f"Setting up chamber {chamber} on slot {s}, channel {c}")
            # power = hv_wrapper.get_ch_power(s, c)
            power = hv_wrapper.get_ch_param_ushort(s, c, "Pw")
            hv_wrapper.set_ch_param_ushort(s, c, "Pw", 0)
    print("All chambers set up successfully.")