from caen_hv_py.CAENHVController import CAENHVController
import multiprocessing.pool
import json
import argparse
from itertools import repeat, starmap
from datetime import datetime
from time import time, sleep
from random import random

DEVICE_IP_ADDRESS = "000.000.000.000"
DEVICE_USER = "user"
DEVICE_PASSWORD = "password"
RESISTANCES = [0.625, 0.525, 0.875, 0.55, 0.438, 0.56, 1.125] # Sum = 4.698 MOhm

'''
Parameters:
V0Set
I0Set
RUp
RDWn
Trip
'''
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

def wait_for_ramping(hv_wrapper, chamber):
    sleep(3)
    while True:
        sleep(0.1)
        ramping = False
        for s, c in mapping[chamber]:
            status = hv_wrapper.get_ch_param_ushort(s, c, 'Status')
            if status & 0x6:  # Bit 1: ramping up, Bit 2: ramping down
                ramping = True
        if not ramping:
            break

def wait_for_ramping_single_ch(hv_wrapper, slot, channel):
    sleep(2)
    while True:
        sleep(0.1)
        status = hv_wrapper.get_ch_param_ushort(slot, channel, 'Status')
        if not (status & 0x6):  # Bit 1: ramping up, Bit 2: ramping down
            break

def RampUp_Chamber_Voltages(hv_wrapper, chamber, config):
    print(f"Ramping up voltages for chamber {chamber} with V_init={config['V_init']}, V_step={config['V_step']}, V_max={config['V_max']}, t_stabilize={config['t_stabilize']}")

    V_init = config["V_init"]
    V_step = config["V_step"]
    V_max  = config["V_max"]
    V_foil = config["V_foil"]
    t_stb  = config["t_stabilize"]
    output = f"ME0-short-stability-{chamber}_{config['Date']}.txt"

    with open(output, "w") as f:
        f.write("G3B Vmon\tG3B Imon\tG3T Vmon\tG3T Imon\tG2B Vmon\tG2B Imon\tG2T Vmon\tG2T Imon\tG1B Vmon\tG1B Imon\tG1T Vmon\tG1T Imon\tDRIFT Vmon\tDRIFT Imon\n")

    V_tot = V_init
    # Set Ramp Up and Initial Current for all channels
    for s, c in mapping[chamber]:
        hv_wrapper.set_ch_param_float(s, c, "RUp", 10)
        hv_wrapper.set_ch_param_float(s, c, "I0Set", 20)
    while V_tot < V_max + 1e-6:  # Allow for floating point precision issues
        voltages = voltage_divider(V_tot, RESISTANCES)
        for s, c in mapping[chamber]:
            hv_wrapper.set_ch_param_float(s, c, "V0Set", voltages[mapping[chamber].index([s, c])])
        # Wait until all channels have finished ramping up (Bit 1 == 0)
        wait_for_ramping(hv_wrapper, chamber)

        # Wait for the voltage to stabilize
        sleep(t_stb/4)
        imon, vmon = [0 for _ in range(7)], [0 for _ in range(7)]
        for _ in range(3):
            for s, c in mapping[chamber]:
                imon[mapping[chamber].index([s, c])] += hv_wrapper.get_ch_param_float(s, c, "IMon")
                vmon[mapping[chamber].index([s, c])] += hv_wrapper.get_ch_param_float(s, c, "VMon")
            sleep(t_stb/4)
        with open(output, "a") as f:
            line = ""
            for i in range(7): line += f"{vmon[i]/3:.3f}\t{imon[i]/3:.3f}\t"
            line += "\n"
            f.write(line)
        V_tot += V_step
    # Set the final voltages to 550 V for all GEM Foils
    for foil in [1, 3, 5]:  # G3T, G2T, G1T
        s, c = mapping[chamber][foil]
        hv_wrapper.set_ch_param_float(s, c, "V0Set", V_foil)
    # Wait until all channels have finished ramping up
    wait_for_ramping(hv_wrapper, chamber)

    sleep(t_stb/4)
    imon, vmon = [0 for _ in range(7)], [0 for _ in range(7)]
    for _ in range(3):
        for s, c in mapping[chamber]:
            imon[mapping[chamber].index([s, c])] += hv_wrapper.get_ch_param_float(s, c, "IMon")
            vmon[mapping[chamber].index([s, c])] += hv_wrapper.get_ch_param_float(s, c, "VMon")
        sleep(t_stb/4)
    with open(output, "a") as f:
        line = ""
        for i in range(7): line += f"{vmon[i]/3:.3f}\t{imon[i]/3:.3f}\t"
        line += "\n"
        f.write(line)
    # Set the current limit to 2 uA for all channels
    for s, c in mapping[chamber]:
        hv_wrapper.set_ch_param_float(s, c, "I0Set", 2) # Set current limit to 2 uA
    print(f"Ramp up completed for chamber {chamber}.")
    return 0

def Stability_Monitor(hv_wrapper, chamber, config):
    Duration = config["Duration"]
    V_max = config["V_max"]
    V_foil = config["V_foil"]
    test_type = config["test_type"]
    output = f"ME0-{test_type}-stability-{chamber}_{config['Date']}-trip-info.txt"

    voltages = voltage_divider(V_max, RESISTANCES)
    voltages[1], voltages[3], voltages[5] = V_foil, V_foil, V_foil  # Set G1T, G2T, G3T to V_foil

    with open(output, "w") as f:
        start_time = time()
        while time() - start_time < Duration:
            is_tripped = [0 for _ in range(7)]
            for s, c in mapping[chamber]:
                status = hv_wrapper.get_ch_param_ushort(s, c, 'Status')
                is_tripped[mapping[chamber].index([s, c])] = (status & 0x40) or (status & 0x200)
            if any(is_tripped):
                line = f"{datetime.now().strftime('%d-%m-%Y %H:%M:%S')}, Channels:"
                for i, trip in enumerate(is_tripped):
                    if trip: 
                        line += f" {mapping[chamber][i]},"
                line += "\n"
                f.write(line)
                # Power off all channels and wait for 200 s
                for s, c in mapping[chamber]:
                    power = hv_wrapper.get_ch_power(s, c)
                    hv_wrapper.set_ch_param_ushort(s, c, "Pw", 0)
                sleep(200)
                # Power on all channels and set voltages
                for s, c in mapping[chamber]:
                    power = hv_wrapper.get_ch_power(s, c)
                    hv_wrapper.set_ch_param_ushort(s, c, "Pw", 1)
                    hv_wrapper.set_ch_param_float(s, c, "I0Set", 20)
                    hv_wrapper.set_ch_param_float(s, c, "V0Set", voltages[mapping[chamber].index([s, c])])
                wait_for_ramping(hv_wrapper, chamber)    
            sleep(0.5)
        end_time = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        f.write(f"End time: {end_time}\n")
    return 0

def Stress_Test(hv_wrapper, chamber, config):
    V_init = config["V_init"]
    V_max = config["V_max"]
    V_step = config["V_step"]
    t_stb = config["t_stabilize"]
    t_hold = config["t_hold"]
    n_cycles = config["n_cycles"]
    output = f"ME0-stress-{chamber}_{config['Date']}.txt"

    with open(output, "w") as f:
        for ifoil, foil in enumerate([5, 3, 1]): # G1T, G2T, G3T
            s, c = mapping[chamber][foil]
            print(f"Chamber {chamber} on slot {s}, channel {c} power status: {hv_wrapper.get_ch_power(s, c)}")
            if hv_wrapper.get_ch_power(s, c) == 0:
                print(f"Powering on chamber {chamber} on slot {s}, channel {c}")
                hv_wrapper.set_ch_param_ushort(s, c, "Pw", 1)
            else:
                print(f"Chamber {chamber} on slot {s}, channel {c} is already powered on.")
                hv_wrapper.set_ch_param_ushort(s, c, "Pw", 0)  # Power off before ramping up
                sleep(20)
                hv_wrapper.set_ch_param_ushort(s, c, "Pw", 1)  # Power on after waiting
            
            hv_wrapper.set_ch_param_float(s, c, "RUp", 5)  # Set ramp up to 5 Vps
            for cycle in range(n_cycles):
                V_current = V_init
                while V_current < V_max + 1e-6:  # Allow for floating point precision issues
                    hv_wrapper.set_ch_param_float(s, c, "I0Set", 20)  # Set current limit to 20 uA
                    hv_wrapper.set_ch_param_float(s, c, "V0Set", V_current)
                    # Wait for the lamp up is complete
                    wait_for_ramping_single_ch(hv_wrapper, s, c)

                    hv_wrapper.set_ch_param_float(s, c, "I0Set", 2)  # Set current limit to 2 uA
                    sleep(t_stb)
                    status = hv_wrapper.get_ch_param_ushort(s, c, 'Status')
                    if status & 0x40 or status & 0x200:  # Trip or Max V protection
                        f.write(f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}, Trip on GEM #{ifoil+1}, at {V_current:.2f} V.\n")
                        break
                    print(V_current, cycle, ifoil+1)
                    V_current += V_step
                hv_wrapper.set_ch_param_float(s, c, "V0Set", V_init)  # Set voltage to 0 V after each cycle
                wait_for_ramping_single_ch(hv_wrapper, s, c)
            print(f"Powering off chamber {chamber} on slot {s}, channel {c}")
            hv_wrapper.set_ch_param_ushort(s, c, "Pw", 0)
            print("a")
            sleep(t_hold)
    return 0

def QC6_Short(hv_wrapper, chamber, config):
    print(f"Setting Power on of chamber {chamber} ")
    for s, c in mapping[chamber]:
        power = hv_wrapper.get_ch_power(s, c)
        if power == 0:
            hv_wrapper.set_ch_param_ushort(s, c, "Pw", 1)
            print(f"Powering on chamber {chamber} on slot {s}, channel {c}")
        else:
            print(f"Chamber {chamber} on slot {s}, channel {c} is already powered on.")
            hv_wrapper.set_ch_param_ushort(s, c, "Pw", 0)  # Power off before ramping up
            sleep(20)
            hv_wrapper.set_ch_param_ushort(s, c, "Pw", 1)  # Power on after waiting
    RampUp_Chamber_Voltages(hv_wrapper, chamber, config)
    Stability_Monitor(hv_wrapper, chamber, config)
    for s, c in mapping[chamber]:
        power = hv_wrapper.get_ch_power(s, c)
        hv_wrapper.set_ch_param_ushort(s, c, "Pw", 0)
        print(f"Powering off chamber {chamber} on slot {s}, channel {c}")
    return 0

def QC6_Long(hv_wrapper, chamber, config):
    print(f"Setting Power on of chamber {chamber} ")
    for s, c in mapping[chamber]:
        power = hv_wrapper.get_ch_power(s, c)
        if power == 0:
            hv_wrapper.set_ch_param_ushort(s, c, "Pw", 1)
            print(f"Powering on chamber {chamber} on slot {s}, channel {c}")
        else:
            print(f"Chamber {chamber} on slot {s}, channel {c} is already powered on.")
    # Ramp up voltages straight to V_max
    V_max = config["V_max"]
    V_foil = config["V_foil"]
    voltages = voltage_divider(V_max, RESISTANCES)
    voltages[1], voltages[3], voltages[5] = V_foil, V_foil, V_foil  # Set G1T, G2T, G3T to V_foil
    for s, c in mapping[chamber]:
        hv_wrapper.set_ch_param_float(s, c, "RUp", 10)
        hv_wrapper.set_ch_param_float(s, c, "I0Set", 20)
    for s, c in mapping[chamber]:
        hv_wrapper.set_ch_param_float(s, c, "V0Set", voltages[mapping[chamber].index([s, c])])
    # Wait until all channels have finished ramping up
    wait_for_ramping(hv_wrapper, chamber)

    Stability_Monitor(hv_wrapper, chamber, config)
    for s, c in mapping[chamber]:
        power = hv_wrapper.get_ch_power(s, c)
        hv_wrapper.set_ch_param_ushort(s, c, "Pw", 0)
        print(f"Powering off chamber {chamber} on slot {s}, channel {c}")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run QC6 tests on CAEN HV system.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    subparser_short = subparsers.add_parser("short", help="Run short term QC6 tests.")
    subparser_short.add_argument("--chamber", nargs="+", type=str, required=True,
                        help="Chamber to run the QC6 tests on. (!Important: Use the full name of the chamber that is used in the mapping.json file.)")
    subparser_short.add_argument("--date", type=int, default=None,
                        help="Date of the run in YYYYMMDD format. If not specified, the current date will be used.")
    subparser_short.add_argument("--V_init", type=int, default=200,
                        help="Initial voltage for the ramp up. Default is 200 V.")
    subparser_short.add_argument("--V_step", type=int, default=200,
                        help="Voltage step for the ramp up. Default is 200 V.")
    subparser_short.add_argument("--V_max", type=int, default=4600,
                        help="Maximum voltage for the ramp up. Default is 4600 V.")
    subparser_short.add_argument("--V_foil", type=int, default=550,
                        help="Voltage set individually for G1T, G2T, and G3T at the end of the scan. Default is 550 V.")
    subparser_short.add_argument("--t_stabilize", type=int, default=40,
                        help="Time to stabilize the voltage in seconds. Default is 40 seconds.")
    subparser_short.add_argument("--Duration", type=int, default=7200,
                        help="Duration for the stability monitoring in seconds. Default is 7200 seconds (2 hours).")

    subparser_long = subparsers.add_parser("long", help="Run long term QC6 tests.")
    subparser_long.add_argument("--chamber", nargs="+", type=str, required=True,
                        help="Chamber to run the QC6 tests on. (!Important: Use the full name of the chamber that is used in the mapping.json file.)")
    subparser_long.add_argument("--date", type=int, default=None,
                        help="Date of the run in YYYYMMDD format. If not specified, the current date will be used.")
    subparser_long.add_argument("--V_max", type=int, default=4600,
                        help="Maximum voltage for the ramp up. Default is 4600 V.")
    subparser_long.add_argument("--V_foil", type=int, default=550,
                        help="Voltage set individually for G1T, G2T, and G3T at the end of the scan. Default is 550 V.")
    subparser_long.add_argument("--Duration", type=int, default=36000,
                        help="Duration for the stability monitoring in seconds. Default is 36000 seconds (10 hours).")

    subparser_stress = subparsers.add_parser("stress", help="Run stress test.")
    subparser_stress.add_argument("--chamber", nargs="+", type=str, required=True,
                        help="Chamber to run the stress test on. (!Important: Use the full name of the chamber that is used in the mapping.json file.)")
    subparser_stress.add_argument("--date", type=int, default=None,
                        help="Date of the run in YYYYMMDD format. If not specified, the current date will be used.")
    subparser_stress.add_argument("--V_init", type=int, default=10,
                        help="Initial voltage for the stress test. Default is 10 V.")
    subparser_stress.add_argument("--V_max", type=int, default=1000,
                        help="Maximum voltage for the stress test. Default is 1000 V.")
    subparser_stress.add_argument("--V_step", type=int, default=10,
                        help="Voltage step for the stress test. Default is 10 V.")
    subparser_stress.add_argument("--t_stabilize", type=int, default=5,
                        help="Time to stabilize the voltage in seconds. Default is 5 seconds.")
    subparser_stress.add_argument("--t_hold", type=int, default=60,
                        help="Time to hold after finishing the cycle in one GEM foil in seconds. Default is 5 seconds.")
    subparser_stress.add_argument("--n_cycles", type=int, default=5,
                        help="Number of cycles for the stress test. Default is 5 cycles.")
    args = parser.parse_args()

    if args.date is None:
        date = int(datetime.now().strftime("%Y%m%d"))
    else:
        date = args.date

    if args.mode == "short":
        config = {
            "Date": date,
            "V_init": args.V_init,
            "V_step": args.V_step,
            "V_max": args.V_max,
            "V_foil": args.V_foil,
            "t_stabilize": args.t_stabilize,
            "Duration": args.Duration,
            "test_type": "short"
        }
    elif args.mode == "long":
        config = {
            "Date": date,
            "V_max": args.V_max,
            "V_foil": args.V_foil,
            "Duration": args.Duration,
            "test_type": "long"
        }
    elif args.mode == "stress":
        config = {
            "Date": date,
            "V_init": args.V_init,
            "V_max": args.V_max,
            "V_step": args.V_step,
            "t_stabilize": args.t_stabilize,
            "t_hold": args.t_hold,
            "n_cycles": args.n_cycles,
            "test_type": "stress"
        }

    with CAENHVController(DEVICE_IP_ADDRESS, DEVICE_USER, DEVICE_PASSWORD) as hv_wrapper:
        with multiprocessing.pool.ThreadPool(processes=len(args.chamber)) as pool:
            if args.mode == "short":
                print("Running short term QC6 tests...")
                pool.starmap(QC6_Short, zip(repeat(hv_wrapper), args.chamber, repeat(config)))
            elif args.mode == "long":
                print("Running long term QC6 tests...")
                pool.starmap(QC6_Long, zip(repeat(hv_wrapper), args.chamber, repeat(config)))
            elif args.mode == "stress":
                print("Running stress test...")
                pool.starmap(Stress_Test, zip(repeat(hv_wrapper), args.chamber, repeat(config)))
