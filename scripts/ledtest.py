import gpiod
import time
from gpiod.line import Direction, Value

chip_path = '/dev/gpiochip0'

with gpiod.Chip(chip_path) as chip:
    info = chip.get_info()
    print(f"{info.name} [{info.label}] ({info.num_lines} lines)")

LINE0 = 23
LINE1 = 24

request =  gpiod.request_lines(
    chip_path,
    consumer="blink-example",
    config={
        LINE0: gpiod.LineSettings(
            direction=Direction.OUTPUT, output_value=Value.ACTIVE
        ),
        LINE1: gpiod.LineSettings(
            direction=Direction.OUTPUT, output_value=Value.ACTIVE
        )
    },
)
while True:
    request.set_value(LINE0, Value.ACTIVE)
    request.set_value(LINE1, Value.INACTIVE)
    time.sleep(1)
    request.set_value(LINE0, Value.INACTIVE)
    request.set_value(LINE1, Value.ACTIVE)
    time.sleep(1)

exit

from gpiozero import LED
from time import sleep

led0 = LED(23)
led1 = LED(24)

while True:
    led0.on()
    led1.on()
    sleep(1)
    led0.off()
    led1.off()
    sleep(1)


exit
import RPi.GPIO as GPIO
import time

GPIO.setmode(GPIO.BCM)

LEDS = [ 23, 24 ]

PWM_1 = 13

for led in LEDS:
    GPIO.setup(led, GPIO.OUT, initial=GPIO.LOW)

# GPIO.setup(PWM_1, GPIO.OUT, initial=GPIO.LOW)
# pwm1 = GPIO.PWM(PWM_1, 10.0) #this spins the laser
# pwm1.start(100)

print(f'Yellow!')

delay = .3
phase = 0

try:
    while True:
        for i in range(len(LEDS)):
            GPIO.output(LEDS[i], phase==i)
        phase += 1
        if phase > len(LEDS)-1:
            phase = 0
        # GPIO.output(LED_0, True)
        # GPIO.output(LED_1, False)
        # print(f'ON')
        # time.sleep(delay)
        # GPIO.output(LED_0, False)
        # GPIO.output(LED_1, True)
        print(f'Beep {phase}')
        time.sleep(delay)
except:
    pass

# pwm1.stop()

GPIO.cleanup()


