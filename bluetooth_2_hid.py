#!/usr/bin/env python

"""
Author:     @PixelGordo && @VladimirCingel
Date:       2021-12-22
Version:    0.2
References: https://www.isticktoit.net/?p=1383
            https://github.com/mikerr/pihidproxy
========================================================================================================================
Script that reads the inputs of one keyboard (typically you would like to read a bluetooth keyboard) and translates them
to HID proxy commands through the USB cable. This way you can have the connections below:

    Keyboard --(bluetooth)--> Raspberry Pi Zero W --(usb)--> Computer

The final result is the computer (on the right) receives inputs as if they were coming from an USB keyboard. The
advantages of this technique are:

    - You can use your bluetooth keyboard on any device that has USB connection, no bluetooth drivers or software is
      required.
    - You can use your bluetooth keyboard in the BIOS or GRUB menu.
    - You can use any bluetooth keyboard, even when they require a pairing password. You must do the pairing between
      the Raspberry Pi and the bluetooth using that password but after that, you don't need to repeat the process, the
      raspberry pi and the keyboard will remain paired even when you connect the Raspberry Pi to a different computer
      (similar to what happens with the typical USB dongles that come with Logitech mouses).
    - ...not implemented, but you could create your own macros in this script that would work in any computer you plug
      the keyboard+raspberry. I'm thinking about Ctr-Shift-L to automatically send the keys of Lorem Ipsum...
"""

import argparse
import time
import atexit
import threading
import select
import sys
from time import sleep

import evdev

from libs import keyboard
from libs import hid_codes

# READ THIS: https://www.isticktoit.net/?p=1383

# Constants
#=======================================================================================================================
u_INPUT_DEV_DEFAULT = u'/dev/input/event0'   # In the Raspberry Pi Zero (apparently)
u_OUTPUT_DEV_DEFAULT = u'/dev/hidg0'
u_LOG_FILE = u'/tmp/blu2hid.log'

#_reply_thread = None
_input_device = None
_output_device = None
_input_device_lock = threading.Lock()
_output_device_lock = threading.Lock()
_is_debug_tracing_enabled = False
_virtual_keyboard = None
_args = None


# Helper Functions
#=======================================================================================================================
def _get_cmd_args():
    """
    Function to get command line arguments.
    :return:
    """
    # [1/?] Defining the parser
    #--------------------------
    o_parser = argparse.ArgumentParser()
    o_parser.add_argument('-i',
                          action='store',
                          default=u_INPUT_DEV_DEFAULT,
                          help='Input device (Bluetooth Keyboard). By default it\'s "/dev/input/event0"')
    o_parser.add_argument('-o',
                          action='store',
                          default=u_OUTPUT_DEV_DEFAULT,
                          help='Output device (HID controller). By default it\'s "/dev/hidg0"')
    o_parser.add_argument('-d',
                          action='store_true',
                          default=False,
                          help='Debug mode ON. It\'ll print on screen information about the input keys detected and the'
                               'output HID command sent')
    o_parser.add_argument('-t',
                          action='store_true',
                          default=False,
                          help='Test mode ON. The program will read the inputs normally but it won\'t generate the'
                               'output HID commands. This mode is useful to check that everything is working fine apart'
                               'from the actual HID command delivery')
    o_parser.add_argument('-l',
                          action='store_true',
                          default=False,
                          help='Log mode ON. The program will write a log file "/tmp/bluetooth2hid.log" containing any'
                               'unknown input key')

    o_parsed_data = o_parser.parse_args()
    u_input = str(o_parsed_data.i)
    u_output = str(o_parsed_data.o)
    b_mode_debug = o_parsed_data.d
    b_mode_test = o_parsed_data.t
    b_mode_log = o_parsed_data.l
    return {'u_input': u_input,
        'u_output': u_output,
        'b_mode_debug': b_mode_debug,
        'b_mode_test': b_mode_test,
        'b_mode_log': b_mode_log}

def _init(cmd_args):
    global _is_debug_tracing_enabled
    _is_debug_tracing_enabled = cmd_args['b_mode_debug']
    global _is_test_mode_enabled
    _is_test_mode_enabled = cmd_args['b_mode_test']

    global _virtual_keyboard
    _virtual_keyboard = keyboard.HidKeyboard()
    _set_input_device(cmd_args['u_input'])
    _set_output_device(cmd_args['u_output'])

    # Grab device and register atexist method
    grab_input_device(input_device = _input_device)
    atexit.register(release_all_keys, virtual_kb = _virtual_keyboard, output_device = _output_device)
    atexit.register(ungrab_input_device, input_device = _input_device)

    print_debug(['input device capabilities:\n\n', str(_input_device.capabilities(verbose = True)), '\n\n'])

    # Create thread that listens for the host reply
    _reply_thread = threading.Thread(target = readHostReply, args = ())
    _reply_thread.daemon = True
    _reply_thread.start()


def _set_input_device(device_path):
    device = None
    while device is None:
        try:
            device = evdev.InputDevice(device_path)
        except OSError:
            print_debug('[ WAIT ] Opening Bluetooth input (%s)...' % device_path)
            time.sleep(0.2)

    print_debug('[ pass ] Bluetooth input open (%s)' % str(device_path))
    global _input_device_lock
    global _input_device
    with _input_device_lock:
        _input_device = device

def _set_output_device(device_path):
    device = None
    while device is None:
        try:
            device = open(device_path, 'wb+', buffering=0)
        except OSError:
            print_debug('[ WAIT ] Opening HID output (%s)...' % device_path)
            time.sleep(0.2)

    print_debug('[ pass ] HID output open (%s)' % str(device_path))
    global _output_device_lock
    global _output_device
    with _output_device_lock:
        _output_device = device

def grab_input_device(input_device):
    input_device.grab()

def ungrab_input_device(input_device):
    input_device.ungrab()

def write_to_output_device(output_device, command):
    try:
        output_device.write(command)
    except IOError:
        print_debug('output device cannot be written')
        _set_output_device(_args['u_output'])
        output_device.write(command)

def release_all_keys(virtual_kb, output_device):
    virtual_kb.deactivate_all_keys()
    virtual_kb.reset_all_modifiers()
    write_to_output_device(output_device, virtual_kb.to_hid_command().encode('utf-8'))

def print_debug(message):
    if _is_debug_tracing_enabled:
        print(message)

def readHostReply():
    while (True):
        rd, _, _ = select.select([_output_device], [], [], 0.5)
        if(_output_device in rd):
            global _output_device_lock
            with _output_device_lock:
                reply = int.from_bytes(_output_device.read(1), byteorder="little")
            
            print_debug(["host replied:", str(reply)])
            global _input_device_lock
            global _input_device
            with _input_device_lock:
                try:
                    _input_device.set_led(evdev.ecodes.LED_NUML, bool(reply & 0x01)) #1st bit is NUM_LOCK
                    _input_device.set_led(evdev.ecodes.LED_CAPSL, bool(reply & 0x02)) #2nd bit is CAPS_LOCK     
                    _input_device.set_led(evdev.ecodes.LED_SCROLLL, bool(reply & 0x04)) #3rd bit is SCROLL_LOCK
                except:
                    e = sys.exc_info()[0]
                    print("readHostReply error: %s" % e)

# Main Code
#=======================================================================================================================
if __name__ == '__main__':
    _args = _get_cmd_args()
    _init(_args)    
    # Main loop
    #----------
    while True:
        try:
            with _input_device_lock:
                o_event = _input_device.read_one()
                if o_event is not None:

                    if o_event.type == evdev.ecodes.EV_KEY:
                        # Pre-parsing the event so it's easier to work with it for our purposes
                        o_data = evdev.categorize(o_event)

                        # The modifier status will only change when they are pressed (1) or released (0) (not when they are
                        # hold)
                        keystate = o_data.keystate
                        if (keystate == 2):
                            #_input_device_lock.release()
                            continue
                        keycode = o_data.keycode

                        # [1/?] Getting the HID byte for the modifier keys
                        #-------------------------------------------------
                        if (keycode in hid_codes.ds_MOD_CODES):
                            _virtual_keyboard.modifier_set(keycode, keystate)

                        # [2/?] Activating or deactivating keys in our HidKeyboard when needed
                        #---------------------------------------------------------------------
                        # We only send hid commands when there is a change, so with key-down (1) and key-up (0) events
                        if keystate == 0:
                            _virtual_keyboard.deactivate_key(keycode)
                        else: #keystate == 1:
                            _virtual_keyboard.activate_key(keycode)

                        # [3/?] When any change occurs, we need to send the HID command
                        #--------------------------------------------------------------
                        print_debug(_virtual_keyboard.to_debug_command())

                        if not _is_test_mode_enabled:
                            s_hid_command = _virtual_keyboard.to_hid_command()
                            write_to_output_device(_output_device, s_hid_command.encode('utf-8'))
            sleep(0.001)
        # The o_input_device cannot be read
        except IOError:
            print_debug('input device cannot be read')
            _set_input_device(_args['u_input'])
