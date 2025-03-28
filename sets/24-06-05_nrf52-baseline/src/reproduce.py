#!/usr/bin/python3

import click
import collections
import enum
import json
import os
from os import path, system
import serial
import sys
import time
import logging
from Crypto.Cipher import AES
import zmq
import subprocess

import numpy as np
from matplotlib import pyplot as plt

from scaff import analyze
import soapyrx.core

logging.basicConfig()
l = logging.getLogger('reproduce')

Radio = enum.Enum("Radio", "USRP USRP_mini USRP_B210 USRP_B210_MIMO HackRF bladeRF PlutoSDR")
FirmwareMode = collections.namedtuple(
    "FirmwareMode",
    [
        "mode_command",         # command for entering the test mode
        "repetition_command",   # command for triggering repeated execution (or None)
        "action_command",       # command for starting (single or repeated) action
        "have_keys",            # whether the test mode works with keys
    ])


# These are the firmware modes we support; they can be selected with the "mode"
# key in the "firmware" section of the config file.
TINY_AES_MODE = FirmwareMode(
    have_keys=True, mode_command='n', repetition_command='n', action_command='r')
HW_CRYPTO_MODE = FirmwareMode(
    have_keys=True, mode_command='u', repetition_command='n', action_command='r')
HW_CRYPTO_KEYGEN_MODE = FirmwareMode(
    have_keys=True, mode_command='u', repetition_command='n', action_command='r')
HW_CRYPTO_ECB_MODE = FirmwareMode(
    have_keys=True, mode_command='U', repetition_command='n', action_command='r')
MASK_AES_MODE = FirmwareMode(
    have_keys=True, mode_command='w', repetition_command='n', action_command='r')
MASK_AES_MODE_SLOW = FirmwareMode(
    have_keys=True, mode_command='w', repetition_command=None, action_command='e')
TINY_AES_MODE_SLOW = FirmwareMode(
    have_keys=True, mode_command='n', repetition_command=None, action_command='e')
HW_CRYPTO_MODE_SLOW = FirmwareMode(
    have_keys=True, mode_command='u', repetition_command=None, action_command='e')
POWER_ANALYSIS_MODE = FirmwareMode(
    have_keys=False, mode_command='v', repetition_command=None, action_command='s')


# The config file's "firmware" section.
FirmwareConfig = collections.namedtuple(
    "FirmwareConfig",
    [
        # Algorithm to attack: tinyaes[_slow], hwcrypto[_slow], power; slow
        # modes use individual serial commands to trigger.
        "mode",
        # True to use a fixed key or False to vary it for each point.
        "fixed_key",
        # True to use a fixed plaintext or False to vary it for each point.
        "fixed_plaintext",
        # Fixed vs Fixed mode: alternate between two fixed p,k pairs
        # which show large distance according to the leak model
        "fixed_vs_fixed",
        # True to modulate data or False to use just the carrier.
        "modulate",

        # Mode-specific options

        # True to disable radio (conventional attack mode). Defaults at false
        # for compatibility
        "conventional",
        # If a masked version of AES is used, this decides which mode
        "mask_mode",
        # The sleep time between individual encryptions in slow mode collections
        "slow_mode_sleep_time",
    ])


# The config file's "collection" section.
CollectionConfig = collections.namedtuple(
    "CollectionConfig",
    [
        # Frequency to tune to, in Hz.
        "target_freq",
        # Sampling rate, in Hz.
        "sampling_rate",
        # How many different plaintext/key combinations to record.
        "num_points",
        # How many traces executed by the firmware.
        "num_traces_per_point",
        # How many traces to keep from the recording.
        "num_traces_per_point_keep",
        # Multiplier to account for traces dropped due to signal processing
        "traces_per_point_multiplier",
        # Lower cut-off frequency of the band-pass filter.
        "bandpass_lower",
        # Upper cut-off frequency of the band-pass filter.
        "bandpass_upper",
        # Cut-off frequency of the low-pass filter.
        "lowpass_freq",
        # How much to drop at the start of the trace, in seconds.
        "drop_start",
        # How much to include before the trigger, in seconds.
        "trigger_offset",
        # True for triggering on a rising edge, False otherwise.
        "trigger_rising",
        # Threshold used for triggering instead of average.
        "trigger_threshold",
        # Length of the signal portion to keep, in seconds, starting at
        # trigger - trigger_offset.
        "signal_length",
        # Name of the template to load, or None.
        "template_name",
        # Traces with a lower correlation will be discarded.
        "min_correlation",
        # Gain.
        "hackrf_gain",
        # Gain BB.
        "hackrf_gain_bb",
        # Gain IF.
        "hackrf_gain_if",
        # Gain.
        "plutosdr_gain",
        # Gain
        "usrp_gain",
        # Keep all raw
        "keep_all",
        # Channel
        "channel"
    ])


# Global settings, for simplicity
DEVICE = None
BAUD = None
RADIO = None
RADIO_ADDRESS = None
RADIO_ANTENNA = None
COMMUNICATE_SLOW = None
YKUSH_PORT = None


class EnumType(click.Choice):
    """Teach click how to handle enums."""
    def __init__(self, enumcls):
        self._enumcls = enumcls
        click.Choice.__init__(self, enumcls.__members__)

    def convert(self, value, param, ctx):
        value = click.Choice.convert(self, value, param, ctx)
        return self._enumcls[value]


@click.group()
@click.option("-d", "--device", default="/dev/ttyACM0", show_default=True,
              help="The serial dev path of device tested for screaming channels")
@click.option("-b", "--baudrate", default=115200, show_default=True,
              help="The baudrate of the serial device")
@click.option("-y", "--ykush-port", default=0, show_default=True,
              help="If set, use given ykush-port to power-cycle the device")
@click.option("-s", "--slowmode", is_flag=True, show_default=True,
              help=("Enables slow communication mode for targets with a small"
                    "serial rx-buffer"))
@click.option("-r", "--radio", default="USRP", type=EnumType(Radio), show_default=True,
              help="The type of SDR to use.")
@click.option("--radio-address", default="10.0.3.40",
              help="Address of the radio (X.X.X.X for USRP, ip:X.X.X.X or usb:X.X.X for PlutoSDR).")
@click.option("--radio-antenna", default="TX/RX",
              help="Name of the antenna to use (USRP: [TX/RX|RX2])")
@click.option("-l", "--loglevel", default="INFO", show_default=True,
              help="The loglevel to be used ([DEBUG|INFO|WARNING|ERROR|CRITICAL])")
def cli(device, baudrate, ykush_port, slowmode, radio, radio_address, radio_antenna,
        loglevel, **kwargs):
    """
    Reproduce screaming channel experiments with vulnerable devices.

    This script assumes that the device has just been plugged in (or is in an
    equivalent state), that it is running our modified firmware, and that an SDR
    is available. It will carry out the chosen experiment, producing a trace and
    possibly other artifacts. 

    Call any experiment with "--help" for details. You most likely want to use
    "collect".
    """
    global DEVICE, RADIO, RADIO_ADDRESS, RADIO_ANTENNA, BAUD, COMMUNICATE_SLOW, YKUSH_PORT
    DEVICE = device
    BAUD = baudrate
    RADIO = radio
    RADIO_ADDRESS = radio_address
    RADIO_ANTENNA = radio_antenna
    COMMUNICATE_SLOW = slowmode
    YKUSH_PORT = ykush_port

    l.setLevel(loglevel)

def _encode_for_device(data):
    """
    Encode the given bytes in our special format.
    """
    return " ".join(str(data_byte) for data_byte in data)


def _send_parameter(ser, command, param):
    """
    Send a parameter (key or plaintext) to the target device.

    The function assumes that we've already entered tiny_aes mode.
    """
    command_line = '%s%s\r\n' % (command, _encode_for_device(param))
    l.debug('Sending command:  %s\n' % command_line)
    if not COMMUNICATE_SLOW:
        ser.write(command_line.encode())
    else:
        for p in command_line.split(' '):
            ser.write((p+' ').encode())
            time.sleep(.05)

    l.debug('Waiting check\n')
    x = ser.readline()
    l.debug ("received: "+x.decode())
    if len(x) == 0:
        l.debug("nothing received on timeout, ignoring error")
        return 
    #check = ''.join(chr(int(word)) for word in x.split(' '))
    # -- create check like this instead for ESP32:
    #response = ser.readline()
    #response = [ a for a in x.decode().split(' ') if a.isdigit() ]
    #check = ''.join(chr(int(word)) for word in response)
    param2 = '%s' %  _encode_for_device(param)
    
    l.debug ("param: "+param2)
    l.debug ("check: "+x.decode())
    if x.decode().strip() != param2.strip():
        print(("ERROR\n%s\n%s" % (_encode_for_device(param),
                                 _encode_for_device(x))))
        ser.write(b'q')
        sys.exit(1)
    l.debug('Check done\n')

def _send_key(ser, key):
    _send_parameter(ser, 'k', key)


def _send_plaintext(ser, plaintext):
    _send_parameter(ser, 'p', plaintext)

def _send_init(ser, init):
    _send_parameter(ser, 'i', init)

# NOTE: Quick and dirty copy and modification of collect().
@cli.command()
@click.argument("config", type=click.File())
@click.argument("file", type=click.File())
@click.argument("target-path", type=click.Path(exists=True, file_okay=False))
@click.option("--name", default="",
              help="Identifier for the experiment (obsolete; only for compatibility).")
@click.option("--average-out", type=click.Path(dir_okay=False),
              help="File to write the average to (i.e. the template candidate).")
@click.option("--plot/--no-plot", default=False, show_default=True,
              help="Plot the results of trace collection.")
@click.option("--plot-out", type=click.Path(dir_okay=False),
              help="File to write the plot to (instead of showing it dynamically).")
@click.option("--max-power/--no-max-power", default=False, show_default=True,
              help="Set the output power of the device to its maximum.")
@click.option("--raw/--no-raw", default=False, show_default=True,
              help="Save the raw IQ data.")
@click.option("--saveplot/--no-saveplot", default=True, show_default=True,
              help="Save the plot of the results of trace collection.")
@click.option("-p", "--set-power", default=0, show_default=True,
              help="If set, sets the device to a specific power level (overrides --max-power)")
def extract(config, file, target_path, name, average_out, plot, plot_out, max_power, raw, saveplot, set_power):
    """Analyze previous collect."""
    cfg_dict = json.load(config)
    cfg_dict["collection"].setdefault('traces_per_point_multiplier', 1.2)
    cfg_dict["collection"].setdefault('hackrf_gain', 0)
    cfg_dict["collection"].setdefault('hackrf_gain_bb', 44)
    cfg_dict["collection"].setdefault('hackrf_gain_if', 40)
    cfg_dict["collection"].setdefault('plutosdr_gain', 64)
    cfg_dict["collection"].setdefault('usrp_gain', 40)
    cfg_dict["collection"].setdefault('keep_all', False)
    cfg_dict["collection"].setdefault('channel', 0)
    collection_config = CollectionConfig(**cfg_dict["collection"])
    analyze.extract(np.load(file), collection_config, average_out, plot, target_path, saveplot, index=0)

@cli.command()
@click.argument("config", type=click.File())
@click.argument("target-path", type=click.Path(exists=True, file_okay=False))
@click.option("--name", default="",
              help="Identifier for the experiment (obsolete; only for compatibility).")
@click.option("--average-out", type=click.Path(dir_okay=False),
              help="File to write the average to (i.e. the template candidate).")
@click.option("--plot/--no-plot", default=False, show_default=True,
              help="Plot the results of trace collection.")
@click.option("--plot-out", type=click.Path(dir_okay=False),
              help="File to write the plot to (instead of showing it dynamically).")
@click.option("--max-power/--no-max-power", default=False, show_default=True,
              help="Set the output power of the device to its maximum.")
@click.option("--raw/--no-raw", default=False, show_default=True,
              help="Save the raw IQ data.")
@click.option("--saveplot/--no-saveplot", default=True, show_default=True,
              help="Save the plot of the results of trace collection.")
@click.option("-p", "--set-power", default=0, show_default=True,
              help="If set, sets the device to a specific power level (overrides --max-power)")
def collect(config, target_path, name, average_out, plot, plot_out, max_power, raw, saveplot, set_power):
    """
    Collect traces for an attack.

    The config is a JSON file containing parameters for trace analysis; see the
    definitions of FirmwareConfig and CollectionConfig for descriptions of each
    parameter.
    """
    # NO-OP defaults for mode dependent config options for backwards compatibility
    cfg_dict = json.load(config)
    cfg_dict["firmware"].setdefault('conventional', False)
    cfg_dict["firmware"].setdefault('mask_mode', 0)
    cfg_dict["firmware"].setdefault('slow_mode_sleep_time', 0.001)
    cfg_dict["firmware"].setdefault('fixed_vs_fixed', False)
    cfg_dict["firmware"].setdefault('fixed_plaintext', False)
    cfg_dict["collection"].setdefault('traces_per_point_multiplier', 1.2)
    cfg_dict["collection"].setdefault('hackrf_gain', 0)
    cfg_dict["collection"].setdefault('hackrf_gain_bb', 44)
    cfg_dict["collection"].setdefault('hackrf_gain_if', 40)
    cfg_dict["collection"].setdefault('plutosdr_gain', 64)
    cfg_dict["collection"].setdefault('usrp_gain', 40)
    cfg_dict["collection"].setdefault('keep_all', False)
    cfg_dict["collection"].setdefault('channel', 0)

    collection_config = CollectionConfig(**cfg_dict["collection"])
    firmware_config = FirmwareConfig(**cfg_dict["firmware"])

    if firmware_config.mode == "tinyaes":
        firmware_mode = TINY_AES_MODE
    elif firmware_config.mode == "tinyaes_slow":
        firmware_mode = TINY_AES_MODE_SLOW
    elif firmware_config.mode == "maskaes":
        firmware_mode = MASK_AES_MODE
    elif firmware_config.mode == "maskaes_slow":
        firmware_mode = MASK_AES_MODE_SLOW
    elif firmware_config.mode == "hwcrypto":
        firmware_mode = HW_CRYPTO_MODE
    elif firmware_config.mode == "hwcrypto_keygen":
        firmware_mode = HW_CRYPTO_KEYGEN_MODE
    elif firmware_config.mode == "hwcrypto_ecb":
        firmware_mode = HW_CRYPTO_ECB_MODE
    elif firmware_config.mode == "hwcrypto_slow":
        firmware_mode = HW_CRYPTO_MODE_SLOW
    elif firmware_config.mode == "power":
        firmware_mode = POWER_ANALYSIS_MODE
    else:
        raise Exception("Unsupported mode %s; this is a bug!" % firmware_config.mode)

    # assert (not plot) or (collection_config.num_traces_per_point <= 500), \
        # "Plotting a lot of data might lock up the computer! Consider reducing " \
        # "num_traces_per_point in the configuration file or enforce limits on resource consumption..."

    # Signal post-processing will drop some traces when their quality is
    # insufficient, so let's collect more traces than requested to make sure
    # that we have enough in the end.
    num_traces_per_point = int(collection_config.num_traces_per_point * collection_config.traces_per_point_multiplier)

    # number of points
    num_points = int(collection_config.num_points)

    # fixed vs fixed
    fixed_vs_fixed = firmware_config.fixed_vs_fixed

    # Generate the plaintexts
    if fixed_vs_fixed:
        plaintexts = ['\x00'*16 for _trace in range(num_points)]
    else:
        plaintexts = [os.urandom(16)
                    for _trace in range(1 if firmware_config.fixed_plaintext else num_points)]
    
    with open(path.join(target_path, 'pt_%s.txt' % name), 'w') as f:
        f.write('\n'.join(p.hex() for p in plaintexts))

    # Generate the key(s)
    if firmware_mode.have_keys:
        if fixed_vs_fixed:
            keys = ['\x00'*16 if i%2==0 else '\x30'*16 for i in range(num_points)]
        else:
            keys = [os.urandom(16)
                    for _key in range(1 if firmware_config.fixed_key else num_points)]
        with open(path.join(target_path, 'key_%s.txt' % name), 'w') as f:
            f.write('\n'.join(k.hex() for k in keys))

    # If requested, reset target
    if YKUSH_PORT != 0:
        l.debug('Resetting device using ykush port %d' % YKUSH_PORT)
        system("ykushcmd -d %d" % YKUSH_PORT)
        system("ykushcmd -u %d" % YKUSH_PORT)
        time.sleep(3)



    with _open_serial_port() as ser:
        if YKUSH_PORT != 0:
            print((ser.readline()))

        # tmp increase power
        #l.debug('POWERPOWER')
        #ser.write(b'p')
        #print ser.readline()
        #ser.write(b'0')
        #print ser.readline()
        if set_power != 0:
            l.debug('Setting power level to '+str(set_power))
            ser.write(('p'+str(set_power)).encode('UTF-8'))
            ser.readline()
            ser.readline()
        elif max_power:
            l.debug('Setting power to the  maximum')
            ser.write(b'p0')
            ser.readline()
            ser.readline()

        if firmware_config.conventional:
            l.debug('Starting conventional mode, the radio is off')
        else:
            l.debug('Selecting channel')
            ser.write(b'a')
            print((ser.readline()))
            ser.write(b'%02d\n'%collection_config.channel)
            print((ser.readline()))
            if firmware_config.modulate:
                l.debug('Starting modulated wave')
                ser.write(b'o')     # start modulated wave
                print((ser.readline()))
            else:
                l.debug('Starting continuous wave')
                ser.write(b'c')     # start continuous wave

        l.debug('Entering test mode')
        ser.write(firmware_mode.mode_command.encode()) # enter test mode
        print((ser.readline()))

        if firmware_mode.repetition_command:
            l.debug('Setting trace repitions')
            ser.write(('n%d\r\n' % num_traces_per_point).encode())
            print((ser.readline()))

        if firmware_mode.have_keys and firmware_config.fixed_key:
            # The key never changes, so we can just set it once and for all.
            _send_key(ser, keys[0])

        if firmware_config.fixed_plaintext:
            # The plaintext never changes, so we can just set it once and for all.
            _send_plaintext(ser, plaintexts[0])

        if firmware_config.mode == 'maskaes' or firmware_config.mode == 'maskaes_slow':
            l.debug('Setting masking mode to %d', firmware_config.mask_mode)
            ser.write(('%d\r\n' % firmware_config.mask_mode).encode())
            print((ser.readline()))

        # Initialize the radio client.
        client = soapyrx.core.SoapyClient()
            
        # with click.progressbar(plaintexts) as bar:
            # for index, plaintext in enumerate(bar):
        index = 0
        with click.progressbar(list(range(num_points)), label="Collecting") as bar:
            # for index, plaintext in enumerate(bar):
            while index < num_points:
                if firmware_mode.have_keys and not firmware_config.fixed_key:
                    _send_key(ser, keys[index])

                if not firmware_config.fixed_plaintext:
                    if firmware_config.mode == "hwcrypto_keygen":
                        _send_init(ser, plaintexts[index])
                    else:
                        _send_plaintext(ser, plaintexts[index])

                print("Start instrumentation #{}...".format(index))

                # Start non-blocking recording for a pre-configured duration.
                client.record_start()
                time.sleep(0.03)

                if RADIO == Radio.USRP_B210_MIMO or RADIO == Radio.USRP_B210:
                    time.sleep(0.08)
                    # time.sleep(0.04)

                if firmware_mode.repetition_command:
                    # The test mode supports repeated actions.
                    l.debug('Start repetitions')
                    ser.write(firmware_mode.action_command.encode())
                    ser.readline() # wait until done
                else:
                    for _iteration in range(num_traces_per_point):
                        time.sleep(firmware_config.slow_mode_sleep_time)
                        ser.write(firmware_mode.action_command.encode()) # single action

                time.sleep(0.09)
                try:
                    # Wait the end of the recording.
                    client.record_stop()
                    # Accept recording.
                    client.accept()
                except Exception as e:
                    print("ERROR: From radio client: {}".format(e))
                    print("INFO: Restart current recording...")
                    continue

                try:
                    trace_amp, trace_phr, trace_i, trace_q, trace_i_augmented, trace_q_augmented = analyze.extract(client.get(), collection_config, average_out, plot, target_path, saveplot, index, return_zero=False)
                except Exception as e:
                    print("ERROR: From extraction function: {}".format(e))
                    print("INFO: Restart current recording...")
                    client.reinit()
                    continue

                np.save(os.path.join(target_path,"amp_%s_%d.npy"%(name,index)),trace_amp)
                np.save(os.path.join(target_path,"phr_%s_%d.npy"%(name,index)),trace_phr)
                np.save(os.path.join(target_path,"i_%s_%d.npy"%(name,index)),trace_i)
                np.save(os.path.join(target_path,"q_%s_%d.npy"%(name,index)),trace_q)
                if index < 30:
                    plt.plot(trace_amp); figure = plt.gcf(); figure.set_size_inches(32, 18)
                    plt.savefig(os.path.join(target_path,"amp_%s_%d.png"%(name,index))); plt.clf()
                # np.save(os.path.join(target_path,"i_augmented_%s_%d.npy"%(name,index)),trace_i_augmented)
                # np.save(os.path.join(target_path,"q_augmented_%s_%d.npy"%(name,index)),trace_q_augmented)
                # if raw:
                #     save_raw(OUTFILE, target_path, index, name)

                # Update index and click progress bar.
                index += 1
                bar.update(1)
                client.reinit()

        ser.write(b'q')     # quit tiny_aes mode
        print((ser.readline()))
        ser.write(b'e')     # turn off continuous wave

        time.sleep(1)
        ser.close()

        # Quit the server.
        client.stop()

def _open_serial_port():
    l.debug("Opening serial port")
    return serial.Serial(DEVICE, BAUD, timeout=5)

if __name__ == "__main__":
    cli()
