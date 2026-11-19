
import io
import time
from pathlib import Path

import serial
from PIL import Image

# =====================================
# SETTINGS
# =====================================

PORT = "COM9"
BAUD = 921600

TOTAL_IMAGES = 10
INTERVAL_SECONDS = 5

OUTPUT_DIR = Path("captures/session_01(dry)")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# =====================================
# CHUTE CROPPING SETTINGS
# =====================================
# Format: (LEFT, TOP, RIGHT, BOTTOM)
#
# Adjust these coordinates if the camera
# or chute position changes.

CROP_LEFT = 45
CROP_TOP = 0
CROP_RIGHT = 610
CROP_BOTTOM = 480

CROP_BOX = (
    CROP_LEFT,
    CROP_TOP,
    CROP_RIGHT,
    CROP_BOTTOM
)

# =====================================
# SERIAL COMMUNICATION
# =====================================

def read_line(ser, timeout=10):

    deadline = time.monotonic() + timeout
    buffer = bytearray()

    while time.monotonic() < deadline:

        char = ser.read(1)

        if char == b"\n":
            return buffer.decode(
                "ascii", errors="replace"
            ).strip()

        if char:
            buffer.extend(char)

    raise TimeoutError("Timed out waiting for serial header.")


def read_exact(ser, length, timeout=20):

    deadline = time.monotonic() + timeout
    received = bytearray()

    while len(received) < length:

        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Image transfer incomplete: "
                f"{len(received)}/{length} bytes"
            )

        chunk = ser.read(length - len(received))

        if chunk:
            received.extend(chunk)

    return bytes(received)


# =====================================
# CAMERA CONNECTION
# =====================================

def wait_for_camera(ser):

    print("Waiting for ESP32-CAM...")

    for _ in range(20):

        ser.write(b"PING\n")
        ser.flush()

        try:
            response = read_line(ser, timeout=1.5)

            if response == "READY":
                print("ESP32-CAM connected successfully.")
                return

        except TimeoutError:
            pass

    raise RuntimeError(
        "Camera did not respond. Check wiring and power."
    )


# =====================================
# CAPTURE AND AUTOMATIC CROPPING
# =====================================

def capture_one(ser, image_number):

    ser.reset_input_buffer()

    ser.write(b"CAPTURE\n")
    ser.flush()

    while True:

        header = read_line(ser, timeout=15)

        if header.startswith("ERROR:"):
            raise RuntimeError(header)

        if header.startswith("IMG:"):
            break

        print("ESP32:", header)

    # Header format: IMG:length,width,height
    values = header[4:].split(",")

    if len(values) != 3:
        raise ValueError(f"Invalid image header: {header}")

    length, width, height = map(int, values)

    if length <= 0 or length > 1_000_000:
        raise ValueError(f"Unexpected image size: {length}")

    payload = read_exact(ser, length)

    # Open received JPEG
    with Image.open(io.BytesIO(payload)) as image:

        image.load()

        # Ensure camera resolution is as expected
        if image.size != (640, 480):
            raise ValueError(
                f"Unexpected camera resolution: {image.size}"
            )

        # Validate crop coordinates
        if not (
            0 <= CROP_LEFT < CROP_RIGHT <= image.width
            and
            0 <= CROP_TOP < CROP_BOTTOM <= image.height
        ):
            raise ValueError("Invalid cropping coordinates.")

        # Automatically crop only the chute region
        cropped_image = image.crop(CROP_BOX)

        # Save cropped JPEG
        filename = OUTPUT_DIR / f"image_{image_number:05d}.jpg"

        cropped_image.save(
            filename,
            format="JPEG",
            quality=95
        )

    print(
        f"[{image_number}/{TOTAL_IMAGES}] "
        f"Saved cropped image: {filename} | "
        f"{CROP_RIGHT-CROP_LEFT}x{CROP_BOTTOM-CROP_TOP}"
    )


# =====================================
# NEXT IMAGE NUMBER
# =====================================

def get_next_image_number():

    existing = list(OUTPUT_DIR.glob("image_*.jpg"))

    numbers = []

    for file in existing:

        try:
            numbers.append(
                int(file.stem.split("_")[-1])
            )
        except ValueError:
            continue

    return max(numbers, default=0) + 1


# =====================================
# MAIN AUTOMATIC CAPTURE
# =====================================

def main():

    print("================================")
    print(" AUTOMATIC WASTE IMAGE CAPTURE ")
    print("================================")

    print(f"Images: {TOTAL_IMAGES}")
    print(f"Interval: {INTERVAL_SECONDS} seconds")
    print(f"Crop region: {CROP_BOX}")
    print(f"Output folder: {OUTPUT_DIR}")

    try:

        with serial.Serial(
            PORT,
            BAUD,
            timeout=0.25,
            write_timeout=5
        ) as ser:

            time.sleep(2)

            wait_for_camera(ser)

            image_number = get_next_image_number()

            next_capture = (
                time.monotonic() + INTERVAL_SECONDS
            )

            for i in range(1, TOTAL_IMAGES + 1):

                remaining = (
                    next_capture - time.monotonic()
                )

                if remaining > 0:

                    print(
                        f"\nCapture {i}/{TOTAL_IMAGES} "
                        f"in {remaining:.1f} seconds..."
                    )

                    time.sleep(remaining)

                capture_one(ser, image_number)

                image_number += 1

                next_capture += INTERVAL_SECONDS

        print("\n================================")
        print(" CAPTURE COMPLETED ")
        print("================================")

        print(f"Successfully captured: {TOTAL_IMAGES}")
        print(f"Saved in: {OUTPUT_DIR.resolve()}")

    except KeyboardInterrupt:
        print("\nCapture manually stopped.")

    except Exception as error:
        print(f"\nCAPTURE FAILED: {error}")


if __name__ == "__main__":
    main()
