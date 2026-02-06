import serial
import time
import binascii
import sys

# Constants
PORT = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02QFFJ-if00-port0"
BAUD = 9600

print(f"--- Breathe Audio Diagnostic Tool ---")
print(f"Target: {PORT} @ {BAUD}")

try:
    # Open Serial
    ser = serial.Serial(
        PORT,
        BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=1,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False
    )
    print("1. Port Opened Successfully")
    
    # Clear buffer
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    
    # Send Wakeup
    print("2. Sending Wakeup CR...")
    ser.write(b'\r')
    time.sleep(0.5)
    
    # Read Wakeup Response
    pre_data = ser.read_all()
    if pre_data:
        print(f"   [Wakeup Response] RAW: {pre_data} | HEX: {binascii.hexlify(pre_data)}")
    else:
        print("   [Wakeup Response] None (Timeout)")

    # Send Query
    cmd = b'*Z01CONSR\r'
    print(f"3. Sending Query: {cmd}...")
    ser.write(cmd)
    
    # Read Response Loop
    print("4. Listening for 5 seconds...")
    start_time = time.time()
    buffer = b""
    while time.time() - start_time < 5:
        if ser.in_waiting:
            chunk = ser.read(ser.in_waiting)
            buffer += chunk
            print(f"   [RX] {chunk} | HEX: {binascii.hexlify(chunk)}")
            if b'\r' in buffer:
                print("   -> Found Terminator (CR)")
        time.sleep(0.1)
        
    print(f"5. Final Buffer: {buffer}")
    ser.close()

except Exception as e:
    print(f"ERROR: {e}")
